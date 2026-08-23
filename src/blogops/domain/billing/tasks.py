"""Celery boundary for durable payment checkout intents."""

from __future__ import annotations

import asyncio
from uuid import UUID

from celery import shared_task

from blogops.core.errors import AppError
from blogops.db.session import apply_workspace_scope, get_database
from blogops.domain.billing.providers import PaymentGatewayRegistry
from blogops.domain.billing.service import BillingService

_payment_gateways: PaymentGatewayRegistry | None = None


def configure_billing_worker_runtime(*, gateways: PaymentGatewayRegistry) -> None:
    """Install explicit checkout adapters during worker bootstrap."""

    global _payment_gateways
    _payment_gateways = gateways


def _gateways() -> PaymentGatewayRegistry:
    return _payment_gateways or PaymentGatewayRegistry()


def _provider_rejected(error: AppError) -> bool:
    return 400 <= error.status_code < 500 and error.status_code not in {408, 429}


async def _run_payment_intent(workspace_id: UUID, command_id: UUID) -> str:
    database = get_database()
    try:
        async with database.session_factory() as session:
            async with session.begin():
                await apply_workspace_scope(session, workspace_id)
                service = BillingService(session)
                try:
                    command = await service.execute_payment_intent(
                        workspace_id,
                        command_id,
                        gateways=_gateways(),
                    )
                except AppError as exc:
                    command = await service.fail_payment_intent(
                        workspace_id,
                        command_id,
                        error_code=exc.code,
                        provider_rejected=_provider_rejected(exc),
                    )
                except Exception:
                    command = await service.fail_payment_intent(
                        workspace_id,
                        command_id,
                        error_code="PAYMENT_CHECKOUT_EXECUTION_FAILED",
                    )
                return command.state
    finally:
        await database.close()
        get_database.cache_clear()


@shared_task(name="billing.payment_intent.process")
def process_payment_intent_task(workspace_id: str, command_id: str) -> str:
    return asyncio.run(_run_payment_intent(UUID(workspace_id), UUID(command_id)))


def enqueue_payment_intent(workspace_id: UUID, command_id: UUID) -> None:
    process_payment_intent_task.apply_async(
        args=(str(workspace_id), str(command_id)),
        countdown=1,
    )
