"""Workspace-scoped analytics repository; every lookup includes tenant identity."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.core.errors import AppError
from blogops.domain.analytics.models import (
    AnalyticsConnection,
    AnalyticsEvidenceBatch,
    AnalyticsExperiment,
    AnalyticsJobCommand,
    AnalyticsMetricDefinition,
    AnalyticsRecommendation,
    AnalyticsReportDefinition,
    AnalyticsReportRun,
    AnalyticsSyncRun,
    ConversionEvent,
    TrackingLink,
)


class AnalyticsRepository:
    def __init__(self, session: AsyncSession, workspace_id: UUID) -> None:
        self.session = session
        self.workspace_id = workspace_id

    async def connection(self, row_id: UUID, *, lock: bool = False) -> AnalyticsConnection:
        return await self._required(AnalyticsConnection, row_id, "ANALYTICS_CONNECTION", lock)

    async def connections(self) -> list[AnalyticsConnection]:
        return list(
            await self.session.scalars(
                select(AnalyticsConnection)
                .where(AnalyticsConnection.workspace_id == self.workspace_id)
                .order_by(AnalyticsConnection.provider, AnalyticsConnection.name)
            )
        )

    async def metric_definition(self, row_id: UUID) -> AnalyticsMetricDefinition:
        return await self._required(AnalyticsMetricDefinition, row_id, "METRIC_DEFINITION")

    async def metric_definitions(
        self, row_ids: Sequence[UUID]
    ) -> list[AnalyticsMetricDefinition]:
        if not row_ids:
            return []
        rows = list(
            await self.session.scalars(
                select(AnalyticsMetricDefinition).where(
                    AnalyticsMetricDefinition.workspace_id == self.workspace_id,
                    AnalyticsMetricDefinition.id.in_(set(row_ids)),
                )
            )
        )
        if len(rows) != len(set(row_ids)):
            raise _not_found("METRIC_DEFINITION")
        return rows

    async def sync_run(self, row_id: UUID, *, lock: bool = False) -> AnalyticsSyncRun:
        return await self._required(AnalyticsSyncRun, row_id, "ANALYTICS_SYNC", lock)

    async def idempotent_sync(
        self, actor_id: UUID, operation: str, idempotency_key: str
    ) -> AnalyticsSyncRun | None:
        return await self.session.scalar(
            select(AnalyticsSyncRun).where(
                AnalyticsSyncRun.workspace_id == self.workspace_id,
                AnalyticsSyncRun.requested_by == actor_id,
                AnalyticsSyncRun.operation == operation,
                AnalyticsSyncRun.idempotency_key == idempotency_key,
            )
        )

    async def evidence_batch(self, row_id: UUID) -> AnalyticsEvidenceBatch:
        return await self._required(AnalyticsEvidenceBatch, row_id, "ANALYTICS_EVIDENCE")

    async def tracking_link(self, row_id: UUID, *, lock: bool = False) -> TrackingLink:
        return await self._required(TrackingLink, row_id, "TRACKING_LINK", lock)

    async def tracking_link_by_hash(self, token_hash: str) -> TrackingLink:
        row = await self.session.scalar(
            select(TrackingLink).where(
                TrackingLink.workspace_id == self.workspace_id,
                TrackingLink.token_hash == token_hash,
            )
        )
        if row is None:
            raise _not_found("TRACKING_LINK")
        return row

    async def idempotent_conversion(
        self, source: str, external_event_id: str
    ) -> ConversionEvent | None:
        return await self.session.scalar(
            select(ConversionEvent).where(
                ConversionEvent.workspace_id == self.workspace_id,
                ConversionEvent.source == source,
                ConversionEvent.external_event_id == external_event_id,
            )
        )

    async def recommendation(self, row_id: UUID) -> AnalyticsRecommendation:
        return await self._required(AnalyticsRecommendation, row_id, "RECOMMENDATION")

    async def experiment(self, row_id: UUID, *, lock: bool = False) -> AnalyticsExperiment:
        return await self._required(AnalyticsExperiment, row_id, "EXPERIMENT", lock)

    async def report_definition(self, row_id: UUID) -> AnalyticsReportDefinition:
        return await self._required(AnalyticsReportDefinition, row_id, "REPORT_DEFINITION")

    async def report_run(self, row_id: UUID, *, lock: bool = False) -> AnalyticsReportRun:
        return await self._required(AnalyticsReportRun, row_id, "REPORT_RUN", lock)

    async def idempotent_report_run(
        self, actor_id: UUID, operation: str, idempotency_key: str
    ) -> AnalyticsReportRun | None:
        return await self.session.scalar(
            select(AnalyticsReportRun).where(
                AnalyticsReportRun.workspace_id == self.workspace_id,
                AnalyticsReportRun.requested_by == actor_id,
                AnalyticsReportRun.operation == operation,
                AnalyticsReportRun.idempotency_key == idempotency_key,
            )
        )

    async def idempotent_command(
        self,
        *,
        sync_run_id: UUID | None,
        report_run_id: UUID | None,
        actor_id: UUID,
        command: str,
        idempotency_key: str,
    ) -> AnalyticsJobCommand | None:
        return await self.session.scalar(
            select(AnalyticsJobCommand).where(
                AnalyticsJobCommand.workspace_id == self.workspace_id,
                AnalyticsJobCommand.sync_run_id == sync_run_id,
                AnalyticsJobCommand.report_run_id == report_run_id,
                AnalyticsJobCommand.actor_id == actor_id,
                AnalyticsJobCommand.command == command,
                AnalyticsJobCommand.idempotency_key == idempotency_key,
            )
        )

    async def _required(
        self, model: type, row_id: UUID, code: str, lock: bool = False
    ) -> object:
        statement: Select = select(model).where(
            model.workspace_id == self.workspace_id,
            model.id == row_id,
        )
        if lock:
            statement = statement.with_for_update()
        row = await self.session.scalar(statement)
        if row is None:
            raise _not_found(code)
        return row


def _not_found(resource: str) -> AppError:
    return AppError(
        code=f"{resource}_NOT_FOUND",
        message="현재 워크스페이스에서 요청한 분석 리소스를 찾을 수 없습니다.",
        status_code=404,
    )
