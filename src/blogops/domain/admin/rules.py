"""Pure least-privilege, approval and notification policy rules."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Iterable
from uuid import UUID

from blogops.core.errors import AppError

SUPPORT_ALLOWED_SCOPES = frozenset(
    {"metadata:read", "job:read", "connection:status", "content:read_masked"}
)
IMPERSONATION_FORBIDDEN_ACTIONS = frozenset(
    {
        "billing.payment_method.change",
        "billing.purchase",
        "workspace.owner.change",
        "api_key.create",
        "api_key.rotate",
        "secret.read",
        "mfa.disable",
    }
)
MANDATORY_NOTIFICATION_TYPES = frozenset({"SECURITY", "PRIVACY", "ACCOUNT_SUSPENSION"})
_SENSITIVE_KEYS = frozenset(
    {
        "password",
        "secret",
        "token",
        "api_key",
        "authorization",
        "cookie",
        "body",
        "content",
        "email",
        "phone",
    }
)


def authorize_support_scopes(
    *, requested: Iterable[str], customer_approved_content: bool
) -> frozenset[str]:
    values = frozenset(requested)
    if not values or not values <= SUPPORT_ALLOWED_SCOPES:
        raise AppError("ADMIN_SUPPORT_SCOPE_DENIED", "지원 접근 Scope가 허용 범위를 넘었습니다.", 403)
    if "content:read_masked" in values and not customer_approved_content:
        raise AppError(
            "ADMIN_CUSTOMER_CONSENT_REQUIRED",
            "마스킹된 본문 접근에도 고객의 명시적 동의가 필요합니다.",
            403,
        )
    return values


def require_active_admin_session(
    *, state: str, expires_at: datetime, now: datetime, action: str
) -> None:
    if state != "ACTIVE" or expires_at <= now:
        raise AppError("ADMIN_SESSION_INACTIVE", "운영자 지원 세션이 활성 상태가 아닙니다.", 403)
    if action in IMPERSONATION_FORBIDDEN_ACTIONS:
        raise AppError(
            "ADMIN_IMPERSONATION_ACTION_BLOCKED",
            "가장 보기 세션에서는 결제·비밀·소유권 보안 작업을 수행할 수 없습니다.",
            403,
        )


def validate_two_person_approval(
    *, requested_by: UUID, approver_id: UUID, prior_approver_ids: Iterable[UUID]
) -> None:
    if requested_by == approver_id or approver_id in frozenset(prior_approver_ids):
        raise AppError(
            "ADMIN_SEPARATION_OF_DUTIES",
            "요청자와 승인자는 달라야 하며 한 운영자가 중복 승인할 수 없습니다.",
            409,
        )


def redact_admin_metadata(value: Any, *, key: str | None = None) -> Any:
    if key is not None:
        normalized = key.casefold().replace("-", "_").replace(".", "_")
        key_parts = frozenset(part for part in normalized.split("_") if part)
        if normalized in _SENSITIVE_KEYS or key_parts.intersection(_SENSITIVE_KEYS):
            return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): redact_admin_metadata(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_admin_metadata(item) for item in value]
    if isinstance(value, tuple):
        return [redact_admin_metadata(item) for item in value]
    return value


def audit_payload_hash(value: dict[str, Any]) -> str:
    redacted = redact_admin_metadata(value)
    encoded = json.dumps(
        redacted,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


def validate_notification_preference(*, event_type: str, frequency: str) -> None:
    event_family = event_type.split(".", 1)[0].upper()
    if event_family in MANDATORY_NOTIFICATION_TYPES and frequency == "DISABLED":
        raise AppError(
            "NOTIFICATION_MANDATORY",
            "필수 보안·개인정보 알림은 해제할 수 없습니다.",
            422,
        )
