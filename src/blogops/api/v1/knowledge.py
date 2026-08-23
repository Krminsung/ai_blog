"""Knowledge source, upload, sync, deletion and search API."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.core.config import get_settings
from blogops.core.context import Principal
from blogops.core.permissions import Permission, require_permissions
from blogops.db.session import get_tenant_session
from blogops.domain.knowledge.adapters import HashingEmbeddingProvider
from blogops.domain.knowledge.schemas import (
    KnowledgeJobResponse,
    SearchResponse,
    SourceCreate,
    SourceListResponse,
    SourceResponse,
    SourceVersionResponse,
    UploadCompleteRequest,
    UploadInitiateRequest,
    UploadInitiateResponse,
)
from blogops.domain.knowledge.services import (
    complete_file_upload,
    create_source,
    get_knowledge_job,
    get_source,
    initiate_file_upload,
    list_sources,
    list_source_versions,
    request_delete,
    request_sync,
    search_knowledge,
)
from blogops.domain.knowledge.storage import ObjectStorage, get_object_storage
from blogops.domain.knowledge.tasks import enqueue_knowledge_job

router = APIRouter(prefix="/knowledge", tags=["knowledge"])
TenantSession = Annotated[AsyncSession, Depends(get_tenant_session)]
KnowledgeReader = Annotated[
    Principal, Depends(require_permissions(Permission.KNOWLEDGE_READ))
]
KnowledgeWriter = Annotated[
    Principal, Depends(require_permissions(Permission.KNOWLEDGE_WRITE))
]


@router.post("/sources", response_model=SourceResponse, status_code=status.HTTP_201_CREATED)
async def create_knowledge_source(
    data: SourceCreate,
    background_tasks: BackgroundTasks,
    session: TenantSession,
    principal: KnowledgeWriter,
) -> SourceResponse:
    source, job = await create_source(session, principal=principal, data=data)
    if job is not None:
        background_tasks.add_task(enqueue_knowledge_job, principal.workspace_id, job.id)
    return SourceResponse.model_validate(source)


@router.get("/sources", response_model=SourceListResponse)
async def get_knowledge_sources(
    session: TenantSession,
    principal: KnowledgeReader,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: UUID | None = None,
) -> SourceListResponse:
    items = await list_sources(session, principal.workspace_id, limit=limit + 1, cursor=cursor)
    next_cursor = items[limit - 1].id if len(items) > limit else None
    return SourceListResponse(
        items=[SourceResponse.model_validate(item) for item in items[:limit]],
        next_cursor=next_cursor,
    )


@router.get("/sources/{source_id}", response_model=SourceResponse)
async def get_knowledge_source(
    source_id: UUID, session: TenantSession, principal: KnowledgeReader
) -> SourceResponse:
    source = await get_source(session, principal.workspace_id, source_id)
    return SourceResponse.model_validate(source)


@router.get("/sources/{source_id}/versions", response_model=list[SourceVersionResponse])
async def get_knowledge_source_versions(
    source_id: UUID, session: TenantSession, principal: KnowledgeReader
) -> list[SourceVersionResponse]:
    versions = await list_source_versions(session, principal.workspace_id, source_id)
    return [SourceVersionResponse.model_validate(item) for item in versions]


@router.post(
    "/files", response_model=UploadInitiateResponse, status_code=status.HTTP_201_CREATED
)
@router.post(
    "/files/initiate",
    response_model=UploadInitiateResponse,
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,
)
async def initiate_knowledge_file(
    data: UploadInitiateRequest,
    session: TenantSession,
    principal: KnowledgeWriter,
    storage: Annotated[ObjectStorage, Depends(get_object_storage)],
) -> UploadInitiateResponse:
    source, grant = await initiate_file_upload(
        session,
        principal=principal,
        data=data,
        storage=storage,
        max_size=get_settings().knowledge_max_upload_bytes,
    )
    return UploadInitiateResponse(
        source_id=source.id,
        object_key=grant.object_key,
        upload_url=grant.upload_url,
        expires_in=grant.expires_in,
    )


@router.post("/files/{source_id}/complete", response_model=KnowledgeJobResponse)
async def complete_knowledge_file(
    source_id: UUID,
    data: UploadCompleteRequest,
    background_tasks: BackgroundTasks,
    session: TenantSession,
    principal: KnowledgeWriter,
    storage: Annotated[ObjectStorage, Depends(get_object_storage)],
) -> KnowledgeJobResponse:
    job = await complete_file_upload(
        session,
        principal=principal,
        source_id=source_id,
        object_key=data.object_key,
        content_hash=data.content_hash,
        storage=storage,
    )
    background_tasks.add_task(enqueue_knowledge_job, principal.workspace_id, job.id)
    return KnowledgeJobResponse(job_id=job.id, state=job.state)


@router.get("/jobs/{job_id}", response_model=KnowledgeJobResponse)
async def get_knowledge_job_status(
    job_id: UUID, session: TenantSession, principal: KnowledgeReader
) -> KnowledgeJobResponse:
    job = await get_knowledge_job(session, principal.workspace_id, job_id)
    return KnowledgeJobResponse(
        job_id=job.id,
        state=job.state,
        error_code=job.error_code,
        result=job.result_json,
    )


@router.post("/sources/{source_id}/sync", response_model=KnowledgeJobResponse, status_code=202)
async def sync_knowledge_source(
    source_id: UUID,
    background_tasks: BackgroundTasks,
    session: TenantSession,
    principal: KnowledgeWriter,
) -> KnowledgeJobResponse:
    job = await request_sync(session, principal=principal, source_id=source_id)
    background_tasks.add_task(enqueue_knowledge_job, principal.workspace_id, job.id)
    return KnowledgeJobResponse(job_id=job.id, state=job.state)


@router.delete("/sources/{source_id}", response_model=KnowledgeJobResponse, status_code=202)
async def delete_knowledge_source(
    source_id: UUID,
    background_tasks: BackgroundTasks,
    session: TenantSession,
    principal: KnowledgeWriter,
) -> KnowledgeJobResponse:
    job = await request_delete(session, principal=principal, source_id=source_id)
    background_tasks.add_task(enqueue_knowledge_job, principal.workspace_id, job.id)
    return KnowledgeJobResponse(job_id=job.id, state=job.state)


@router.get("/search", response_model=SearchResponse)
async def search_sources(
    session: TenantSession,
    principal: KnowledgeReader,
    q: Annotated[str, Query(min_length=2, max_length=300)],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> SearchResponse:
    return SearchResponse(
        items=await search_knowledge(
            session,
            workspace_id=principal.workspace_id,
            query=q,
            limit=limit,
            embeddings=HashingEmbeddingProvider(),
        )
    )
