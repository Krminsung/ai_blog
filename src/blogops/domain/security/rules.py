"""Pure security, data-rights, retention, and evidence rules."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from blogops.core.errors import AppError
from blogops.domain.security.enums import (
    CopyrightCaseState,
    DataClass,
    PrivacyActionKind,
    PrivacyActionState,
    PrivacyRequestKind,
    PrivacyRequestState,
    SecurityIncidentState,
)

_SENSITIVE_MARKERS = frozenset(
    {
        "authorization",
        "cookie",
        "credential",
        "email",
        "password",
        "phone",
        "secret",
        "token",
        "api_key",
        "apikey",
        "raw_content",
        "prompt",
    }
)
_EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE_PATTERN = re.compile(r"(?<!\d)(?:\+?82[- .]?)?0?1[016789](?:[- .]?\d){7,8}(?!\d)")
_BEARER_PATTERN = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)


def canonical_json_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def is_sha256_hex(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and set(value).issubset(frozenset("0123456789abcdef"))
    )


def append_evidence_hash(previous_hash: str | None, payload: Mapping[str, Any]) -> str:
    return canonical_json_hash(
        {"previous_event_hash": previous_hash, "payload": redact_safe_metadata(payload)}
    )


def redact_safe_metadata(value: Any, *, key: str | None = None) -> Any:
    if key is not None:
        normalized = key.casefold().replace("-", "_").replace(".", "_")
        parts = frozenset(part for part in normalized.split("_") if part)
        if normalized in _SENSITIVE_MARKERS or parts.intersection(_SENSITIVE_MARKERS):
            return "[REDACTED]"
    if isinstance(value, Mapping):
        return {
            str(child_key): redact_safe_metadata(child, key=str(child_key))
            for child_key, child in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_safe_metadata(child) for child in value]
    if isinstance(value, str):
        return redact_safe_text(value)
    return value


def redact_safe_text(value: str) -> str:
    value = _EMAIL_PATTERN.sub("[EMAIL]", value)
    value = _PHONE_PATTERN.sub("[PHONE]", value)
    return _BEARER_PATTERN.sub("Bearer [REDACTED]", value)


def require_secret_reference(value: str, *, path: str) -> str:
    allowed = ("secret-manager://", "vault://", "kms-envelope://")
    if not value.startswith(allowed):
        raise AppError(
            code="SECURE_REFERENCE_REQUIRED",
            message="민감 정보는 평문이 아닌 보안 저장소 참조로 전달해야 합니다.",
            status_code=422,
            fields=[{"path": path, "reason": "secure reference required"}],
        )
    return value


def validate_secure_download_url(value: str) -> None:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise AppError(
            "PRIVACY_DOWNLOAD_URL_UNSAFE",
            "내보내기 다운로드 주소는 자격 증명이 없는 HTTPS 주소여야 합니다.",
            503,
        )
    host = parsed.hostname.rstrip(".").casefold()
    if host == "localhost" or host.endswith(".localhost"):
        raise AppError(
            "PRIVACY_DOWNLOAD_URL_UNSAFE",
            "내보내기 다운로드 주소가 안전하지 않습니다.",
            503,
        )
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return
    if not address.is_global:
        raise AppError(
            "PRIVACY_DOWNLOAD_URL_UNSAFE",
            "내보내기 다운로드 주소가 안전하지 않습니다.",
            503,
        )


def validate_retention_rules(
    rules: Mapping[str, Mapping[str, Any]],
    *,
    minimum_days: Mapping[str, int],
    maximum_days: Mapping[str, int | None],
) -> None:
    expected = {item.value for item in DataClass}
    missing = sorted(expected.difference(rules))
    unknown = sorted(set(rules).difference(expected))
    if missing or unknown:
        raise AppError(
            code="RETENTION_POLICY_INCOMPLETE",
            message="보존 정책에는 모든 데이터 유형의 규칙이 정확히 포함되어야 합니다.",
            status_code=422,
            fields=[
                *({"path": f"rules.{value}", "reason": "required"} for value in missing),
                *({"path": f"rules.{value}", "reason": "unknown"} for value in unknown),
            ],
        )
    allowed_dispositions = {"DELETE", "ANONYMIZE", "RESTRICT", "LEGAL_ARCHIVE"}
    for data_class, rule in rules.items():
        required = {"retention_days", "grace_days", "disposition"}
        if not required <= set(rule):
            raise AppError(
                code="RETENTION_RULE_INCOMPLETE",
                message="데이터 유형별 보존 규칙이 불완전합니다.",
                status_code=422,
                fields=[{"path": f"rules.{data_class}", "reason": "missing fields"}],
            )
        retention_days = rule["retention_days"]
        grace_days = rule["grace_days"]
        if not isinstance(retention_days, int) or not isinstance(grace_days, int):
            raise AppError(
                code="RETENTION_DURATION_INVALID",
                message="보존 및 유예 기간은 일 단위 정수여야 합니다.",
                status_code=422,
            )
        minimum = minimum_days.get(data_class)
        maximum = maximum_days.get(data_class)
        if retention_days < 0 or grace_days < 0:
            raise AppError(
                code="RETENTION_DURATION_INVALID",
                message="보존 및 유예 기간은 음수일 수 없습니다.",
                status_code=422,
            )
        if minimum is None or retention_days < minimum:
            raise AppError(
                code="RETENTION_LEGAL_MINIMUM_VIOLATION",
                message="보존 기간이 서버 법률·계약 정책의 최소 기간보다 짧습니다.",
                status_code=422,
                fields=[{"path": f"rules.{data_class}.retention_days", "reason": "minimum"}],
            )
        if maximum is not None and retention_days > maximum:
            raise AppError(
                code="RETENTION_MINIMIZATION_VIOLATION",
                message="보존 기간이 서버 최소수집 정책의 최대 기간을 넘었습니다.",
                status_code=422,
                fields=[{"path": f"rules.{data_class}.retention_days", "reason": "maximum"}],
            )
        if rule["disposition"] not in allowed_dispositions:
            raise AppError(
                code="RETENTION_DISPOSITION_INVALID",
                message="지원하지 않는 보존 만료 처리 방식입니다.",
                status_code=422,
            )


_PRIVACY_TRANSITIONS: dict[str, frozenset[str]] = {
    PrivacyRequestState.IDENTITY_PENDING.value: frozenset(
        {
            PrivacyRequestState.VERIFIED.value,
            PrivacyRequestState.REJECTED.value,
            PrivacyRequestState.CANCELLED.value,
        }
    ),
    PrivacyRequestState.VERIFIED.value: frozenset(
        {
            PrivacyRequestState.ON_HOLD.value,
            PrivacyRequestState.QUEUED.value,
            PrivacyRequestState.REJECTED.value,
            PrivacyRequestState.CANCELLED.value,
        }
    ),
    PrivacyRequestState.ON_HOLD.value: frozenset(
        {PrivacyRequestState.QUEUED.value, PrivacyRequestState.CANCELLED.value}
    ),
    PrivacyRequestState.QUEUED.value: frozenset(
        {
            PrivacyRequestState.ON_HOLD.value,
            PrivacyRequestState.PROCESSING.value,
            PrivacyRequestState.CANCELLED.value,
        }
    ),
    PrivacyRequestState.PROCESSING.value: frozenset(
        {
            PrivacyRequestState.PARTIAL.value,
            PrivacyRequestState.COMPLETED.value,
            PrivacyRequestState.FAILED.value,
        }
    ),
    PrivacyRequestState.PARTIAL.value: frozenset(
        {PrivacyRequestState.QUEUED.value, PrivacyRequestState.COMPLETED.value}
    ),
    PrivacyRequestState.COMPLETED.value: frozenset(),
    PrivacyRequestState.REJECTED.value: frozenset(),
    PrivacyRequestState.CANCELLED.value: frozenset(),
    PrivacyRequestState.FAILED.value: frozenset({PrivacyRequestState.QUEUED.value}),
}


def ensure_privacy_transition(current: str, target: str) -> None:
    if target not in _PRIVACY_TRANSITIONS.get(current, frozenset()):
        raise AppError(
            code="PRIVACY_REQUEST_TRANSITION_INVALID",
            message="데이터 권리 요청 상태를 해당 상태로 변경할 수 없습니다.",
            status_code=409,
            fields=[{"path": "state", "reason": f"{current}->{target}"}],
        )


def required_actions_for_request(kind: PrivacyRequestKind) -> frozenset[PrivacyActionKind]:
    if kind in {PrivacyRequestKind.ACCESS, PrivacyRequestKind.EXPORT}:
        return frozenset({PrivacyActionKind.EXPORT})
    if kind == PrivacyRequestKind.CORRECT:
        return frozenset({PrivacyActionKind.CORRECT})
    if kind == PrivacyRequestKind.RESTRICT_PROCESSING:
        return frozenset({PrivacyActionKind.RESTRICT_PROCESSING})
    return frozenset(
        {
            PrivacyActionKind.DELETE_DATABASE,
            PrivacyActionKind.DELETE_SEARCH,
            PrivacyActionKind.DELETE_VECTOR,
            PrivacyActionKind.DELETE_OBJECTS,
            PrivacyActionKind.REVOKE_CREDENTIALS,
            PrivacyActionKind.ANONYMIZE_ANALYTICS,
            PrivacyActionKind.SCHEDULE_BACKUP_ERASURE,
        }
    )


def validate_action_plan(
    *,
    request_kind: PrivacyRequestKind,
    requested_data_classes: Iterable[str],
    actions: Sequence[Mapping[str, Any]],
) -> None:
    requested_classes = set(requested_data_classes)
    if not actions:
        raise AppError(
            code="PRIVACY_ACTION_PLAN_EMPTY",
            message="검증된 데이터 권리 실행 계획이 없습니다.",
            status_code=503,
        )
    planned_kinds: set[PrivacyActionKind] = set()
    covered_classes: set[str] = set()
    for action in actions:
        try:
            kind = PrivacyActionKind(str(action["kind"]))
        except (KeyError, ValueError) as exc:
            raise AppError(
                code="PRIVACY_ACTION_PLAN_INVALID",
                message="실행 계획에 지원하지 않는 작업이 포함되어 있습니다.",
                status_code=503,
            ) from exc
        classes = {str(value) for value in action.get("data_classes", [])}
        if (
            not classes
            or not classes <= {item.value for item in DataClass}
            or not classes <= requested_classes
        ):
            raise AppError(
                code="PRIVACY_ACTION_DATA_CLASS_INVALID",
                message="실행 계획의 데이터 범위가 요청 범위를 벗어났습니다.",
                status_code=503,
            )
        target_system = action.get("target_system")
        if not isinstance(target_system, str) or not 1 <= len(target_system) <= 120:
            raise AppError(
                code="PRIVACY_ACTION_TARGET_INVALID",
                message="실행 계획의 대상 시스템이 올바르지 않습니다.",
                status_code=503,
            )
        planned_kinds.add(kind)
        covered_classes.update(classes)
    missing_kinds = required_actions_for_request(request_kind).difference(planned_kinds)
    missing_classes = requested_classes.difference(covered_classes)
    if missing_kinds or missing_classes:
        raise AppError(
            code="PRIVACY_ACTION_PLAN_INCOMPLETE",
            message="실행 계획이 요청 유형 또는 데이터 범위를 완전히 다루지 않습니다.",
            status_code=503,
            fields=[
                *(
                    {"path": "actions", "reason": f"missing:{value.value}"}
                    for value in sorted(missing_kinds, key=lambda item: item.value)
                ),
                *(
                    {"path": "data_classes", "reason": f"uncovered:{value}"}
                    for value in sorted(missing_classes)
                ),
            ],
        )


def privacy_completion_state(action_states: Iterable[str]) -> PrivacyRequestState:
    states = list(action_states)
    if not states:
        return PrivacyRequestState.FAILED
    if any(state == PrivacyActionState.FAILED.value for state in states):
        if any(state == PrivacyActionState.SUCCEEDED.value for state in states):
            return PrivacyRequestState.PARTIAL
        return PrivacyRequestState.FAILED
    if all(
        state
        in {
            PrivacyActionState.SUCCEEDED.value,
            PrivacyActionState.SKIPPED_LEGAL_HOLD.value,
        }
        for state in states
    ):
        if any(state == PrivacyActionState.SKIPPED_LEGAL_HOLD.value for state in states):
            return PrivacyRequestState.PARTIAL
        return PrivacyRequestState.COMPLETED
    return PrivacyRequestState.PROCESSING


def authorize_export_download(
    *,
    request_state: str,
    expires_at: datetime,
    now: datetime,
    download_count: int,
    maximum_downloads: int,
) -> None:
    if request_state != PrivacyRequestState.COMPLETED.value:
        raise AppError(
            code="PRIVACY_EXPORT_NOT_READY",
            message="데이터 내보내기가 완료되지 않았습니다.",
            status_code=409,
        )
    if expires_at <= now:
        raise AppError(
            code="PRIVACY_EXPORT_EXPIRED",
            message="데이터 내보내기 링크가 만료되었습니다.",
            status_code=410,
        )
    if maximum_downloads <= 0 or download_count >= maximum_downloads:
        raise AppError(
            code="PRIVACY_EXPORT_DOWNLOAD_LIMIT",
            message="데이터 내보내기 다운로드 한도에 도달했습니다.",
            status_code=410,
        )


_COPYRIGHT_TRANSITIONS: dict[str, frozenset[str]] = {
    CopyrightCaseState.RECEIVED.value: frozenset(
        {CopyrightCaseState.VALIDATING.value, CopyrightCaseState.REJECTED.value}
    ),
    CopyrightCaseState.VALIDATING.value: frozenset(
        {
            CopyrightCaseState.TEMPORARY_ACTION.value,
            CopyrightCaseState.LEGAL_REVIEW.value,
            CopyrightCaseState.REJECTED.value,
            CopyrightCaseState.FAILED.value,
        }
    ),
    CopyrightCaseState.TEMPORARY_ACTION.value: frozenset(
        {
            CopyrightCaseState.WAITING_COUNTER_NOTICE.value,
            CopyrightCaseState.LEGAL_REVIEW.value,
            CopyrightCaseState.REMOVED.value,
            CopyrightCaseState.FAILED.value,
        }
    ),
    CopyrightCaseState.WAITING_COUNTER_NOTICE.value: frozenset(
        {
            CopyrightCaseState.LEGAL_REVIEW.value,
            CopyrightCaseState.REMOVED.value,
        }
    ),
    CopyrightCaseState.LEGAL_REVIEW.value: frozenset(
        {
            CopyrightCaseState.RESTORED.value,
            CopyrightCaseState.REMOVED.value,
            CopyrightCaseState.REJECTED.value,
        }
    ),
    CopyrightCaseState.RESTORED.value: frozenset({CopyrightCaseState.CLOSED.value}),
    CopyrightCaseState.REMOVED.value: frozenset({CopyrightCaseState.CLOSED.value}),
    CopyrightCaseState.REJECTED.value: frozenset({CopyrightCaseState.CLOSED.value}),
    CopyrightCaseState.FAILED.value: frozenset({CopyrightCaseState.VALIDATING.value}),
    CopyrightCaseState.CLOSED.value: frozenset(),
}


def ensure_copyright_transition(current: str, target: str) -> None:
    if target not in _COPYRIGHT_TRANSITIONS.get(current, frozenset()):
        raise AppError(
            code="COPYRIGHT_CASE_TRANSITION_INVALID",
            message="저작권 신고 상태를 해당 상태로 변경할 수 없습니다.",
            status_code=409,
        )


_INCIDENT_TRANSITIONS: dict[str, frozenset[str]] = {
    SecurityIncidentState.DETECTED.value: frozenset(
        {SecurityIncidentState.TRIAGED.value, SecurityIncidentState.CONTAINING.value}
    ),
    SecurityIncidentState.TRIAGED.value: frozenset(
        {SecurityIncidentState.CONTAINING.value, SecurityIncidentState.CONTAINED.value}
    ),
    SecurityIncidentState.CONTAINING.value: frozenset(
        {SecurityIncidentState.CONTAINED.value}
    ),
    SecurityIncidentState.CONTAINED.value: frozenset(
        {SecurityIncidentState.RECOVERING.value}
    ),
    SecurityIncidentState.RECOVERING.value: frozenset(
        {SecurityIncidentState.MONITORING.value, SecurityIncidentState.RESOLVED.value}
    ),
    SecurityIncidentState.MONITORING.value: frozenset(
        {SecurityIncidentState.RECOVERING.value, SecurityIncidentState.RESOLVED.value}
    ),
    SecurityIncidentState.RESOLVED.value: frozenset(),
}


def ensure_security_incident_transition(current: str, target: str) -> None:
    if target not in _INCIDENT_TRANSITIONS.get(current, frozenset()):
        raise AppError(
            code="SECURITY_INCIDENT_TRANSITION_INVALID",
            message="보안 사건 상태를 해당 상태로 변경할 수 없습니다.",
            status_code=409,
        )
