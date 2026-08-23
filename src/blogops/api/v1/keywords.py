"""Keyword research, metrics, clustering and strategy-support API."""

from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.core.context import Principal
from blogops.core.permissions import Permission, require_permissions
from blogops.db.session import get_tenant_session
from blogops.domain.keywords.enums import KeywordIntent, ProviderKind
from blogops.domain.keywords.schemas import (
    AlertRuleCreate,
    ClusterRequest,
    ClusterResponse,
    CollectionCreate,
    CollectionMemberRequest,
    ContentLinkCreate,
    ContentLinkView,
    ContentOverlapRequest,
    ContentOverlapResponse,
    ExportRequest,
    IntentUpdateRequest,
    KeywordCompareRequest,
    KeywordCompareResponse,
    KeywordImportRequest,
    KeywordJobItemView,
    KeywordJobView,
    KeywordListResponse,
    KeywordMetricsResponse,
    KeywordView,
    MetricSnapshotView,
    ProviderConnectionCreate,
    ProviderConnectionView,
    ProviderStatusView,
    ResearchRequest,
    SavedViewCreate,
    ScoreProfileCreate,
    ScoreProfileView,
    TrendSummary,
)
from blogops.domain.keywords.services import (
    add_collection_member,
    analyze_content_overlap,
    compare_keywords,
    create_alert_rule,
    create_collection,
    create_import_job,
    create_keyword_clusters,
    create_provider_connection,
    create_research_job,
    create_score_profile,
    export_keywords,
    get_keyword_metrics,
    get_research_job,
    keyword_trend_summary,
    link_keyword_content,
    list_keywords,
    list_provider_connections,
    list_research_items,
    provider_statuses,
    request_cancel,
    request_retry,
    save_keyword_view,
    update_keyword_intent,
)
from blogops.domain.keywords.tasks import enqueue_keyword_job

router = APIRouter(prefix="/keywords", tags=["keywords"])
TenantSession = Annotated[AsyncSession, Depends(get_tenant_session)]
KeywordReader = Annotated[Principal, Depends(require_permissions(Permission.KEYWORD_READ))]
KeywordWriter = Annotated[Principal, Depends(require_permissions(Permission.KEYWORD_WRITE))]
KeywordExporter = Annotated[
    Principal, Depends(require_permissions(Permission.KEYWORD_READ, Permission.KEYWORD_EXPORT))
]
KeywordProviderManager = Annotated[
    Principal, Depends(require_permissions(Permission.KEYWORD_WRITE, Permission.API_MANAGE))
]


@router.post(
    "/provider-connections",
    response_model=ProviderConnectionView,
    status_code=status.HTTP_201_CREATED,
)
async def register_provider_connection(
    data: ProviderConnectionCreate,
    session: TenantSession,
    principal: KeywordProviderManager,
) -> ProviderConnectionView:
    connection = await create_provider_connection(session, principal=principal, data=data)
    return ProviderConnectionView.model_validate(connection)


@router.get("/provider-connections", response_model=list[ProviderConnectionView])
async def get_provider_connections(
    session: TenantSession, principal: KeywordReader
) -> list[ProviderConnectionView]:
    return [
        ProviderConnectionView.model_validate(item)
        for item in await list_provider_connections(session, principal.workspace_id)
    ]


@router.get("/provider-status", response_model=list[ProviderStatusView])
async def get_provider_statuses(
    session: TenantSession, principal: KeywordProviderManager
) -> list[ProviderStatusView]:
    return [
        ProviderStatusView.model_validate(item)
        for item in await provider_statuses(session, principal.workspace_id)
    ]


@router.post("/research", response_model=KeywordJobView, status_code=status.HTTP_202_ACCEPTED)
async def research_keywords(
    data: ResearchRequest,
    background_tasks: BackgroundTasks,
    session: TenantSession,
    principal: KeywordWriter,
) -> KeywordJobView:
    job, enqueue_needed = await create_research_job(session, principal=principal, data=data)
    if enqueue_needed:
        background_tasks.add_task(enqueue_keyword_job, principal.workspace_id, job.id)
    return KeywordJobView.model_validate(job)


