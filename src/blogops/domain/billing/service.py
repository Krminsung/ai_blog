"""Transactional billing service with max-cost holds and append-only ledgers."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal, DecimalException
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.core.context import Principal
from blogops.core.errors import AppError
from blogops.db.session import apply_workspace_scope
from blogops.domain.billing.enums import (
    CreditBucketKind,
    CreditEntryKind,
    CreditHoldState,
    LedgerDirection,
    PaymentCommandState,
    UsageRecordState,
)
from blogops.domain.billing.models import (
    BillingPlanVersion,
    BillingPriceVersion,
    BillingSubscription,
    CreditAccount,
    CreditGrant,
    CreditHold,
    CreditHoldAllocation,
    CreditLedgerEntry,
    PaymentCommand,
    PaymentProviderEvent,
    UsageRecord,
)
from blogops.domain.billing.providers import (
    PaymentGateway,
    PaymentGatewayRegistry,
    PaymentPayloadArchive,
    ProviderCheckoutRequest,
)
from blogops.domain.billing.rules import (
    UsageLimitDecision,
    canonical_hash,
    ensure_balance_transition,
    evaluate_usage_limit,
    finalize_hold_amounts,
    pricing_is_effective,
)
from blogops.domain.billing.schemas import (
    CreditGrantCreate,
    CreditHoldCreate,
    CreditHoldFinalize,
    CreditHoldRelease,
    PaymentIntentCreate,
    UsageLimitCheck,
    UsageRecordCreate,
)
from blogops.services.audit import append_audit_log
from blogops.services.outbox import add_outbox_event

_SCHEMA_VERSION = "1.0"
_PAYMENT_SENSITIVE_KEYS = frozenset(
    {"card", "pan", "cvc", "cvv", "secret", "token", "password", "email", "phone"}
)


def _payment_sensitive_paths(value: Any, path: str = "payload") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for raw_key, nested in value.items():
            key = str(raw_key)
            normalized = key.casefold().replace("-", "_").replace(".", "_")
            current = f"{path}.{key}"
            if frozenset(normalized.split("_")).intersection(_PAYMENT_SENSITIVE_KEYS):
                found.append(current)
            else:
                found.extend(_payment_sensitive_paths(nested, current))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            found.extend(_payment_sensitive_paths(nested, f"{path}[{index}]"))
    return found


class BillingService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _scope(self, workspace_id: UUID) -> None:
        await apply_workspace_scope(self._session, workspace_id)

    async def _record(
        self,
        *,
        principal: Principal,
        action: str,
        target_type: str,
        target_id: UUID,
        details: dict[str, Any],
    ) -> None:
        await append_audit_log(
            self._session,
            workspace_id=principal.workspace_id,
            actor_id=principal.subject_id,
            action=action,
            target_type=target_type,
            target_id=str(target_id),
            details=details,
        )
        await add_outbox_event(
            self._session,
            workspace_id=principal.workspace_id,
            aggregate_type=target_type,
            aggregate_id=str(target_id),
            event_type=action,
            schema_version=_SCHEMA_VERSION,
            payload={
                "workspace_id": str(principal.workspace_id),
                "actor_id": str(principal.subject_id),
                "aggregate_id": str(target_id),
                **details,
            },
        )

    async def _account(self, workspace_id: UUID, *, for_update: bool) -> CreditAccount:
        query = select(CreditAccount).where(CreditAccount.workspace_id == workspace_id)
        if for_update:
            query = query.with_for_update()
        account = await self._session.scalar(query)
        if account is None:
            raise AppError(
                "CREDIT_ACCOUNT_NOT_CONFIGURED",
                "크레딧 계정이 아직 구성되지 않았습니다.",
                409,
            )
        return account

    async def _price(
        self, workspace_id: UUID, price_version_id: UUID, *, at: datetime
    ) -> BillingPriceVersion:
        price = await self._session.scalar(
            select(BillingPriceVersion).where(BillingPriceVersion.id == price_version_id)
        )
        if price is None or (
            price.workspace_id is not None and price.workspace_id != workspace_id
        ):
            raise AppError("PRICING_VERSION_NOT_FOUND", "가격표 버전을 찾을 수 없습니다.", 404)
        if not pricing_is_effective(
            state=price.state,
            effective_at=price.effective_at,
            retired_at=price.retired_at,
            at=at,
        ):
            raise AppError(
                "PRICING_VERSION_INACTIVE",
                "요청 시점에 유효한 정확한 가격표 버전이 필요합니다.",
                409,
            )
        return price

    async def _plan(
        self,
        workspace_id: UUID,
        plan_version_id: UUID,
        *,
        at: datetime,
    ) -> BillingPlanVersion:
        plan = await self._session.scalar(
            select(BillingPlanVersion).where(BillingPlanVersion.id == plan_version_id)
        )
        if plan is None or (
            plan.workspace_id is not None and plan.workspace_id != workspace_id
        ):
            raise AppError("BILLING_PLAN_NOT_FOUND", "요금제 버전을 찾을 수 없습니다.", 404)
        if not pricing_is_effective(
            state=plan.state,
            effective_at=plan.effective_at,
            retired_at=plan.retired_at,
            at=at,
        ):
            raise AppError(
                "BILLING_PLAN_INACTIVE",
                "현재 유효한 요금제 버전만 선택할 수 있습니다.",
                409,
            )
        return plan

    @staticmethod
    def _price_snapshot(price: BillingPriceVersion) -> dict[str, Any]:
        return {
            "price_version_id": str(price.id),
            "metric_key": price.metric_key,
            "vendor": price.vendor,
            "vendor_sku": price.vendor_sku,
            "version": price.version,
            "unit_name": price.unit_name,
            "unit_size": str(price.unit_size),
            "credit_unit_price": str(price.credit_unit_price),
            "internal_unit_cost": str(price.internal_unit_cost),
            "cost_currency": price.cost_currency,
            "pricing_hash": price.pricing_hash,
            "rounding": price.pricing_metadata.get("rounding"),
        }

    @staticmethod
    def _priced_quantity(price: BillingPriceVersion, quantity: Decimal) -> Decimal:
        rounding = price.pricing_metadata.get("rounding")
        if rounding == "PROPORTIONAL":
            return quantity / price.unit_size
        if rounding == "CEILING_UNIT":
            return (quantity / price.unit_size).to_integral_value(rounding=ROUND_CEILING)
        raise AppError(
            "PRICING_ROUNDING_CONFIG_MISSING",
            "가격표의 단위 반올림 정책이 구성되지 않았습니다.",
            503,
        )

    @classmethod
    def price_credit_amount(
        cls,
        price: BillingPriceVersion,
        quantity: Decimal,
    ) -> Decimal:
        """Convert cost units without under-holding at the six-decimal ledger scale."""

        try:
            value = cls._priced_quantity(price, quantity) * price.credit_unit_price
            return value.quantize(Decimal("0.000001"), rounding=ROUND_CEILING)
        except DecimalException as exc:
            raise AppError(
                "PRICING_CONVERSION_INVALID",
                "가격표 단위를 크레딧 원장 단위로 안전하게 변환할 수 없습니다.",
                503,
            ) from exc

    async def create_credit_account(self, principal: Principal) -> CreditAccount:
        await self._scope(principal.workspace_id)
        existing = await self._session.scalar(
            select(CreditAccount).where(CreditAccount.workspace_id == principal.workspace_id)
        )
        if existing is not None:
            return existing
        account = CreditAccount(workspace_id=principal.workspace_id)
        self._session.add(account)
        await self._session.flush()
        await self._record(
            principal=principal,
            action="billing.credit_account.created",
            target_type="credit_account",
            target_id=account.id,
            details={},
        )
        return account

    async def get_credit_account(self, principal: Principal) -> CreditAccount:
        await self._scope(principal.workspace_id)
        return await self._account(principal.workspace_id, for_update=False)

    async def get_subscription(self, principal: Principal) -> BillingSubscription:
        await self._scope(principal.workspace_id)
        value = await self._session.scalar(
            select(BillingSubscription).where(
                BillingSubscription.workspace_id == principal.workspace_id
            )
        )
        if value is None:
            raise AppError("SUBSCRIPTION_NOT_FOUND", "구독 정보를 찾을 수 없습니다.", 404)
        return value

    async def grant_credits(
        self, principal: Principal, data: CreditGrantCreate
    ) -> CreditGrant:
        await self._scope(principal.workspace_id)
        account = await self._account(principal.workspace_id, for_update=True)
        existing = await self._session.scalar(
            select(CreditGrant).where(
                CreditGrant.workspace_id == principal.workspace_id,
                CreditGrant.source_type == data.source_type,
                CreditGrant.source_id == data.source_id,
            )
        )
        if existing is not None:
            if (
                existing.original_amount != data.amount
                or existing.bucket_kind != data.bucket_kind.value
            ):
                raise AppError(
                    "CREDIT_GRANT_SOURCE_REUSED",
                    "같은 지급 출처를 다른 크레딧 지급에 재사용할 수 없습니다.",
                    409,
                )
            return existing
        now = datetime.now(UTC)
        if data.expires_at is not None and data.expires_at <= now:
            raise AppError("CREDIT_GRANT_ALREADY_EXPIRED", "이미 만료된 크레딧을 지급할 수 없습니다.", 422)
        grant = CreditGrant(
            workspace_id=principal.workspace_id,
            account_id=account.id,
            bucket_kind=data.bucket_kind.value,
            original_amount=data.amount,
            available_amount=data.amount,
            source_type=data.source_type,
            source_id=data.source_id,
            granted_at=now,
            expires_at=data.expires_at,
            grant_policy_snapshot=data.grant_policy_snapshot,
        )
        self._session.add(grant)
        account.available_balance += data.amount
        await self._session.flush()
        entry = CreditLedgerEntry(
            workspace_id=principal.workspace_id,
            account_id=account.id,
            grant_id=grant.id,
            kind=CreditEntryKind.GRANT.value,
            direction=LedgerDirection.CREDIT.value,
            amount=data.amount,
            available_delta=data.amount,
            held_delta=Decimal("0"),
            available_after=account.available_balance,
            held_after=account.held_balance,
            transaction_group_id=uuid4(),
            idempotency_key=f"grant:{data.source_type}:{data.source_id}",
            source_type=data.source_type,
            source_id=data.source_id,
            reason_code=data.reason_code,
            actor_id=principal.subject_id,
        )
        self._session.add(entry)
        await self._session.flush()
        await self._record(
            principal=principal,
            action="billing.credit.granted",
            target_type="credit_grant",
            target_id=grant.id,
            details={"amount": str(data.amount), "bucket_kind": data.bucket_kind.value},
        )
        return grant

    async def create_hold(self, principal: Principal, data: CreditHoldCreate) -> CreditHold:
        await self._scope(principal.workspace_id)
        now = datetime.now(UTC)
        if data.expires_at <= now:
            raise AppError("CREDIT_HOLD_EXPIRY_INVALID", "Hold 만료 시각은 미래여야 합니다.", 422)
        request_hash = canonical_hash(data.model_dump(mode="json"))
        existing = await self._session.scalar(
            select(CreditHold).where(
                CreditHold.workspace_id == principal.workspace_id,
                CreditHold.subject_type == data.subject_type,
                CreditHold.subject_id == data.subject_id,
                CreditHold.idempotency_key == data.idempotency_key,
            )
        )
        if existing is not None:
            if existing.request_hash != request_hash:
                raise AppError("IDEMPOTENCY_KEY_REUSED", "같은 멱등키의 Hold 요청이 다릅니다.", 409)
            return existing
        price = await self._price(principal.workspace_id, data.price_version_id, at=now)
        account = await self._account(principal.workspace_id, for_update=True)
        if account.available_balance < data.maximum_amount:
            raise AppError(
                "CREDIT_BALANCE_INSUFFICIENT",
                "최대 예상 비용을 Hold할 크레딧이 부족합니다.",
                402,
                remediation={
                    "required": str(data.maximum_amount),
                    "available": str(account.available_balance),
                },
            )
        grants = list(
            await self._session.scalars(
                select(CreditGrant)
                .where(
                    CreditGrant.workspace_id == principal.workspace_id,
                    CreditGrant.account_id == account.id,
                    CreditGrant.available_amount > 0,
                    or_(CreditGrant.expires_at.is_(None), CreditGrant.expires_at > now),
                )
                .order_by(
                    CreditGrant.expires_at.asc().nullslast(),
                    CreditGrant.granted_at,
                    CreditGrant.id,
                )
                .with_for_update()
            )
        )
        if (
            sum((value.available_amount for value in grants), Decimal("0"))
            < data.maximum_amount
        ):
            raise AppError(
                "CREDIT_BUCKETS_INSUFFICIENT",
                "사용 가능한 유효기간별 크레딧 합계가 Hold에 부족합니다.",
                409,
            )
        hold = CreditHold(
            workspace_id=principal.workspace_id,
            account_id=account.id,
            subject_type=data.subject_type,
            subject_id=data.subject_id,
            operation=data.operation,
            idempotency_key=data.idempotency_key,
            request_hash=request_hash,
            price_version_id=price.id,
            pricing_snapshot=self._price_snapshot(price),
            maximum_amount=data.maximum_amount,
            expires_at=data.expires_at,
            requested_by=principal.subject_id,
        )
        self._session.add(hold)
        await self._session.flush()
        remaining = data.maximum_amount
        order = 0
        for grant in grants:
            if remaining == 0:
                break
            amount = min(grant.available_amount, remaining)
            grant.available_amount -= amount
            grant.held_amount += amount
            order += 1
            self._session.add(
                CreditHoldAllocation(
                    workspace_id=principal.workspace_id,
                    hold_id=hold.id,
                    grant_id=grant.id,
                    allocation_order=order,
                    held_amount=amount,
                )
            )
            remaining -= amount
        available_before = account.available_balance
        held_before = account.held_balance
        account.available_balance -= data.maximum_amount
        account.held_balance += data.maximum_amount
        ensure_balance_transition(
            available_before=available_before,
            held_before=held_before,
            available_after=account.available_balance,
            held_after=account.held_balance,
        )
        self._session.add(
            CreditLedgerEntry(
                workspace_id=principal.workspace_id,
                account_id=account.id,
                hold_id=hold.id,
                kind=CreditEntryKind.HOLD.value,
                direction=LedgerDirection.DEBIT.value,
                amount=data.maximum_amount,
                available_delta=-data.maximum_amount,
                held_delta=data.maximum_amount,
                available_after=account.available_balance,
                held_after=account.held_balance,
                transaction_group_id=uuid4(),
                idempotency_key=f"hold:{hold.id}",
                source_type=data.subject_type,
                source_id=data.subject_id,
                reason_code="MAX_COST_RESERVATION",
                pricing_snapshot=hold.pricing_snapshot,
                actor_id=principal.subject_id,
            )
        )
        await self._session.flush()
        await self._record(
            principal=principal,
            action="billing.credit.held",
            target_type="credit_hold",
            target_id=hold.id,
            details={"maximum_amount": str(hold.maximum_amount), "price_version_id": str(price.id)},
        )
        return hold

    async def get_hold(self, principal: Principal, hold_id: UUID) -> CreditHold:
        await self._scope(principal.workspace_id)
        value = await self._session.scalar(
            select(CreditHold).where(
                CreditHold.workspace_id == principal.workspace_id,
                CreditHold.id == hold_id,
            )
        )
        if value is None:
            raise AppError("CREDIT_HOLD_NOT_FOUND", "크레딧 Hold를 찾을 수 없습니다.", 404)
        return value

    async def finalize_hold(
        self, principal: Principal, hold_id: UUID, data: CreditHoldFinalize
    ) -> CreditHold:
        await self._scope(principal.workspace_id)
        hold = await self._session.scalar(
            select(CreditHold)
            .where(CreditHold.workspace_id == principal.workspace_id, CreditHold.id == hold_id)
            .with_for_update()
        )
        if hold is None:
            raise AppError("CREDIT_HOLD_NOT_FOUND", "크레딧 Hold를 찾을 수 없습니다.", 404)
        result = finalize_hold_amounts(
            state=hold.state,
            maximum_amount=hold.maximum_amount,
            actual_amount=data.actual_amount,
            finalized_amount=hold.actual_amount,
        )
        if result.replay:
            if (
                hold.finalization_event_id != data.finalization_event_id
                or hold.failure_class != data.failure_class
            ):
                raise AppError(
                    "CREDIT_HOLD_FINALIZATION_CONFLICT",
                    "이미 다른 완료 이벤트 또는 실패 분류로 확정된 Hold입니다.",
                    409,
                )
            return hold
        account = await self._account(principal.workspace_id, for_update=True)
        allocations = list(
            await self._session.scalars(
                select(CreditHoldAllocation)
                .where(
                    CreditHoldAllocation.workspace_id == principal.workspace_id,
                    CreditHoldAllocation.hold_id == hold.id,
                )
                .order_by(CreditHoldAllocation.allocation_order)
                .with_for_update()
            )
        )
        grants = {
            value.id: value
            for value in await self._session.scalars(
                select(CreditGrant)
                .where(CreditGrant.id.in_([value.grant_id for value in allocations]))
                .with_for_update()
            )
        }
        remaining = result.consumed
        for allocation in allocations:
            consumed = min(allocation.held_amount, remaining)
            released = allocation.held_amount - consumed
            allocation.consumed_amount = consumed
            allocation.released_amount = released
            grant = grants[allocation.grant_id]
            grant.held_amount -= allocation.held_amount
            grant.available_amount += released
            remaining -= consumed
        if remaining != 0:
            raise AppError("CREDIT_ALLOCATION_CORRUPT", "Hold 배부 합계가 실제 비용과 일치하지 않습니다.", 500)
        available_before = account.available_balance
        held_before = account.held_balance
        account.held_balance -= hold.maximum_amount
        account.available_balance += result.released
        ensure_balance_transition(
            available_before=available_before,
            held_before=held_before,
            available_after=account.available_balance,
            held_after=account.held_balance,
        )
        hold.actual_amount = result.consumed
        hold.released_amount = result.released
        hold.finalization_event_id = data.finalization_event_id
        hold.failure_class = data.failure_class
        hold.state = CreditHoldState.FINALIZED.value
        hold.finalized_at = datetime.now(UTC)
        transaction_group_id = uuid4()
        if result.consumed > 0:
            self._session.add(
                CreditLedgerEntry(
                    workspace_id=principal.workspace_id,
                    account_id=account.id,
                    hold_id=hold.id,
                    kind=CreditEntryKind.CONSUME.value,
                    direction=LedgerDirection.DEBIT.value,
                    amount=result.consumed,
                    available_delta=Decimal("0"),
                    held_delta=-result.consumed,
                    available_after=account.available_balance,
                    held_after=account.held_balance,
                    transaction_group_id=transaction_group_id,
                    idempotency_key=f"finalize:{data.finalization_event_id}:consume",
                    source_type=hold.subject_type,
                    source_id=hold.subject_id,
                    reason_code=data.reason_code or "ACTUAL_COST",
                    pricing_snapshot=hold.pricing_snapshot,
                    actor_id=principal.subject_id,
                )
            )
        if result.released > 0:
            self._session.add(
                CreditLedgerEntry(
                    workspace_id=principal.workspace_id,
                    account_id=account.id,
                    hold_id=hold.id,
                    kind=CreditEntryKind.RELEASE.value,
                    direction=LedgerDirection.CREDIT.value,
                    amount=result.released,
                    available_delta=result.released,
                    held_delta=-result.released,
                    available_after=account.available_balance,
                    held_after=account.held_balance,
                    transaction_group_id=transaction_group_id,
                    idempotency_key=f"finalize:{data.finalization_event_id}:release",
                    source_type=hold.subject_type,
                    source_id=hold.subject_id,
                    reason_code="MAX_COST_DIFFERENCE",
                    pricing_snapshot=hold.pricing_snapshot,
                    actor_id=principal.subject_id,
                )
            )
        await self._session.flush()
        await self._record(
            principal=principal,
            action="billing.credit.finalized",
            target_type="credit_hold",
            target_id=hold.id,
            details={
                "actual_amount": str(result.consumed),
                "released_amount": str(result.released),
                "finalization_event_id": data.finalization_event_id,
                "failure_class": data.failure_class,
                "reason_code": data.reason_code,
            },
        )
        return hold

    async def release_hold(
        self, principal: Principal, hold_id: UUID, data: CreditHoldRelease
    ) -> CreditHold:
        await self._scope(principal.workspace_id)
        hold = await self._session.scalar(
            select(CreditHold)
            .where(CreditHold.workspace_id == principal.workspace_id, CreditHold.id == hold_id)
            .with_for_update()
        )
        if hold is None:
            raise AppError("CREDIT_HOLD_NOT_FOUND", "크레딧 Hold를 찾을 수 없습니다.", 404)
        if hold.state == CreditHoldState.RELEASED.value:
            if (
                hold.finalization_event_id != data.release_event_id
                or hold.failure_class != data.failure_class
            ):
                raise AppError(
                    "CREDIT_HOLD_RELEASE_CONFLICT",
                    "이미 다른 이벤트로 해제된 Hold입니다.",
                    409,
                )
            return hold
        if hold.state != CreditHoldState.HELD.value:
            raise AppError("CREDIT_HOLD_NOT_ACTIVE", "활성 Hold만 실패 해제할 수 있습니다.", 409)
        account = await self._account(principal.workspace_id, for_update=True)
        allocations = list(
            await self._session.scalars(
                select(CreditHoldAllocation)
                .where(
                    CreditHoldAllocation.workspace_id == principal.workspace_id,
                    CreditHoldAllocation.hold_id == hold.id,
                )
                .with_for_update()
            )
        )
        grants = {
            value.id: value
            for value in await self._session.scalars(
                select(CreditGrant)
                .where(CreditGrant.id.in_([value.grant_id for value in allocations]))
                .with_for_update()
            )
        }
        for allocation in allocations:
            allocation.released_amount = allocation.held_amount
            grant = grants[allocation.grant_id]
            grant.held_amount -= allocation.held_amount
            grant.available_amount += allocation.held_amount
        account.held_balance -= hold.maximum_amount
        account.available_balance += hold.maximum_amount
        ensure_balance_transition(
            available_before=account.available_balance - hold.maximum_amount,
            held_before=account.held_balance + hold.maximum_amount,
            available_after=account.available_balance,
            held_after=account.held_balance,
        )
        hold.actual_amount = Decimal("0")
        hold.released_amount = hold.maximum_amount
        hold.finalization_event_id = data.release_event_id
        hold.failure_class = data.failure_class
        hold.state = CreditHoldState.RELEASED.value
        hold.finalized_at = datetime.now(UTC)
        self._session.add(
            CreditLedgerEntry(
                workspace_id=principal.workspace_id,
                account_id=account.id,
                hold_id=hold.id,
                kind=CreditEntryKind.RELEASE.value,
                direction=LedgerDirection.CREDIT.value,
                amount=hold.maximum_amount,
                available_delta=hold.maximum_amount,
                held_delta=-hold.maximum_amount,
                available_after=account.available_balance,
                held_after=account.held_balance,
                transaction_group_id=uuid4(),
                idempotency_key=f"release:{data.release_event_id}",
                source_type=hold.subject_type,
                source_id=hold.subject_id,
                reason_code=data.reason_code,
                pricing_snapshot=hold.pricing_snapshot,
                actor_id=principal.subject_id,
            )
        )
        await self._session.flush()
        await self._record(
            principal=principal,
            action="billing.credit.released",
            target_type="credit_hold",
            target_id=hold.id,
            details={
                "release_event_id": data.release_event_id,
                "failure_class": data.failure_class,
            },
        )
        return hold

    async def record_usage(self, principal: Principal, data: UsageRecordCreate) -> UsageRecord:
        await self._scope(principal.workspace_id)
        existing = await self._session.scalar(
            select(UsageRecord).where(
                UsageRecord.workspace_id == principal.workspace_id,
                UsageRecord.source_event_id == data.source_event_id,
            )
        )
        if existing is not None:
            if (
                existing.quantity != data.quantity
                or existing.metric_key != data.metric_key
                or existing.price_version_id != data.price_version_id
                or existing.subject_type != data.subject_type
                or existing.subject_id != data.subject_id
            ):
                raise AppError("USAGE_EVENT_REUSED", "같은 사용량 이벤트의 내용이 다릅니다.", 409)
            return existing
        price = await self._price(
            principal.workspace_id,
            data.price_version_id,
            at=data.occurred_at,
        )
        if price.metric_key != data.metric_key:
            raise AppError("USAGE_PRICE_METRIC_MISMATCH", "사용량 지표와 가격표 지표가 다릅니다.", 422)
        units = self._priced_quantity(price, data.quantity)
        value = UsageRecord(
            workspace_id=principal.workspace_id,
            source_event_id=data.source_event_id,
            subject_type=data.subject_type,
            subject_id=data.subject_id,
            metric_key=data.metric_key,
            quantity=data.quantity,
            unit_name=price.unit_name,
            state=UsageRecordState.FINAL.value,
            price_version_id=price.id,
            pricing_snapshot=self._price_snapshot(price),
            credit_amount=self.price_credit_amount(price, data.quantity),
            internal_cost=units * price.internal_unit_cost,
            cost_currency=price.cost_currency,
            api_key_id=data.api_key_id,
            endpoint=data.endpoint,
            occurred_at=data.occurred_at,
            finalized_at=datetime.now(UTC),
            metadata_json=data.metadata,
        )
        self._session.add(value)
        await self._session.flush()
        await self._record(
            principal=principal,
            action="billing.usage.recorded",
            target_type="usage_record",
            target_id=value.id,
            details={
                "source_event_id": data.source_event_id,
                "metric_key": data.metric_key,
                "quantity": str(data.quantity),
            },
        )
        return value

    async def reverse_credit_debit(
        self,
        principal: Principal,
        entry_id: UUID,
        *,
        idempotency_key: str,
        reason_code: str,
        bucket_kind: CreditBucketKind,
        expires_at: datetime | None,
        reversal_policy_snapshot: dict[str, Any],
    ) -> CreditLedgerEntry:
        """Refund a finalized debit as a linked append-only entry and fresh grant."""

        await self._scope(principal.workspace_id)
        original = await self._session.scalar(
            select(CreditLedgerEntry)
            .where(
                CreditLedgerEntry.workspace_id == principal.workspace_id,
                CreditLedgerEntry.id == entry_id,
            )
            .with_for_update()
        )
        if original is None:
            raise AppError("CREDIT_ENTRY_NOT_FOUND", "크레딧 원장 항목을 찾을 수 없습니다.", 404)
        existing = await self._session.scalar(
            select(CreditLedgerEntry).where(
                CreditLedgerEntry.workspace_id == principal.workspace_id,
                CreditLedgerEntry.reversal_of_id == original.id,
            )
        )
        if existing is not None:
            if existing.idempotency_key != idempotency_key:
                raise AppError("CREDIT_ENTRY_ALREADY_REVERSED", "이미 역분개된 원장 항목입니다.", 409)
            return existing
        if original.kind not in {
            CreditEntryKind.CONSUME.value,
            CreditEntryKind.EXPIRE.value,
            CreditEntryKind.RECLAIM.value,
        } or original.direction != LedgerDirection.DEBIT.value:
            raise AppError(
                "CREDIT_ENTRY_NOT_REVERSIBLE",
                "확정 소비·만료·회수 Debit만 역분개할 수 있습니다.",
                409,
            )
        account = await self._account(principal.workspace_id, for_update=True)
        now = datetime.now(UTC)
        if expires_at is not None and expires_at <= now:
            raise AppError(
                "CREDIT_REVERSAL_POLICY_INVALID",
                "역분개 크레딧 만료 정책은 미래 시각이어야 합니다.",
                422,
            )
        grant = CreditGrant(
            workspace_id=principal.workspace_id,
            account_id=account.id,
            bucket_kind=bucket_kind.value,
            original_amount=original.amount,
            available_amount=original.amount,
            held_amount=Decimal("0"),
            source_type="LEDGER_REVERSAL",
            source_id=str(original.id),
            granted_at=now,
            expires_at=expires_at,
            grant_policy_snapshot={
                "reversal_of_entry_id": str(original.id),
                "reason_code": reason_code,
                **reversal_policy_snapshot,
            },
        )
        self._session.add(grant)
        account.available_balance += original.amount
        await self._session.flush()
        reversal = CreditLedgerEntry(
            workspace_id=principal.workspace_id,
            account_id=account.id,
            grant_id=grant.id,
            kind=CreditEntryKind.REVERSAL.value,
            direction=LedgerDirection.CREDIT.value,
            amount=original.amount,
            available_delta=original.amount,
            held_delta=Decimal("0"),
            available_after=account.available_balance,
            held_after=account.held_balance,
            transaction_group_id=original.transaction_group_id,
            reversal_of_id=original.id,
            idempotency_key=idempotency_key,
            source_type="LEDGER_REVERSAL",
            source_id=str(original.id),
            reason_code=reason_code,
            pricing_snapshot=original.pricing_snapshot,
            actor_id=principal.subject_id,
        )
        self._session.add(reversal)
        await self._session.flush()
        await self._record(
            principal=principal,
            action="billing.credit.reversed",
            target_type="credit_ledger_entry",
            target_id=reversal.id,
            details={"reversal_of_id": str(original.id), "amount": str(original.amount)},
        )
        return reversal

    async def queue_payment_intent(
        self, principal: Principal, data: PaymentIntentCreate
    ) -> PaymentCommand:
        """Persist an intent and outbox event; never claim provider success here."""

        await self._scope(principal.workspace_id)
        payload = data.model_dump(mode="json")
        request_hash = canonical_hash(payload)
        existing = await self._session.scalar(
            select(PaymentCommand).where(
                PaymentCommand.workspace_id == principal.workspace_id,
                PaymentCommand.requested_by == principal.subject_id,
                PaymentCommand.operation == data.operation,
                PaymentCommand.idempotency_key == data.idempotency_key,
            )
        )
        if existing is not None:
            if existing.request_hash != request_hash:
                raise AppError("IDEMPOTENCY_KEY_REUSED", "같은 멱등키의 결제 요청이 다릅니다.", 409)
            return existing
        if data.plan_version_id is not None:
            await self._plan(
                principal.workspace_id,
                data.plan_version_id,
                at=datetime.now(UTC),
            )
        command = PaymentCommand(
            workspace_id=principal.workspace_id,
            requested_by=principal.subject_id,
            operation=data.operation,
            idempotency_key=data.idempotency_key,
            request_hash=request_hash,
            request_snapshot=payload,
            provider=data.provider,
            state=PaymentCommandState.PENDING_PROVIDER.value,
        )
        self._session.add(command)
        await self._session.flush()
        await self._record(
            principal=principal,
            action="billing.payment_intent.queued",
            target_type="payment_command",
            target_id=command.id,
            details={"operation": command.operation, "provider": command.provider},
        )
        return command

    async def execute_payment_intent(
        self,
        workspace_id: UUID,
        command_id: UUID,
        *,
        gateways: PaymentGatewayRegistry,
    ) -> PaymentCommand:
        """Worker boundary for an idempotent provider checkout creation."""

        await self._scope(workspace_id)
        command = await self._session.scalar(
            select(PaymentCommand)
            .where(
                PaymentCommand.workspace_id == workspace_id,
                PaymentCommand.id == command_id,
            )
            .with_for_update()
        )
        if command is None:
            raise AppError("PAYMENT_COMMAND_NOT_FOUND", "결제 요청을 찾을 수 없습니다.", 404)
        if command.state in {
            PaymentCommandState.PROVIDER_ACCEPTED.value,
            PaymentCommandState.PROVIDER_REJECTED.value,
            PaymentCommandState.FAILED.value,
        }:
            return command
        if command.state != PaymentCommandState.PENDING_PROVIDER.value:
            raise AppError(
                "PAYMENT_COMMAND_NOT_PENDING",
                "공급자 처리 대기 중인 결제 요청만 실행할 수 있습니다.",
                409,
            )
        return_url = command.request_snapshot.get("return_url")
        if not isinstance(return_url, str) or not return_url:
            raise AppError(
                "PAYMENT_COMMAND_SNAPSHOT_INVALID",
                "결제 요청 Snapshot에 반환 URL이 없습니다.",
                503,
            )
        result = await gateways.resolve(command.provider).create_checkout(
            ProviderCheckoutRequest(
                command_id=command.id,
                workspace_id=workspace_id,
                operation=command.operation,
                request_snapshot=command.request_snapshot,
                idempotency_key=f"payment-command:{command.id}",
                return_url=return_url,
            )
        )
        now = datetime.now(UTC)
        provider_ref = result.provider_request_ref.strip()
        checkout_url = result.checkout_url.strip()
        parsed = urlsplit(checkout_url)
        if (
            not provider_ref
            or provider_ref != result.provider_request_ref
            or len(provider_ref) > 500
            or not checkout_url
            or checkout_url != result.checkout_url
            or len(checkout_url) > 2_048
            or parsed.scheme != "https"
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or result.expires_at.tzinfo is None
            or result.expires_at <= now
        ):
            raise AppError(
                "PAYMENT_PROVIDER_RESULT_INVALID",
                "결제 공급자의 Checkout 응답이 안전한 계약과 일치하지 않습니다.",
                503,
            )
        conflicting_command = await self._session.scalar(
            select(PaymentCommand.id).where(
                PaymentCommand.provider_request_ref == provider_ref,
                PaymentCommand.id != command.id,
            )
        )
        if conflicting_command is not None:
            raise AppError(
                "PAYMENT_PROVIDER_REF_REUSED",
                "결제 공급자 요청 참조가 다른 명령에 이미 사용되었습니다.",
                503,
            )
        command.provider_request_ref = provider_ref
        command.checkout_url = checkout_url
        command.expires_at = result.expires_at
        command.error_code = None
        command.state = PaymentCommandState.PROVIDER_ACCEPTED.value
        await add_outbox_event(
            self._session,
            workspace_id=workspace_id,
            aggregate_type="payment_command",
            aggregate_id=str(command.id),
            event_type="billing.payment_intent.provider_accepted",
            schema_version=_SCHEMA_VERSION,
            payload={
                "workspace_id": str(workspace_id),
                "payment_command_id": str(command.id),
                "provider": command.provider,
                "provider_request_ref": provider_ref,
            },
        )
        await self._session.flush()
        return command

    async def fail_payment_intent(
        self,
        workspace_id: UUID,
        command_id: UUID,
        *,
        error_code: str,
        provider_rejected: bool = False,
    ) -> PaymentCommand:
        """Persist a terminal worker failure without retrying a terminal replay."""

        await self._scope(workspace_id)
        command = await self._session.scalar(
            select(PaymentCommand)
            .where(
                PaymentCommand.workspace_id == workspace_id,
                PaymentCommand.id == command_id,
            )
            .with_for_update()
        )
        if command is None:
            raise AppError("PAYMENT_COMMAND_NOT_FOUND", "결제 요청을 찾을 수 없습니다.", 404)
        if command.state in {
            PaymentCommandState.PROVIDER_ACCEPTED.value,
            PaymentCommandState.PROVIDER_REJECTED.value,
            PaymentCommandState.FAILED.value,
        }:
            return command
        if command.state != PaymentCommandState.PENDING_PROVIDER.value:
            raise AppError(
                "PAYMENT_COMMAND_NOT_PENDING",
                "공급자 처리 대기 중인 결제 요청만 실패 처리할 수 있습니다.",
                409,
            )
        safe_code = error_code[:120] if error_code else "PAYMENT_CHECKOUT_FAILED"
        command.state = (
            PaymentCommandState.PROVIDER_REJECTED.value
            if provider_rejected
            else PaymentCommandState.FAILED.value
        )
        command.error_code = safe_code
        await add_outbox_event(
            self._session,
            workspace_id=workspace_id,
            aggregate_type="payment_command",
            aggregate_id=str(command.id),
            event_type="billing.payment_intent.failed",
            schema_version=_SCHEMA_VERSION,
            payload={
                "workspace_id": str(workspace_id),
                "payment_command_id": str(command.id),
                "state": command.state,
                "error_code": safe_code,
            },
        )
        await self._session.flush()
        return command

    async def ingest_payment_event(
        self,
        *,
        provider: str,
        headers: dict[str, str],
        body: bytes,
        gateway: PaymentGateway,
        archive: PaymentPayloadArchive,
    ) -> PaymentProviderEvent:
        """Accept only an adapter-verified provider fact and make replays idempotent."""

        verified = await gateway.verify_event(headers=headers, body=body)
        if verified.workspace_id.int == 0 or not verified.provider_event_id:
            raise AppError("PAYMENT_EVENT_INVALID", "검증된 결제 이벤트 식별자가 없습니다.", 422)
        sensitive_paths = _payment_sensitive_paths(verified.normalized_payload)
        if sensitive_paths:
            raise AppError(
                "PAYMENT_EVENT_NORMALIZATION_UNSAFE",
                "정규화된 결제 이벤트에 민감 정보가 포함되었습니다.",
                503,
                fields=[
                    {"path": path, "reason": "store only opaque references"}
                    for path in sensitive_paths
                ],
            )
        raw_hash = hashlib.sha256(body).hexdigest()
        await self._scope(verified.workspace_id)
        existing = await self._session.scalar(
            select(PaymentProviderEvent).where(
                PaymentProviderEvent.provider == provider,
                PaymentProviderEvent.provider_event_id == verified.provider_event_id,
            )
        )
        if existing is not None:
            if existing.raw_payload_hash != raw_hash:
                raise AppError(
                    "PAYMENT_EVENT_REPLAY_CONFLICT",
                    "같은 결제 이벤트 ID의 Payload Hash가 다릅니다.",
                    409,
                )
            return existing
        raw_ref = await archive.store(
            provider=provider,
            provider_event_id=verified.provider_event_id,
            body=body,
        )
        if not raw_ref:
            raise AppError("PAYMENT_PAYLOAD_ARCHIVE_FAILED", "결제 Payload 보관에 실패했습니다.", 503)
        value = PaymentProviderEvent(
            workspace_id=verified.workspace_id,
            provider=provider,
            provider_event_id=verified.provider_event_id,
            event_type=verified.event_type,
            signature_key_version=verified.signature_key_version,
            raw_payload_hash=raw_hash,
            raw_payload_ref=raw_ref,
            normalized_payload=verified.normalized_payload,
            occurred_at=verified.occurred_at,
        )
        self._session.add(value)
        await self._session.flush()
        await add_outbox_event(
            self._session,
            workspace_id=verified.workspace_id,
            aggregate_type="payment_provider_event",
            aggregate_id=str(value.id),
            event_type="billing.payment_provider_event.verified",
            schema_version=_SCHEMA_VERSION,
            payload={
                "workspace_id": str(verified.workspace_id),
                "payment_provider_event_id": str(value.id),
                "provider": provider,
                "provider_event_id": verified.provider_event_id,
                "event_type": verified.event_type,
            },
        )
        return value

    async def list_credit_entries(
        self, principal: Principal, *, limit: int, offset: int
    ) -> list[CreditLedgerEntry]:
        await self._scope(principal.workspace_id)
        return list(
            await self._session.scalars(
                select(CreditLedgerEntry)
                .where(CreditLedgerEntry.workspace_id == principal.workspace_id)
                .order_by(CreditLedgerEntry.posted_at.desc(), CreditLedgerEntry.id.desc())
                .limit(limit)
                .offset(offset)
            )
        )

    async def list_usage(
        self, principal: Principal, *, limit: int, offset: int
    ) -> list[UsageRecord]:
        await self._scope(principal.workspace_id)
        return list(
            await self._session.scalars(
                select(UsageRecord)
                .where(UsageRecord.workspace_id == principal.workspace_id)
                .order_by(UsageRecord.occurred_at.desc(), UsageRecord.id.desc())
                .limit(limit)
                .offset(offset)
            )
        )

    @staticmethod
    def check_usage_limit(data: UsageLimitCheck) -> UsageLimitDecision:
        return evaluate_usage_limit(
            used=data.used,
            requested=data.requested,
            limit=data.limit,
            policy=data.overage_policy,
        )
