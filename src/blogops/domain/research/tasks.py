"""Celery consumer and enqueue boundary for standalone research runs."""

from __future__ import annotations

import asyncio
from typing import Protocol
from uuid import UUID

from celery import shared_task
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.db.session import apply_workspace_scope, get_database
from blogops.domain.research.service import ResearchService


class ResearchRunExecutor(Protocol):
    async def execute(
        self,
        session: AsyncSession,
        *,
        workspace_id: UUID,
        run_id: UUID,
    ) -> None: ...


_executor: ResearchRunExecutor | None = None


def configure_research_run_executor(executor: ResearchRunExecutor) -> None:
    global _executor
    _executor = executor


async def _run_research(workspace_id: UUID, run_id: UUID) -> str:
    database = get_database()
    try:
        async with database.session_factory() as session:
            async with session.begin():
                await apply_workspace_scope(session, workspace_id)
                service = ResearchService(session)
                if _executor is None:
                    run = await service.fail_run(
                        workspace_id=workspace_id,
                        run_id=run_id,
                        error_code="RESEARCH_RUNTIME_UNAVAILABLE",
                        error_detail="approved search executor is not configured",
                        retryable=False,
                    )
                    return run.state
                await service.mark_researching(workspace_id=workspace_id, run_id=run_id)
                await _executor.execute(
                    session,
                    workspace_id=workspace_id,
                    run_id=run_id,
                )
                run = await service._run(workspace_id, run_id)
                return run.state
    finally:
        await database.close()
        get_database.cache_clear()


@shared_task(name="research.process")
def process_research_run_task(workspace_id: str, run_id: str) -> str:
    return asyncio.run(_run_research(UUID(workspace_id), UUID(run_id)))


def enqueue_research_run(workspace_id: UUID, run_id: UUID) -> None:
    process_research_run_task.apply_async(
        args=(str(workspace_id), str(run_id)),
        countdown=1,
    )
