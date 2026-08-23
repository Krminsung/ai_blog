"""Version-pinned, approval-gated content repurposing API."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.core.context import Principal
from blogops.core.permissions import Permission, require_permissions
from blogops.db.session import get_tenant_session
from blogops.domain.repurpose.providers import (
    BudgetAuthorizationGateway,
    FailClosedRepurposeBudgetGateway,
    FailClosedRepurposeExportStore,
    RepurposeExportStore,
)
from blogops.domain.repurpose.schemas import (
    ChannelTemplateCreate,
    ChannelTemplateRead,
    ChannelTemplateVersionCreate,
    ChannelTemplateVersionRead,
    RepurposeApprovalCreate,
    RepurposeApprovalRead,
    RepurposeDeliveryCreate,
    RepurposeDeliveryRead,
    RepurposeExportCreate,
    RepurposeExportRead,
    RepurposeJobCommandCreate,
    RepurposeJobCreate,
    RepurposeJobItemRead,
    RepurposeJobRead,
    RepurposeVariantRead,
)
from blogops.domain.repurpose.service import RepurposeService
from blogops.domain.repurpose.tasks import enqueue_repurpose_job


router = APIRouter(prefix="/repurpose", tags=["repurpose"])
TenantSession = Annotated[AsyncSession, Depends(get_tenant_session)]
Reader = Annotated[Principal, Depends(require_permissions(Permission.CONTENT_READ))]
Writer = Annotated[Principal, Depends(require_permissions(Permission.CONTENT_WRITE))]
Approver = Annotated[Principal, Depends(require_permissions(Permission.CONTENT_APPROVE))]
Publisher = Annotated[Principal, Depends(require_permissions(Permission.CONTENT_PUBLISH))]
IdempotencyKey = Annotated[
    str, Header(alias="Idempotency-Key", min_length=1, max_length=255)
]


def repurpose_budget_gateway() -> BudgetAuthorizationGateway:
    """Production wiring must override this with the billing reservation adapter."""

    return FailClosedRepurposeBudgetGateway()


def repurpose_export_store() -> RepurposeExportStore:
    """Production wiring must override this with a private object store."""

    return FailClosedRepurposeExportStore()


BudgetGateway = Annotated[
    BudgetAuthorizationGateway, Depends(repurpose_budget_gateway)
]
ExportStore = Annotated[RepurposeExportStore, Depends(repurpose_export_store)]


def repurpose_service(
    session: TenantSession,
    budget_gateway: BudgetGateway,
    export_store: ExportStore,
) -> RepurposeService:
    return RepurposeService(
        session,
        budget_gateway=budget_gateway,
        export_store=export_store,
    )


Service = Annotated[RepurposeService, Depends(repurpose_service)]


@router.post(
    "/templates",
    response_model=ChannelTemplateRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_template(
    data: ChannelTemplateCreate, principal: Approver, service: Service
) -> ChannelTemplateRead:
    return ChannelTemplateRead.model_validate(
        await service.create_template(principal, data)
    )


@router.get("/templates", response_model=list[ChannelTemplateRead])
async def list_templates(
    principal: Reader, service: Service
) -> list[ChannelTemplateRead]:
    return [
        ChannelTemplateRead.model_validate(row)
        for row in await service.list_templates(principal)
    ]


@router.post(
    "/templates/{template_id}/versions",
    response_model=ChannelTemplateVersionRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_template_version(
    template_id: UUID,
    data: ChannelTemplateVersionCreate,
    principal: Approver,
    service: Service,
) -> ChannelTemplateVersionRead:
    return ChannelTemplateVersionRead.model_validate(
        await service.create_template_version(principal, template_id, data)
    )


@router.get(
    "/template-versions/{version_id}", response_model=ChannelTemplateVersionRead
)
async def get_template_version(
    version_id: UUID, principal: Reader, service: Service
) -> ChannelTemplateVersionRead:
    return ChannelTemplateVersionRead.model_validate(
        await service.get_template_version(principal, version_id)
    )


@router.post(
    "/jobs", response_model=RepurposeJobRead, status_code=status.HTTP_202_ACCEPTED
)
async def create_job(
    data: RepurposeJobCreate,
    principal: Writer,
    service: Service,
    background_tasks: BackgroundTasks,
    idempotency_key: IdempotencyKey,
    response: Response,
) -> RepurposeJobRead:
    row, created = await service.create_job(
        principal, data, idempotency_key=idempotency_key
    )
    if created:
        background_tasks.add_task(enqueue_repurpose_job, principal.workspace_id, row.id)
    response.headers["Idempotency-Replayed"] = "false" if created else "true"
    return RepurposeJobRead.model_validate(row)


@router.get("/jobs/{job_id}", response_model=RepurposeJobRead)
async def get_job(
    job_id: UUID, principal: Reader, service: Service
) -> RepurposeJobRead:
    return RepurposeJobRead.model_validate(await service.get_job(principal, job_id))


@router.get("/jobs/{job_id}/items", response_model=list[RepurposeJobItemRead])
async def list_job_items(
    job_id: UUID, principal: Reader, service: Service
) -> list[RepurposeJobItemRead]:
    return [
        RepurposeJobItemRead.model_validate(row)
        for row in await service.job_items(principal, job_id)
    ]


@router.get("/jobs/{job_id}/variants", response_model=list[RepurposeVariantRead])
async def list_job_variants(
    job_id: UUID, principal: Reader, service: Service
) -> list[RepurposeVariantRead]:
    return [
        RepurposeVariantRead.model_validate(row)
        for row in await service.job_variants(principal, job_id)
    ]


@router.post("/jobs/{job_id}/commands", response_model=RepurposeJobRead)
async def command_job(
    job_id: UUID,
    data: RepurposeJobCommandCreate,
    principal: Writer,
    service: Service,
    background_tasks: BackgroundTasks,
    idempotency_key: IdempotencyKey,
) -> RepurposeJobRead:
    row = await service.command_job(
        principal, job_id, data, idempotency_key=idempotency_key
    )
    background_tasks.add_task(enqueue_repurpose_job, principal.workspace_id, row.id)
    return RepurposeJobRead.model_validate(row)


@router.post(
    "/variants/{variant_id}/approvals",
    response_model=RepurposeApprovalRead,
    status_code=status.HTTP_201_CREATED,
)
async def approve_variant(
    variant_id: UUID,
    data: RepurposeApprovalCreate,
    principal: Approver,
    service: Service,
) -> RepurposeApprovalRead:
    return RepurposeApprovalRead.model_validate(
        await service.approve_variant(principal, variant_id, data)
    )


@router.post(
    "/variants/{variant_id}/exports",
    response_model=RepurposeExportRead,
    status_code=status.HTTP_201_CREATED,
)
async def export_variant(
    variant_id: UUID,
    data: RepurposeExportCreate,
    principal: Reader,
    service: Service,
) -> RepurposeExportRead:
    return RepurposeExportRead.model_validate(
        await service.export_variant(principal, variant_id, data)
    )


@router.post(
    "/variants/{variant_id}/deliveries",
    response_model=RepurposeDeliveryRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def deliver_variant(
    variant_id: UUID,
    data: RepurposeDeliveryCreate,
    principal: Publisher,
    service: Service,
    idempotency_key: IdempotencyKey,
    response: Response,
) -> RepurposeDeliveryRead:
    row, created = await service.deliver_variant(
        principal, variant_id, data, idempotency_key=idempotency_key
    )
    response.headers["Idempotency-Replayed"] = "false" if created else "true"
    return RepurposeDeliveryRead.model_validate(row)
