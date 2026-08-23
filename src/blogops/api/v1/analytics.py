"""Evidence-backed analytics, insight, experiment, and report API."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.core.context import Principal
from blogops.core.permissions import Permission, require_permissions
from blogops.db.session import get_tenant_session
from blogops.domain.analytics.schemas import (
    AnalyticsConnectionCreate,
    AnalyticsConnectionRead,
    AnalyticsSyncCreate,
    AnalyticsSyncRead,
    ComparisonSnapshotCreate,
    ConversionCreate,
    ConversionRead,
    EvidenceBatchCreate,
    EvidenceBatchRead,
    ExperimentCreate,
    ExperimentResultCreate,
    JobCommandCreate,
    ManualMetricFactCreate,
    MetricDefinitionCreate,
    MetricDefinitionRead,
    OperationalSnapshotCreate,
    RecommendationCreate,
    RecommendationDecisionCreate,
    RecommendationRead,
    ReportDefinitionCreate,
    ReportRunCreate,
    ReportRunRead,
    ROISnapshotCreate,
    TrackingClickCreate,
    TrackingLinkCreate,
    TrackingLinkCreated,
)
from blogops.domain.analytics.service import AnalyticsService
from blogops.domain.analytics.tasks import enqueue_analytics_report, enqueue_analytics_sync


router = APIRouter(prefix="/analytics", tags=["analytics"])
TenantSession = Annotated[AsyncSession, Depends(get_tenant_session)]
Reader = Annotated[Principal, Depends(require_permissions(Permission.CONTENT_READ))]
Writer = Annotated[Principal, Depends(require_permissions(Permission.CONTENT_WRITE))]
Approver = Annotated[Principal, Depends(require_permissions(Permission.CONTENT_APPROVE))]
Manager = Annotated[
    Principal,
    Depends(require_permissions(Permission.WORKSPACE_MANAGE, Permission.API_MANAGE)),
]
IdempotencyKey = Annotated[
    str, Header(alias="Idempotency-Key", min_length=1, max_length=255)
]


def analytics_service(session: TenantSession) -> AnalyticsService:
    return AnalyticsService(session)


Service = Annotated[AnalyticsService, Depends(analytics_service)]


@router.post(
    "/connections",
    response_model=AnalyticsConnectionRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_connection(
    data: AnalyticsConnectionCreate, principal: Manager, service: Service
) -> AnalyticsConnectionRead:
    return AnalyticsConnectionRead.model_validate(
        await service.create_connection(principal, data)
    )


@router.get("/connections", response_model=list[AnalyticsConnectionRead])
async def list_connections(
    principal: Reader, service: Service
) -> list[AnalyticsConnectionRead]:
    return [
        AnalyticsConnectionRead.model_validate(row)
        for row in await service.list_connections(principal)
    ]


@router.post(
    "/metric-definitions",
    response_model=MetricDefinitionRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_metric_definition(
    data: MetricDefinitionCreate, principal: Manager, service: Service
) -> MetricDefinitionRead:
    return MetricDefinitionRead.model_validate(
        await service.create_metric_definition(principal, data)
    )


@router.post(
    "/sync-runs", response_model=AnalyticsSyncRead, status_code=status.HTTP_202_ACCEPTED
)
async def create_sync(
    data: AnalyticsSyncCreate,
    principal: Writer,
    service: Service,
    background_tasks: BackgroundTasks,
    idempotency_key: IdempotencyKey,
    response: Response,
) -> AnalyticsSyncRead:
    row, created = await service.create_sync(
        principal, data, idempotency_key=idempotency_key
    )
    if created:
        background_tasks.add_task(enqueue_analytics_sync, principal.workspace_id, row.id)
    response.headers["Idempotency-Replayed"] = "false" if created else "true"
    return AnalyticsSyncRead.model_validate(row)


@router.get("/sync-runs/{run_id}", response_model=AnalyticsSyncRead)
async def get_sync(
    run_id: UUID, principal: Reader, service: Service
) -> AnalyticsSyncRead:
    return AnalyticsSyncRead.model_validate(await service.get_sync(principal, run_id))


@router.post("/sync-runs/{run_id}/commands", response_model=AnalyticsSyncRead)
async def command_sync(
    run_id: UUID,
    data: JobCommandCreate,
    principal: Writer,
    service: Service,
    background_tasks: BackgroundTasks,
    idempotency_key: IdempotencyKey,
) -> AnalyticsSyncRead:
    row = await service.command_sync(
        principal, run_id, data, idempotency_key=idempotency_key
    )
    background_tasks.add_task(enqueue_analytics_sync, principal.workspace_id, row.id)
    return AnalyticsSyncRead.model_validate(row)


@router.post("/report-runs/{run_id}/commands", response_model=ReportRunRead)
async def command_report(
    run_id: UUID,
    data: JobCommandCreate,
    principal: Writer,
    service: Service,
    background_tasks: BackgroundTasks,
    idempotency_key: IdempotencyKey,
) -> ReportRunRead:
    row = await service.command_report(
        principal, run_id, data, idempotency_key=idempotency_key
    )
    background_tasks.add_task(enqueue_analytics_report, principal.workspace_id, row.id)
    return ReportRunRead.model_validate(row)


@router.post(
    "/evidence-batches",
    response_model=EvidenceBatchRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_evidence_batch(
    data: EvidenceBatchCreate, principal: Writer, service: Service
) -> EvidenceBatchRead:
    return EvidenceBatchRead.model_validate(
        await service.create_evidence_batch(principal, data)
    )


@router.post("/manual-facts", status_code=status.HTTP_201_CREATED)
async def append_manual_fact(
    data: ManualMetricFactCreate, principal: Writer, service: Service
) -> dict[str, str]:
    row = await service.append_manual_fact(principal, data)
    return {"id": str(row.id), "evidence_hash": row.evidence_hash}


@router.get("/content/{content_id}/facts")
async def list_content_facts(
    content_id: UUID,
    principal: Reader,
    service: Service,
    limit: Annotated[int, Query(ge=1, le=1_000)] = 500,
) -> list[dict[str, Any]]:
    return [
        {
            "id": str(row.id),
            "content_id": str(row.content_id),
            "metric_definition_id": str(row.metric_definition_id),
            "fact_date": row.fact_date.isoformat(),
            "source": row.source,
            "value": str(row.value),
            "value_kind": row.value_kind,
            "dimensions": row.dimensions,
            "source_delay": row.source_delay,
            "evidence_hash": row.evidence_hash,
        }
        for row in await service.list_content_facts(principal, content_id, limit=limit)
    ]


@router.post(
    "/tracking-links",
    response_model=TrackingLinkCreated,
    status_code=status.HTTP_201_CREATED,
)
async def create_tracking_link(
    data: TrackingLinkCreate, principal: Writer, service: Service
) -> TrackingLinkCreated:
    row, token, redirect = await service.create_tracking_link(principal, data)
    return TrackingLinkCreated(
        id=row.id, token=token, redirect_url=redirect, expires_at=row.expires_at
    )


@router.post("/tracking-links/resolve/{token}", status_code=status.HTTP_201_CREATED)
async def record_tracking_click(
    token: str,
    data: TrackingClickCreate,
    principal: Reader,
    service: Service,
) -> dict[str, str]:
    event, redirect = await service.record_click(principal, token, data)
    return {"event_id": str(event.id), "redirect_url": redirect}


@router.post(
    "/conversions", response_model=ConversionRead, status_code=status.HTTP_201_CREATED
)
async def record_conversion(
    data: ConversionCreate,
    principal: Writer,
    service: Service,
    response: Response,
) -> ConversionRead:
    row, created = await service.record_conversion(principal, data)
    response.headers["Idempotency-Replayed"] = "false" if created else "true"
    return ConversionRead.model_validate(row)


@router.post("/operational-snapshots", status_code=status.HTTP_201_CREATED)
async def create_operational_snapshot(
    data: OperationalSnapshotCreate, principal: Writer, service: Service
) -> dict[str, str]:
    row = await service.create_operational_snapshot(principal, data)
    return {"id": str(row.id), "snapshot_hash": row.snapshot_hash}


@router.post("/roi-snapshots", status_code=status.HTTP_201_CREATED)
async def create_roi_snapshot(
    data: ROISnapshotCreate, principal: Writer, service: Service
) -> dict[str, str | None]:
    row = await service.create_roi_snapshot(principal, data)
    return {
        "id": str(row.id),
        "snapshot_hash": row.snapshot_hash,
        "net_return": str(row.net_return),
        "roi_ratio": str(row.roi_ratio) if row.roi_ratio is not None else None,
    }


@router.post("/comparison-snapshots", status_code=status.HTTP_201_CREATED)
async def create_comparison_snapshot(
    data: ComparisonSnapshotCreate, principal: Writer, service: Service
) -> dict[str, str]:
    row = await service.create_comparison_snapshot(principal, data)
    return {"id": str(row.id), "snapshot_hash": row.snapshot_hash}


@router.post(
    "/recommendations",
    response_model=RecommendationRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_recommendation(
    data: RecommendationCreate, principal: Writer, service: Service
) -> RecommendationRead:
    return RecommendationRead.model_validate(
        await service.create_recommendation(principal, data)
    )


@router.get(
    "/content/{content_id}/recommendations", response_model=list[RecommendationRead]
)
async def list_recommendations(
    content_id: UUID, principal: Reader, service: Service
) -> list[RecommendationRead]:
    return [
        RecommendationRead.model_validate(row)
        for row in await service.list_recommendations(principal, content_id)
    ]


@router.post("/recommendations/{recommendation_id}/decisions")
async def decide_recommendation(
    recommendation_id: UUID,
    data: RecommendationDecisionCreate,
    principal: Approver,
    service: Service,
) -> dict[str, str]:
    row = await service.decide_recommendation(principal, recommendation_id, data)
    return {"id": str(row.id), "decision": row.decision}


@router.post("/experiments", status_code=status.HTTP_201_CREATED)
async def create_experiment(
    data: ExperimentCreate, principal: Writer, service: Service
) -> dict[str, str]:
    row = await service.create_experiment(principal, data)
    return {"id": str(row.id), "state": row.state}


@router.post("/experiments/{experiment_id}/results", status_code=status.HTTP_201_CREATED)
async def append_experiment_result(
    experiment_id: UUID,
    data: ExperimentResultCreate,
    principal: Writer,
    service: Service,
) -> dict[str, str]:
    row = await service.append_experiment_result(principal, experiment_id, data)
    return {"id": str(row.id), "result_hash": row.result_hash}


@router.post("/report-definitions", status_code=status.HTTP_201_CREATED)
async def create_report_definition(
    data: ReportDefinitionCreate, principal: Manager, service: Service
) -> dict[str, str]:
    row = await service.create_report_definition(principal, data)
    return {"id": str(row.id), "definition_hash": row.definition_hash}


@router.post(
    "/report-definitions/{definition_id}/runs",
    response_model=ReportRunRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_report_run(
    definition_id: UUID,
    data: ReportRunCreate,
    principal: Writer,
    service: Service,
    background_tasks: BackgroundTasks,
    idempotency_key: IdempotencyKey,
    response: Response,
) -> ReportRunRead:
    row, created = await service.create_report_run(
        principal, definition_id, data, idempotency_key=idempotency_key
    )
    if created:
        background_tasks.add_task(enqueue_analytics_report, principal.workspace_id, row.id)
    response.headers["Idempotency-Replayed"] = "false" if created else "true"
    return ReportRunRead.model_validate(row)


@router.get("/report-runs/{run_id}", response_model=ReportRunRead)
async def get_report_run(
    run_id: UUID, principal: Reader, service: Service
) -> ReportRunRead:
    return ReportRunRead.model_validate(await service.get_report_run(principal, run_id))
