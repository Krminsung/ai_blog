"""Canonical content-job, immutable version and library API."""

from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    Query,
    Response,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.core.context import Principal
from blogops.core.permissions import Permission, require_permissions
from blogops.db.session import get_tenant_session
from blogops.domain.generation.providers import (
    BudgetEntitlementGateway,
    FailClosedBudgetEntitlementGateway,
)
from blogops.domain.generation.schemas import (
    CollaborationEventCreate,
    ContentCreate,
    ContentFeedbackCreate,
    ContentJobCreate,
    ContentJobRead,
    ContentRead,
    ContentUpdate,
    ContentVersionCreate,
    ContentVersionRead,
    GenerationStepRead,
    JobCommandRequest,
    RestoreVersionRequest,
    TemplateCreate,
    TemplateRead,
    TemplateVersionCreate,
    TemplateVersionRead,
)
from blogops.domain.generation.service import GenerationService
from blogops.domain.generation.snapshots import SQLAlchemyGenerationSnapshotResolver
from blogops.domain.generation.tasks import enqueue_generation_job


router = APIRouter(tags=["content"])
TenantSession = Annotated[AsyncSession, Depends(get_tenant_session)]
ContentReader = Annotated[Principal, Depends(require_permissions(Permission.CONTENT_READ))]
ContentWriter = Annotated[Principal, Depends(require_permissions(Permission.CONTENT_WRITE))]
IdempotencyKey = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=1, max_length=255),
]


def get_budget_entitlement_gateway() -> BudgetEntitlementGateway:
    """Fail closed until the billing composition root overrides this dependency."""

    return FailClosedBudgetEntitlementGateway()


BudgetGateway = Annotated[
    BudgetEntitlementGateway,
    Depends(get_budget_entitlement_gateway),
]


def generation_service(
    session: TenantSession,
    budget: BudgetGateway,
) -> GenerationService:
    return GenerationService(
        session,
        snapshots=SQLAlchemyGenerationSnapshotResolver(session),
        budget=budget,
    )


Service = Annotated[GenerationService, Depends(generation_service)]


