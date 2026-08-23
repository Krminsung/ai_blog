"""Celery consumers for durable publishing; HTTP request handlers never publish remotely."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

from celery import shared_task
from sqlalchemy import select

from blogops.domain.billing.adapters import create_publishing_entitlement_resolver
from blogops.db.session import apply_workspace_scope, get_database
from blogops.domain.jobs.state import JobState
from blogops.core.context import Principal
from blogops.domain.publishing.enums import (
    ConflictAction,
    ConnectionOperation,
    ConnectionState,
    PublishOperation,
    PublishedPostState,
)
from blogops.domain.publishing.models import (
    PublishedPost,
    PublishingConnection,
    PublishingConnectionJob,
    PublishingNotification,
    PublishJob,
)
from blogops.domain.publishing.providers import (
    get_media_resolver,
    get_provider_registry,
    get_secret_resolver,
)
from blogops.domain.publishing.references import SQLAlchemyPublishingReadinessResolver
from blogops.domain.publishing.saga import process_connection_job, process_publish_job
from blogops.domain.publishing.schemas import ConnectionCommandCreate, ReconcileCreate
from blogops.domain.publishing.service import PublishingService
from blogops.services.outbox import add_outbox_event


async def _run_publish(workspace_id: UUID, job_id: UUID) -> tuple[str, int | None]:
    database = get_database()
    try:
        async with database.session_factory() as session:
            async with session.begin():
                await apply_workspace_scope(session, workspace_id)
                return await process_publish_job(
                    session,
                    workspace_id=workspace_id,
                    job_id=job_id,
                    readiness=SQLAlchemyPublishingReadinessResolver(session),
                    providers=get_provider_registry(),
                    secrets=get_secret_resolver(),
                    media_resolver=get_media_resolver(),
                )
    finally:
        await database.close()
        get_database.cache_clear()


@shared_task(
    bind=True,
    name="publishing.process",
    max_retries=3,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=3_600,
    retry_jitter=True,
)
def process_publish_job_task(task, workspace_id: str, job_id: str) -> str:
    state, retry_after = asyncio.run(_run_publish(UUID(workspace_id), UUID(job_id)))
    if state == JobState.RETRYABLE_FAILED.value:
        raise task.retry(
            countdown=retry_after or min(3_600, 5 * (2 ** task.request.retries))
        )
    return state


def enqueue_publish_job(
    workspace_id: UUID, job_id: UUID, scheduled_at_utc: datetime | None
) -> None:
    if scheduled_at_utc is not None and scheduled_at_utc > datetime.now(UTC):
        process_publish_job_task.apply_async(
            args=(str(workspace_id), str(job_id)), eta=scheduled_at_utc
        )
    else:
        process_publish_job_task.apply_async(
            args=(str(workspace_id), str(job_id)), countdown=1
        )


async def _run_connection(workspace_id: UUID, job_id: UUID) -> tuple[str, int | None]:
    database = get_database()
    try:
        async with database.session_factory() as session:
            async with session.begin():
                await apply_workspace_scope(session, workspace_id)
                return await process_connection_job(
                    session,
                    workspace_id=workspace_id,
                    job_id=job_id,
                    providers=get_provider_registry(),
                    secrets=get_secret_resolver(),
                )
    finally:
        await database.close()
        get_database.cache_clear()


@shared_task(
    bind=True,
    name="publishing.connection.process",
    max_retries=3,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=3_600,
    retry_jitter=True,
)
def process_connection_job_task(task, workspace_id: str, job_id: str) -> str:
    state, retry_after = asyncio.run(_run_connection(UUID(workspace_id), UUID(job_id)))
    if state == JobState.RETRYABLE_FAILED.value:
        raise task.retry(
            countdown=retry_after or min(3_600, 5 * (2 ** task.request.retries))
        )
    return state


def enqueue_connection_job(workspace_id: UUID, job_id: UUID) -> None:
    process_connection_job_task.apply_async(
        args=(str(workspace_id), str(job_id)), countdown=1
    )


async def _schedule_connection_monitors(
    workspace_id: UUID, stale_minutes: int, limit: int
) -> list[UUID]:
    database = get_database()
    try:
        async with database.session_factory() as session:
            async with session.begin():
                await apply_workspace_scope(session, workspace_id)
                now = datetime.now(UTC)
                cutoff = now - timedelta(minutes=stale_minutes)
                connections = list(
                    await session.scalars(
                        select(PublishingConnection)
                        .where(
                            PublishingConnection.workspace_id == workspace_id,
                            PublishingConnection.state.in_(
                                {
                                    ConnectionState.ACTIVE.value,
                                    ConnectionState.DEGRADED.value,
                                    ConnectionState.EXPIRED.value,
                                }
                            ),
                            (
                                PublishingConnection.last_diagnosed_at.is_(None)
                                | (PublishingConnection.last_diagnosed_at <= cutoff)
                            ),
                        )
                        .order_by(
                            PublishingConnection.last_diagnosed_at.asc().nullsfirst(),
                            PublishingConnection.id,
                        )
                        .limit(limit)
                        .with_for_update(skip_locked=True)
                    )
                )
                service = PublishingService(
                    session,
                    readiness=SQLAlchemyPublishingReadinessResolver(session),
                    entitlements=create_publishing_entitlement_resolver(session),
                )
                queued: list[UUID] = []
                bucket = now.strftime("%Y%m%d%H%M")
                for connection in connections:
                    active_job = await session.scalar(
                        select(PublishingConnectionJob.id)
                        .where(
                            PublishingConnectionJob.workspace_id == workspace_id,
                            PublishingConnectionJob.connection_id == connection.id,
                            PublishingConnectionJob.state.in_(
                                {
                                    JobState.QUEUED.value,
                                    JobState.VALIDATING.value,
                                    JobState.RETRYABLE_FAILED.value,
                                }
                            ),
                        )
                        .limit(1)
                    )
                    if active_job is not None:
                        continue
                    principal = Principal(
                        subject_id=connection.created_by,
                        workspace_id=workspace_id,
                        session_id=None,
                        permissions=frozenset(),
                        authentication_method="worker-monitor",
                    )
                    created = await service.create_connection_job(
                        principal,
                        connection.id,
                        ConnectionOperation.DIAGNOSE,
                        ConnectionCommandCreate(
                            expected_lock_version=connection.lock_version,
                            idempotency_key=f"monitor:{connection.id}:{bucket}",
                        ),
                    )
                    if created.created:
                        queued.append(created.job.id)
                return queued
    finally:
        await database.close()
        get_database.cache_clear()


@shared_task(name="publishing.connections.schedule_monitors")
def schedule_publishing_connection_monitors_task(
    workspace_id: str, stale_minutes: int = 15, limit: int = 100
) -> int:
    job_ids = asyncio.run(
        _schedule_connection_monitors(
            UUID(workspace_id),
            min(max(stale_minutes, 5), 1_440),
            min(max(limit, 1), 500),
        )
    )
    for job_id in job_ids:
        enqueue_connection_job(UUID(workspace_id), job_id)
    return len(job_ids)


async def _schedule_reconciliations(
    workspace_id: UUID, stale_minutes: int, limit: int
) -> list[UUID]:
    database = get_database()
    try:
        async with database.session_factory() as session:
            async with session.begin():
                await apply_workspace_scope(session, workspace_id)
                now = datetime.now(UTC)
                cutoff = now - timedelta(minutes=stale_minutes)
                rows = list(
                    await session.execute(
                        select(PublishedPost, PublishJob.requested_by)
                        .join(
                            PublishJob,
                            (PublishJob.workspace_id == PublishedPost.workspace_id)
                            & (PublishJob.id == PublishedPost.created_by_job_id),
                        )
                        .where(
                            PublishedPost.workspace_id == workspace_id,
                            PublishedPost.connection_id.is_not(None),
                            PublishedPost.state.not_in(
                                {
                                    PublishedPostState.DELETED.value,
                                    PublishedPostState.TRASHED.value,
                                }
                            ),
                            (
                                PublishedPost.last_reconciled_at.is_(None)
                                | (PublishedPost.last_reconciled_at <= cutoff)
                            ),
                        )
                        .order_by(
                            PublishedPost.last_reconciled_at.asc().nullsfirst(),
                            PublishedPost.id,
                        )
                        .limit(limit)
                        .with_for_update(skip_locked=True)
                    )
                )
                service = PublishingService(
                    session,
                    readiness=SQLAlchemyPublishingReadinessResolver(session),
                    entitlements=create_publishing_entitlement_resolver(session),
                )
                queued: list[UUID] = []
                bucket = now.strftime("%Y%m%d%H%M")
                for post, requested_by in rows:
                    active_job = await session.scalar(
                        select(PublishJob.id)
                        .where(
                            PublishJob.workspace_id == workspace_id,
                            PublishJob.target_published_post_id == post.id,
                            PublishJob.operation == PublishOperation.RECONCILE.value,
                            PublishJob.state.in_(
                                {
                                    JobState.SCHEDULED.value,
                                    JobState.PUBLISHING.value,
                                    JobState.RETRYABLE_FAILED.value,
                                }
                            ),
                        )
                        .limit(1)
                    )
                    if active_job is not None:
                        continue
                    principal = Principal(
                        subject_id=requested_by,
                        workspace_id=workspace_id,
                        session_id=None,
                        permissions=frozenset(),
                        authentication_method="worker-reconcile",
                    )
                    created = await service.reconcile_published_post(
                        principal,
                        post.id,
                        ReconcileCreate(
                            expected_lock_version=post.lock_version,
                            conflict_action=ConflictAction.ABORT,
                        ),
                        idempotency_key=f"reconcile:{post.id}:{bucket}",
                    )
                    if created.created:
                        queued.append(created.job.id)
                return queued
    finally:
        await database.close()
        get_database.cache_clear()


@shared_task(name="publishing.posts.schedule_reconciliation")
def schedule_publishing_reconciliation_task(
    workspace_id: str, stale_minutes: int = 60, limit: int = 100
) -> int:
    job_ids = asyncio.run(
        _schedule_reconciliations(
            UUID(workspace_id),
            min(max(stale_minutes, 15), 10_080),
            min(max(limit, 1), 500),
        )
    )
    for job_id in job_ids:
        enqueue_publish_job(UUID(workspace_id), job_id, None)
    return len(job_ids)


async def _emit_due_notifications(workspace_id: UUID, limit: int) -> int:
    database = get_database()
    try:
        async with database.session_factory() as session:
            async with session.begin():
                await apply_workspace_scope(session, workspace_id)
                now = datetime.now(UTC)
                notifications = list(
                    await session.scalars(
                        select(PublishingNotification)
                        .where(
                            PublishingNotification.workspace_id == workspace_id,
                            PublishingNotification.due_at <= now,
                            PublishingNotification.delivered_at.is_(None),
                        )
                        .order_by(PublishingNotification.due_at)
                        .limit(limit)
                        .with_for_update(skip_locked=True)
                    )
                )
                for item in notifications:
                    await add_outbox_event(
                        session,
                        workspace_id=workspace_id,
                        aggregate_type="publishing_notification",
                        aggregate_id=str(item.id),
                        event_type="publishing.notification.due",
                        schema_version="1",
                        payload={
                            "workspace_id": str(workspace_id),
                            "recipient_id": str(item.recipient_id),
                            "notification_type": item.notification_type,
                            **item.payload_json,
                        },
                    )
                    item.delivered_at = now
                return len(notifications)
    finally:
        await database.close()
        get_database.cache_clear()


@shared_task(name="publishing.notifications.emit_due")
def emit_due_publishing_notifications_task(workspace_id: str, limit: int = 100) -> int:
    return asyncio.run(_emit_due_notifications(UUID(workspace_id), min(max(limit, 1), 500)))
