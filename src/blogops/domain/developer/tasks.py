"""Celery boundary for durable outbound webhook delivery."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID

from celery import shared_task

from blogops.core.errors import AppError
from blogops.db.session import apply_workspace_scope, get_database
from blogops.domain.developer.enums import WebhookDeliveryState
from blogops.domain.developer.providers import (
    ApiKeySecrets,
    DnsResolver,
    FailClosedDeveloperAdapters,
    PrivateWebhookPayloads,
    WebhookTransport,
)
from blogops.domain.developer.service import DeveloperService

_webhook_secrets: ApiKeySecrets | None = None
_webhook_payloads: PrivateWebhookPayloads | None = None
_webhook_dns: DnsResolver | None = None
_webhook_transport: WebhookTransport | None = None


def configure_developer_worker_runtime(
    *,
    secrets: ApiKeySecrets,
    payloads: PrivateWebhookPayloads,
    dns: DnsResolver,
    transport: WebhookTransport,
) -> None:
    """Install all outbound webhook adapters during worker bootstrap."""

    global _webhook_secrets, _webhook_payloads, _webhook_dns, _webhook_transport
    _webhook_secrets = secrets
    _webhook_payloads = payloads
    _webhook_dns = dns
    _webhook_transport = transport


async def _run_webhook_delivery(
    workspace_id: UUID,
    delivery_id: UUID,
) -> tuple[str, datetime | None]:
    database = get_database()
    try:
        async with database.session_factory() as session:
            async with session.begin():
                await apply_workspace_scope(session, workspace_id)
                service = DeveloperService(session)
                unavailable = FailClosedDeveloperAdapters()
                try:
                    delivery = await service.execute_delivery(
                        workspace_id,
                        delivery_id,
                        secrets_provider=_webhook_secrets or unavailable,
                        payloads=_webhook_payloads or unavailable,
                        dns=_webhook_dns or unavailable,
                        transport=_webhook_transport or unavailable,
                    )
                except AppError as exc:
                    delivery = await service.fail_delivery(
                        workspace_id,
                        delivery_id,
                        error_code=exc.code,
                    )
                except Exception:
                    delivery = await service.fail_delivery(
                        workspace_id,
                        delivery_id,
                        error_code="WEBHOOK_DELIVERY_EXECUTION_FAILED",
                    )
                return delivery.state, delivery.next_attempt_at
    finally:
        await database.close()
        get_database.cache_clear()


@shared_task(name="developer.webhook_delivery.process")
def process_webhook_delivery_task(workspace_id: str, delivery_id: str) -> str:
    parsed_workspace_id = UUID(workspace_id)
    parsed_delivery_id = UUID(delivery_id)
    state, next_attempt_at = asyncio.run(
        _run_webhook_delivery(parsed_workspace_id, parsed_delivery_id)
    )
    if state == WebhookDeliveryState.RETRY_WAIT.value:
        enqueue_webhook_delivery(
            parsed_workspace_id,
            parsed_delivery_id,
            next_attempt_at,
        )
    return state


def enqueue_webhook_delivery(
    workspace_id: UUID,
    delivery_id: UUID,
    next_attempt_at: datetime | None = None,
) -> None:
    if next_attempt_at is not None and next_attempt_at > datetime.now(UTC):
        process_webhook_delivery_task.apply_async(
            args=(str(workspace_id), str(delivery_id)),
            eta=next_attempt_at,
        )
    else:
        process_webhook_delivery_task.apply_async(
            args=(str(workspace_id), str(delivery_id)),
            countdown=1,
        )
