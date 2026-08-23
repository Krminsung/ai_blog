"""Append-only audit writer."""

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from blogops.core.context import request_id_context
from blogops.db.models.foundation import AuditLog


async def append_audit_log(
    session: AsyncSession,
    *,
    workspace_id: UUID | None,
    actor_id: UUID | None,
    action: str,
    target_type: str,
    target_id: str,
    details: dict[str, Any] | None = None,
    ip_hash: str | None = None,
) -> AuditLog:
    entry = AuditLog(
        workspace_id=workspace_id,
        actor_id=actor_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        details=details or {},
        request_id=request_id_context.get(),
        ip_hash=ip_hash,
    )
    session.add(entry)
    await session.flush()
    return entry
