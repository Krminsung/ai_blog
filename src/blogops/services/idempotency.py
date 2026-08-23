"""Transactional idempotency reservations and response replay."""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.core.config import get_settings
from blogops.core.errors import AppError
from blogops.db.models.foundation import IdempotencyRecord, IdempotencyStatus


class ReservationKind(StrEnum):
    RESERVED = "RESERVED"
    REPLAY = "REPLAY"


@dataclass(frozen=True, slots=True)
class Reservation:
    kind: ReservationKind
    record_id: UUID
    response_status: int | None = None
    response_body: dict[str, Any] | None = None


def request_fingerprint(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def reserve(
    session: AsyncSession,
    *,
    namespace: str,
    operation: str,
    key: str,
    request_hash: str,
    workspace_id: UUID | None,
) -> Reservation:
    settings = get_settings()
    now = datetime.now(UTC)
    candidate_id = uuid4()
    await session.execute(
        insert(IdempotencyRecord)
        .values(
            id=candidate_id,
            workspace_id=workspace_id,
            namespace=namespace,
            operation=operation,
            key=key,
            request_hash=request_hash,
            status=IdempotencyStatus.PROCESSING.value,
            locked_until=now + timedelta(seconds=settings.idempotency_lock_seconds),
            expires_at=now + timedelta(seconds=settings.idempotency_ttl_seconds),
        )
        .on_conflict_do_nothing(constraint="idempotency_identity")
    )
    record = await session.scalar(
        select(IdempotencyRecord)
        .where(
            IdempotencyRecord.namespace == namespace,
            IdempotencyRecord.operation == operation,
            IdempotencyRecord.key == key,
        )
        .with_for_update()
    )
    if record is None:
        raise RuntimeError("idempotency reservation disappeared inside its transaction")
    if record.request_hash != request_hash:
        raise AppError(
            code="IDEMPOTENCY_KEY_REUSED",
            message="동일한 Idempotency-Key가 다른 요청에 사용되었습니다.",
            status_code=409,
        )
    if record.id == candidate_id:
        return Reservation(ReservationKind.RESERVED, record.id)
    if record.status == IdempotencyStatus.COMPLETED.value:
        return Reservation(
            ReservationKind.REPLAY,
            record.id,
            response_status=record.response_status,
            response_body=record.response_body,
        )
    if record.locked_until > now:
        raise AppError(
            code="IDEMPOTENT_REQUEST_IN_PROGRESS",
            message="동일한 요청이 이미 처리 중입니다.",
            status_code=409,
        )
    record.status = IdempotencyStatus.PROCESSING.value
    record.locked_until = now + timedelta(seconds=settings.idempotency_lock_seconds)
    record.expires_at = now + timedelta(seconds=settings.idempotency_ttl_seconds)
    record.response_status = None
    record.response_body = None
    return Reservation(ReservationKind.RESERVED, record.id)


async def complete(
    session: AsyncSession,
    record_id: UUID,
    *,
    response_status: int,
    response_body: dict[str, Any],
) -> None:
    record = await session.get(IdempotencyRecord, record_id, with_for_update=True)
    if record is None:
        raise RuntimeError("unknown idempotency record")
    record.status = IdempotencyStatus.COMPLETED.value
    record.response_status = response_status
    record.response_body = response_body


async def fail(session: AsyncSession, record_id: UUID) -> None:
    record = await session.get(IdempotencyRecord, record_id, with_for_update=True)
    if record is not None and record.status != IdempotencyStatus.COMPLETED.value:
        record.status = IdempotencyStatus.FAILED.value
        record.locked_until = datetime.now(UTC)
