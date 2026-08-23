"""Shared permission vocabulary and FastAPI authorization dependency."""

from collections.abc import Callable
from enum import StrEnum

from fastapi import Request

from blogops.core.context import Principal
from blogops.core.errors import AppError


class Permission(StrEnum):
    WORKSPACE_READ = "workspace:read"
    WORKSPACE_MANAGE = "workspace:manage"
    AUDIT_READ = "audit:read"
    BRAND_READ = "brand:read"
    BRAND_WRITE = "brand:write"
    KNOWLEDGE_READ = "knowledge:read"
    KNOWLEDGE_WRITE = "knowledge:write"
    KEYWORD_READ = "keyword:read"
    KEYWORD_WRITE = "keyword:write"
    KEYWORD_EXPORT = "keyword:export"
    PLANNING_READ = "planning:read"
    PLANNING_WRITE = "planning:write"
    PLANNING_APPROVE = "planning:approve"
    PLANNING_EXPORT = "planning:export"
    CONTENT_READ = "content:read"
    CONTENT_WRITE = "content:write"
    CONTENT_APPROVE = "content:approve"
    CONTENT_PUBLISH = "content:publish"
    MEDIA_READ = "media:read"
    MEDIA_WRITE = "media:write"
    MEDIA_MANAGE = "media:manage"
    BULK_READ = "bulk:read"
    BULK_WRITE = "bulk:write"
    BULK_APPROVE = "bulk:approve"
    BULK_EXPORT = "bulk:export"
    BULK_MANAGE = "bulk:manage"
    BILLING_READ = "billing:read"
    BILLING_MANAGE = "billing:manage"
    API_MANAGE = "api:manage"
    AGENCY_READ = "agency:read"
    AGENCY_MANAGE = "agency:manage"
    PORTAL_MANAGE = "portal:manage"


def get_principal(request: Request) -> Principal:
    principal = getattr(request.state, "principal", None)
    if not isinstance(principal, Principal):
        raise AppError(
            code="AUTHENTICATION_REQUIRED",
            message="인증이 필요합니다.",
            status_code=401,
        )
    return principal


def require_permissions(*required: Permission) -> Callable[[Request], Principal]:
    required_values = frozenset(item.value for item in required)

    def dependency(request: Request) -> Principal:
        principal = get_principal(request)
        missing = sorted(required_values.difference(principal.permissions))
        if missing:
            raise AppError(
                code="PERMISSION_DENIED",
                message="이 작업을 수행할 권한이 없습니다.",
                status_code=403,
                fields=[{"path": "permissions", "reason": value} for value in missing],
            )
        return principal

    return dependency
