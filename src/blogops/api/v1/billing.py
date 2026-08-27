"""Billing, credit balance, hold and usage APIs."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.core.context import Principal
from blogops.core.errors import AppError
from blogops.core.permissions import Permission, require_permission_value, require_permissions
from blogops.db.session import get_session, get_tenant_session
from blogops.domain.billing.providers import (
    FailClosedPaymentGateway,
    PaymentGatewayRegistry,
    PaymentPayloadArchive,
)
from blogops.domain.billing.schemas import (
    BillingSubscriptionRead,
    CreditAccountRead,
    CreditHoldCreate,
    CreditHoldFinalize,
    CreditHoldRead,
    CreditHoldRelease,
    CreditLedgerRead,
    CreditPurchaseCreate,
    PaymentCommandRead,
    PaymentIntentCreate,
    PaymentProviderEventRead,
    SubscriptionCheckoutCreate,
    UsageLimitCheck,
    UsageLimitRead,
    UsageRecordRead,
)
from blogops.domain.billing.service import BillingService
from blogops.domain.billing.tasks import enqueue_payment_intent

router = APIRouter(prefix="/billing", tags=["billing"])
usage_router = APIRouter(tags=["billing"])
payment_webhook_router = APIRouter(prefix="/webhooks/payments", tags=["webhooks"])
TenantSession = Annotated[AsyncSession, Depends(get_tenant_session)]
UnscopedSession = Annotated[AsyncSession, Depends(get_session)]
BillingReader = Annotated[Principal, Depends(require_permissions(Permission.BILLING_READ))]
BillingManager = Annotated[Principal, Depends(require_permissions(Permission.BILLING_MANAGE))]
require_meter_permission = require_permission_value(
    "billing:meter",
    message="사용량 확정 권한이 없습니다.",
)
BillingMeter = Annotated[Principal, Depends(require_meter_permission)]


def billing_service(session: TenantSession) -> BillingService:
    return BillingService(session)


def payment_event_service(session: UnscopedSession) -> BillingService:
    return BillingService(session)


def payment_gateway_registry() -> PaymentGatewayRegistry:
    """Production must register explicit provider verifiers."""

    return PaymentGatewayRegistry()


def payment_payload_archive() -> PaymentPayloadArchive:
    """Production must override with a private immutable payload archive."""

    return FailClosedPaymentGateway()


Service = Annotated[BillingService, Depends(billing_service)]
PaymentEventService = Annotated[BillingService, Depends(payment_event_service)]
PaymentRegistry = Annotated[PaymentGatewayRegistry, Depends(payment_gateway_registry)]
PaymentArchive = Annotated[PaymentPayloadArchive, Depends(payment_payload_archive)]


async def _read_limited_webhook_body(request: Request) -> bytes:
    limit = request.app.state.settings.payment_webhook_max_bytes
    raw_length = request.headers.get("content-length")
    if raw_length is not None:
        try:
            declared_length = int(raw_length)
        except ValueError as exc:
            raise AppError(
                "PAYMENT_WEBHOOK_LENGTH_INVALID",
                "요청 크기가 올바르지 않습니다.",
                400,
            ) from exc
        if declared_length < 0 or declared_length > limit:
            raise AppError("PAYMENT_WEBHOOK_TOO_LARGE", "결제 Webhook 요청이 너무 큽니다.", 413)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            raise AppError("PAYMENT_WEBHOOK_TOO_LARGE", "결제 Webhook 요청이 너무 큽니다.", 413)
    return bytes(body)


@router.get("/subscription", response_model=BillingSubscriptionRead)
async def get_subscription(
    principal: BillingReader, service: Service
) -> BillingSubscriptionRead:
    return BillingSubscriptionRead.model_validate(await service.get_subscription(principal))


@router.post(
    "/payment-intents",
    response_model=PaymentCommandRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def queue_payment_intent(
    data: PaymentIntentCreate,
    principal: BillingManager,
    service: Service,
    background_tasks: BackgroundTasks,
) -> PaymentCommandRead:
    value = await service.queue_payment_intent(principal, data)
    background_tasks.add_task(enqueue_payment_intent, principal.workspace_id, value.id)
    return PaymentCommandRead.model_validate(value)


async def _queue_plan_intent(
    *,
    operation: str,
    data: SubscriptionCheckoutCreate,
    principal: Principal,
    service: BillingService,
    background_tasks: BackgroundTasks,
) -> PaymentCommandRead:
    command = PaymentIntentCreate(
        operation=operation,
        provider=data.provider,
        plan_version_id=data.plan_version_id,
        billing_cycle=data.billing_cycle,
        idempotency_key=data.idempotency_key,
        return_url=data.return_url,
    )
    value = await service.queue_payment_intent(principal, command)
    background_tasks.add_task(enqueue_payment_intent, principal.workspace_id, value.id)
    return PaymentCommandRead.model_validate(value)


@router.post(
    "/subscribe",
    response_model=PaymentCommandRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def subscribe(
    data: SubscriptionCheckoutCreate,
    principal: BillingManager,
    service: Service,
    background_tasks: BackgroundTasks,
) -> PaymentCommandRead:
    return await _queue_plan_intent(
        operation="SUBSCRIBE",
        data=data,
        principal=principal,
        service=service,
        background_tasks=background_tasks,
    )


@router.post(
    "/change-plan",
    response_model=PaymentCommandRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def change_plan(
    data: SubscriptionCheckoutCreate,
    principal: BillingManager,
    service: Service,
    background_tasks: BackgroundTasks,
) -> PaymentCommandRead:
    return await _queue_plan_intent(
        operation="CHANGE_PLAN",
        data=data,
        principal=principal,
        service=service,
        background_tasks=background_tasks,
    )


@payment_webhook_router.post(
    "/{provider}",
    response_model=PaymentProviderEventRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def receive_payment_provider_event(
    provider: str,
    request: Request,
    service: PaymentEventService,
    registry: PaymentRegistry,
    archive: PaymentArchive,
) -> PaymentProviderEventRead:
    gateway = registry.resolve(provider)
    body = await _read_limited_webhook_body(request)
    value = await service.ingest_payment_event(
        provider=provider,
        headers={key.casefold(): value for key, value in request.headers.items()},
        body=body,
        gateway=gateway,
        archive=archive,
    )
    return PaymentProviderEventRead.model_validate(value)


@router.get("/credits", response_model=CreditAccountRead)
async def get_credit_balance(
    principal: BillingReader, service: Service
) -> CreditAccountRead:
    return CreditAccountRead.model_validate(await service.get_credit_account(principal))


@router.post(
    "/credits/account",
    response_model=CreditAccountRead,
    status_code=status.HTTP_201_CREATED,
)
async def initialize_credit_account(
    principal: BillingManager, service: Service
) -> CreditAccountRead:
    return CreditAccountRead.model_validate(await service.create_credit_account(principal))


@router.post(
    "/credits/purchase",
    response_model=PaymentCommandRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def purchase_credits(
    data: CreditPurchaseCreate,
    principal: BillingManager,
    service: Service,
    background_tasks: BackgroundTasks,
) -> PaymentCommandRead:
    command = PaymentIntentCreate(
        operation="PURCHASE_CREDITS",
        provider=data.provider,
        purchase_sku=data.purchase_sku,
        idempotency_key=data.idempotency_key,
        return_url=data.return_url,
    )
    value = await service.queue_payment_intent(principal, command)
    background_tasks.add_task(enqueue_payment_intent, principal.workspace_id, value.id)
    return PaymentCommandRead.model_validate(value)


@router.get("/credits/ledger", response_model=list[CreditLedgerRead])
async def list_credit_ledger(
    principal: BillingReader,
    service: Service,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[CreditLedgerRead]:
    return [
        CreditLedgerRead.model_validate(value)
        for value in await service.list_credit_entries(principal, limit=limit, offset=offset)
    ]


@router.post(
    "/credit-holds",
    response_model=CreditHoldRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_credit_hold(
    data: CreditHoldCreate,
    principal: BillingMeter,
    service: Service,
) -> CreditHoldRead:
    return CreditHoldRead.model_validate(await service.create_hold(principal, data))


@router.post("/credit-holds/{hold_id}/finalize", response_model=CreditHoldRead)
async def finalize_credit_hold(
    hold_id: UUID,
    data: CreditHoldFinalize,
    principal: BillingMeter,
    service: Service,
) -> CreditHoldRead:
    return CreditHoldRead.model_validate(await service.finalize_hold(principal, hold_id, data))


@router.post("/credit-holds/{hold_id}/release", response_model=CreditHoldRead)
async def release_credit_hold(
    hold_id: UUID,
    data: CreditHoldRelease,
    principal: BillingMeter,
    service: Service,
) -> CreditHoldRead:
    return CreditHoldRead.model_validate(await service.release_hold(principal, hold_id, data))


@router.get("/usage", response_model=list[UsageRecordRead])
@usage_router.get("/usage", response_model=list[UsageRecordRead])
async def list_usage(
    principal: BillingReader,
    service: Service,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[UsageRecordRead]:
    return [
        UsageRecordRead.model_validate(value)
        for value in await service.list_usage(principal, limit=limit, offset=offset)
    ]


@router.post("/usage/check", response_model=UsageLimitRead)
async def check_usage_limit(
    data: UsageLimitCheck,
    _principal: BillingReader,
) -> UsageLimitRead:
    value = BillingService.check_usage_limit(data)
    return UsageLimitRead(
        allowed=value.allowed,
        remaining=value.remaining,
        overage=value.overage,
        policy=value.policy,
    )
