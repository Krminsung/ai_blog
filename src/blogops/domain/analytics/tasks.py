"""Celery consumers for analytics sync and report jobs."""

from __future__ import annotations

import asyncio
from typing import Protocol
from uuid import UUID

from celery import shared_task
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.core.errors import AppError
from blogops.db.session import apply_workspace_scope, get_database
from blogops.domain.analytics.models import AnalyticsSyncInputSnapshot
from blogops.domain.analytics.providers import (
    AnalyticsAdapterRegistry,
    AnalyticsFetchRequest,
    CredentialResolver,
)
from blogops.domain.analytics.repository import AnalyticsRepository
from blogops.domain.analytics.service import AnalyticsService
from blogops.domain.jobs.state import JobState


class AnalyticsSyncExecutor(Protocol):
    async def execute(
        self, session: AsyncSession, *, workspace_id: UUID, run_id: UUID
    ) -> None: ...


class AnalyticsReportExecutor(Protocol):
    async def execute(
        self, session: AsyncSession, *, workspace_id: UUID, run_id: UUID
    ) -> None: ...


class OfficialAnalyticsSyncExecutor:
    def __init__(
        self,
        registry: AnalyticsAdapterRegistry,
        credential_resolver: CredentialResolver,
    ) -> None:
        self.registry = registry
        self.credential_resolver = credential_resolver

    async def execute(
        self, session: AsyncSession, *, workspace_id: UUID, run_id: UUID
    ) -> None:
        repo = AnalyticsRepository(session, workspace_id)
        run = await repo.sync_run(run_id, lock=True)
        snapshot = await session.scalar(
            select(AnalyticsSyncInputSnapshot).where(
                AnalyticsSyncInputSnapshot.workspace_id == workspace_id,
                AnalyticsSyncInputSnapshot.id == run.input_snapshot_id,
            )
        )
        if snapshot is None:
            raise AppError(
                "ANALYTICS_SNAPSHOT_NOT_FOUND", "분석 입력 스냅샷이 없습니다.", 404
            )
        adapter = self.registry.require(snapshot.provider)
        expected_contract = str(snapshot.connection_snapshot["official_contract"])
        if adapter.official_contract != expected_contract:
            raise AppError(
                "ANALYTICS_OFFICIAL_CONTRACT_MISMATCH",
                "공식 분석 어댑터 계약이 연결 스냅샷과 다릅니다.",
                422,
            )
        credentials = await self.credential_resolver.resolve(
            str(snapshot.connection_snapshot["credential_secret_ref"])
        )
        request = AnalyticsFetchRequest(
            property_id=str(snapshot.connection_snapshot["external_property_id"]),
            date_from=snapshot.date_from,
            date_to=snapshot.date_to,
            metric_fields=tuple(
                str(item["source_field"])
                for item in snapshot.metric_definition_snapshots
            ),
            dimensions=tuple(snapshot.dimensions),
            request_metadata={
                "sync_run_id": str(run.id),
                "request_hash": snapshot.request_hash,
                "official_contract": expected_contract,
            },
        )
        result = await adapter.fetch(request, credentials=credentials)
        await AnalyticsService(session).record_provider_result(
            workspace_id=workspace_id,
            run_id=run.id,
            result=result,
        )


_sync_executor: AnalyticsSyncExecutor | None = None
_report_executor: AnalyticsReportExecutor | None = None


def configure_analytics_sync_executor(executor: AnalyticsSyncExecutor) -> None:
    global _sync_executor
    _sync_executor = executor


def configure_analytics_report_executor(executor: AnalyticsReportExecutor) -> None:
    global _report_executor
    _report_executor = executor


