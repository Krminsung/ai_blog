"""Pure billing invariants shared by HTTP handlers and job workers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from blogops.core.errors import AppError
from blogops.domain.billing.enums import CreditHoldState, LedgerDirection, OveragePolicy


def canonical_hash(value: dict[str, Any]) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def require_positive_amount(value: Decimal, *, field: str = "amount") -> Decimal:
    if not value.is_finite() or value <= 0:
        raise AppError(
            "BILLING_AMOUNT_INVALID",
            "금액과 크레딧 수량은 0보다 큰 유한값이어야 합니다.",
            422,
            fields=[{"path": field, "reason": "positive finite value required"}],
        )
    return value


def require_nonnegative_amount(value: Decimal, *, field: str = "amount") -> Decimal:
    if not value.is_finite() or value < 0:
        raise AppError(
            "BILLING_AMOUNT_INVALID",
            "금액과 크레딧 수량은 음수가 아닌 유한값이어야 합니다.",
            422,
            fields=[{"path": field, "reason": "non-negative finite value required"}],
        )
    return value


def signed_amount(direction: LedgerDirection | str, amount: Decimal) -> Decimal:
    require_positive_amount(amount)
    normalized = LedgerDirection(direction)
    return amount if normalized == LedgerDirection.CREDIT else -amount


@dataclass(frozen=True, slots=True)
class HoldFinalization:
    consumed: Decimal
    released: Decimal
    replay: bool


def finalize_hold_amounts(
    *,
    state: CreditHoldState | str,
    maximum_amount: Decimal,
    actual_amount: Decimal,
    finalized_amount: Decimal | None = None,
) -> HoldFinalization:
    """Validate max-cost hold finalization and make exact replays observable."""

    require_positive_amount(maximum_amount, field="maximum_amount")
    require_nonnegative_amount(actual_amount, field="actual_amount")
    normalized = CreditHoldState(state)
    if normalized == CreditHoldState.FINALIZED:
        if finalized_amount == actual_amount:
            return HoldFinalization(
                consumed=actual_amount,
                released=maximum_amount - actual_amount,
                replay=True,
            )
        raise AppError(
            "CREDIT_HOLD_ALREADY_FINALIZED",
            "이미 확정된 Hold에 다른 실제 비용을 적용할 수 없습니다.",
            409,
        )
    if normalized != CreditHoldState.HELD:
        raise AppError("CREDIT_HOLD_NOT_ACTIVE", "활성 Hold만 확정할 수 있습니다.", 409)
    if actual_amount > maximum_amount:
        raise AppError(
            "CREDIT_HOLD_MAXIMUM_EXCEEDED",
            "실제 비용이 사전에 승인된 최대 Hold를 초과했습니다.",
            409,
            remediation={"action": "request_additional_budget"},
        )
    return HoldFinalization(
        consumed=actual_amount,
        released=maximum_amount - actual_amount,
        replay=False,
    )


def ensure_balance_transition(
    *,
    available_before: Decimal,
    held_before: Decimal,
    available_after: Decimal,
    held_after: Decimal,
) -> None:
    for field, value in (
        ("available_before", available_before),
        ("held_before", held_before),
        ("available_after", available_after),
        ("held_after", held_after),
    ):
        require_nonnegative_amount(value, field=field)


@dataclass(frozen=True, slots=True)
class UsageLimitDecision:
    allowed: bool
    remaining: Decimal
    overage: Decimal
    policy: OveragePolicy


def evaluate_usage_limit(
    *, used: Decimal, requested: Decimal, limit: Decimal, policy: OveragePolicy | str
) -> UsageLimitDecision:
    require_nonnegative_amount(used, field="used")
    require_positive_amount(requested, field="requested")
    require_nonnegative_amount(limit, field="limit")
    normalized = OveragePolicy(policy)
    projected = used + requested
    overage = max(Decimal("0"), projected - limit)
    return UsageLimitDecision(
        allowed=overage == 0 or normalized != OveragePolicy.BLOCK,
        remaining=max(Decimal("0"), limit - projected),
        overage=overage,
        policy=normalized,
    )


def due_usage_thresholds(
    *, used_before: Decimal, used_after: Decimal, limit: Decimal, thresholds: tuple[int, ...]
) -> tuple[int, ...]:
    """Return only newly crossed configured thresholds; no product defaults are invented."""

    require_nonnegative_amount(used_before, field="used_before")
    require_nonnegative_amount(used_after, field="used_after")
    require_positive_amount(limit, field="limit")
    if used_after < used_before:
        raise AppError("USAGE_COUNTER_REGRESSION", "사용량 원본 이벤트는 감소할 수 없습니다.", 409)
    if not thresholds or any(value <= 0 for value in thresholds):
        raise AppError(
            "USAGE_THRESHOLD_CONFIG_MISSING",
            "사용량 임계 알림 정책이 구성되지 않았습니다.",
            503,
        )
    before_percent = used_before * Decimal("100") / limit
    after_percent = used_after * Decimal("100") / limit
    return tuple(
        sorted(
            value
            for value in set(thresholds)
            if before_percent < Decimal(value) <= after_percent
        )
    )


def pricing_is_effective(
    *, state: str, effective_at: datetime, retired_at: datetime | None, at: datetime
) -> bool:
    if (
        at.tzinfo is None
        or effective_at.tzinfo is None
        or (retired_at is not None and retired_at.tzinfo is None)
    ):
        raise AppError("PRICING_TIME_INVALID", "가격표 시각은 timezone-aware여야 합니다.", 500)
    return state == "ACTIVE" and effective_at <= at and (retired_at is None or at < retired_at)


def validate_reversal(
    *, original_entry_id: object | None, reversal_of_entry_id: object | None, amount: Decimal
) -> None:
    require_positive_amount(amount)
    if original_entry_id is not None or reversal_of_entry_id is None:
        raise AppError(
            "LEDGER_REVERSAL_INVALID",
            "역분개는 원본을 직접 수정하지 않고 정확히 한 원장 항목을 참조해야 합니다.",
            422,
        )
