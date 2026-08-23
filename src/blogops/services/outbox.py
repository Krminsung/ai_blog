"""Transactional outbox creation and worker-safe claiming."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.db.models.foundation import OutboxEvent


async def add_outbox_event(
    session: AsyncSession,
    *,
    workspace_id: UUID | None,
    aggregate_type: str,
    aggregate_id: str,
    event_type: str,
    schema_version: str,
    payload: dict[str, Any],
) -> OutboxEvent:
    event = OutboxEvent(
        workspace_id=workspace_id,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        event_type=event_type,
        schema_version=schema_version,
        payload=payload,
    )
    session.add(event)
    await session.flush()
    return event


async def claim_outbox_batch(session: AsyncSession, limit: int) -> list[OutboxEvent]:
    now = datetime.now(UTC)
    events = list(
        await session.scalars(
            select(OutboxEvent)
            .where(
                OutboxEvent.published_at.is_(None),
                OutboxEvent.next_attempt_at <= now,
            )
            .order_by(OutboxEvent.occurred_at, OutboxEvent.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )
    for event in events:
        event.locked_at = now
        event.attempt_count += 1
    return events