async def _run_sync(workspace_id: UUID, run_id: UUID) -> str:
    database = get_database()
    try:
        async with database.session_factory() as session:
            async with session.begin():
                await apply_workspace_scope(session, workspace_id)
                service = AnalyticsService(session)
                row = await service.finalize_sync_cancellation(
                    workspace_id=workspace_id, run_id=run_id
                )
                if row.state in _COMPLETED_ATTEMPT_STATES:
                    return row.state
                if _sync_executor is None:
                    row = await service.fail_sync_runtime(
                        workspace_id=workspace_id,
                        run_id=run_id,
                        code="ANALYTICS_RUNTIME_UNAVAILABLE",
                        detail="official analytics adapter runtime is not configured",
                        retryable=True,
                    )
                    return row.state
                try:
                    await _sync_executor.execute(
                        session, workspace_id=workspace_id, run_id=run_id
                    )
                except AppError as exc:
                    row = await service.fail_sync_runtime(
                        workspace_id=workspace_id,
                        run_id=run_id,
                        code=exc.code,
                        detail=exc.message,
                        retryable=_is_retryable_runtime_error(exc),
                    )
                    return row.state
                except Exception as exc:
                    row = await service.fail_sync_runtime(
                        workspace_id=workspace_id,
                        run_id=run_id,
                        code="ANALYTICS_PROVIDER_FAILED",
                        detail=type(exc).__name__,
                        retryable=True,
                    )
                    return row.state
                row = await service.finalize_sync_cancellation(
                    workspace_id=workspace_id, run_id=run_id
                )
                return row.state
    finally:
        await database.close()
        get_database.cache_clear()


async def _run_report(workspace_id: UUID, run_id: UUID) -> str:
    database = get_database()
    try:
        async with database.session_factory() as session:
            async with session.begin():
                await apply_workspace_scope(session, workspace_id)
                service = AnalyticsService(session)
                row = await service.finalize_report_cancellation(
                    workspace_id=workspace_id, run_id=run_id
                )
                if row.state in _COMPLETED_ATTEMPT_STATES:
                    return row.state
                if _report_executor is None:
                    row = await service.fail_report_runtime(
                        workspace_id=workspace_id,
                        run_id=run_id,
                        code="ANALYTICS_REPORT_RUNTIME_UNAVAILABLE",
                        detail="analytics report executor is not configured",
                        retryable=True,
                    )
                    return row.state
                try:
                    await _report_executor.execute(
                        session, workspace_id=workspace_id, run_id=run_id
                    )
                except AppError as exc:
                    row = await service.fail_report_runtime(
                        workspace_id=workspace_id,
                        run_id=run_id,
                        code=exc.code,
                        detail=exc.message,
                        retryable=_is_retryable_runtime_error(exc),
                    )
                    return row.state
                except Exception as exc:
                    row = await service.fail_report_runtime(
                        workspace_id=workspace_id,
                        run_id=run_id,
                        code="ANALYTICS_REPORT_FAILED",
                        detail=type(exc).__name__,
                        retryable=True,
                    )
                    return row.state
                row = await service.finalize_report_cancellation(
                    workspace_id=workspace_id, run_id=run_id
                )
                return row.state
    finally:
        await database.close()
        get_database.cache_clear()


@shared_task(name="analytics.sync.process")
def process_analytics_sync_task(workspace_id: str, run_id: str) -> str:
    return asyncio.run(_run_sync(UUID(workspace_id), UUID(run_id)))


@shared_task(name="analytics.report.process")
def process_analytics_report_task(workspace_id: str, run_id: str) -> str:
    return asyncio.run(_run_report(UUID(workspace_id), UUID(run_id)))


def enqueue_analytics_sync(workspace_id: UUID, run_id: UUID) -> None:
    process_analytics_sync_task.apply_async(args=(str(workspace_id), str(run_id)), countdown=1)


def enqueue_analytics_report(workspace_id: UUID, run_id: UUID) -> None:
    process_analytics_report_task.apply_async(args=(str(workspace_id), str(run_id)), countdown=1)


_COMPLETED_ATTEMPT_STATES = {
    JobState.SUCCEEDED.value,
    JobState.RETRYABLE_FAILED.value,
    JobState.FINAL_FAILED.value,
    JobState.CANCELLED.value,
}


def _is_retryable_runtime_error(error: Exception) -> bool:
    if not isinstance(error, AppError):
        return True
    return error.status_code in {408, 425, 429} or error.status_code >= 500
