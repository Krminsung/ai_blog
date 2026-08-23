"""Celery consumer and enqueue boundary for durable generation jobs."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Callable, Protocol
from uuid import UUID

from celery import shared_task
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.core.errors import AppError
from blogops.db.session import apply_workspace_scope, get_database
from blogops.domain.generation.models import GenerationJob, ModelRun
from blogops.domain.generation.providers import (
    BudgetEntitlementGateway,
    UsageSettlement,
)
from blogops.domain.generation.service import GenerationService
from blogops.domain.generation.snapshots import SQLAlchemyGenerationSnapshotResolver
from blogops.domain.jobs.state import JobState


class GenerationStepExecutor(Protocol):
    async def execute(
        self,
        session: AsyncSession,
        *,
        workspace_id: UUID,
        job_id: UUID,
        step_id: UUID,
        step_kind: str,
    ) -> None: ...


_executor: GenerationStepExecutor | None = None
_budget_gateway_factory: Callable[
    [AsyncSession], BudgetEntitlementGateway
] | None = None

_GENERATION_SUCCESS_STATES = frozenset(
    {JobState.WAITING_REVIEW.value, JobState.SUCCEEDED.value}
)
_GENERATION_FAILURE_STATES = frozenset(
    {
        JobState.FINAL_FAILED.value,
        JobState.CANCELLED.value,
        JobState.EXPIRED.value,
    }
)
_BILLING_SETTLEMENT_RESULT_KEY = "billing_settlement"


def configure_generation_step_executor(executor: GenerationStepExecutor) -> None:
    """Worker composition hook for the approved model/research runtime."""

    global _executor
    _executor = executor


def configure_generation_budget_gateway_factory(
    factory: Callable[[AsyncSession], BudgetEntitlementGateway],
) -> None:
    """Worker composition hook for an explicitly approved billing adapter."""

    global _budget_gateway_factory
    _budget_gateway_factory = factory


def _generation_budget_gateway(session: AsyncSession) -> BudgetEntitlementGateway:
    if _budget_gateway_factory is not None:
        return _budget_gateway_factory(session)
    # Delay the cross-domain import until a worker transaction exists. The database
    # adapter itself fails closed when no exact subscription/pricing policy exists.
    from blogops.domain.billing.adapters import create_generation_budget_gateway

    return create_generation_budget_gateway(session)


async def settle_generation_terminal(
    session: AsyncSession,
    budget: BudgetEntitlementGateway,
    *,
    workspace_id: UUID,
    job_id: UUID,
) -> bool:
    """Settle a generation provider lifecycle exactly once in the current transaction."""

    await apply_workspace_scope(session, workspace_id)
    job = await session.scalar(
        select(GenerationJob)
        .where(
            GenerationJob.workspace_id == workspace_id,
            GenerationJob.id == job_id,
        )
        .with_for_update()
    )
    if job is None:
        raise AppError(
            "GENERATION_JOB_NOT_FOUND",
            "요청한 생성 작업을 찾을 수 없습니다.",
            404,
        )
    if job.state not in _GENERATION_SUCCESS_STATES | _GENERATION_FAILURE_STATES:
        return False

    model_runs = (
        await session.execute(
            select(ModelRun.id, ModelRun.cost, ModelRun.currency)
            .where(
                ModelRun.workspace_id == workspace_id,
                ModelRun.job_id == job.id,
            )
            .order_by(ModelRun.id)
        )
    ).all()
    mismatched_currencies = sorted(
        {str(row.currency) for row in model_runs if row.currency != job.currency}
    )
    if mismatched_currencies:
        raise AppError(
            "GENERATION_ACTUAL_COST_CURRENCY_MISMATCH",
            "모델 실행 비용 통화가 생성 작업의 승인 통화와 일치하지 않습니다.",
            409,
            remediation={
                "job_currency": job.currency,
                "model_run_currencies": mismatched_currencies,
            },
        )
    actual_cost = sum((row.cost for row in model_runs), Decimal("0"))
    if not actual_cost.is_finite() or actual_cost < 0:
        raise AppError(
            "GENERATION_ACTUAL_COST_INVALID",
            "모델 실행 실제 비용 합계가 올바르지 않습니다.",
            409,
        )

    existing_result = dict(job.result or {})
    settlement_evidence = existing_result.get(_BILLING_SETTLEMENT_RESULT_KEY)
    created_settlement = settlement_evidence is None
    if settlement_evidence is not None:
        if not isinstance(settlement_evidence, dict) or (
            settlement_evidence.get("actual_cost") != str(actual_cost)
            or settlement_evidence.get("currency") != job.currency
            or settlement_evidence.get("model_run_count") != len(model_runs)
        ):
            raise AppError(
                "GENERATION_BILLING_SETTLEMENT_CONFLICT",
                "저장된 생성 비용 정산 증거가 실제 모델 실행 비용과 다릅니다.",
                409,
            )
        terminal_event_id = settlement_evidence.get("terminal_event_id")
        failure_class = settlement_evidence.get("failure_class")
        reason_code = settlement_evidence.get("reason_code")
        settlement_state = settlement_evidence.get("terminal_state")
        if (
            not isinstance(terminal_event_id, str)
            or not terminal_event_id.startswith(f"generation:{job.id}:")
            or (failure_class is not None and not isinstance(failure_class, str))
            or (failure_class is not None and not isinstance(reason_code, str))
            or not isinstance(settlement_state, str)
        ):
            raise AppError(
                "GENERATION_BILLING_SETTLEMENT_CONFLICT",
                "저장된 생성 비용 정산 이벤트 형식이 올바르지 않습니다.",
                409,
            )
    else:
        success = job.state in _GENERATION_SUCCESS_STATES
        failure_class = None if success else job.state
        reason_code = (
            None
            if success
            else job.error_code or f"GENERATION_{job.state}"
        )
        settlement_state = job.state
        terminal_event_id = (
            f"generation:{job.id}:success"
            if success
            else f"generation:{job.id}:{job.state.casefold()}"
        )

    if failure_class is None:
        await budget.settle(
            workspace_id=workspace_id,
            actor_id=job.requested_by,
            reservation_ref=job.budget_reservation_ref,
            settlement=UsageSettlement(
                actual_cost=actual_cost,
                currency=job.currency,
                usage={
                    "source": "sum(model_runs.cost)",
                    "model_run_count": len(model_runs),
                    "terminal_state": settlement_state,
                },
            ),
            terminal_event_id=terminal_event_id,
        )
    else:
        await budget.release(
            workspace_id=workspace_id,
            actor_id=job.requested_by,
            reservation_ref=job.budget_reservation_ref,
            actual_cost=actual_cost,
            currency=job.currency,
            reason=reason_code,
            terminal_event_id=terminal_event_id,
            failure_class=failure_class,
        )
    job.actual_cost = actual_cost
    if settlement_evidence is None:
        job.result = {
            **existing_result,
            _BILLING_SETTLEMENT_RESULT_KEY: {
                "actual_cost": str(actual_cost),
                "currency": job.currency,
                "terminal_event_id": terminal_event_id,
                "terminal_state": settlement_state,
                "failure_class": failure_class,
                "reason_code": reason_code,
                "actual_cost_source": "sum(model_runs.cost)",
                "model_run_count": len(model_runs),
            },
        }
    await session.flush()
    return created_settlement


async def _run_job(workspace_id: UUID, job_id: UUID) -> tuple[str, bool]:
    database = get_database()
    try:
        async with database.session_factory() as session:
            async with session.begin():
                await apply_workspace_scope(session, workspace_id)
                service = GenerationService(
                    session,
                    snapshots=SQLAlchemyGenerationSnapshotResolver(session),
                    budget=_generation_budget_gateway(session),
                )
                step = await service.claim_next_step(
                    workspace_id=workspace_id,
                    job_id=job_id,
                )
                if step is None:
                    job = await service._job(workspace_id, job_id)
                    await settle_generation_terminal(
                        session,
                        service.budget,
                        workspace_id=workspace_id,
                        job_id=job_id,
                    )
                    return job.state, False
                if step.step_kind == "VALIDATE_INPUT":
                    await service.complete_step(
                        workspace_id=workspace_id,
                        job_id=job_id,
                        step_id=step.id,
                        result={"validated": True},
                        output_ref=None,
                    )
                    job = await service._job(workspace_id, job_id)
                    await settle_generation_terminal(
                        session,
                        service.budget,
                        workspace_id=workspace_id,
                        job_id=job_id,
                    )
                    return job.state, True
                if _executor is None:
                    job = await service.fail_step(
                        workspace_id=workspace_id,
                        job_id=job_id,
                        step_id=step.id,
                        error_code="GENERATION_RUNTIME_UNAVAILABLE",
                        error_detail="approved model/research executor is not configured",
                        retryable=False,
                    )
                    await settle_generation_terminal(
                        session,
                        service.budget,
                        workspace_id=workspace_id,
                        job_id=job_id,
                    )
                    return job.state, False
                await _executor.execute(
                    session,
                    workspace_id=workspace_id,
                    job_id=job_id,
                    step_id=step.id,
                    step_kind=step.step_kind,
                )
                job = await service._job(workspace_id, job_id)
                await settle_generation_terminal(
                    session,
                    service.budget,
                    workspace_id=workspace_id,
                    job_id=job_id,
                )
                has_more = job.state not in {
                    JobState.RETRYABLE_FAILED.value,
                    JobState.FINAL_FAILED.value,
                    JobState.CANCELLED.value,
                    JobState.WAITING_REVIEW.value,
                }
                return job.state, has_more
    finally:
        await database.close()
        get_database.cache_clear()


@shared_task(name="generation.process")
def process_generation_job_task(workspace_id: str, job_id: str) -> str:
    state, has_more = asyncio.run(_run_job(UUID(workspace_id), UUID(job_id)))
    if has_more:
        process_generation_job_task.apply_async(args=(workspace_id, job_id), countdown=1)
    return state


def enqueue_generation_job(workspace_id: UUID, job_id: UUID) -> None:
    # The API schedules only this broker handoff after its database transaction exits.
    process_generation_job_task.apply_async(
        args=(str(workspace_id), str(job_id)),
        countdown=1,
    )
