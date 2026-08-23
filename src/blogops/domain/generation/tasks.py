"""Celery consumer and enqueue boundary for durable generation jobs."""

from __future__ import annotations

import asyncio
from typing import Protocol
from uuid import UUID

from celery import shared_task
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.db.session import apply_workspace_scope, get_database
from blogops.domain.generation.providers import FailClosedBudgetEntitlementGateway
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


def configure_generation_step_executor(executor: GenerationStepExecutor) -> None:
    """Worker composition hook for the approved model/research runtime."""

    global _executor
    _executor = executor


async def _run_job(workspace_id: UUID, job_id: UUID) -> tuple[str, bool]:
    database = get_database()
    try:
        async with database.session_factory() as session:
            async with session.begin():
                await apply_workspace_scope(session, workspace_id)
                service = GenerationService(
                    session,
                    snapshots=SQLAlchemyGenerationSnapshotResolver(session),
                    budget=FailClosedBudgetEntitlementGateway(),
                )
                step = await service.claim_next_step(
                    workspace_id=workspace_id,
                    job_id=job_id,
                )
                if step is None:
                    job = await service._job(workspace_id, job_id)
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
                    return job.state, False
                await _executor.execute(
                    session,
                    workspace_id=workspace_id,
                    job_id=job_id,
                    step_id=step.id,
                    step_kind=step.step_kind,
                )
                job = await service._job(workspace_id, job_id)
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
