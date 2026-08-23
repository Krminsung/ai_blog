"""Billing-backed publishing entitlements and paid-job budget boundaries.

API services call ``reserve`` or ``authorize`` before persisting a queued job. Workers
call ``finalize``/``settle`` for successful terminal events and ``release`` for failed
or cancelled events. A failed job with incurred provider cost is finalized for that
actual cost and only the remainder is released. All calls share the caller's database
transaction.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.core.context import Principal
from blogops.core.errors import AppError
from blogops.db.session import apply_workspace_scope
from blogops.domain.billing.enums import SubscriptionState
from blogops.domain.billing.models import (
    BillingPlanVersion,
    BillingPriceVersion,
    BillingSubscription,
    CreditAccount,
    CreditHold,
    EntitlementSnapshot,
)
from blogops.domain.billing.rules import pricing_is_effective
from blogops.domain.billing.schemas import (
    CreditHoldCreate,
    CreditHoldFinalize,
    CreditHoldRelease,
    UsageRecordCreate,
)
from blogops.domain.billing.service import BillingService
from blogops.domain.bulk.providers import BulkBudgetReservation
from blogops.domain.media.enums import MediaOperation
from blogops.domain.media.providers import MediaBudgetReservation

if TYPE_CHECKING:
    from blogops.domain.generation.providers import (
        BudgetAuthorization as GenerationBudgetAuthorization,
        UsageSettlement as GenerationUsageSettlement,
    )
    from blogops.domain.repurpose.providers import (
        BudgetReservation as RepurposeBudgetReservation,
    )

_HOLD_REF_PREFIX = "credit-hold:"
_BUDGET_POLICY_SCHEMA_VERSION = 1
_MAX_HOLD_TTL_SECONDS = 7 * 24 * 60 * 60
_MAX_LEDGER_AMOUNT = Decimal("10000000000000")
_PUBLISHING_CONNECTION_ENTITLEMENT_KEY = "max_publishing_connections"
_BUDGET_CAPABLE_SUBSCRIPTION_STATES = frozenset(
    {
        SubscriptionState.ACTIVE.value,
        SubscriptionState.CANCELLATION_SCHEDULED.value,
        SubscriptionState.TRIALING.value,
    }
)


@dataclass(frozen=True, slots=True)
class BudgetAuthorization:
    """Server-side, versioned conversion from provider cost to customer credits."""

    price_version_id: UUID
    maximum_credit_amount: Decimal
    authorized_cost: Decimal
    currency: str
    hold_expires_at: datetime
    entitlement_snapshot: dict[str, Any]
    policy_snapshot: dict[str, Any]


class BudgetAuthorizationResolver(Protocol):
    async def authorize(
        self,
        *,
        workspace_id: UUID,
        operation_kind: str,
        estimated_cost: Decimal,
        requested_maximum_cost: Decimal | None,
        currency: str,
    ) -> BudgetAuthorization: ...


class FailClosedBudgetAuthorizationResolver:
    async def authorize(
        self,
        *,
        workspace_id: UUID,
        operation_kind: str,
        estimated_cost: Decimal,
        requested_maximum_cost: Decimal | None,
        currency: str,
    ) -> BudgetAuthorization:
        del workspace_id, operation_kind, estimated_cost, requested_maximum_cost, currency
        raise AppError(
            "BILLING_BUDGET_POLICY_UNAVAILABLE",
            "가격표·최대 비용·Entitlement 변환 정책이 구성되지 않았습니다.",
            503,
        )


def _budget_configuration_error(path: str, reason: str) -> AppError:
    return AppError(
        "BILLING_BUDGET_POLICY_UNAVAILABLE",
        "가격표·최대 비용·Entitlement 변환 정책이 완전하지 않습니다.",
        503,
        fields=[{"path": path, "reason": reason}],
    )


def _required_mapping(value: Any, *, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _budget_configuration_error(path, "object required")
    return value


def _required_positive_decimal(value: Any, *, path: str) -> Decimal:
    if isinstance(value, bool):
        raise _budget_configuration_error(path, "positive decimal required")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise _budget_configuration_error(path, "positive decimal required") from exc
    if not result.is_finite() or result <= 0:
        raise _budget_configuration_error(path, "positive finite decimal required")
    return result


def _requested_positive_decimal(value: Any, *, path: str) -> Decimal:
    """Parse a caller-supplied monetary boundary without policy fallbacks."""

    if isinstance(value, bool):
        result = None
    else:
        try:
            result = Decimal(str(value))
        except (InvalidOperation, ValueError):
            result = None
    if result is None or not result.is_finite() or result <= 0:
        raise AppError(
            "BILLING_BUDGET_REQUEST_INVALID",
            "예상 비용과 최대 비용은 양수인 유한값이어야 합니다.",
            422,
            fields=[{"path": path, "reason": "positive finite decimal required"}],
        )
    return result


def _requested_mapping(value: Any, *, path: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise AppError(
            "BILLING_BUDGET_REQUEST_INVALID",
            "비용 산정 세부 내역 형식이 올바르지 않습니다.",
            422,
            fields=[{"path": path, "reason": "object required"}],
        )
    return dict(value)


def _budget_idempotency_key(namespace: str, *parts: object) -> str:
    digest = sha256("\x1f".join(str(part) for part in parts).encode()).hexdigest()
    return f"{namespace}:{digest}"


def _required_uuid(value: Any, *, path: str) -> UUID:
    try:
        return UUID(str(value))
    except (AttributeError, TypeError, ValueError) as exc:
        raise _budget_configuration_error(path, "UUID required") from exc


def _required_hold_ttl(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _budget_configuration_error(
            "credit_policy.job_budget_authorization.hold_ttl_seconds",
            "integer required",
        )
    if value <= 0 or value > _MAX_HOLD_TTL_SECONDS:
        raise _budget_configuration_error(
            "credit_policy.job_budget_authorization.hold_ttl_seconds",
            f"must be between 1 and {_MAX_HOLD_TTL_SECONDS}",
        )
    return value


class DatabaseBudgetAuthorizationResolver:
    """Resolve Stage 6 holds from versioned billing rows in the current transaction.

    ``BillingPlanVersion.credit_policy.job_budget_authorization`` must contain
    ``schema_version``, ``allowed_subscription_states``, ``hold_ttl_seconds`` and an
    ``operations`` mapping. Each operation must explicitly enable the operation and
    name its currency, maximum cost, metric key, and exact ``price_version_id``.
    ``EntitlementSnapshot.entitlements.job_budget_operations`` must independently
    enable the same operation and provide the same currency plus its maximum cost.
    No estimated-price or default-policy fallback is used.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _validate_request(
        *,
        estimated_cost: Decimal,
        requested_maximum_cost: Decimal | None,
        currency: str,
    ) -> tuple[Decimal, str]:
        if (
            not estimated_cost.is_finite()
            or estimated_cost <= 0
            or requested_maximum_cost is None
            or not requested_maximum_cost.is_finite()
            or requested_maximum_cost <= 0
            or estimated_cost > requested_maximum_cost
        ):
            raise AppError(
                "BILLING_BUDGET_REQUEST_INVALID",
                "예상 비용과 최대 비용은 양수이며 예상 비용은 최대 비용 이하여야 합니다.",
                422,
            )
        normalized_currency = currency.strip().upper()
        if (
            currency != normalized_currency
            or len(normalized_currency) != 3
            or not normalized_currency.isascii()
            or not normalized_currency.isalpha()
        ):
            raise AppError(
                "BILLING_COST_CURRENCY_INVALID",
                "비용 통화는 대문자 ISO 4217 코드여야 합니다.",
                422,
            )
        return requested_maximum_cost, normalized_currency

    @staticmethod
    def _subscription_deadline(
        subscription: BillingSubscription,
        *,
        now: datetime,
    ) -> datetime:
        if subscription.state == SubscriptionState.TRIALING.value:
            starts_at = subscription.trial_started_at
            ends_at = subscription.trial_ends_at
        else:
            starts_at = subscription.current_period_start
            ends_at = subscription.current_period_end
        if (
            starts_at is None
            or ends_at is None
            or starts_at.tzinfo is None
            or ends_at.tzinfo is None
            or not starts_at <= now < ends_at
        ):
            raise AppError(
                "BILLING_SUBSCRIPTION_PERIOD_INACTIVE",
                "현재 유효한 구독 또는 체험 기간이 없어 작업 비용을 승인할 수 없습니다.",
                402,
            )
        return ends_at

    @staticmethod
    def _operation_policy(
        plan: BillingPlanVersion,
        entitlement: EntitlementSnapshot,
        *,
        operation_kind: str,
        currency: str,
    ) -> tuple[dict[str, Any], dict[str, Any], int, frozenset[str]]:
        root_path = "credit_policy.job_budget_authorization"
        root = _required_mapping(
            plan.credit_policy.get("job_budget_authorization"),
            path=root_path,
        )
        schema_version = root.get("schema_version")
        if (
            isinstance(schema_version, bool)
            or not isinstance(schema_version, int)
            or schema_version != _BUDGET_POLICY_SCHEMA_VERSION
        ):
            raise _budget_configuration_error(
                f"{root_path}.schema_version",
                f"must equal {_BUDGET_POLICY_SCHEMA_VERSION}",
            )
        states = root.get("allowed_subscription_states")
        if (
            not isinstance(states, list)
            or not states
            or any(not isinstance(value, str) for value in states)
            or not set(states) <= _BUDGET_CAPABLE_SUBSCRIPTION_STATES
        ):
            raise _budget_configuration_error(
                f"{root_path}.allowed_subscription_states",
                "non-empty safe subscription-state list required",
            )
        operation = _required_mapping(
            _required_mapping(
                root.get("operations"),
                path=f"{root_path}.operations",
            ).get(operation_kind),
            path=f"{root_path}.operations.{operation_kind}",
        )
        entitlement_path = f"entitlements.job_budget_operations.{operation_kind}"
        entitlement_operation = _required_mapping(
            _required_mapping(
                entitlement.entitlements.get("job_budget_operations"),
                path="entitlements.job_budget_operations",
            ).get(operation_kind),
            path=entitlement_path,
        )
        if operation.get("enabled") is not True:
            raise AppError(
                "BILLING_OPERATION_DISABLED",
                "현재 요금제 정책에서 요청한 작업이 비활성화되어 있습니다.",
                403,
            )
        if entitlement_operation.get("enabled") is not True:
            raise AppError(
                "BILLING_OPERATION_NOT_ENTITLED",
                "현재 Entitlement에서 요청한 작업을 사용할 수 없습니다.",
                403,
            )
        for path, value in (
            (
                f"{root_path}.operations.{operation_kind}.currency",
                operation.get("currency"),
            ),
            (
                f"{entitlement_path}.currency",
                entitlement_operation.get("currency"),
            ),
        ):
            if value != currency:
                raise _budget_configuration_error(path, f"must equal {currency}")
        return (
            operation,
            entitlement_operation,
            _required_hold_ttl(root.get("hold_ttl_seconds")),
            frozenset(states),
        )

    async def authorize(
        self,
        *,
        workspace_id: UUID,
        operation_kind: str,
        estimated_cost: Decimal,
        requested_maximum_cost: Decimal | None,
        currency: str,
    ) -> BudgetAuthorization:
        maximum_cost, normalized_currency = self._validate_request(
            estimated_cost=estimated_cost,
            requested_maximum_cost=requested_maximum_cost,
            currency=currency,
        )
        await apply_workspace_scope(self._session, workspace_id)
        now = datetime.now(UTC)
        subscription = await self._session.scalar(
            select(BillingSubscription)
            .where(BillingSubscription.workspace_id == workspace_id)
            .with_for_update()
        )
        if subscription is None:
            raise AppError(
                "BILLING_SUBSCRIPTION_REQUIRED",
                "활성 구독이 없어 작업 비용을 승인할 수 없습니다.",
                402,
            )
        plan = await self._session.scalar(
            select(BillingPlanVersion).where(
                BillingPlanVersion.id == subscription.plan_version_id
            )
        )
        if plan is None or plan.workspace_id not in {None, workspace_id}:
            raise _budget_configuration_error(
                "subscription.plan_version_id",
                "workspace-visible plan version required",
            )
        if not pricing_is_effective(
            state=plan.state,
            effective_at=plan.effective_at,
            retired_at=plan.retired_at,
            at=now,
        ):
            raise AppError(
                "BILLING_PLAN_VERSION_INACTIVE",
                "현재 유효한 요금제 버전이 없어 작업 비용을 승인할 수 없습니다.",
                402,
            )
        if subscription.entitlement_snapshot_id is None:
            raise _budget_configuration_error(
                "subscription.entitlement_snapshot_id",
                "pinned entitlement snapshot required",
            )
        entitlement = await self._session.scalar(
            select(EntitlementSnapshot).where(
                EntitlementSnapshot.workspace_id == workspace_id,
                EntitlementSnapshot.id == subscription.entitlement_snapshot_id,
            )
        )
        if entitlement is None or entitlement.plan_version_id != plan.id:
            raise _budget_configuration_error(
                "subscription.entitlement_snapshot_id",
                "matching plan entitlement snapshot required",
            )
        if (
            entitlement.valid_from.tzinfo is None
            or entitlement.valid_from > now
            or (
                entitlement.valid_until is not None
                and (
                    entitlement.valid_until.tzinfo is None
                    or now >= entitlement.valid_until
                )
            )
        ):
            raise AppError(
                "BILLING_ENTITLEMENT_INACTIVE",
                "현재 유효한 Entitlement Snapshot이 없습니다.",
                403,
            )
        operation, entitlement_operation, hold_ttl, allowed_states = (
            self._operation_policy(
                plan,
                entitlement,
                operation_kind=operation_kind,
                currency=normalized_currency,
            )
        )
        if subscription.state not in allowed_states:
            raise AppError(
                "BILLING_SUBSCRIPTION_INACTIVE",
                "현재 구독 상태에서는 작업 비용을 승인할 수 없습니다.",
                402,
            )
        subscription_deadline = self._subscription_deadline(subscription, now=now)
        plan_maximum = _required_positive_decimal(
            operation.get("maximum_cost"),
            path=(
                "credit_policy.job_budget_authorization.operations."
                f"{operation_kind}.maximum_cost"
            ),
        )
        entitlement_maximum = _required_positive_decimal(
            entitlement_operation.get("maximum_cost"),
            path=f"entitlements.job_budget_operations.{operation_kind}.maximum_cost",
        )
        authorized_ceiling = min(plan_maximum, entitlement_maximum)
        if maximum_cost > authorized_ceiling:
            raise AppError(
                "BILLING_MAXIMUM_COST_NOT_AUTHORIZED",
                "요청한 최대 비용이 요금제 또는 Entitlement 한도를 초과합니다.",
                402,
                remediation={
                    "requested_maximum_cost": str(maximum_cost),
                    "authorized_ceiling": str(authorized_ceiling),
                    "currency": normalized_currency,
                },
            )
        price_version_id = _required_uuid(
            operation.get("price_version_id"),
            path=(
                "credit_policy.job_budget_authorization.operations."
                f"{operation_kind}.price_version_id"
            ),
        )
        price = await self._session.scalar(
            select(BillingPriceVersion).where(BillingPriceVersion.id == price_version_id)
        )
        if price is None or price.workspace_id not in {None, workspace_id}:
            raise _budget_configuration_error(
                "credit_policy.job_budget_authorization.operations.price_version_id",
                "workspace-visible price version required",
            )
        if not pricing_is_effective(
            state=price.state,
            effective_at=price.effective_at,
            retired_at=price.retired_at,
            at=now,
        ):
            raise AppError(
                "PRICING_VERSION_INACTIVE",
                "현재 유효한 정확한 가격표 버전이 필요합니다.",
                409,
            )
        expected_metric = operation.get("metric_key")
        if not isinstance(expected_metric, str) or price.metric_key != expected_metric:
            raise _budget_configuration_error(
                "credit_policy.job_budget_authorization.operations.metric_key",
                "must match the pinned price metric",
            )
        if price.cost_currency != normalized_currency:
            raise _budget_configuration_error(
                "billing_price_versions.cost_currency",
                f"must equal {normalized_currency}",
            )
        if price.credit_unit_price <= 0:
            raise _budget_configuration_error(
                "billing_price_versions.credit_unit_price",
                "positive credit price required for a hold",
            )
        maximum_credit_amount = BillingService.price_credit_amount(
            price,
            maximum_cost,
        )
        if (
            not maximum_credit_amount.is_finite()
            or maximum_credit_amount <= 0
            or maximum_credit_amount >= _MAX_LEDGER_AMOUNT
        ):
            raise _budget_configuration_error(
                "billing_price_versions",
                "price conversion must produce positive finite credits",
            )
        hold_expires_at = now + timedelta(seconds=hold_ttl)
        validity_deadlines = [subscription_deadline]
        for deadline in (plan.retired_at, price.retired_at, entitlement.valid_until):
            if deadline is not None:
                validity_deadlines.append(deadline)
        if any(deadline < hold_expires_at for deadline in validity_deadlines):
            raise AppError(
                "BILLING_BUDGET_VALIDITY_TOO_SHORT",
                "가격표·구독·Entitlement 유효기간이 Hold 정책 기간보다 짧습니다.",
                409,
            )
        account = await self._session.scalar(
            select(CreditAccount)
            .where(CreditAccount.workspace_id == workspace_id)
            .with_for_update()
        )
        if account is None:
            raise AppError(
                "CREDIT_ACCOUNT_NOT_CONFIGURED",
                "크레딧 계정이 아직 구성되지 않았습니다.",
                409,
            )
        if account.available_balance < maximum_credit_amount:
            raise AppError(
                "CREDIT_BALANCE_INSUFFICIENT",
                "승인된 최대 비용을 Hold할 크레딧이 부족합니다.",
                402,
                remediation={
                    "required": str(maximum_credit_amount),
                    "available": str(account.available_balance),
                },
            )
        return BudgetAuthorization(
            price_version_id=price.id,
            maximum_credit_amount=maximum_credit_amount,
            authorized_cost=maximum_cost,
            currency=normalized_currency,
            hold_expires_at=hold_expires_at,
            entitlement_snapshot={
                **entitlement.entitlements,
                "billing_evidence": {
                    "entitlement_snapshot_id": str(entitlement.id),
                    "snapshot_hash": entitlement.snapshot_hash,
                    "plan_version_id": str(plan.id),
                },
            },
            policy_snapshot={
                "schema_version": _BUDGET_POLICY_SCHEMA_VERSION,
                "operation_kind": operation_kind,
                "plan_version_id": str(plan.id),
                "plan_policy_hash": plan.policy_hash,
                "price_version_id": str(price.id),
                "pricing_hash": price.pricing_hash,
                "metric_key": price.metric_key,
                "authorized_cost": str(maximum_cost),
                "authorized_ceiling": str(authorized_ceiling),
                "currency": normalized_currency,
                "hold_ttl_seconds": hold_ttl,
            },
        )