@router.post(
    "/content-jobs",
    response_model=ContentJobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_content_job(
    data: ContentJobCreate,
    principal: ContentWriter,
    service: Service,
    background_tasks: BackgroundTasks,
    idempotency_key: IdempotencyKey,
    response: Response,
) -> ContentJobRead:
    result = await service.create_job(
        principal,
        data,
        idempotency_key=idempotency_key,
    )
    if result.created:
        background_tasks.add_task(
            enqueue_generation_job,
            principal.workspace_id,
            result.job.id,
        )
    response.headers["Idempotency-Replayed"] = "false" if result.created else "true"
    return ContentJobRead.model_validate(result.job)


@router.get("/content-jobs/{job_id}", response_model=ContentJobRead)
async def get_content_job(
    job_id: UUID,
    principal: ContentReader,
    service: Service,
) -> ContentJobRead:
    return ContentJobRead.model_validate(await service.get_job(principal, job_id))


@router.get(
    "/content-jobs/{job_id}/steps",
    response_model=list[GenerationStepRead],
)
async def list_content_job_steps(
    job_id: UUID,
    principal: ContentReader,
    service: Service,
) -> list[GenerationStepRead]:
    rows = await service.list_job_steps(principal, job_id)
    return [GenerationStepRead.model_validate(item) for item in rows]


@router.post("/content-jobs/{job_id}/cancel", response_model=ContentJobRead)
async def cancel_content_job(
    job_id: UUID,
    data: JobCommandRequest,
    principal: ContentWriter,
    service: Service,
    idempotency_key: IdempotencyKey,
) -> ContentJobRead:
    job = await service.cancel_job(
        principal,
        job_id,
        idempotency_key=idempotency_key,
        reason=data.reason,
    )
    return ContentJobRead.model_validate(job)


@router.post("/content-jobs/{job_id}/retry", response_model=ContentJobRead)
async def retry_content_job(
    job_id: UUID,
    data: JobCommandRequest,
    principal: ContentWriter,
    service: Service,
    background_tasks: BackgroundTasks,
    idempotency_key: IdempotencyKey,
) -> ContentJobRead:
    job = await service.retry_job(
        principal,
        job_id,
        idempotency_key=idempotency_key,
        reason=data.reason,
    )
    background_tasks.add_task(enqueue_generation_job, principal.workspace_id, job.id)
    return ContentJobRead.model_validate(job)


@router.post("/content", response_model=ContentRead, status_code=status.HTTP_201_CREATED)
async def create_content(
    data: ContentCreate,
    principal: ContentWriter,
    service: Service,
) -> ContentRead:
    return ContentRead.model_validate(await service.create_content(principal, data))


@router.get("/content", response_model=list[ContentRead])
async def list_contents(
    principal: ContentReader,
    service: Service,
    state_filter: Annotated[str | None, Query(alias="state")] = None,
    content_type: str | None = None,
    brand_id: UUID | None = None,
    author_id: UUID | None = None,
    query: Annotated[str | None, Query(max_length=500)] = None,
    include_archived: bool = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ContentRead]:
    rows = await service.list_contents(
        principal,
        state=state_filter,
        content_type=content_type,
        brand_id=brand_id,
        author_id=author_id,
        query=query,
        include_archived=include_archived,
        limit=limit,
        offset=offset,
    )
    return [ContentRead.model_validate(item) for item in rows]


@router.get("/content/{content_id}", response_model=ContentRead)
async def get_content(
    content_id: UUID,
    principal: ContentReader,
    service: Service,
) -> ContentRead:
    return ContentRead.model_validate(await service.get_content(principal, content_id))


@router.patch("/content/{content_id}", response_model=ContentRead)
async def update_content(
    content_id: UUID,
    data: ContentUpdate,
    principal: ContentWriter,
    service: Service,
) -> ContentRead:
    return ContentRead.model_validate(
        await service.update_content(principal, content_id, data)
    )


@router.delete("/content/{content_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_content(
    content_id: UUID,
    principal: ContentWriter,
    service: Service,
) -> Response:
    await service.soft_delete_content(principal, content_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/content/{content_id}/versions", response_model=list[ContentVersionRead])
async def list_content_versions(
    content_id: UUID,
    principal: ContentReader,
    service: Service,
) -> list[ContentVersionRead]:
    rows = await service.list_versions(principal, content_id)
    return [ContentVersionRead.model_validate(item) for item in rows]


@router.post(
    "/content/{content_id}/versions",
    response_model=ContentVersionRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_content_version(
    content_id: UUID,
    data: ContentVersionCreate,
    principal: ContentWriter,
    service: Service,
) -> ContentVersionRead:
    return ContentVersionRead.model_validate(
        await service.create_version(principal, content_id, data)
    )


@router.get(
    "/content/{content_id}/versions/{version_id}",
    response_model=ContentVersionRead,
)
async def get_content_version(
    content_id: UUID,
    version_id: UUID,
    principal: ContentReader,
    service: Service,
) -> ContentVersionRead:
    return ContentVersionRead.model_validate(
        await service.get_version(principal, content_id, version_id)
    )


@router.post(
    "/content/{content_id}/versions/{version_id}/restore",
    response_model=ContentVersionRead,
    status_code=status.HTTP_201_CREATED,
)
async def restore_content_version(
    content_id: UUID,
    version_id: UUID,
    data: RestoreVersionRequest,
    principal: ContentWriter,
    service: Service,
) -> ContentVersionRead:
    return ContentVersionRead.model_validate(
        await service.restore_version(principal, content_id, version_id, data)
    )


@router.post("/content/{content_id}/feedback", status_code=status.HTTP_201_CREATED)
async def add_content_feedback(
    content_id: UUID,
    data: ContentFeedbackCreate,
    principal: ContentWriter,
    service: Service,
) -> dict[str, str]:
    row = await service.add_feedback(principal, content_id, data)
    return {"id": str(row.id)}


@router.post(
    "/content/{content_id}/collaboration-events",
    status_code=status.HTTP_202_ACCEPTED,
)
async def append_collaboration_event(
    content_id: UUID,
    data: CollaborationEventCreate,
    principal: ContentWriter,
    service: Service,
) -> dict[str, str]:
    row = await service.append_collaboration_event(principal, content_id, data)
    return {"id": str(row.id), "event_kind": row.event_kind}


@router.get("/content/{content_id}/export")
async def export_content(
    content_id: UUID,
    principal: ContentReader,
    service: Service,
    format: Annotated[str, Query(pattern="^(md|html|txt|json)$")] = "md",
) -> Response:
    body, media_type = await service.export_content(
        principal,
        content_id,
        format=format,
    )
    return Response(content=body, media_type=media_type)


@router.post(
    "/content-templates",
    response_model=TemplateRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_template(
    data: TemplateCreate,
    principal: ContentWriter,
    service: Service,
) -> TemplateRead:
    return TemplateRead.model_validate(await service.create_template(principal, data))


@router.post(
    "/content-templates/{template_id}/versions",
    response_model=TemplateVersionRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_template_version(
    template_id: UUID,
    data: TemplateVersionCreate,
    principal: ContentWriter,
    service: Service,
) -> TemplateVersionRead:
    return TemplateVersionRead.model_validate(
        await service.create_template_version(principal, template_id, data)
    )