@router.post("/import", response_model=KeywordJobView, status_code=status.HTTP_202_ACCEPTED)
async def import_keywords(
    data: KeywordImportRequest,
    background_tasks: BackgroundTasks,
    session: TenantSession,
    principal: KeywordWriter,
) -> KeywordJobView:
    job, enqueue_needed = await create_import_job(session, principal=principal, data=data)
    if enqueue_needed:
        background_tasks.add_task(enqueue_keyword_job, principal.workspace_id, job.id)
    return KeywordJobView.model_validate(job)


@router.get("/jobs/{job_id}", response_model=KeywordJobView)
async def get_keyword_job(
    job_id: UUID, session: TenantSession, principal: KeywordReader
) -> KeywordJobView:
    return KeywordJobView.model_validate(
        await get_research_job(session, principal.workspace_id, job_id)
    )


@router.get("/jobs/{job_id}/items", response_model=list[KeywordJobItemView])
async def get_keyword_job_items(
    job_id: UUID, session: TenantSession, principal: KeywordReader
) -> list[KeywordJobItemView]:
    return [
        KeywordJobItemView.model_validate(item)
        for item in await list_research_items(session, principal.workspace_id, job_id)
    ]


@router.post("/jobs/{job_id}/cancel", response_model=KeywordJobView, status_code=202)
async def cancel_keyword_job(
    job_id: UUID, session: TenantSession, principal: KeywordWriter
) -> KeywordJobView:
    return KeywordJobView.model_validate(
        await request_cancel(session, principal=principal, job_id=job_id)
    )


@router.post("/jobs/{job_id}/retry", response_model=KeywordJobView, status_code=202)
async def retry_keyword_job(
    job_id: UUID,
    background_tasks: BackgroundTasks,
    session: TenantSession,
    principal: KeywordWriter,
) -> KeywordJobView:
    job = await request_retry(session, principal=principal, job_id=job_id)
    background_tasks.add_task(enqueue_keyword_job, principal.workspace_id, job.id)
    return KeywordJobView.model_validate(job)


@router.post("/cluster", response_model=ClusterResponse)
async def cluster_keyword_set(
    data: ClusterRequest, session: TenantSession, principal: KeywordWriter
) -> ClusterResponse:
    return ClusterResponse.model_validate(
        await create_keyword_clusters(session, principal=principal, data=data)
    )


@router.post("/compare", response_model=KeywordCompareResponse)
async def compare_keyword_set(
    data: KeywordCompareRequest, session: TenantSession, principal: KeywordReader
) -> KeywordCompareResponse:
    return KeywordCompareResponse.model_validate(
        await compare_keywords(
            session,
            workspace_id=principal.workspace_id,
            keyword_ids=data.keyword_ids,
            provider=data.provider.value if data.provider else None,
            allow_stale=data.allow_stale,
        )
    )


@router.post("/score-profiles", response_model=ScoreProfileView, status_code=201)
async def add_keyword_score_profile(
    data: ScoreProfileCreate, session: TenantSession, principal: KeywordWriter
) -> ScoreProfileView:
    return ScoreProfileView.model_validate(
        await create_score_profile(session, principal=principal, data=data)
    )


@router.post("/saved-views", status_code=201)
async def add_keyword_saved_view(
    data: SavedViewCreate, session: TenantSession, principal: KeywordWriter
) -> dict[str, Any]:
    view = await save_keyword_view(session, principal=principal, data=data)
    return {"id": view.id, "name": view.name, "filters": view.filters_json, "sort": view.sort_json}


@router.post("/collections", status_code=201)
async def add_keyword_collection(
    data: CollectionCreate, session: TenantSession, principal: KeywordWriter
) -> dict[str, Any]:
    collection = await create_collection(session, principal=principal, data=data)
    return {"id": collection.id, "name": collection.name, "kind": collection.kind}


@router.post("/collections/{collection_id}/members", status_code=201)
async def add_keyword_to_collection(
    collection_id: UUID,
    data: CollectionMemberRequest,
    session: TenantSession,
    principal: KeywordWriter,
) -> dict[str, UUID]:
    member = await add_collection_member(
        session,
        principal=principal,
        collection_id=collection_id,
        keyword_id=data.keyword_id,
    )
    return {"id": member.id, "keyword_id": member.keyword_id}


@router.post("/alerts", status_code=201)
async def add_keyword_alert(
    data: AlertRuleCreate, session: TenantSession, principal: KeywordWriter
) -> dict[str, Any]:
    rule = await create_alert_rule(session, principal=principal, data=data)
    return {
        "id": rule.id,
        "keyword_id": rule.keyword_id,
        "kinds": rule.kinds_json,
        "channels": rule.channels_json,
        "next_evaluate_at": rule.next_evaluate_at,
    }


