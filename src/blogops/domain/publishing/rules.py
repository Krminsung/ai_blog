"""Pure publishing rules for hashing, retry policy, schedules and Naver handoff."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
import hashlib
import json
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from blogops.core.errors import AppError
from blogops.domain.publishing.enums import RetryClass


NAVER_MANUAL_POLICY_VERSION = "2026-08-23"
NAVER_MANUAL_POLICY_NOTICE = (
    "네이버 계정 비밀번호·쿠키를 수집하지 않으며 자동 로그인, DOM 자동 입력, "
    "헤드리스 게시를 수행하지 않습니다. 사용자가 내용을 검토하고 직접 게시해야 합니다."
)
RETRYABLE_HTTP_STATUSES = frozenset({429})
SECRET_KEY_PATTERN = re.compile(
    r"(?i)(authorization|cookie|token|secret|password|api[-_]?key|credential)"
)
AUTH_VALUE_PATTERN = re.compile(
    r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]+"
)
SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(password|token|secret|api[-_]?key|cookie|authorization)"
    r"(\s*[:=]\s*)([^\s,;]+)"
)
_POST_ID = re.compile(r"^[0-9]{1,20}$")
_NAVER_BLOG_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,48}[A-Za-z0-9]$")


def canonical_hash(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def redact_metadata(value: Any) -> Any:
    """Return bounded metadata with secret-like keys and authorization values removed."""

    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = str(raw_key)[:160]
            redacted[key] = "[REDACTED]" if SECRET_KEY_PATTERN.search(key) else redact_metadata(item)
        return redacted
    if isinstance(value, list):
        return [redact_metadata(item) for item in value[:1_000]]
    if isinstance(value, str):
        return redact_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:4_000]


def redact_text(value: str) -> str:
    sanitized = AUTH_VALUE_PATTERN.sub("[REDACTED]", value[:4_000])
    return SECRET_ASSIGNMENT_PATTERN.sub(r"\1\2[REDACTED]", sanitized)


def classify_retry(*, network_error: bool, status_code: int | None) -> RetryClass:
    if network_error:
        return RetryClass.NETWORK
    if status_code == 429:
        return RetryClass.RATE_LIMIT
    if status_code in RETRYABLE_HTTP_STATUSES or (
        status_code is not None and 500 <= status_code <= 599
    ):
        return RetryClass.SERVER
    return RetryClass.FINAL


def retry_allowed(*, network_error: bool, status_code: int | None) -> bool:
    return classify_retry(network_error=network_error, status_code=status_code) is not RetryClass.FINAL


@dataclass(frozen=True, slots=True)
class ValidatedSchedule:
    scheduled_at_utc: datetime
    scheduled_local: datetime
    timezone_name: str
    fold: int
    local_day: date


def validate_schedule(
    *,
    scheduled_at_utc: datetime,
    scheduled_local: datetime,
    timezone_name: str,
    fold: int | None,
) -> ValidatedSchedule:
    if scheduled_at_utc.tzinfo is None or scheduled_at_utc.utcoffset() is None:
        raise AppError("PUBLISH_SCHEDULE_UTC_REQUIRED", "UTC 예약 시각은 시간대 정보가 필요합니다.", 422)
    supplied_utc = scheduled_at_utc.astimezone(UTC)
    if scheduled_at_utc.utcoffset() != UTC.utcoffset(scheduled_at_utc):
        raise AppError("PUBLISH_SCHEDULE_NOT_UTC", "scheduled_at_utc는 UTC 오프셋이어야 합니다.", 422)
    if scheduled_local.tzinfo is not None:
        raise AppError("PUBLISH_LOCAL_TIME_MUST_BE_NAIVE", "로컬 예약 시각에는 오프셋을 넣지 마세요.", 422)
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise AppError("PUBLISH_TIMEZONE_INVALID", "사이트 시간대를 찾을 수 없습니다.", 422) from exc

    candidates: dict[int, datetime] = {}
    for candidate_fold in (0, 1):
        aware = scheduled_local.replace(tzinfo=zone, fold=candidate_fold)
        round_trip = aware.astimezone(UTC).astimezone(zone).replace(tzinfo=None)
        if round_trip == scheduled_local:
            candidates[candidate_fold] = aware.astimezone(UTC)
    if not candidates:
        raise AppError("PUBLISH_DST_NONEXISTENT_TIME", "DST 전환으로 존재하지 않는 로컬 시각입니다.", 422)
    distinct = {value for value in candidates.values()}
    ambiguous = len(distinct) > 1
    if ambiguous and fold is None:
        raise AppError("PUBLISH_DST_FOLD_REQUIRED", "중복되는 DST 로컬 시각에는 dst_fold가 필요합니다.", 422)
    if not ambiguous and fold == 1:
        raise AppError(
            "PUBLISH_DST_FOLD_INVALID",
            "중복되지 않는 로컬 시각에는 dst_fold=1을 사용할 수 없습니다.",
            422,
        )
    chosen_fold = fold if fold is not None else 0
    if chosen_fold not in candidates:
        raise AppError("PUBLISH_DST_FOLD_INVALID", "dst_fold가 로컬 시각과 일치하지 않습니다.", 422)
    resolved_utc = candidates[chosen_fold]
    if resolved_utc != supplied_utc:
        raise AppError(
            "PUBLISH_SCHEDULE_MISMATCH",
            "UTC 시각과 사이트 로컬 시각이 같은 순간을 가리키지 않습니다.",
            422,
        )
    return ValidatedSchedule(
        scheduled_at_utc=supplied_utc,
        scheduled_local=scheduled_local,
        timezone_name=timezone_name,
        fold=chosen_fold,
        local_day=scheduled_local.date(),
    )


def validate_naver_post(value: str, post_id: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme.lower() != "https" or parsed.hostname != "blog.naver.com":
        raise AppError(
            "NAVER_POST_URL_INVALID",
            "수동 완료 URL은 https://blog.naver.com 형식이어야 합니다.",
            422,
        )
    if parsed.username or parsed.password or parsed.port not in {None, 443}:
        raise AppError("NAVER_POST_URL_INVALID", "인증정보나 사용자 지정 포트는 허용되지 않습니다.", 422)
    if parsed.query or parsed.fragment:
        raise AppError("NAVER_POST_URL_INVALID", "쿼리와 프래그먼트가 없는 게시 URL을 입력하세요.", 422)
    if not _POST_ID.fullmatch(post_id):
        raise AppError("NAVER_POST_ID_INVALID", "네이버 Post ID 형식이 올바르지 않습니다.", 422)
    segments = [segment for segment in parsed.path.split("/") if segment]
    if (
        len(segments) != 2
        or not _NAVER_BLOG_ID.fullmatch(segments[0])
        or segments[1] != post_id
    ):
        raise AppError("NAVER_POST_ID_MISMATCH", "URL의 마지막 경로와 Post ID가 일치해야 합니다.", 422)
    return urlunsplit(("https", "blog.naver.com", "/" + "/".join(segments), "", ""))


def quota_for(policy: dict[str, int], provider: str, channel: str) -> int | None:
    for key in (f"{provider}:{channel}", provider, f"channel:{channel}", "default"):
        if key in policy:
            value = policy[key]
            if value < 1:
                raise AppError("PUBLISH_QUOTA_POLICY_INVALID", "일일 발행 한도는 1 이상이어야 합니다.", 409)
            return value
    return None
