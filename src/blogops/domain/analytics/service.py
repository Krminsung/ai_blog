"""Tenant-scoped analytics ingestion, evidence, insight, and reporting services."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.core.context import Principal
from blogops.core.errors import AppError
from blogops.db.session import apply_workspace_scope
from blogops.domain.analytics.enums import (
    AnalyticsConnectionState,
    AnalyticsJobOperation,
    AnalyticsProvider,
    EvidenceKind,
    MetricSubject,
)
from blogops.domain.analytics.models import (
    AnalyticsComparisonSnapshot,
    AnalyticsConnection,
    AnalyticsEvidenceBatch,
    AnalyticsExperiment,
    AnalyticsExperimentResult,
    AnalyticsJobCommand,
    AnalyticsMetricDefinition,
    AnalyticsProviderCall,
    AnalyticsRecommendation,
    AnalyticsRecommendationDecision,
    AnalyticsReportArtifact,
    AnalyticsReportDefinition,
    AnalyticsReportMetric,
    AnalyticsReportRun,
    AnalyticsSyncInputSnapshot,
    AnalyticsSyncRun,
    ChannelMetricDailyFact,
    ContentMetricDailyFact,
    ContentROISnapshot,
    ConversionEvent,
    OperationalMetricSnapshot,
    QueryMetricDailyFact,
    TrackingClickEvent,
    TrackingLink,
)
from blogops.domain.analytics.providers import AnalyticsFetchResult
from blogops.domain.analytics.repository import AnalyticsRepository
from blogops.domain.analytics.rules import (
    calculate_roi,
    canonical_json_hash,
    ensure_secret_free_config,
    hash_tracking_token,
    new_tracking_token,
    require_confirmed_or_estimated_status,
    safe_tracking_destination,
    validate_comparable_metrics,
    validate_fact_evidence,
    validate_metric_definition,
)
from blogops.domain.analytics.schemas import (
    AnalyticsConnectionCreate,
    AnalyticsSyncCreate,
    ComparisonSnapshotCreate,
    ConversionCreate,
    EvidenceBatchCreate,
    ExperimentCreate,
    ExperimentResultCreate,
    JobCommandCreate,
    ManualMetricFactCreate,
    MetricDefinitionCreate,
    OperationalSnapshotCreate,
    RecommendationCreate,
    RecommendationDecisionCreate,
    ReportDefinitionCreate,
    ReportRunCreate,
    ROISnapshotCreate,
    TrackingClickCreate,
    TrackingLinkCreate,
)
from blogops.domain.generation.models import ContentItem, ContentVersion
from blogops.domain.jobs.state import JobState, ensure_job_transition
from blogops.domain.planning.models import Campaign
from blogops.domain.publishing.models import PublishedPost
from blogops.services.audit import append_audit_log
from blogops.services.outbox import add_outbox_event


OUTBOX_SCHEMA_VERSION = "1"


class AnalyticsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_connection(
        self, principal: Principal, data: AnalyticsConnectionCreate
    ) -> AnalyticsConnection:
        await apply_workspace_scope(self.session, principal.workspace_id)
        provider = AnalyticsProvider(data.provider)
        ensure_secret_free_config(data.safe_config)
        row = AnalyticsConnection(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            provider=provider.value,
            name=data.name,
            external_property_id=data.external_property_id,
            site_url=str(data.site_url) if data.site_url else None,
            official_contract=data.official_contract,
            api_version=data.api_version,
            credential_secret_ref=data.credential_secret_ref,
            capabilities=list(dict.fromkeys(data.capabilities)),
            safe_config=dict(data.safe_config),
            source_delay=dict(data.source_delay),
            state=AnalyticsConnectionState.PENDING.value,
            created_by=principal.subject_id,
        )
        self.session.add(row)
        await self.session.flush()
        await self._record(
            principal,
            action="analytics.connection.created",
            target_type="analytics_connection",
            target_id=row.id,
            event_type="analytics.connection.created",
            payload={"connection_id": str(row.id), "provider": row.provider},
        )
        return row

    async def list_connections(self, principal: Principal) -> list[AnalyticsConnection]:
        await apply_workspace_scope(self.session, principal.workspace_id)
        return await AnalyticsRepository(self.session, principal.workspace_id).connections()

    async def create_metric_definition(
        self, principal: Principal, data: MetricDefinitionCreate
    ) -> AnalyticsMetricDefinition:
        await apply_workspace_scope(self.session, principal.workspace_id)
        payload = data.model_dump(mode="json")
        validate_metric_definition(payload)
        row = AnalyticsMetricDefinition(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            key=data.key,
            version=data.version,
            name=data.name,
            description=data.description,
            subject=data.subject.value,
            unit=data.unit,
            value_kind=data.value_kind.value,
            formula=dict(data.formula),
            source_provider=str(data.source_provider),
            source_field=data.source_field,
            source_contract_version=data.source_contract_version,
            latency=dict(data.latency),
            supported_dimensions=list(dict.fromkeys(data.supported_dimensions)),
            caveats=list(data.caveats),
            effective_at=data.effective_at,
            deprecated_at=data.deprecated_at,
            definition_hash=canonical_json_hash(payload),
            created_by=principal.subject_id,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def create_sync(
        self,
        principal: Principal,
        data: AnalyticsSyncCreate,
        *,
        idempotency_key: str,
    ) -> tuple[AnalyticsSyncRun, bool]:
        await apply_workspace_scope(self.session, principal.workspace_id)
        repo = AnalyticsRepository(self.session, principal.workspace_id)
        request_payload = data.model_dump(mode="json")
        request_hash = canonical_json_hash(request_payload)
        existing = await repo.idempotent_sync(
            principal.subject_id, AnalyticsJobOperation.SYNC.value, idempotency_key
        )
        if existing is not None:
            _require_same_request(existing.request_hash, request_hash)
            return existing, False
        connection = await repo.connection(data.connection_id)
        if connection.state == AnalyticsConnectionState.DISCONNECTED.value:
            raise AppError(
                code="ANALYTICS_CONNECTION_DISCONNECTED",
                message="연결 해제된 분석 연결은 동기화할 수 없습니다.",
                status_code=409,
            )
        definitions = await repo.metric_definitions(data.metric_definition_ids)
        snapshots = [_metric_snapshot(item) for item in definitions]
        invalid_sources = [
            item.key
            for item in definitions
            if item.source_provider != connection.provider
        ]
        invalid_dimensions = [
            dimension
            for dimension in data.dimensions
            if any(dimension not in definition.supported_dimensions for definition in definitions)
        ]
        if invalid_sources or invalid_dimensions:
            raise AppError(
                code="ANALYTICS_SYNC_DEFINITION_MISMATCH",
                message="연결 공급자 또는 지원 차원과 지표 정의가 일치하지 않습니다.",
                status_code=422,
                fields=[
                    *(
                        {"path": "metric_definition_ids", "reason": value}
                        for value in invalid_sources
                    ),
                    *({"path": "dimensions", "reason": value} for value in invalid_dimensions),
                ],
            )
        connection_snapshot = {
            "id": str(connection.id),
            "provider": connection.provider,
            "external_property_id": connection.external_property_id,
            "official_contract": connection.official_contract,
            "api_version": connection.api_version,
            "credential_secret_ref": connection.credential_secret_ref,
            "capabilities": connection.capabilities,
            "safe_config": connection.safe_config,
            "source_delay": connection.source_delay,
        }
        snapshot_payload = {
            "connection": connection_snapshot,
            "metric_definitions": snapshots,
            "request": request_payload,
        }
        snapshot = AnalyticsSyncInputSnapshot(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            connection_id=connection.id,
            provider=connection.provider,
            connection_snapshot=connection_snapshot,
            metric_definition_snapshots=snapshots,
            date_from=data.date_from,
            date_to=data.date_to,
            dimensions=list(dict.fromkeys(data.dimensions)),
            request_snapshot=request_payload,
            request_hash=request_hash,
            snapshot_hash=canonical_json_hash(snapshot_payload),
            created_by=principal.subject_id,
        )
        self.session.add(snapshot)
        await self.session.flush()
        run = AnalyticsSyncRun(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            connection_id=connection.id,
            input_snapshot_id=snapshot.id,
            requested_by=principal.subject_id,
            operation=AnalyticsJobOperation.SYNC.value,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            state=JobState.QUEUED.value,
        )
        self.session.add(run)
        await self.session.flush()
        await self._record(
            principal,
            action="analytics.sync.created",
            target_type="analytics_sync",
            target_id=run.id,
            event_type="analytics.sync.queued",
            payload={"sync_run_id": str(run.id), "snapshot_hash": snapshot.snapshot_hash},
        )
        return run, True

    async def get_sync(self, principal: Principal, run_id: UUID) -> AnalyticsSyncRun:
        await apply_workspace_scope(self.session, principal.workspace_id)
        return await AnalyticsRepository(self.session, principal.workspace_id).sync_run(run_id)

    async def list_content_facts(
        self, principal: Principal, content_id: UUID, *, limit: int = 500
    ) -> list[ContentMetricDailyFact]:
        await apply_workspace_scope(self.session, principal.workspace_id)
        await self._content(principal.workspace_id, content_id)
        return list(
            await self.session.scalars(
                select(ContentMetricDailyFact)
                .where(
                    ContentMetricDailyFact.workspace_id == principal.workspace_id,
                    ContentMetricDailyFact.content_id == content_id,
                )
                .order_by(
                    ContentMetricDailyFact.fact_date.desc(),
                    ContentMetricDailyFact.id.desc(),
                )
                .limit(limit)
            )
        )

    async def fail_sync_runtime(
        self,
        *,
        workspace_id: UUID,
        run_id: UUID,
        code: str,
        detail: str,
        retryable: bool = False,
    ) -> AnalyticsSyncRun:
        await apply_workspace_scope(self.session, workspace_id)
        row = await AnalyticsRepository(self.session, workspace_id).sync_run(run_id, lock=True)
        if row.state == JobState.CANCEL_REQUESTED.value:
            _finalize_cancelled_run(row)
            await self.session.flush()
            return row
        if row.state in {
            JobState.SUCCEEDED.value,
            JobState.CANCELLED.value,
            JobState.FINAL_FAILED.value,
            JobState.RETRYABLE_FAILED.value,
        }:
            return row
        target = JobState.RETRYABLE_FAILED if retryable else JobState.FINAL_FAILED
        row.state = target.value
        row.error_code = code
        row.error_detail = detail
        row.finished_at = datetime.now(UTC)
        await self.session.flush()
        await add_outbox_event(
            self.session,
            workspace_id=workspace_id,
            aggregate_type="analytics_sync",
            aggregate_id=str(row.id),
            event_type=(
                "analytics.sync.retryable_failed"
                if retryable
                else "analytics.sync.final_failed"
            ),
            schema_version=OUTBOX_SCHEMA_VERSION,
            payload={"sync_run_id": str(row.id), "error_code": code},
        )
        return row

    async def finalize_sync_cancellation(
        self, *, workspace_id: UUID, run_id: UUID
    ) -> AnalyticsSyncRun:
        await apply_workspace_scope(self.session, workspace_id)
        row = await AnalyticsRepository(self.session, workspace_id).sync_run(
            run_id, lock=True
        )
        if row.state == JobState.CANCEL_REQUESTED.value:
            _finalize_cancelled_run(row)
            await self.session.flush()
        return row

    async def record_provider_result(
        self,
        *,
        workspace_id: UUID,
        run_id: UUID,
        result: AnalyticsFetchResult,
    ) -> AnalyticsSyncRun:
        await apply_workspace_scope(self.session, workspace_id)
        repo = AnalyticsRepository(self.session, workspace_id)
        run = await repo.sync_run(run_id, lock=True)
        if run.state == JobState.CANCEL_REQUESTED.value:
            _finalize_cancelled_run(run)
            await self.session.flush()
            return run
        if run.state == JobState.SUCCEEDED.value:
            if run.result and run.result.get("raw_response_hash") == result.raw_response_hash:
                return run
            raise AppError(
                code="ANALYTICS_SYNC_RESULT_CONFLICT",
                message="완료된 동기화 작업에 다른 공급자 결과를 기록할 수 없습니다.",
                status_code=409,
            )
        snapshot = await self.session.scalar(
            select(AnalyticsSyncInputSnapshot).where(
                AnalyticsSyncInputSnapshot.workspace_id == workspace_id,
                AnalyticsSyncInputSnapshot.id == run.input_snapshot_id,
            )
        )
        if snapshot is None:
            raise AppError("ANALYTICS_SNAPSHOT_NOT_FOUND", "분석 입력 스냅샷이 없습니다.", 404)
        if (
            result.official_contract
            != str(snapshot.connection_snapshot["official_contract"])
            or result.api_version != str(snapshot.connection_snapshot["api_version"])
        ):
            raise AppError(
                code="ANALYTICS_OFFICIAL_CONTRACT_MISMATCH",
                message="공급자 응답의 공식 계약 또는 API 버전이 입력 스냅샷과 다릅니다.",
                status_code=422,
            )
        call_payload = {
            "run_id": str(run.id),
            "adapter": result.adapter_name,
            "api_version": result.api_version,
            "request_hash": snapshot.request_hash,
        }
        call = AnalyticsProviderCall(
            id=uuid4(),
            workspace_id=workspace_id,
            sync_run_id=run.id,
            connection_id=run.connection_id,
            provider=snapshot.provider,
            adapter_name=result.adapter_name,
            adapter_version=result.adapter_version,
            api_version=result.api_version,
            request_hash=canonical_json_hash(call_payload),
            request_metadata=snapshot.request_snapshot,
            raw_object_ref=result.raw_object_ref,
            raw_response_hash=result.raw_response_hash,
            response_metadata=dict(result.response_metadata),
            source_delay=dict(result.source_delay),
            row_count=len(result.facts),
            started_at=result.started_at,
            completed_at=result.completed_at,
        )
        self.session.add(call)
        await self.session.flush()
        definitions = {
            str(item["source_field"]): item
            for item in snapshot.metric_definition_snapshots
        }
        for fact in result.facts:
            definition = definitions.get(fact.metric_field)
            if definition is None:
                raise AppError(
                    code="ANALYTICS_UNDECLARED_METRIC",
                    message="스냅샷에 없는 공급자 지표가 반환되었습니다.",
                    status_code=422,
                )
            if str(definition["subject"]) != str(fact.subject):
                raise AppError(
                    code="ANALYTICS_FACT_SUBJECT_MISMATCH",
                    message="공급자 사실 주체가 고정된 지표 정의와 다릅니다.",
                    status_code=422,
                )
            await self._append_provider_fact(workspace_id, call, fact, definition)
        run.row_count = len(result.facts)
        run.result = {
            "provider_call_id": str(call.id),
            "raw_response_hash": result.raw_response_hash,
            "row_count": len(result.facts),
        }
        run.state = JobState.SUCCEEDED.value
        run.finished_at = result.completed_at
        connection = await repo.connection(run.connection_id, lock=True)
        connection.state = AnalyticsConnectionState.ACTIVE.value
        connection.last_synced_at = result.completed_at
        connection.last_error_code = None
        await self.session.flush()
        return run

    async def create_evidence_batch(
        self, principal: Principal, data: EvidenceBatchCreate
    ) -> AnalyticsEvidenceBatch:
        await apply_workspace_scope(self.session, principal.workspace_id)
        if data.source not in {EvidenceKind.CSV, EvidenceKind.MANUAL}:
            raise AppError(
                code="MANUAL_EVIDENCE_SOURCE_REQUIRED",
                message="이 경계는 CSV 또는 수동 증거만 허용합니다.",
                status_code=422,
            )
        existing = await self.session.scalar(
            select(AnalyticsEvidenceBatch).where(
                AnalyticsEvidenceBatch.workspace_id == principal.workspace_id,
                AnalyticsEvidenceBatch.source == data.source.value,
                AnalyticsEvidenceBatch.external_batch_id == data.external_batch_id,
            )
        )
        if existing is not None:
            if existing.object_hash != data.object_hash:
                raise AppError(
                    code="ANALYTICS_EVIDENCE_IDEMPOTENCY_CONFLICT",
                    message="같은 외부 증거 배치 ID에 다른 파일 해시를 기록할 수 없습니다.",
                    status_code=409,
                )
            return existing
        row = AnalyticsEvidenceBatch(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            source=data.source.value,
            external_batch_id=data.external_batch_id,
            object_ref=data.object_ref,
            object_hash=data.object_hash,
            mapping_snapshot=dict(data.mapping_snapshot),
            evidence_metadata=dict(data.evidence_metadata),
            submitted_by=principal.subject_id,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def append_manual_fact(
        self, principal: Principal, data: ManualMetricFactCreate
    ) -> object:
        await apply_workspace_scope(self.session, principal.workspace_id)
        repo = AnalyticsRepository(self.session, principal.workspace_id)
        evidence = await repo.evidence_batch(data.evidence_batch_id)
        if evidence.source not in {EvidenceKind.CSV.value, EvidenceKind.MANUAL.value}:
            raise AppError(
                code="MANUAL_EVIDENCE_SOURCE_INVALID",
                message="수동 사실에는 CSV 또는 수동 증거 배치가 필요합니다.",
                status_code=422,
            )
        definition = await repo.metric_definition(data.metric_definition_id)
        if (
            definition.subject != data.subject.value
            or definition.value_kind != data.value_kind.value
        ):
            raise AppError(
                code="MANUAL_FACT_DEFINITION_MISMATCH",
                message="수동 사실의 주체·값 유형이 고정된 지표 정의와 다릅니다.",
                status_code=422,
            )
        validate_fact_evidence(provider_call_id=None, evidence_batch_id=evidence.id)
        dimensions_hash = canonical_json_hash(data.dimensions)
        evidence_hash = canonical_json_hash(
            {"batch_hash": evidence.object_hash, "external_fact_id": data.external_fact_id}
        )
        common = dict(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            metric_definition_id=definition.id,
            provider_call_id=None,
            evidence_batch_id=evidence.id,
            fact_date=data.fact_date,
            source=evidence.source,
            external_fact_id=data.external_fact_id,
            dimensions=dict(data.dimensions),
            dimensions_hash=dimensions_hash,
            value=data.value,
            value_kind=data.value_kind.value,
            observed_at=data.observed_at,
            retrieved_at=datetime.now(UTC),
            source_delay=dict(data.source_delay),
            evidence_hash=evidence_hash,
        )
        if data.subject is MetricSubject.CONTENT:
            if data.content_id is None:
                raise _field_required("content_id")
            await self._content(principal.workspace_id, data.content_id)
            row = ContentMetricDailyFact(
                **common,
                content_id=data.content_id,
                published_post_id=data.published_post_id,
            )
        elif data.subject is MetricSubject.CHANNEL:
            if data.connection_id is None or not data.channel:
                raise _field_required("connection_id/channel")
            await repo.connection(data.connection_id)
            row = ChannelMetricDailyFact(
                **common,
                connection_id=data.connection_id,
                channel=data.channel,
            )
        elif data.subject is MetricSubject.QUERY:
            if not data.query_text:
                raise _field_required("query_text")
            row = QueryMetricDailyFact(
                **common,
                content_id=data.content_id,
                query_text=data.query_text,
                query_hash=canonical_json_hash(data.query_text),
            )
        else:
            raise AppError(
                code="DAILY_FACT_SUBJECT_INVALID",
                message="날짜별 수동 사실은 콘텐츠·채널·검색어 주체만 허용합니다.",
                status_code=422,
            )
        self.session.add(row)
        await self.session.flush()
        return row

    async def create_tracking_link(
        self, principal: Principal, data: TrackingLinkCreate
    ) -> tuple[TrackingLink, str, str]:
        await apply_workspace_scope(self.session, principal.workspace_id)
        await self._content(principal.workspace_id, data.content_id)
        if data.campaign_id:
            await self._require_tenant_row(Campaign, principal.workspace_id, data.campaign_id)
        destination = safe_tracking_destination(
            str(data.destination_url), data.tracking_parameters
        )
        token, token_hash = new_tracking_token()
        row = TrackingLink(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            content_id=data.content_id,
            campaign_id=data.campaign_id,
            token_hash=token_hash,
            destination_url=str(data.destination_url),
            tracking_parameters=dict(data.tracking_parameters),
            expires_at=data.expires_at,
            created_by=principal.subject_id,
        )
        self.session.add(row)
        await self.session.flush()
        return row, token, destination

    async def record_click(
        self, principal: Principal, token: str, data: TrackingClickCreate
    ) -> tuple[TrackingClickEvent, str]:
        await apply_workspace_scope(self.session, principal.workspace_id)
        link = await AnalyticsRepository(
            self.session, principal.workspace_id
        ).tracking_link_by_hash(hash_tracking_token(token))
        now = datetime.now(UTC)
        if link.disabled_at or (link.expires_at and link.expires_at <= now):
            raise AppError("TRACKING_LINK_INACTIVE", "비활성 추적 링크입니다.", 410)
        destination = safe_tracking_destination(link.destination_url, link.tracking_parameters)
        existing = await self.session.scalar(
            select(TrackingClickEvent).where(
                TrackingClickEvent.workspace_id == principal.workspace_id,
                TrackingClickEvent.tracking_link_id == link.id,
                TrackingClickEvent.external_event_id == data.external_event_id,
            )
        )
        if existing is not None:
            if (
                existing.clicked_at != data.clicked_at
                or existing.user_agent_hash != data.user_agent_hash
                or existing.ip_network_hash != data.ip_network_hash
                or existing.metadata_json != data.metadata
            ):
                raise AppError(
                    code="TRACKING_CLICK_IDEMPOTENCY_CONFLICT",
                    message="같은 클릭 이벤트 ID에 다른 증거를 기록할 수 없습니다.",
                    status_code=409,
                )
            return existing, destination
        row = TrackingClickEvent(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            tracking_link_id=link.id,
            external_event_id=data.external_event_id,
            clicked_at=data.clicked_at,
            referrer_origin=data.referrer_origin,
            user_agent_hash=data.user_agent_hash,
            ip_network_hash=data.ip_network_hash,
            metadata_json=dict(data.metadata),
        )
        self.session.add(row)
        await self.session.flush()
        return row, destination

    async def record_conversion(
        self, principal: Principal, data: ConversionCreate
    ) -> tuple[ConversionEvent, bool]:
        await apply_workspace_scope(self.session, principal.workspace_id)
        repo = AnalyticsRepository(self.session, principal.workspace_id)
        existing = await repo.idempotent_conversion(
            data.source.value, data.external_event_id
        )
        request_hash = canonical_json_hash(data.model_dump(mode="json"))
        if existing is not None:
            if (
                existing.evidence_hash != data.evidence_hash
                or existing.raw_metadata.get("request_hash") != request_hash
            ):
                raise AppError(
                    "CONVERSION_IDEMPOTENCY_CONFLICT",
                    "같은 외부 전환 ID에 다른 증거를 연결할 수 없습니다.",
                    409,
                )
            return existing, False
        await self._validate_conversion_refs(principal.workspace_id, data)
        row = ConversionEvent(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            content_id=data.content_id,
            published_post_id=data.published_post_id,
            tracking_link_id=data.tracking_link_id,
            evidence_batch_id=data.evidence_batch_id,
            source=data.source.value,
            external_event_id=data.external_event_id,
            event_type=data.event_type,
            occurred_at=data.occurred_at,
            amount=data.amount,
            currency=data.currency.upper() if data.currency else None,
            attribution_model=data.attribution_model.value,
            attribution_snapshot=dict(data.attribution_snapshot),
            is_confirmed=data.is_confirmed,
            evidence_kind=data.evidence_kind.value,
            evidence_ref=data.evidence_ref,
            evidence_hash=data.evidence_hash,
            source_delay=dict(data.source_delay),
            raw_metadata={**data.raw_metadata, "request_hash": request_hash},
            recorded_by=principal.subject_id,
        )
        self.session.add(row)
        await self.session.flush()
        await self._record(
            principal,
            action="analytics.conversion.recorded",
            target_type="conversion",
            target_id=row.id,
            event_type="analytics.conversion.recorded",
            payload={"conversion_id": str(row.id), "confirmed": row.is_confirmed},
        )
        return row, True

    async def create_operational_snapshot(
        self, principal: Principal, data: OperationalSnapshotCreate
    ) -> OperationalMetricSnapshot:
        await apply_workspace_scope(self.session, principal.workspace_id)
        await self._validate_content_version_refs(
            principal.workspace_id, data.content_id, data.content_version_id
        )
        payload = data.model_dump(mode="json")
        row = OperationalMetricSnapshot(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            content_id=data.content_id,
            content_version_id=data.content_version_id,
            snapshot_kind=data.snapshot_kind.value,
            scope=dict(data.scope),
            scope_hash=canonical_json_hash(data.scope),
            period_start=data.period_start,
            period_end=data.period_end,
            metrics=dict(data.metrics),
            metric_definition_snapshots=list(data.metric_definition_snapshots),
            sample_size=data.sample_size,
            completeness=dict(data.completeness),
            caveats=list(data.caveats),
            snapshot_hash=canonical_json_hash(payload),
            created_by=principal.subject_id,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def create_roi_snapshot(
        self, principal: Principal, data: ROISnapshotCreate
    ) -> ContentROISnapshot:
        await apply_workspace_scope(self.session, principal.workspace_id)
        await self._content(principal.workspace_id, data.content_id)
        require_confirmed_or_estimated_status(data.revenue_status.value)
        require_confirmed_or_estimated_status(data.cost_status.value)
        result = calculate_roi(revenue=data.attributed_revenue, cost=data.production_cost)
        payload = data.model_dump(mode="json")
        row = ContentROISnapshot(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            content_id=data.content_id,
            period_start=data.period_start,
            period_end=data.period_end,
            attributed_revenue=data.attributed_revenue,
            production_cost=data.production_cost,
            net_return=result.net_return,
            roi_ratio=result.roi_ratio,
            currency=data.currency.upper(),
            revenue_status=data.revenue_status.value,
            cost_status=data.cost_status.value,
            attribution_model=data.attribution_model.value,
            formula_snapshot=dict(data.formula_snapshot),
            evidence_snapshot=dict(data.evidence_snapshot),
            caveats=list(data.caveats),
            snapshot_hash=canonical_json_hash(payload),
            created_by=principal.subject_id,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def create_comparison_snapshot(
        self, principal: Principal, data: ComparisonSnapshotCreate
    ) -> AnalyticsComparisonSnapshot:
        await apply_workspace_scope(self.session, principal.workspace_id)
        definitions = await AnalyticsRepository(
            self.session, principal.workspace_id
        ).metric_definitions(data.metric_definition_ids)
        snapshots = [_metric_snapshot(item) for item in definitions]
        validate_comparable_metrics(snapshots)
        payload = data.model_dump(mode="json")
        row = AnalyticsComparisonSnapshot(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            comparison_kind=data.comparison_kind.value,
            scope=dict(data.scope),
            scope_hash=canonical_json_hash(data.scope),
            period_start=data.period_start,
            period_end=data.period_end,
            results=dict(data.results),
            metric_compatibility={"compatible": True},
            definition_snapshots=snapshots,
            sample_size=data.sample_size,
            caveats=list(data.caveats),
            snapshot_hash=canonical_json_hash(payload),
            created_by=principal.subject_id,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def create_recommendation(
        self, principal: Principal, data: RecommendationCreate
    ) -> AnalyticsRecommendation:
        await apply_workspace_scope(self.session, principal.workspace_id)
        version = await self._content_version(
            principal.workspace_id, data.content_id, data.content_version_id
        )
        definitions = await AnalyticsRepository(
            self.session, principal.workspace_id
        ).metric_definitions(data.metric_definition_ids)
        payload = data.model_dump(mode="json")
        row = AnalyticsRecommendation(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            content_id=data.content_id,
            content_version_id=version.id,
            content_hash=version.content_hash,
            kind=data.kind.value,
            rule_name=data.rule_name,
            rule_version=data.rule_version,
            model_name=data.model_name,
            model_version=data.model_version,
            metric_definition_snapshots=[_metric_snapshot(item) for item in definitions],
            evidence_snapshot=dict(data.evidence_snapshot),
            explanation=data.explanation,
            proposed_actions=list(data.proposed_actions),
            limitations=list(data.limitations),
            proposal_hash=canonical_json_hash(payload),
            created_by=principal.subject_id,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def decide_recommendation(
        self,
        principal: Principal,
        recommendation_id: UUID,
        data: RecommendationDecisionCreate,
    ) -> AnalyticsRecommendationDecision:
        await apply_workspace_scope(self.session, principal.workspace_id)
        await AnalyticsRepository(
            self.session, principal.workspace_id
        ).recommendation(recommendation_id)
        row = AnalyticsRecommendationDecision(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            recommendation_id=recommendation_id,
            decision=data.decision.value,
            reason=data.reason,
            decided_by=principal.subject_id,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def list_recommendations(
        self, principal: Principal, content_id: UUID
    ) -> list[AnalyticsRecommendation]:
        await apply_workspace_scope(self.session, principal.workspace_id)
        await self._content(principal.workspace_id, content_id)
        return list(
            await self.session.scalars(
                select(AnalyticsRecommendation)
                .where(
                    AnalyticsRecommendation.workspace_id == principal.workspace_id,
                    AnalyticsRecommendation.content_id == content_id,
                )
                .order_by(
                    AnalyticsRecommendation.created_at.desc(),
                    AnalyticsRecommendation.id.desc(),
                )
            )
        )

    async def create_experiment(
        self, principal: Principal, data: ExperimentCreate
    ) -> AnalyticsExperiment:
        await apply_workspace_scope(self.session, principal.workspace_id)
        await self._content(principal.workspace_id, data.content_id)
        await AnalyticsRepository(
            self.session, principal.workspace_id
        ).metric_definition(data.metric_definition_id)
        row = AnalyticsExperiment(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            content_id=data.content_id,
            kind=data.kind.value,
            metric_definition_id=data.metric_definition_id,
            variants=list(data.variants),
            allocation_policy=dict(data.allocation_policy),
            required_sample_size=data.required_sample_size,
            analysis_policy=dict(data.analysis_policy),
            caveats=list(data.caveats),
            created_by=principal.subject_id,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def append_experiment_result(
        self,
        principal: Principal,
        experiment_id: UUID,
        data: ExperimentResultCreate,
    ) -> AnalyticsExperimentResult:
        await apply_workspace_scope(self.session, principal.workspace_id)
        experiment = await AnalyticsRepository(
            self.session, principal.workspace_id
        ).experiment(experiment_id)
        if data.sample_size < experiment.required_sample_size and data.conclusion is not None:
            raise AppError(
                code="EXPERIMENT_SAMPLE_INSUFFICIENT",
                message="필수 표본 수 전에는 결론을 기록할 수 없습니다.",
                status_code=422,
            )
        payload = data.model_dump(mode="json")
        row = AnalyticsExperimentResult(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            experiment_id=experiment_id,
            window_start=data.window_start,
            window_end=data.window_end,
            variant_results=list(data.variant_results),
            sample_size=data.sample_size,
            analysis=dict(data.analysis),
            conclusion=data.conclusion,
            caveats=list(data.caveats),
            result_hash=canonical_json_hash(payload),
            created_by=principal.subject_id,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def create_report_definition(
        self, principal: Principal, data: ReportDefinitionCreate
    ) -> AnalyticsReportDefinition:
        await apply_workspace_scope(self.session, principal.workspace_id)
        definitions = await AnalyticsRepository(
            self.session, principal.workspace_id
        ).metric_definitions(data.metric_definition_ids)
        payload = data.model_dump(mode="json")
        row = AnalyticsReportDefinition(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            name=data.name,
            cadence=data.cadence.value,
            timezone=data.timezone,
            scope=dict(data.scope),
            formats=[item.value for item in data.formats],
            delivery_policy=dict(data.delivery_policy),
            branding_snapshot=dict(data.branding_snapshot),
            caveats=list(data.caveats),
            definition_hash=canonical_json_hash(payload),
            enabled=True,
            next_run_at=data.next_run_at,
            created_by=principal.subject_id,
        )
        self.session.add(row)
        await self.session.flush()
        for position, definition in enumerate(definitions):
            self.session.add(
                AnalyticsReportMetric(
                    id=uuid4(),
                    workspace_id=principal.workspace_id,
                    report_definition_id=row.id,
                    metric_definition_id=definition.id,
                    position=position,
                    definition_snapshot=_metric_snapshot(definition),
                )
            )
        await self.session.flush()
        return row

    async def create_report_run(
        self,
        principal: Principal,
        definition_id: UUID,
        data: ReportRunCreate,
        *,
        idempotency_key: str,
    ) -> tuple[AnalyticsReportRun, bool]:
        await apply_workspace_scope(self.session, principal.workspace_id)
        repo = AnalyticsRepository(self.session, principal.workspace_id)
        payload = data.model_dump(mode="json") | {"definition_id": str(definition_id)}
        request_hash = canonical_json_hash(payload)
        existing = await repo.idempotent_report_run(
            principal.subject_id, AnalyticsJobOperation.REPORT.value, idempotency_key
        )
        if existing:
            _require_same_request(existing.request_hash, request_hash)
            return existing, False
        definition = await repo.report_definition(definition_id)
        metric_rows = list(
            await self.session.scalars(
                select(AnalyticsReportMetric)
                .where(
                    AnalyticsReportMetric.workspace_id == principal.workspace_id,
                    AnalyticsReportMetric.report_definition_id == definition.id,
                )
                .order_by(AnalyticsReportMetric.position)
            )
        )
        definition_snapshot = {
            "id": str(definition.id),
            "definition_hash": definition.definition_hash,
            "name": definition.name,
            "cadence": definition.cadence,
            "timezone": definition.timezone,
            "scope": definition.scope,
            "formats": definition.formats,
            "delivery_policy": definition.delivery_policy,
            "branding": definition.branding_snapshot,
            "metrics": [item.definition_snapshot for item in metric_rows],
            "caveats": definition.caveats,
        }
        row = AnalyticsReportRun(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            definition_id=definition.id,
            requested_by=principal.subject_id,
            operation=AnalyticsJobOperation.REPORT.value,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            definition_snapshot=definition_snapshot,
            definition_snapshot_hash=canonical_json_hash(definition_snapshot),
            period_start=data.period_start,
            period_end=data.period_end,
            state=JobState.QUEUED.value,
        )
        self.session.add(row)
        await self.session.flush()
        await self._record(
            principal,
            action="analytics.report.created",
            target_type="analytics_report_run",
            target_id=row.id,
            event_type="analytics.report.queued",
            payload={"report_run_id": str(row.id)},
        )
        return row, True

    async def get_report_run(
        self, principal: Principal, run_id: UUID
    ) -> AnalyticsReportRun:
        await apply_workspace_scope(self.session, principal.workspace_id)
        return await AnalyticsRepository(self.session, principal.workspace_id).report_run(run_id)

    async def fail_report_runtime(
        self,
        *,
        workspace_id: UUID,
        run_id: UUID,
        code: str,
        detail: str,
        retryable: bool = False,
    ) -> AnalyticsReportRun:
        await apply_workspace_scope(self.session, workspace_id)
        row = await AnalyticsRepository(self.session, workspace_id).report_run(run_id, lock=True)
        if row.state == JobState.CANCEL_REQUESTED.value:
            _finalize_cancelled_run(row)
            await self.session.flush()
            return row
        if row.state in {
            JobState.SUCCEEDED.value,
            JobState.CANCELLED.value,
            JobState.FINAL_FAILED.value,
            JobState.RETRYABLE_FAILED.value,
        }:
            return row
        row.state = (
            JobState.RETRYABLE_FAILED.value
            if retryable
            else JobState.FINAL_FAILED.value
        )
        row.error_code = code
        row.error_detail = detail
        row.finished_at = datetime.now(UTC)
        await self.session.flush()
        return row

    async def finalize_report_cancellation(
        self, *, workspace_id: UUID, run_id: UUID
    ) -> AnalyticsReportRun:
        await apply_workspace_scope(self.session, workspace_id)
        row = await AnalyticsRepository(self.session, workspace_id).report_run(
            run_id, lock=True
        )
        if row.state == JobState.CANCEL_REQUESTED.value:
            _finalize_cancelled_run(row)
            await self.session.flush()
        return row

    async def record_report_artifact(
        self,
        *,
        workspace_id: UUID,
        run_id: UUID,
        output_format: str,
        object_ref: str,
        object_hash: str,
        media_type: str,
        size_bytes: int,
        manifest: dict[str, Any],
    ) -> AnalyticsReportArtifact:
        await apply_workspace_scope(self.session, workspace_id)
        run = await AnalyticsRepository(self.session, workspace_id).report_run(
            run_id, lock=True
        )
        if run.state == JobState.CANCEL_REQUESTED.value:
            _finalize_cancelled_run(run)
            await self.session.flush()
            raise AppError(
                code="ANALYTICS_REPORT_CANCELLED",
                message="취소 요청된 보고서에는 산출물을 기록할 수 없습니다.",
                status_code=409,
            )
        allowed_formats = {str(value) for value in run.definition_snapshot["formats"]}
        if output_format not in allowed_formats or size_bytes < 0:
            raise AppError(
                code="ANALYTICS_REPORT_ARTIFACT_INVALID",
                message="보고서 정의에 없는 형식이거나 크기가 올바르지 않습니다.",
                status_code=422,
            )
        row = AnalyticsReportArtifact(
            id=uuid4(),
            workspace_id=workspace_id,
            report_run_id=run.id,
            format=output_format,
            object_ref=object_ref,
            object_hash=object_hash,
            media_type=media_type,
            size_bytes=size_bytes,
            manifest={
                **manifest,
                "definition_snapshot_hash": run.definition_snapshot_hash,
                "period_start": run.period_start.isoformat(),
                "period_end": run.period_end.isoformat(),
            },
        )
        existing = await self.session.scalar(
            select(AnalyticsReportArtifact).where(
                AnalyticsReportArtifact.workspace_id == workspace_id,
                AnalyticsReportArtifact.report_run_id == run.id,
                AnalyticsReportArtifact.format == output_format,
            )
        )
        if existing is not None:
            if existing.object_hash != object_hash:
                raise AppError(
                    code="ANALYTICS_REPORT_ARTIFACT_CONFLICT",
                    message="같은 보고서 형식에 다른 산출물 해시를 기록할 수 없습니다.",
                    status_code=409,
                )
            return existing
        self.session.add(row)
        await self.session.flush()
        artifact_count = len(
            list(
                await self.session.scalars(
                    select(AnalyticsReportArtifact.id).where(
                        AnalyticsReportArtifact.workspace_id == workspace_id,
                        AnalyticsReportArtifact.report_run_id == run.id,
                    )
                )
            )
        )
        if artifact_count >= len(allowed_formats):
            run.state = JobState.SUCCEEDED.value
            run.finished_at = datetime.now(UTC)
        await self.session.flush()
        return row

    async def command_sync(
        self,
        principal: Principal,
        run_id: UUID,
        data: JobCommandCreate,
        *,
        idempotency_key: str,
    ) -> AnalyticsSyncRun:
        await apply_workspace_scope(self.session, principal.workspace_id)
        repo = AnalyticsRepository(self.session, principal.workspace_id)
        run = await repo.sync_run(run_id, lock=True)
        request_hash = canonical_json_hash(data.model_dump(mode="json"))
        existing = await repo.idempotent_command(
            sync_run_id=run.id,
            report_run_id=None,
            actor_id=principal.subject_id,
            command=data.command.value,
            idempotency_key=idempotency_key,
        )
        if existing:
            _require_same_request(existing.request_hash, request_hash)
            return run
        current = JobState(run.state)
        target = JobState.CANCEL_REQUESTED if data.command.value == "CANCEL" else JobState.QUEUED
        ensure_job_transition(current, target)
        run.state = target.value
        if target is JobState.QUEUED:
            run.error_code = None
            run.error_detail = None
            run.finished_at = None
            run.attempt += 1
        self.session.add(
            AnalyticsJobCommand(
                id=uuid4(),
                workspace_id=principal.workspace_id,
                sync_run_id=run.id,
                report_run_id=None,
                target_id=run.id,
                actor_id=principal.subject_id,
                command=data.command.value,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                from_state=current.value,
                to_state=target.value,
                reason=data.reason,
            )
        )
        await self.session.flush()
        return run

    async def command_report(
        self,
        principal: Principal,
        run_id: UUID,
        data: JobCommandCreate,
        *,
        idempotency_key: str,
    ) -> AnalyticsReportRun:
        await apply_workspace_scope(self.session, principal.workspace_id)
        repo = AnalyticsRepository(self.session, principal.workspace_id)
        run = await repo.report_run(run_id, lock=True)
        request_hash = canonical_json_hash(data.model_dump(mode="json"))
        existing = await repo.idempotent_command(
            sync_run_id=None,
            report_run_id=run.id,
            actor_id=principal.subject_id,
            command=data.command.value,
            idempotency_key=idempotency_key,
        )
        if existing:
            _require_same_request(existing.request_hash, request_hash)
            return run
        current = JobState(run.state)
        target = (
            JobState.CANCEL_REQUESTED
            if data.command.value == "CANCEL"
            else JobState.QUEUED
        )
        ensure_job_transition(current, target)
        run.state = target.value
        if target is JobState.QUEUED:
            run.error_code = None
            run.error_detail = None
            run.finished_at = None
            run.attempt += 1
        self.session.add(
            AnalyticsJobCommand(
                id=uuid4(),
                workspace_id=principal.workspace_id,
                sync_run_id=None,
                report_run_id=run.id,
                target_id=run.id,
                actor_id=principal.subject_id,
                command=data.command.value,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                from_state=current.value,
                to_state=target.value,
                reason=data.reason,
            )
        )
        await self.session.flush()
        return run

    async def _append_provider_fact(
        self,
        workspace_id: UUID,
        call: AnalyticsProviderCall,
        fact: Any,
        definition: dict[str, Any],
    ) -> None:
        dimensions = dict(fact.dimensions)
        common = dict(
            id=uuid4(),
            workspace_id=workspace_id,
            metric_definition_id=UUID(str(definition["id"])),
            provider_call_id=call.id,
            evidence_batch_id=None,
            fact_date=fact.fact_date,
            source=call.provider,
            external_fact_id=fact.external_fact_id,
            dimensions=dimensions,
            dimensions_hash=canonical_json_hash(dimensions),
            value=fact.value,
            value_kind=definition["value_kind"],
            observed_at=fact.observed_at,
            retrieved_at=call.completed_at,
            source_delay=call.source_delay,
            evidence_hash=canonical_json_hash(
                {"raw_response_hash": call.raw_response_hash, "fact": fact.external_fact_id}
            ),
        )
        subject = MetricSubject(fact.subject)
        if subject is MetricSubject.CONTENT:
            row = ContentMetricDailyFact(
                **common,
                content_id=UUID(str(dimensions["content_id"])),
                published_post_id=_optional_uuid(dimensions.get("published_post_id")),
            )
        elif subject is MetricSubject.CHANNEL:
            row = ChannelMetricDailyFact(
                **common,
                connection_id=call.connection_id,
                channel=str(dimensions["channel"]),
            )
        elif subject is MetricSubject.QUERY:
            query = str(dimensions["query"])
            row = QueryMetricDailyFact(
                **common,
                content_id=_optional_uuid(dimensions.get("content_id")),
                query_text=query,
                query_hash=canonical_json_hash(query),
            )
        else:
            raise AppError(
                "ANALYTICS_FACT_SUBJECT_INVALID",
                "공식 공급자 fact 주체가 날짜별 fact 테이블과 맞지 않습니다.",
                422,
            )
        self.session.add(row)

    async def _validate_conversion_refs(
        self, workspace_id: UUID, data: ConversionCreate
    ) -> None:
        for model, row_id in (
            (ContentItem, data.content_id),
            (PublishedPost, data.published_post_id),
            (TrackingLink, data.tracking_link_id),
            (AnalyticsEvidenceBatch, data.evidence_batch_id),
        ):
            if row_id:
                await self._require_tenant_row(model, workspace_id, row_id)

    async def _validate_content_version_refs(
        self,
        workspace_id: UUID,
        content_id: UUID | None,
        content_version_id: UUID | None,
    ) -> None:
        if content_version_id is not None and content_id is None:
            raise _field_required("content_id")
        if content_id is not None:
            await self._content(workspace_id, content_id)
        if content_version_id is not None and content_id is not None:
            await self._content_version(workspace_id, content_id, content_version_id)

    async def _content(self, workspace_id: UUID, content_id: UUID) -> ContentItem:
        return await self._require_tenant_row(ContentItem, workspace_id, content_id)

    async def _content_version(
        self, workspace_id: UUID, content_id: UUID, version_id: UUID
    ) -> ContentVersion:
        row = await self.session.scalar(
            select(ContentVersion).where(
                ContentVersion.workspace_id == workspace_id,
                ContentVersion.content_id == content_id,
                ContentVersion.id == version_id,
            )
        )
        if row is None:
            raise AppError("CONTENT_VERSION_NOT_FOUND", "콘텐츠 버전을 찾을 수 없습니다.", 404)
        return row

    async def _require_tenant_row(
        self, model: type, workspace_id: UUID, row_id: UUID
    ) -> Any:
        row = await self.session.scalar(
            select(model).where(model.workspace_id == workspace_id, model.id == row_id)
        )
        if row is None:
            raise AppError("TENANT_REFERENCE_NOT_FOUND", "워크스페이스 참조가 없습니다.", 404)
        return row

    async def _record(
        self,
        principal: Principal,
        *,
        action: str,
        target_type: str,
        target_id: UUID,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        await append_audit_log(
            self.session,
            workspace_id=principal.workspace_id,
            actor_id=principal.subject_id,
            action=action,
            target_type=target_type,
            target_id=str(target_id),
            details=payload,
        )
        await add_outbox_event(
            self.session,
            workspace_id=principal.workspace_id,
            aggregate_type=target_type,
            aggregate_id=str(target_id),
            event_type=event_type,
            schema_version=OUTBOX_SCHEMA_VERSION,
            payload=payload,
        )


def _metric_snapshot(row: AnalyticsMetricDefinition) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "key": row.key,
        "version": row.version,
        "subject": row.subject,
        "unit": row.unit,
        "value_kind": row.value_kind,
        "formula": row.formula,
        "source_provider": row.source_provider,
        "source_field": row.source_field,
        "source_contract_version": row.source_contract_version,
        "latency": row.latency,
        "supported_dimensions": row.supported_dimensions,
        "caveats": row.caveats,
        "definition_hash": row.definition_hash,
    }


def _require_same_request(existing_hash: str, request_hash: str) -> None:
    if existing_hash != request_hash:
        raise AppError(
            code="IDEMPOTENCY_KEY_REUSED",
            message="같은 멱등 키를 다른 분석 요청에 재사용할 수 없습니다.",
            status_code=409,
        )


def _finalize_cancelled_run(row: AnalyticsSyncRun | AnalyticsReportRun) -> None:
    ensure_job_transition(JobState(row.state), JobState.CANCELLED)
    row.state = JobState.CANCELLED.value
    row.error_code = None
    row.error_detail = None
    row.finished_at = datetime.now(UTC)


def _field_required(field: str) -> AppError:
    return AppError(
        code="ANALYTICS_FIELD_REQUIRED",
        message="분석 사실 유형에 필요한 필드가 없습니다.",
        status_code=422,
        fields=[{"path": field, "reason": "required"}],
    )


def _optional_uuid(value: object | None) -> UUID | None:
    return None if value is None else UUID(str(value))
