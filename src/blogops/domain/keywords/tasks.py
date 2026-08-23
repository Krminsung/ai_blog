"""Celery entry points for durable keyword research jobs."""

import asyncio
from uuid import UUID

from celery import shared_task

from blogops.core.errors import AppError
from blogops.db.session import apply_workspace_scope, get_database
from blogops.domain.keywords.enums import ResearchJobState
from blogops.domain.keywords.services import evaluate_due_alerts, process_research_job
from blogops.domain.knowledge.storage import get_object_storage


async def _run_job(workspace_id: UUID, job_id: UUID) -> tuple[str, int | None]:
    database = get_database()
    try:
        try:
            raw_store = get_object_storage()
        except AppError:
            # Metric lineage still retains request/response hashes. The production compose
            # config supplies storage; absence must not make CSV-only jobs unusable.
            raw_store = None
        async with database.session_factory() as session:
            async with session.begin():
                await apply_workspace_scope(session, workspace_id)
                job = await process_research_job(
                    session,
                    workspace_id=workspace_id,
                    job_id=job_id,
                    raw_store=raw_store,
                )
                return job.state, job.retry_after_seconds
    finally:
        await database.close()
        get_database.cache_clear()


@shared_task(bind=True, name="keyword.process", max_retries=3)
def process_keyword_job_task(task, workspace_id: str, job_id: str) -> str:
    state, retry_after = asyncio.run(_run_job(UUID(workspace_id), UUID(job_id)))
    if state == ResearchJobState.RETRYABLE_FAILED.value:
        raise task.retry(countdown=retry_after or min(3_600, 5 * (2 ** task.request.retries)))
    return state


def enqueue_keyword_job(workspace_id: UUID, job_id: UUID) -> None:
    # Give the request transaction time to commit before the worker resolves the durable row.
    process_keyword_job_task.apply_async(args=(str(workspace_id), str(job_id)), countdown=1)


async def _run_alerts(workspace_id: UUID) -> int:
    database = get_database()
    try:
        async with database.session_factory() as session:
            async with session.begin():
                await apply_workspace_scope(session, workspace_id)
                return await evaluate_due_alerts(session, workspace_id=workspace_id)
    finally:
        await database.close()
        get_database.cache_clear()


@shared_task(name="keyword.alerts.evaluate")
def evaluate_keyword_alerts_task(workspace_id: str) -> int:
    return asyncio.run(_run_alerts(UUID(workspace_id)))
