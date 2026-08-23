"""Pure mapping, capacity, retry, budget and signed-callback rules."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Mapping, Sequence

from blogops.core.errors import AppError


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def validate_row_capacity(row_count: int, entitled_limit: int | None) -> None:
    """Apply a versioned entitlement, not a product-wide hard-coded row ceiling."""

    if row_count < 1:
        raise AppError("BULK_INPUT_EMPTY", "대량 입력에는 하나 이상의 행이 필요합니다.", 422)
    if entitled_limit is not None and row_count > entitled_limit:
        raise AppError(
            "BULK_ROW_ENTITLEMENT_EXCEEDED",
            "입력 행 수가 고정된 플랜 한도를 초과했습니다.",
            402,
            remediation={"row_count": row_count, "entitled_limit": entitled_limit},
        )


def validate_mapping(
    *,
    available_columns: Sequence[str],
    column_mapping: Mapping[str, str],
    required_variables: Sequence[str],
) -> tuple[str, ...]:
    available = set(available_columns)
    missing_columns = sorted(set(column_mapping).difference(available))
    mapped_variables = set(column_mapping.values())
    missing_variables = sorted(set(required_variables).difference(mapped_variables))
    problems = tuple(
        [f"unknown_column:{value}" for value in missing_columns]
        + [f"missing_variable:{value}" for value in missing_variables]
    )
    return problems


@dataclass(frozen=True, slots=True)
class BudgetDecision:
    allowed: bool
    kill_switch: bool
    projected_total: Decimal
    remaining: Decimal
    reason: str | None


@dataclass(frozen=True, slots=True)
class SpamGateDecision:
    auto_publish_allowed: bool
    reasons: tuple[str, ...]


def evaluate_spam_gate(
    *,
    similarity_score: Decimal | None,
    value_score: Decimal | None,
    maximum_similarity: Decimal,
    minimum_value: Decimal,
) -> SpamGateDecision:
    """Apply versioned campaign thresholds; absent measurements fail closed."""

    if not Decimal("0") <= maximum_similarity <= Decimal("1"):
        raise AppError("BULK_SPAM_POLICY_INVALID", "유사도 정책 범위가 올바르지 않습니다.", 422)
    if not Decimal("0") <= minimum_value <= Decimal("100"):
        raise AppError("BULK_SPAM_POLICY_INVALID", "가치 점수 정책 범위가 올바르지 않습니다.", 422)
    reasons: list[str] = []
    if similarity_score is None:
        reasons.append("similarity_not_measured")
    elif similarity_score >= maximum_similarity:
        reasons.append("similarity_threshold_exceeded")
    if value_score is None:
        reasons.append("value_not_measured")
    elif value_score < minimum_value:
        reasons.append("value_threshold_not_met")
    return SpamGateDecision(auto_publish_allowed=not reasons, reasons=tuple(reasons))


def input_snapshot_changed(previous_hash: str | None, current_hash: str) -> bool:
    if len(current_hash) != 64:
        raise AppError("BULK_INPUT_HASH_INVALID", "입력 Snapshot Hash가 올바르지 않습니다.", 422)
    return previous_hash != current_hash


def evaluate_budget_boundary(
    *,
    finalized_cost: Decimal,
    held_cost: Decimal,
    next_estimated_cost: Decimal,
    maximum_cost: Decimal,
) -> BudgetDecision:
    values = (finalized_cost, held_cost, next_estimated_cost, maximum_cost)
    if any(value < 0 for value in values):
        raise AppError("BULK_BUDGET_INVALID", "예산 금액은 음수일 수 없습니다.", 422)
    projected = finalized_cost + held_cost + next_estimated_cost
    remaining = max(Decimal("0"), maximum_cost - finalized_cost - held_cost)
    exceeds = projected > maximum_cost
    return BudgetDecision(
        allowed=not exceeds,
        kill_switch=exceeds,
        projected_total=projected,
        remaining=remaining,
        reason="maximum_cost_exceeded" if exceeds else None,
    )


def can_retry(*, attempt: int, max_attempts: int, retryable_error: bool) -> bool:
    if attempt < 0 or max_attempts < 1:
        raise AppError("BULK_RETRY_POLICY_INVALID", "재시도 정책 값이 올바르지 않습니다.", 422)
    return retryable_error and attempt < max_attempts


def callback_signature(secret: bytes, *, timestamp: int, payload: bytes) -> str:
    message = str(timestamp).encode("ascii") + b"." + payload
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def verify_callback_signature(
    secret: bytes,
    *,
    timestamp: int,
    payload: bytes,
    supplied_signature: str,
    now: datetime | None = None,
    tolerance_seconds: int,
) -> None:
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        raise AppError("CALLBACK_TIMEZONE_REQUIRED", "검증 시각에는 시간대가 필요합니다.", 422)
    if tolerance_seconds < 1:
        raise AppError("CALLBACK_POLICY_INVALID", "Callback 허용 시간이 올바르지 않습니다.", 500)
    if abs(int(current.timestamp()) - timestamp) > tolerance_seconds:
        raise AppError("CALLBACK_SIGNATURE_EXPIRED", "Callback 서명이 만료되었습니다.", 401)
    expected = callback_signature(secret, timestamp=timestamp, payload=payload)
    if not hmac.compare_digest(expected, supplied_signature.casefold()):
        raise AppError("CALLBACK_SIGNATURE_INVALID", "Callback 서명이 올바르지 않습니다.", 401)
