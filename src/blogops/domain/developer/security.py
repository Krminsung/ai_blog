"""API-key, scope, rate-limit and webhook boundary security."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Iterable
from urllib.parse import urlsplit

from blogops.core.errors import AppError
from blogops.domain.knowledge.security import validate_resolved_addresses, validate_source_url

_KEY_MARKER = "bops"


@dataclass(frozen=True, slots=True)
class ApiKeyMaterial:
    raw: str
    prefix: str
    digest: str


def _require_pepper(pepper: bytes) -> None:
    if len(pepper) < 32:
        raise AppError(
            "API_KEY_HASHER_UNAVAILABLE",
            "API Key 해시용 관리형 비밀이 구성되지 않았습니다.",
            503,
        )


def hash_api_key(raw: str, *, pepper: bytes) -> str:
    _require_pepper(pepper)
    if not raw.startswith(f"{_KEY_MARKER}_") or len(raw) < 40:
        raise AppError("API_KEY_FORMAT_INVALID", "API Key 형식이 올바르지 않습니다.", 401)
    return hmac.new(pepper, raw.encode("utf-8"), hashlib.sha256).hexdigest()


def issue_api_key(*, environment: str, pepper: bytes) -> ApiKeyMaterial:
    _require_pepper(pepper)
    env = environment.casefold()
    if env not in {"production", "sandbox"}:
        raise AppError("API_KEY_ENVIRONMENT_INVALID", "지원하지 않는 API 환경입니다.", 422)
    raw = f"{_KEY_MARKER}_{'live' if env == 'production' else 'test'}_{secrets.token_urlsafe(32)}"
    # Prefix is an identifier, not enough material to authenticate.
    prefix = raw[:18]
    return ApiKeyMaterial(raw=raw, prefix=prefix, digest=hash_api_key(raw, pepper=pepper))


def verify_api_key(raw: str, expected_digest: str, *, pepper: bytes) -> bool:
    actual = hash_api_key(raw, pepper=pepper)
    return hmac.compare_digest(actual, expected_digest)


def authorize_key_scopes(
    *, requested: Iterable[str], actor_permissions: Iterable[str], workspace_scopes: Iterable[str]
) -> frozenset[str]:
    requested_set = frozenset(value.strip() for value in requested if value.strip())
    actor_set = frozenset(actor_permissions)
    workspace_set = frozenset(workspace_scopes)
    if not requested_set:
        raise AppError("API_KEY_SCOPE_REQUIRED", "API Key Scope를 하나 이상 선택해야 합니다.", 422)
    effective = requested_set.intersection(actor_set, workspace_set)
    if effective != requested_set:
        denied = sorted(requested_set.difference(effective))
        raise AppError(
            "API_KEY_SCOPE_ESCALATION",
            "사용자 또는 워크스페이스 한도를 넘는 Scope를 부여할 수 없습니다.",
            403,
            fields=[{"path": "scopes", "reason": value} for value in denied],
        )
    return effective


def validate_ip_allowlist(values: Iterable[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in values:
        try:
            network = ipaddress.ip_network(value, strict=False)
        except ValueError as exc:
            raise AppError("API_KEY_IP_INVALID", "IP 허용 범위가 올바르지 않습니다.", 422) from exc
        normalized.append(str(network))
    return tuple(sorted(set(normalized)))


def ip_is_allowed(address: str, allowlist: Iterable[str]) -> bool:
    values = tuple(allowlist)
    if not values:
        return True
    try:
        candidate = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(candidate in ipaddress.ip_network(value) for value in values)


@dataclass(frozen=True, slots=True)
class RateLimitRule:
    identity: str
    limit: int
    window_seconds: int
    burst: int
    concurrent_limit: int | None = None


def required_rate_limit_rules(
    *,
    workspace_rule: RateLimitRule | None,
    endpoint_rule: RateLimitRule | None,
    key_rule: RateLimitRule | None,
) -> tuple[RateLimitRule, ...]:
    """Return every independently enforced boundary; missing required config fails closed."""

    if workspace_rule is None or endpoint_rule is None or key_rule is None:
        raise AppError(
            "API_RATE_LIMIT_CONFIG_MISSING",
            "Workspace, Endpoint, Key Rate Limit 정책이 모두 필요합니다.",
            503,
        )
    values = (workspace_rule, endpoint_rule, key_rule)
    if any(
        value.limit <= 0
        or value.window_seconds <= 0
        or value.burst < 0
        or (value.concurrent_limit is not None and value.concurrent_limit <= 0)
        for value in values
    ):
        raise AppError("API_RATE_LIMIT_CONFIG_INVALID", "Rate Limit 정책이 올바르지 않습니다.", 503)
    return values


def validate_webhook_destination(url: str, *, resolved_addresses: list[str]) -> str:
    original = urlsplit(url.strip())
    if original.query or original.fragment:
        raise AppError(
            "WEBHOOK_URL_SECRET_CHANNEL_BLOCKED",
            "Webhook URL에는 Query 또는 Fragment를 포함할 수 없습니다. HMAC Secret을 사용하세요.",
            422,
        )
    validated = validate_source_url(url)
    if urlsplit(validated.normalized).scheme != "https":
        raise AppError("WEBHOOK_HTTPS_REQUIRED", "Webhook Endpoint는 HTTPS여야 합니다.", 422)
    validate_resolved_addresses(resolved_addresses)
    return validated.normalized


def webhook_signature(*, secret: bytes, timestamp: int, body: bytes) -> str:
    if len(secret) < 32:
        raise AppError("WEBHOOK_SECRET_INVALID", "Webhook 서명 비밀이 안전하지 않습니다.", 503)
    signed = str(timestamp).encode("ascii") + b"." + body
    return "v1=" + hmac.new(secret, signed, hashlib.sha256).hexdigest()


def verify_webhook_signature(
    *,
    secret: bytes,
    timestamp: int,
    body: bytes,
    provided: str,
    now: datetime,
    tolerance_seconds: int,
) -> None:
    if now.tzinfo is None:
        raise AppError("WEBHOOK_TIME_INVALID", "검증 시각은 timezone-aware여야 합니다.", 500)
    if tolerance_seconds <= 0:
        raise AppError("WEBHOOK_REPLAY_POLICY_MISSING", "Replay 허용 시간이 구성되지 않았습니다.", 503)
    current = int(now.astimezone(UTC).timestamp())
    if abs(current - timestamp) > tolerance_seconds:
        raise AppError("WEBHOOK_TIMESTAMP_EXPIRED", "Webhook Timestamp가 허용 범위를 벗어났습니다.", 401)
    expected = webhook_signature(secret=secret, timestamp=timestamp, body=body)
    if not hmac.compare_digest(expected, provided):
        raise AppError("WEBHOOK_SIGNATURE_INVALID", "Webhook 서명이 올바르지 않습니다.", 401)


def webhook_replay_key(*, endpoint_id: object, event_id: str, timestamp: int, body: bytes) -> str:
    digest = hashlib.sha256(body).hexdigest()
    return hashlib.sha256(
        f"{endpoint_id}:{event_id}:{timestamp}:{digest}".encode("utf-8")
    ).hexdigest()
