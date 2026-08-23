"""Pure agency hierarchy and client-portal authorization rules."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable
from uuid import UUID

from blogops.core.errors import AppError

PORTAL_ALLOWED_SCOPES = frozenset(
    {
        "content:read",
        "content:comment",
        "content:approve",
        "report:read",
        "notification:read",
    }
)
PORTAL_FORBIDDEN_SCOPES = frozenset(
    {
        "billing:manage",
        "api:manage",
        "workspace:manage",
        "content:publish",
        "secret:read",
        "admin:operate",
    }
)


def ensure_client_isolation(*, agency_workspace_id: UUID, client_workspace_id: UUID) -> None:
    if agency_workspace_id == client_workspace_id:
        raise AppError(
            "AGENCY_CLIENT_WORKSPACE_REQUIRED",
            "대행사와 고객은 서로 다른 워크스페이스여야 합니다.",
            422,
        )


def authorize_portal_scopes(
    *, requested: Iterable[str], relationship_permissions: Iterable[str]
) -> frozenset[str]:
    values = frozenset(requested)
    permitted = frozenset(relationship_permissions).intersection(PORTAL_ALLOWED_SCOPES)
    if not values or values.intersection(PORTAL_FORBIDDEN_SCOPES) or not values <= permitted:
        raise AppError(
            "PORTAL_SCOPE_ESCALATION",
            "고객 포털은 해당 고객의 검수·댓글·보고서 최소 권한만 사용할 수 있습니다.",
            403,
        )
    return values


def require_portal_target(
    *, grant_client_workspace_id: UUID, requested_workspace_id: UUID, grant_state: str,
    expires_at: datetime | None, now: datetime
) -> None:
    if requested_workspace_id != grant_client_workspace_id:
        raise AppError("PORTAL_RESOURCE_NOT_FOUND", "리소스를 찾을 수 없습니다.", 404)
    if grant_state != "ACTIVE" or (expires_at is not None and expires_at <= now):
        raise AppError("PORTAL_GRANT_INACTIVE", "고객 포털 권한이 만료되었거나 해제되었습니다.", 403)


@dataclass(frozen=True, slots=True)
class PortalTokenMaterial:
    raw: str
    prefix: str
    digest: str


def issue_portal_token(*, pepper: bytes) -> PortalTokenMaterial:
    if len(pepper) < 32:
        raise AppError("PORTAL_TOKEN_HASHER_UNAVAILABLE", "포털 Token 비밀이 구성되지 않았습니다.", 503)
    raw = f"prt_{secrets.token_urlsafe(32)}"
    return PortalTokenMaterial(
        raw=raw,
        prefix=raw[:12],
        digest=hmac.new(pepper, raw.encode(), hashlib.sha256).hexdigest(),
    )


def verify_portal_token(raw: str, digest: str, *, pepper: bytes) -> bool:
    if len(pepper) < 32:
        raise AppError("PORTAL_TOKEN_HASHER_UNAVAILABLE", "포털 Token 비밀이 구성되지 않았습니다.", 503)
    candidate = hmac.new(pepper, raw.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(candidate, digest)


def normalize_white_label_domain(value: str) -> str:
    domain = value.strip().rstrip(".").encode("idna").decode("ascii").lower()
    if len(domain) > 253 or not re.fullmatch(
        r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", domain
    ):
        raise AppError("WHITE_LABEL_DOMAIN_INVALID", "화이트라벨 도메인이 올바르지 않습니다.", 422)
    return domain
