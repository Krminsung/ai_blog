"""Celery boundaries for approved admin commands and due notifications."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID

from celery import shared_task
from sqlalchemy import select

from blogops.core.errors import AppError
from blogops.db.session import apply_workspace_scope, get_database
from blogops.domain.admin.enums import NotificationDeliveryState
from blogops.domain.admin.models import NotificationDelivery
from blogops.domain.admin.providers import (
    AdminCommandExecutor,
    FailClosedAdminAdapters,
    NotificationSender,
)
from blogops.domain.admin.service import AdminService

_admin_command_executor: AdminCommandExecutor | None = None
_admin_worker_actor_id: UUID | None = None
_notification_sender: NotificationSender | None = None


def configure_admin_command_runtime(
    *,
    executor: AdminCommandExecutor,
    worker_actor_id: UUID,
) -> None:
    """Install the controlled executor and its auditable service identity."""

    global _admin_command_executor, _admin_worker_actor_id
    _admin_command_executor = executor
    _admin_worker_actor_id = worker_actor_id


def configure_notification_runtime(*, sender: NotificationSender) -> None:
    """Install the outbound notification sender during worker bootstrap."""

    global _notification_sender
    _notification_sender = sender


async def _run_admin_command(
    target_workspace_id: UUID | None,
    command_id: UUID,
) -> str:
    database = get_database()
    try:
        async with database.session_factory() as session:
            async with session.begin():
                if target_workspace_id is not None:
                    await apply_workspace_scope(session, target_workspace_id)
                service = AdminService(session)
                actor_id = _admin_worker_actor_id
                try:
                    if actor_id is None:
                        raise AppError(
                            "ADMIN_WORKER_IDENTITY_UNAVAILABLE",
                            "운영 명령 worker 서비스 ID가 구성되지 않았습니다.",
                            503,
                        )
                    command = await service.execute_ready_command(
                        command_id,
                        worker_actor_id=actor_id,
                        executor=(
                            _admin_command_executor or FailClosedAdminAdapters()
                        ),
                        expected_target_workspace_id=target_workspace_id,
                    )
                except AppError as exc:
                    command = await service.fail_ready_command(
                        command_id,
                        error_code=exc.code,
                        worker_actor_id=actor_id,
                        expected_target_workspace_id=target_workspace_id,
                    )
                except Exception:
                    command = await service.fail_ready_command(
                        command_id,
                        error_code="ADMIN_COMMAND_EXECUTION_FAILED",
                        worker_actor_id=actor_id,
                        expected_target_workspace_id=target_workspace_id,
                    )
                return command.state
    finally:
        await database.close()
        get_database.cache_clear()


@shared_task(name="admin.command.process")
def process_admin_command_task(
    target_workspace_id: str | None,
    command_id: str,
) -> str:
    workspace_id = UUID(target_workspace_id) if target_workspace_id is not None else None
    return asyncio.run(_run_admin_command(workspace_id, UUID(command_id)))


def enqueue_admin_command(
    target_workspace_id: UUID | None,
    command_id: UUID,
) -> None:
    process_admin_command_task.apply_async(
        args=(
            str(target_workspace_id) if target_workspace_id is not None else None,
            str(command_id),
        ),
        countdown=1,
    )


def _notification_retryable(error: AppError) -> bool:
    return error.status_code >= 500 or error.status_code in {408, 429}


async def _run_notification_delivery(
    workspace_id: UUID,
    delivery_id: UUID,
) -> tuple[str, datetime | None]:
    database = get_database()
    try:
        async with database.session_factory() as session:
            async with session.begin():
                await apply_workspace_scope(session, workspace_id)
                service = AdminService(session)
                try:
                    delivery = await service.execute_notification_delivery(
                        workspace_id,
                        delivery_id,
                        sender=_notification_sender or FailClosedAdminAdapters(),
                    )
                except AppError as exc:
                    delivery = await service.fail_notification_delivery(
                        workspace_id,
                        delivery_id,
                        error_code=exc.code,
                        retryable=_notification_retryable(exc),
                    )
                except Exception:
                    delivery = await service.fail_notification_delivery(
                        workspace_id,
                        delivery_id,
                        error_code="NOTIFICATION_DELIVERY_EXECUTION_FAILED",
                        retryable=True,
                    )
                return delivery.state, delivery.next_attempt_at
    finally:
        await database.close()
        get_database.cache_clear()


@shared_task(name="admin.notification_delivery.process")
def process_notification_delivery_task(workspace_id: str, delivery_id: str) -> str:
    parsed_workspace_id = UUID(workspace_id)
    parsed_delivery_id = UUID(delivery_id)
    state, next_attempt_at = asyncio.run(
        _run_notification_delivery(parsed_workspace_id, parsed_delivery_id)
    )
    if state == NotificationDeliveryState.PENDING.value:
        enqueue_notification_delivery(
            parsed_workspace_id,
            parsed_delivery_id,
            next_attempt_at,
        )
    return state


def enqueue_notification_delivery(
    workspace_id: UUID,
    delivery_id: UUID,
    next_attempt_at: datetime | None = None,
) -> None:
    if next_attempt_at is not None and next_attempt_at > datetime.now(UTC):
        process_notification_delivery_task.apply_async(
            args=(str(workspace_id), str(delivery_id)),
            eta=next_attempt_at,
        )
    else:
        process_notification_delivery_task.apply_async(
            args=(str(workspace_id), str(delivery_id)),
            countdown=1,
        )


async def _due_notification_delivery_ids(
    workspace_id: UUID,
    limit: int,
) -> list[UUID]:
    database = get_database()
    try:
        async with database.session_factory() as session:
            async with session.begin():
                await apply_workspace_scope(session, workspace_id)
                return list(
                    await session.scalars(
                        select(NotificationDelivery.id)
                        .where(
                            NotificationDelivery.workspace_id == workspace_id,
                            NotificationDelivery.state
                            == NotificationDeliveryState.PENDING.value,
                            NotificationDelivery.next_attempt_at <= datetime.now(UTC),
                        )
                        .order_by(
                            NotificationDelivery.next_attempt_at,
                            NotificationDelivery.id,
                        )
                        .limit(limit)
                        .with_for_update(skip_locked=True)
                    )
                )
    finally:
        await database.close()
        get_database.cache_clear()


@shared_task(name="admin.notification_deliveries.schedule_due")
def schedule_due_notification_deliveries_task(
    workspace_id: str,
    limit: int = 100,
) -> int:
    parsed_workspace_id = UUID(workspace_id)
    delivery_ids = asyncio.run(
        _due_notification_delivery_ids(
            parsed_workspace_id,
            min(max(limit, 1), 500),
        )
    )
    for delivery_id in delivery_ids:
        enqueue_notification_delivery(parsed_workspace_id, delivery_id)
    return len(delivery_ids)