@router.post("/content-links", response_model=ContentLinkView, status_code=201)
async def add_keyword_content_link(
    data: ContentLinkCreate, session: TenantSession, principal: KeywordWriter
) -> ContentLinkView:
    return ContentLinkView.model_validate(
        await link_keyword_content(session, principal=principal, data=data)
    )


@router.post("/content-overlap", response_model=ContentOverlapResponse)
async def inspect_keyword_content_overlap(
    data: ContentOverlapRequest, session: TenantSession, principal: KeywordReader
) -> ContentOverlapResponse:
    return ContentOverlapResponse.model_validate(
        await analyze_content_overlap(
            session, workspace_id=principal.workspace_id, keyword_ids=data.keyword_ids
        )
    )


@router.post("/export")
async def download_keywords(
    data: ExportRequest, session: TenantSession, principal: KeywordExporter
) -> Response:
    content, media_type, filename = await export_keywords(
        session,
        principal=principal,
        keyword_ids=data.keyword_ids,
        export_format=data.format,
    )
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("", response_model=KeywordListResponse)
async def get_keywords(
    session: TenantSession,
    principal: KeywordReader,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: UUID | None = None,
    intent: KeywordIntent | None = None,
    region: Annotated[str | None, Query(min_length=2, max_length=32)] = None,
    excluded: bool | None = None,
    q: Annotated[str | None, Query(min_length=1, max_length=1_000)] = None,
) -> KeywordListResponse:
    items = await list_keywords(
        session,
        workspace_id=principal.workspace_id,
        limit=limit + 1,
        cursor=cursor,
        intent=intent.value if intent else None,
        region=region,
        excluded=excluded,
        query=q,
    )
    return KeywordListResponse(
        items=[KeywordView.model_validate(item) for item in items[:limit]],
        next_cursor=items[limit - 1].id if len(items) > limit else None,
    )


@router.get("/{keyword_id}/metrics", response_model=KeywordMetricsResponse)
async def get_metrics(
    keyword_id: UUID,
    session: TenantSession,
    principal: KeywordReader,
    provider: ProviderKind | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> KeywordMetricsResponse:
    keyword, snapshots, connections = await get_keyword_metrics(
        session,
        workspace_id=principal.workspace_id,
        keyword_id=keyword_id,
        provider=provider.value if provider else None,
        limit=limit,
    )
    now = datetime.now(UTC)
    views: list[MetricSnapshotView] = []
    for snapshot in snapshots:
        connection = (
            connections.get(snapshot.provider_connection_id)
            if snapshot.provider_connection_id
            else None
        )
        views.append(
            MetricSnapshotView.model_validate(snapshot).model_copy(
                update={
                    "is_cached": True,
                    "is_stale": snapshot.expires_at <= now,
                    "quota_remaining": connection.quota_remaining if connection else None,
                    "quota_reset_at": connection.quota_reset_at if connection else None,
                }
            )
        )
    return KeywordMetricsResponse(keyword=KeywordView.model_validate(keyword), snapshots=views)


@router.get("/{keyword_id}/trend", response_model=TrendSummary)
async def get_keyword_trend(
    keyword_id: UUID,
    session: TenantSession,
    principal: KeywordReader,
    provider: ProviderKind | None = None,
) -> TrendSummary:
    trend = await keyword_trend_summary(
        session,
        workspace_id=principal.workspace_id,
        keyword_id=keyword_id,
        provider=provider.value if provider else None,
    )
    return TrendSummary(
        direction=trend.direction.value,
        growth_rate=trend.growth_rate,
        volatility=trend.volatility,
        peak_periods=list(trend.peak_periods),
        trough_periods=list(trend.trough_periods),
        seasonal=trend.seasonal,
        confidence=trend.confidence,
    )


@router.patch("/{keyword_id}/intent", response_model=KeywordView)
async def revise_keyword_intent(
    keyword_id: UUID,
    data: IntentUpdateRequest,
    session: TenantSession,
    principal: KeywordWriter,
) -> KeywordView:
    return KeywordView.model_validate(
        await update_keyword_intent(
            session,
            principal=principal,
            keyword_id=keyword_id,
            intent=data.intent,
            reason=data.reason,
        )
    )
