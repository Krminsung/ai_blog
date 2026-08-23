"""Fail-closed model, entitlement and budget boundaries.

No production fallback provider is installed here. The composition root must explicitly register
approved adapters and the billing/entitlement implementation before paid generation can run.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Mapping, Protocol
from uuid import UUID

from blogops.core.errors import AppError


@dataclass(frozen=True, slots=True)
class ModelRequest:
    workspace_id: UUID
    job_id: UUID
    step_id: UUID
    provider: str
    model: str
    model_version: str
    region: str
    prompt: Mapping[str, Any]
    context: Mapping[str, Any]
    output_schema: Mapping[str, Any]
    parameters: Mapping[str, Any]
    allowed_tools: tuple[str, ...]
    request_hash: str


@dataclass(frozen=True, slots=True)
class ModelResponse:
    structured_output: Mapping[str, Any]
    response_hash: str
    response_object_ref: str | None
    metadata: Mapping[str, Any]
    input_tokens: int
    output_tokens: int
    tool_usage: tuple[Mapping[str, Any], ...]
    started_at: datetime
    completed_at: datetime


@dataclass(frozen=True, slots=True)
class BudgetAuthorization:
    reservation_ref: str
    estimated_cost: Decimal
    currency: str
    estimate_breakdown: Mapping[str, Any]
    entitlement_snapshot: Mapping[str, Any]
    budget_snapshot: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class UsageSettlement:
    actual_cost: Decimal
    currency: str
    usage: Mapping[str, Any]


class ModelGateway(Protocol):
    key: str

    async def generate(self, request: ModelRequest) -> ModelResponse: ...

    async def cancel(self, *, workspace_id: UUID, provider_run_ref: str) -> None: ...


class BudgetEntitlementGateway(Protocol):
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
    ) -> BudgetAuthorization: ...

    async def settle(
        self,
        *,
        workspace_id: UUID,
        reservation_ref: str,
        settlement: UsageSettlement,
        idempotency_key: str,
    ) -> None: ...

    async def release(
        self,
        *,
        workspace_id: UUID,
        reservation_ref: str,
        reason: str,
        idempotency_key: str,
    ) -> None: ...


class ModelGatewayRegistry:
    def __init__(self, adapters: Mapping[str, ModelGateway] | None = None) -> None:
        self._adapters = dict(adapters or {})

    def resolve(self, key: str, *, allowed_keys: tuple[str, ...]) -> ModelGateway:
        if key not in allowed_keys:
            raise AppError(
                code="MODEL_PROVIDER_NOT_ALLOWED",
                message="고정된 모델 정책이 이 공급자를 허용하지 않습니다.",
                status_code=422,
                fields=[{"path": "provider", "reason": key}],
            )
        adapter = self._adapters.get(key)
        if adapter is None:
            raise _unavailable("MODEL_PROVIDER_UNAVAILABLE", key)
        return adapter


class FailClosedModelGateway:
    key = "unconfigured"

    async def generate(self, request: ModelRequest) -> ModelResponse:
        raise _unavailable("MODEL_PROVIDER_UNAVAILABLE", request.provider)

    async def cancel(self, *, workspace_id: UUID, provider_run_ref: str) -> None:
        del workspace_id, provider_run_ref
        raise _unavailable("MODEL_PROVIDER_UNAVAILABLE", self.key)


class FailClosedBudgetEntitlementGateway:
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
    ) -> BudgetAuthorization:
        del (
            workspace_id,
            actor_id,
            operation,
            input_snapshot_hash,
            model_snapshot,
            requested_limits,
            idempotency_key,
        )
        raise _unavailable("BUDGET_ENTITLEMENT_UNAVAILABLE", "billing-boundary")

    async def settle(
        self,
        *,
        workspace_id: UUID,
        reservation_ref: str,
        settlement: UsageSettlement,
        idempotency_key: str,
    ) -> None:
        del workspace_id, reservation_ref, settlement, idempotency_key
        raise _unavailable("BUDGET_ENTITLEMENT_UNAVAILABLE", "billing-boundary")

    async def release(
        self,
        *,
        workspace_id: UUID,
        reservation_ref: str,
        reason: str,
        idempotency_key: str,
    ) -> None:
        del workspace_id, reservation_ref, reason, idempotency_key
        raise _unavailable("BUDGET_ENTITLEMENT_UNAVAILABLE", "billing-boundary")


def explicit_failover_keys(
    current_key: str,
    *,
    policy_snapshot: Mapping[str, Any],
) -> tuple[str, ...]:
    """Return only administrator-configured fallbacks, in their persisted order."""

    routes = policy_snapshot.get("provider_failover", {})
    configured = routes.get(current_key, [])
    if not isinstance(configured, list):
        raise AppError(
            code="MODEL_POLICY_INVALID",
            message="공급자 대체 정책 형식이 올바르지 않습니다.",
            status_code=500,
        )
    denied = frozenset(policy_snapshot.get("denied_providers", []))
    return tuple(str(key) for key in configured if str(key) not in denied)


def validate_required_output_fields(
    output: Mapping[str, Any], output_schema: Mapping[str, Any]
) -> None:
    missing = [key for key in output_schema.get("required", []) if key not in output]
    if missing:
        raise AppError(
            code="MODEL_OUTPUT_SCHEMA_INVALID",
            message="모델의 구조화 결과가 고정된 출력 스키마와 다릅니다.",
            status_code=502,
            fields=[{"path": str(key), "reason": "required"} for key in missing],
        )


def _unavailable(code: str, key: str) -> AppError:
    return AppError(
        code=code,
        message="승인된 외부 어댑터가 구성되지 않아 안전하게 중단했습니다.",
        status_code=503,
        fields=[{"path": "adapter", "reason": key}],
    )