class DatabasePublishingEntitlementResolver:
    """Resolve the publishing connection limit from the pinned billing snapshot.

    The resolver accepts only a currently usable subscription whose exact plan and
    entitlement snapshot are both active. Missing or malformed limits never fall
    back to a product default.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def max_connections(self, *, workspace_id: UUID) -> int:
        await apply_workspace_scope(self._session, workspace_id)
        now = datetime.now(UTC)
        subscription = await self._session.scalar(
            select(BillingSubscription)
            .where(BillingSubscription.workspace_id == workspace_id)
            .with_for_update()
        )
        if subscription is None:
            raise AppError(
                "PUBLISH_CONNECTION_ENTITLEMENT_REQUIRED",
                "활성 구독과 게시 연결 Entitlement Snapshot이 필요합니다.",
                409,
            )
        if subscription.state not in _BUDGET_CAPABLE_SUBSCRIPTION_STATES:
            raise AppError(
                "PUBLISH_CONNECTION_ENTITLEMENT_REQUIRED",
                "현재 구독 상태에서는 게시 연결을 추가할 수 없습니다.",
                409,
            )
        DatabaseBudgetAuthorizationResolver._subscription_deadline(
            subscription,
            now=now,
        )
        plan = await self._session.scalar(
            select(BillingPlanVersion).where(
                BillingPlanVersion.id == subscription.plan_version_id
            )
        )
        if plan is None or plan.workspace_id not in {None, workspace_id}:
            raise _publishing_entitlement_error(
                "subscription.plan_version_id",
                "workspace-visible plan version required",
            )
        if not pricing_is_effective(
            state=plan.state,
            effective_at=plan.effective_at,
            retired_at=plan.retired_at,
            at=now,
        ):
            raise AppError(
                "PUBLISH_CONNECTION_ENTITLEMENT_REQUIRED",
                "현재 유효한 요금제 버전이 없어 게시 연결을 추가할 수 없습니다.",
                409,
            )
        if subscription.entitlement_snapshot_id is None:
            raise _publishing_entitlement_error(
                "subscription.entitlement_snapshot_id",
                "pinned entitlement snapshot required",
            )
        entitlement = await self._session.scalar(
            select(EntitlementSnapshot).where(
                EntitlementSnapshot.workspace_id == workspace_id,
                EntitlementSnapshot.id == subscription.entitlement_snapshot_id,
            )
        )
        if entitlement is None or entitlement.plan_version_id != plan.id:
            raise _publishing_entitlement_error(
                "subscription.entitlement_snapshot_id",
                "matching plan entitlement snapshot required",
            )
        if (
            entitlement.valid_from.tzinfo is None
            or entitlement.valid_from > now
            or (
                entitlement.valid_until is not None
                and (
                    entitlement.valid_until.tzinfo is None
                    or now >= entitlement.valid_until
                )
            )
        ):
            raise AppError(
                "PUBLISH_CONNECTION_ENTITLEMENT_REQUIRED",
                "현재 유효한 게시 연결 Entitlement Snapshot이 필요합니다.",
                409,
            )
        if not isinstance(entitlement.entitlements, dict):
            raise _publishing_entitlement_error(
                "entitlements",
                "object required",
            )
        value = entitlement.entitlements.get(_PUBLISHING_CONNECTION_ENTITLEMENT_KEY)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise _publishing_entitlement_error(
                f"entitlements.{_PUBLISHING_CONNECTION_ENTITLEMENT_KEY}",
                "positive integer required",
            )
        return value


def _publishing_entitlement_error(path: str, reason: str) -> AppError:
    return AppError(
        "PUBLISH_CONNECTION_ENTITLEMENT_REQUIRED",
        "게시 연결 한도가 포함된 유효한 Entitlement Snapshot이 필요합니다.",
        409,
        fields=[{"path": path, "reason": reason}],
    )


def _worker_principal(*, workspace_id: UUID, actor_id: UUID) -> Principal:
    return Principal(
        subject_id=actor_id,
        workspace_id=workspace_id,
        session_id=None,
        permissions=frozenset({"billing:meter"}),
        authentication_method="trusted_job_worker",
    )


def _reservation_ref(hold_id: UUID) -> str:
    return f"{_HOLD_REF_PREFIX}{hold_id}"


def _hold_id(value: str) -> UUID:
    if not value.startswith(_HOLD_REF_PREFIX):
        raise AppError("BILLING_HOLD_REF_INVALID", "비용 Hold 참조가 올바르지 않습니다.", 422)
    try:
        return UUID(value.removeprefix(_HOLD_REF_PREFIX))
    except ValueError as exc:
        raise AppError("BILLING_HOLD_REF_INVALID", "비용 Hold 참조가 올바르지 않습니다.", 422) from exc


class _BillingJobBudgetAdapter:
    def __init__(
        self,
        billing: BillingService,
        authorization_resolver: BudgetAuthorizationResolver,
    ) -> None:
        self._billing = billing
        self._resolver = authorization_resolver

    async def _reserve(
        self,
        *,
        workspace_id: UUID,
        actor_id: UUID,
        operation_kind: str,
        subject_type: str,
        subject_id: str,
        idempotency_key: str,
        estimated_cost: Decimal,
        requested_maximum_cost: Decimal | None,
        currency: str,
    ) -> tuple[CreditHold, BudgetAuthorization]:
        authorization = await self._resolver.authorize(
            workspace_id=workspace_id,
            operation_kind=operation_kind,
            estimated_cost=estimated_cost,
            requested_maximum_cost=requested_maximum_cost,
            currency=currency,
        )
        if authorization.currency != currency or authorization.authorized_cost < estimated_cost:
            raise AppError(
                "BILLING_BUDGET_AUTHORIZATION_INVALID",
                "비용 승인 결과가 요청 통화 또는 최소 예상 비용과 일치하지 않습니다.",
                503,
            )
        if (
            requested_maximum_cost is not None
            and authorization.authorized_cost < requested_maximum_cost
        ):
            raise AppError(
                "BILLING_MAXIMUM_COST_NOT_AUTHORIZED",
                "요청한 최대 비용 전액이 승인되지 않아 작업을 시작할 수 없습니다.",
                402,
            )
        principal = _worker_principal(workspace_id=workspace_id, actor_id=actor_id)
        hold = await self._billing.create_hold(
            principal,
            CreditHoldCreate(
                subject_type=subject_type,
                subject_id=subject_id,
                operation=operation_kind,
                price_version_id=authorization.price_version_id,
                maximum_amount=authorization.maximum_credit_amount,
                idempotency_key=idempotency_key,
                expires_at=authorization.hold_expires_at,
            ),
        )
        return hold, authorization

    async def _finalize(
        self,
        *,
        workspace_id: UUID,
        actor_id: UUID,
        reservation_ref: str,
        actual_cost: Decimal,
        currency: str,
        completion_event_id: str,
        subject_type: str,
        failure_class: str | None = None,
        reason_code: str | None = None,
        usage_metadata: Mapping[str, Any] | None = None,
    ) -> CreditHold:
        if not actual_cost.is_finite() or actual_cost < 0:
            raise AppError(
                "BILLING_ACTUAL_COST_INVALID",
                "실제 비용은 음수가 아닌 유한값이어야 합니다.",
                422,
            )
        principal = _worker_principal(workspace_id=workspace_id, actor_id=actor_id)
        hold = await self._billing.get_hold(principal, _hold_id(reservation_ref))
        snapshot_currency = str(hold.pricing_snapshot.get("cost_currency", ""))
        if snapshot_currency != currency:
            raise AppError("BILLING_COST_CURRENCY_MISMATCH", "실제 비용 통화가 Hold 가격표와 다릅니다.", 409)
        actual_credits = Decimal("0")
        if actual_cost > 0:
            usage = await self._billing.record_usage(
                principal,
                UsageRecordCreate(
                    source_event_id=completion_event_id,
                    subject_type=subject_type,
                    subject_id=hold.subject_id,
                    metric_key=str(hold.pricing_snapshot["metric_key"]),
                    quantity=actual_cost,
                    price_version_id=hold.price_version_id,
                    occurred_at=datetime.now(UTC),
                    metadata={
                        **dict(usage_metadata or {}),
                        "provider_cost_currency": currency,
                        "credit_hold_id": str(hold.id),
                        "terminal_failure_class": failure_class,
                    },
                ),
            )
            actual_credits = usage.credit_amount
        return await self._billing.finalize_hold(
            principal,
            hold.id,
            CreditHoldFinalize(
                finalization_event_id=completion_event_id,
                actual_amount=actual_credits,
                failure_class=failure_class,
                reason_code=reason_code,
            ),
        )

    async def _release(
        self,
        *,
        workspace_id: UUID,
        actor_id: UUID,
        reservation_ref: str,
        actual_cost: Decimal,
        currency: str,
        terminal_event_id: str,
        failure_class: str,
        reason_code: str,
        subject_type: str,
    ) -> CreditHold:
        if not actual_cost.is_finite() or actual_cost < 0:
            raise AppError(
                "BILLING_ACTUAL_COST_INVALID",
                "실제 비용은 음수가 아닌 유한값이어야 합니다.",
                422,
            )
        if actual_cost > 0:
            return await self._finalize(
                workspace_id=workspace_id,
                actor_id=actor_id,
                reservation_ref=reservation_ref,
                actual_cost=actual_cost,
                currency=currency,
                completion_event_id=terminal_event_id,
                subject_type=subject_type,
                failure_class=failure_class,
                reason_code=reason_code,
            )
        principal = _worker_principal(workspace_id=workspace_id, actor_id=actor_id)
        hold_id = _hold_id(reservation_ref)
        hold = await self._billing.get_hold(principal, hold_id)
        if hold.pricing_snapshot.get("cost_currency") != currency:
            raise AppError(
                "BILLING_COST_CURRENCY_MISMATCH",
                "실제 비용 통화가 Hold 가격표와 다릅니다.",
                409,
            )
        return await self._billing.release_hold(
            principal,
            hold_id,
            CreditHoldRelease(
                release_event_id=terminal_event_id,
                failure_class=failure_class,
                reason_code=reason_code,
            ),
        )


class BillingMediaBudgetAdapter(_BillingJobBudgetAdapter):
    """Implements the max-cost MediaBudgetGate plus terminal settlement methods."""

    async def reserve(
        self,
        *,
        workspace_id: UUID,
        actor_id: UUID,
        operation: MediaOperation,
        estimated_cost: Decimal,
        maximum_cost: Decimal,
        currency: str,
        idempotency_key: str,
    ) -> MediaBudgetReservation:
        hold, authorization = await self._reserve(
            workspace_id=workspace_id,
            actor_id=actor_id,
            operation_kind=f"media.{operation.value.casefold()}",
            subject_type="media_operation_job",
            subject_id=idempotency_key,
            idempotency_key=f"media:{idempotency_key}",
            estimated_cost=estimated_cost,
            requested_maximum_cost=maximum_cost,
            currency=currency,
        )
        return MediaBudgetReservation(
            reservation_ref=_reservation_ref(hold.id),
            authorized_amount=authorization.authorized_cost,
            currency=authorization.currency,
            policy_snapshot={
                **authorization.policy_snapshot,
                "entitlement_snapshot": authorization.entitlement_snapshot,
                "credit_hold_id": str(hold.id),
                "price_version_id": str(authorization.price_version_id),
                "maximum_credit_amount": str(authorization.maximum_credit_amount),
            },
        )

    async def finalize(
        self,
        *,
        workspace_id: UUID,
        actor_id: UUID,
        reservation_ref: str,
        actual_cost: Decimal,
        currency: str,
        terminal_event_id: str,
    ) -> CreditHold:
        return await self._finalize(
            workspace_id=workspace_id,
            actor_id=actor_id,
            reservation_ref=reservation_ref,
            actual_cost=actual_cost,
            currency=currency,
            completion_event_id=terminal_event_id,
            subject_type="media_operation_job",
        )

    async def release(
        self,
        *,
        workspace_id: UUID,
        actor_id: UUID,
        reservation_ref: str,
        actual_cost: Decimal,
        currency: str,
        terminal_event_id: str,
        failure_class: str,
    ) -> CreditHold:
        return await self._release(
            workspace_id=workspace_id,
            actor_id=actor_id,
            reservation_ref=reservation_ref,
            actual_cost=actual_cost,
            currency=currency,
            terminal_event_id=terminal_event_id,
            failure_class=failure_class,
            reason_code=f"MEDIA_TERMINAL_{failure_class}",
            subject_type="media_operation_job",
        )


class BillingBulkBudgetAdapter(_BillingJobBudgetAdapter):
    """Implements BulkBudgetGate plus explicit success/failure/cancel settlement."""

    async def reserve(
        self,
        *,
        workspace_id: UUID,
        actor_id: UUID,
        job_key: str,
        estimated_cost: Decimal,
        maximum_cost: Decimal,
        currency: str,
    ) -> BulkBudgetReservation:
        hold, authorization = await self._reserve(
            workspace_id=workspace_id,
            actor_id=actor_id,
            operation_kind="bulk.generate",
            subject_type="bulk_job",
            subject_id=job_key,
            idempotency_key=f"bulk:{job_key}",
            estimated_cost=estimated_cost,
            requested_maximum_cost=maximum_cost,
            currency=currency,
        )
        return BulkBudgetReservation(
            reservation_ref=_reservation_ref(hold.id),
            authorized_amount=authorization.authorized_cost,
            currency=authorization.currency,
            entitlement_snapshot=authorization.entitlement_snapshot,
            budget_policy_snapshot={
                **authorization.policy_snapshot,
                "credit_hold_id": str(hold.id),
                "price_version_id": str(authorization.price_version_id),
                "maximum_credit_amount": str(authorization.maximum_credit_amount),
            },
        )

    async def finalize(
        self,
        *,
        workspace_id: UUID,
        actor_id: UUID,
        reservation_ref: str,
        actual_cost: Decimal,
        currency: str,
        terminal_event_id: str,
    ) -> CreditHold:
        return await self._finalize(
            workspace_id=workspace_id,
            actor_id=actor_id,
            reservation_ref=reservation_ref,
            actual_cost=actual_cost,
            currency=currency,
            completion_event_id=terminal_event_id,
            subject_type="bulk_job",
        )

    async def release(
        self,
        *,
        workspace_id: UUID,
        actor_id: UUID,
        reservation_ref: str,
        actual_cost: Decimal,
        currency: str,
        terminal_event_id: str,
        failure_class: str,
    ) -> CreditHold:
        return await self._release(
            workspace_id=workspace_id,
            actor_id=actor_id,
            reservation_ref=reservation_ref,
            actual_cost=actual_cost,
            currency=currency,
            terminal_event_id=terminal_event_id,
            failure_class=failure_class,
            reason_code=f"BULK_TERMINAL_{failure_class}",
            subject_type="bulk_job",
        )


class BillingGenerationBudgetAdapter(_BillingJobBudgetAdapter):
    """Maximum-cost hold and terminal usage settlement for generation jobs."""

    async def authorize(
        self,
        *,
        workspace_id: UUID,
        actor_id: UUID,
        operation: str,
        input_snapshot_hash: str,
        model_snapshot: Mapping[str, Any],
        requested_limits: Mapping[str, Any],
        idempotency_key: str,
    ) -> GenerationBudgetAuthorization:
        # Imported only at the boundary invocation so billing model imports never
        # participate in generation's module initialization graph.
        from blogops.domain.generation.providers import (
            BudgetAuthorization as GenerationBudgetAuthorization,
        )

        estimated_cost = _requested_positive_decimal(
            requested_limits.get("estimated_cost"),
            path="requested_limits.estimated_cost",
        )
        maximum_cost = _requested_positive_decimal(
            requested_limits.get("maximum_cost"),
            path="requested_limits.maximum_cost",
        )
        currency_value = requested_limits.get("currency")
        if not isinstance(currency_value, str):
            raise AppError(
                "BILLING_COST_CURRENCY_INVALID",
                "비용 통화는 대문자 ISO 4217 코드여야 합니다.",
                422,
                fields=[{"path": "requested_limits.currency", "reason": "string required"}],
            )
        estimate_breakdown = _requested_mapping(
            requested_limits.get("estimate_breakdown", {}),
            path="requested_limits.estimate_breakdown",
        )
        hold, authorization = await self._reserve(
            workspace_id=workspace_id,
            actor_id=actor_id,
            operation_kind=f"generation.{operation.casefold()}",
            subject_type="generation_job",
            subject_id=f"{actor_id}:{input_snapshot_hash}",
            idempotency_key=_budget_idempotency_key(
                "generation",
                actor_id,
                operation,
                idempotency_key,
            ),
            estimated_cost=estimated_cost,
            requested_maximum_cost=maximum_cost,
            currency=currency_value,
        )
        return GenerationBudgetAuthorization(
            reservation_ref=_reservation_ref(hold.id),
            estimated_cost=estimated_cost,
            currency=authorization.currency,
            estimate_breakdown=estimate_breakdown,
            entitlement_snapshot=authorization.entitlement_snapshot,
            budget_snapshot={
                **authorization.policy_snapshot,
                "credit_hold_id": str(hold.id),
                "price_version_id": str(authorization.price_version_id),
                "maximum_credit_amount": str(authorization.maximum_credit_amount),
                "maximum_cost": str(authorization.authorized_cost),
                "model_snapshot": dict(model_snapshot),
            },
        )

    async def settle(
        self,
        *,
        workspace_id: UUID,
        actor_id: UUID,
        reservation_ref: str,
        settlement: GenerationUsageSettlement,
        terminal_event_id: str,
    ) -> None:
        await self._finalize(
            workspace_id=workspace_id,
            actor_id=actor_id,
            reservation_ref=reservation_ref,
            actual_cost=settlement.actual_cost,
            currency=settlement.currency,
            completion_event_id=terminal_event_id,
            subject_type="generation_job",
            usage_metadata=settlement.usage,
        )

    async def release(
        self,
        *,
        workspace_id: UUID,
        actor_id: UUID,
        reservation_ref: str,
        actual_cost: Decimal,
        currency: str,
        reason: str,
        terminal_event_id: str,
        failure_class: str,
    ) -> None:
        await self._release(
            workspace_id=workspace_id,
            actor_id=actor_id,
            reservation_ref=reservation_ref,
            actual_cost=actual_cost,
            currency=currency,
            terminal_event_id=terminal_event_id,
            failure_class=failure_class,
            reason_code=reason,
            subject_type="generation_job",
        )


class BillingRepurposeBudgetAdapter(_BillingJobBudgetAdapter):
    """Maximum-cost hold and terminal settlement for repurposing jobs."""

    async def reserve(
        self,
        *,
        workspace_id: UUID,
        actor_id: UUID,
        amount: Decimal,
        currency: str,
        request_hash: str,
    ) -> RepurposeBudgetReservation:
        # Keep the cross-domain value type out of billing's import graph.
        from blogops.domain.repurpose.providers import BudgetReservation

        hold, authorization = await self._reserve(
            workspace_id=workspace_id,
            actor_id=actor_id,
            operation_kind="repurpose.generate",
            subject_type="repurpose_job",
            subject_id=request_hash,
            idempotency_key=_budget_idempotency_key("repurpose", actor_id, request_hash),
            estimated_cost=amount,
            requested_maximum_cost=amount,
            currency=currency,
        )
        return BudgetReservation(
            reference=_reservation_ref(hold.id),
            reserved_amount=authorization.authorized_cost,
            currency=authorization.currency,
        )

    async def finalize(
        self,
        *,
        workspace_id: UUID,
        actor_id: UUID,
        reservation_ref: str,
        actual_cost: Decimal,
        currency: str,
        terminal_event_id: str,
    ) -> None:
        await self._finalize(
            workspace_id=workspace_id,
            actor_id=actor_id,
            reservation_ref=reservation_ref,
            actual_cost=actual_cost,
            currency=currency,
            completion_event_id=terminal_event_id,
            subject_type="repurpose_job",
        )

    async def release(
        self,
        *,
        workspace_id: UUID,
        actor_id: UUID,
        reservation_ref: str,
        actual_cost: Decimal,
        currency: str,
        terminal_event_id: str,
        failure_class: str,
        reason_code: str | None = None,
    ) -> None:
        await self._release(
            workspace_id=workspace_id,
            actor_id=actor_id,
            reservation_ref=reservation_ref,
            actual_cost=actual_cost,
            currency=currency,
            terminal_event_id=terminal_event_id,
            failure_class=failure_class,
            reason_code=reason_code or f"REPURPOSE_TERMINAL_{failure_class}",
            subject_type="repurpose_job",
        )


def create_budget_authorization_resolver(
    session: AsyncSession,
) -> DatabaseBudgetAuthorizationResolver:
    """Build the production resolver bound to an API or worker transaction."""

    return DatabaseBudgetAuthorizationResolver(session)


def create_publishing_entitlement_resolver(
    session: AsyncSession,
) -> DatabasePublishingEntitlementResolver:
    """Build the publishing entitlement resolver in the caller's transaction."""

    return DatabasePublishingEntitlementResolver(session)


def create_media_budget_gate(session: AsyncSession) -> BillingMediaBudgetAdapter:
    """FastAPI dependency and Media worker factory using the same transaction."""

    return BillingMediaBudgetAdapter(
        BillingService(session),
        create_budget_authorization_resolver(session),
    )


def create_bulk_budget_gate(session: AsyncSession) -> BillingBulkBudgetAdapter:
    """FastAPI dependency and Bulk worker factory using the same transaction."""

    return BillingBulkBudgetAdapter(
        BillingService(session),
        create_budget_authorization_resolver(session),
    )


def create_generation_budget_gateway(
    session: AsyncSession,
) -> BillingGenerationBudgetAdapter:
    """Bind generation authorization and settlement to the current transaction."""

    return BillingGenerationBudgetAdapter(
        BillingService(session),
        create_budget_authorization_resolver(session),
    )


def create_repurpose_budget_gateway(
    session: AsyncSession,
) -> BillingRepurposeBudgetAdapter:
    """Bind repurpose reservation and settlement to the current transaction."""

    return BillingRepurposeBudgetAdapter(
        BillingService(session),
        create_budget_authorization_resolver(session),
    )
