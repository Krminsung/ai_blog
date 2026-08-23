"""Canonical media library, image job, planning and license API."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.core.config import get_settings
from blogops.core.context import Principal
from blogops.core.permissions import Permission, require_permissions
from blogops.db.session import get_tenant_session
from blogops.domain.media.providers import MediaBudgetGate
from blogops.domain.media.schemas import (
    ImagePlanCreate,
    ImagePlanItemRead,
    ImagePlanRead,
    ImagePlanWithItems,
    ImageSelection,
    MediaAssetRead,
    MediaDeleteRequest,
    MediaJobRead,
    MediaJobCommandRequest,
    MediaLicenseRead,
    MediaLicenseRevisionCreate,
    MediaLicenseRevisionRead,
    MediaOperationCreate,
    MediaProviderConnectionCreate,
    MediaProviderConnectionRead,
    MediaRestoreVersion,
    MediaSensitiveReview,
    MediaUploadComplete,
    MediaUploadGrant,
    MediaUploadInitiate,
    MediaUsageCreate,
    MediaUsageRead,
    MediaVersionRead,
)
from blogops.domain.media.service import MediaService
from blogops.domain.media.storage import (
    PrivateObjectStorage,
    get_private_object_storage,
)
from blogops.domain.media.tasks import enqueue_media_operation, enqueue_media_upload

router = APIRouter(prefix="/media", tags=["media"])
TenantSession = Annotated[AsyncSession, Depends(get_tenant_session)]
MediaReader = Annotated[Principal, Depends(require_permissions(Permission.MEDIA_READ))]
MediaWriter = Annotated[Principal, Depends(require_permissions(Permission.MEDIA_WRITE))]
MediaManager = Annotated[Principal, Depends(require_permissions(Permission.MEDIA_MANAGE))]
ProviderManager = Annotated[
    Principal,
    Depends(require_permissions(Permission.MEDIA_MANAGE, Permission.API_MANAGE)),
]
Storage = Annotated[PrivateObjectStorage, Depends(get_private_object_storage)]


def media_service(session: TenantSession) -> MediaService:
    return MediaService(session)


def media_budget_gate(session: TenantSession) -> MediaBudgetGate:
    """Build the billing-backed gate in the request's tenant transaction."""

    from blogops.domain.billing.adapters import create_media_budget_gate

    return create_media_budget_gate(session)


Service = Annotated[MediaService, Depends(media_service)]
BudgetGate = Annotated[MediaBudgetGate, Depends(media_budget_gate)]


@router.post(
    "/provider-connections",
    response_model=MediaProviderConnectionRead,
    status_code=status.HTTP_201_CREATED,
)
async def register_media_provider(
    data: MediaProviderConnectionCreate,
    principal: ProviderManager,
    service: Service,
) -> MediaProviderConnectionRead:
    return MediaProviderConnectionRead.model_validate(
        await service.register_provider(principal, data)
    )


@router.get("/provider-connections", response_model=list[MediaProviderConnectionRead])
async def list_media_providers(
    principal: MediaManager,
    service: Service,
) -> list[MediaProviderConnectionRead]:
    return [
        MediaProviderConnectionRead.model_validate(value)
        for value in await service.list_providers(principal)
    ]


@router.post(
    "/uploads",
    response_model=MediaUploadGrant,
    status_code=status.HTTP_201_CREATED,
)
async def initiate_media_upload(
    data: MediaUploadInitiate,
    principal: MediaWriter,
    service: Service,
    storage: Storage,
) -> MediaUploadGrant:
    asset, grant = await service.initiate_upload(
        principal,
        data,
        storage=storage,
        max_upload_bytes=get_settings().knowledge_max_upload_bytes,
    )
    return MediaUploadGrant(
        asset_id=asset.id,
        state=asset.state,
        upload_url=grant.upload_url,
        expires_in=grant.expires_in,
    )


@router.post(
    "/assets/{asset_id}/uploads/complete",
    response_model=MediaAssetRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def complete_media_upload(
    asset_id: UUID,
    data: MediaUploadComplete,
    principal: MediaWriter,
    service: Service,
    storage: Storage,
    background_tasks: BackgroundTasks,
) -> MediaAssetRead:
    asset = await service.complete_upload(principal, asset_id, data, storage=storage)
    if asset.state == "QUARANTINED":
        background_tasks.add_task(
            enqueue_media_upload,
            principal.workspace_id,
            asset.id,
        )
    return MediaAssetRead.model_validate(asset)


@router.post("/assets/{asset_id}/sensitive-review", response_model=MediaAssetRead)
async def review_sensitive_media(
    asset_id: UUID,
    data: MediaSensitiveReview,
    principal: MediaManager,
    service: Service,
    storage: Storage,
) -> MediaAssetRead:
    return MediaAssetRead.model_validate(
        await service.review_sensitive_upload(
            principal,
            asset_id,
            data,
            storage=storage,
        )
    )


@router.get("/assets", response_model=list[MediaAssetRead])
async def list_media_assets(
    principal: MediaReader,
    service: Service,
    asset_state: str | None = Query(default=None, alias="state"),
    folder_path: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[MediaAssetRead]:
    return [
        MediaAssetRead.model_validate(value)
        for value in await service.list_assets(
            principal,
            state=asset_state,
            folder_path=folder_path,
            limit=limit,
            offset=offset,
        )
    ]


@router.get("/assets/{asset_id}", response_model=MediaAssetRead)
async def get_media_asset(
    asset_id: UUID,
    principal: MediaReader,
    service: Service,
) -> MediaAssetRead:
    return MediaAssetRead.model_validate(await service.get_asset(principal, asset_id))


@router.get("/assets/{asset_id}/versions", response_model=list[MediaVersionRead])
async def list_media_versions(
    asset_id: UUID,
    principal: MediaReader,
    service: Service,
) -> list[MediaVersionRead]:
    return [
        MediaVersionRead.model_validate(value)
        for value in await service.list_versions(principal, asset_id)
    ]


@router.post("/assets/{asset_id}/restore", response_model=MediaAssetRead)
async def restore_media_version(
    asset_id: UUID,
    data: MediaRestoreVersion,
    principal: MediaWriter,
    service: Service,
) -> MediaAssetRead:
    return MediaAssetRead.model_validate(
        await service.restore_version(principal, asset_id, data)
    )


@router.post(
    "/assets/{asset_id}/license-revisions",
    response_model=MediaLicenseRevisionRead,
    status_code=status.HTTP_201_CREATED,
)
async def add_media_license_revision(
    asset_id: UUID,
    data: MediaLicenseRevisionCreate,
    principal: MediaManager,
    service: Service,
) -> MediaLicenseRevisionRead:
    _ledger, revision = await service.add_license_revision(principal, asset_id, data)
    return MediaLicenseRevisionRead.model_validate(revision)


@router.get("/assets/{asset_id}/license", response_model=MediaLicenseRead)
async def get_media_license(
    asset_id: UUID,
    principal: MediaReader,
    service: Service,
) -> MediaLicenseRead:
    ledger, _revision = await service.current_license(principal, asset_id)
    return MediaLicenseRead.model_validate(ledger)


@router.post(
    "/operations",
    response_model=MediaJobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_media_operation(
    data: MediaOperationCreate,
    principal: MediaWriter,
    service: Service,
    budget_gate: BudgetGate,
    background_tasks: BackgroundTasks,
) -> MediaJobRead:
    job, enqueue_needed = await service.create_operation_job(
        principal,
        data,
        budget_gate=budget_gate,
    )
    if enqueue_needed:
        background_tasks.add_task(
            enqueue_media_operation,
            principal.workspace_id,
            job.id,
        )
    return MediaJobRead.model_validate(job)


@router.get("/jobs/{job_id}", response_model=MediaJobRead)
async def get_media_job(
    job_id: UUID,
    principal: MediaReader,
    service: Service,
) -> MediaJobRead:
    return MediaJobRead.model_validate(await service.get_operation_job(principal, job_id))


@router.post("/jobs/{job_id}/cancel", response_model=MediaJobRead)
async def cancel_media_job(
    job_id: UUID,
    data: MediaJobCommandRequest,
    principal: MediaWriter,
    service: Service,
    background_tasks: BackgroundTasks,
) -> MediaJobRead:
    job = await service.command_operation_job(
        principal,
        job_id,
        data,
        command_kind="CANCEL",
    )
    background_tasks.add_task(
        enqueue_media_operation,
        principal.workspace_id,
        job.id,
    )
    return MediaJobRead.model_validate(job)


@router.post("/jobs/{job_id}/retry", response_model=MediaJobRead)
async def retry_media_job(
    job_id: UUID,
    data: MediaJobCommandRequest,
    principal: MediaWriter,
    service: Service,
    background_tasks: BackgroundTasks,
) -> MediaJobRead:
    job = await service.command_operation_job(
        principal,
        job_id,
        data,
        command_kind="RETRY",
    )
    background_tasks.add_task(
        enqueue_media_operation,
        principal.workspace_id,
        job.id,
    )
    return MediaJobRead.model_validate(job)


@router.post(
    "/plans",
    response_model=ImagePlanWithItems,
    status_code=status.HTTP_201_CREATED,
)
async def create_image_plan(
    data: ImagePlanCreate,
    principal: MediaWriter,
    service: Service,
) -> ImagePlanWithItems:
    plan, items = await service.create_plan(principal, data)
    return ImagePlanWithItems(
        plan=ImagePlanRead.model_validate(plan),
        items=[ImagePlanItemRead.model_validate(value) for value in items],
    )


@router.get("/plans/{plan_id}", response_model=ImagePlanWithItems)
async def get_image_plan(
    plan_id: UUID,
    principal: MediaReader,
    service: Service,
) -> ImagePlanWithItems:
    plan, items = await service.get_plan(principal, plan_id)
    return ImagePlanWithItems(
        plan=ImagePlanRead.model_validate(plan),
        items=[ImagePlanItemRead.model_validate(value) for value in items],
    )


@router.post("/plan-items/{item_id}/selection", response_model=ImagePlanItemRead)
async def select_image_plan_asset(
    item_id: UUID,
    data: ImageSelection,
    principal: MediaWriter,
    service: Service,
) -> ImagePlanItemRead:
    return ImagePlanItemRead.model_validate(
        await service.select_plan_asset(principal, item_id, data)
    )


@router.post(
    "/usages",
    response_model=MediaUsageRead,
    status_code=status.HTTP_201_CREATED,
)
async def register_media_usage(
    data: MediaUsageCreate,
    principal: MediaWriter,
    service: Service,
) -> MediaUsageRead:
    return MediaUsageRead.model_validate(await service.register_usage(principal, data))


@router.get("/license-report", response_model=list[MediaUsageRead])
async def media_license_report(
    principal: MediaReader,
    service: Service,
    content_version_id: UUID | None = None,
    asset_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[MediaUsageRead]:
    return [
        MediaUsageRead.model_validate(value)
        for value in await service.usage_report(
            principal,
            content_version_id=content_version_id,
            asset_id=asset_id,
            limit=limit,
            offset=offset,
        )
    ]


@router.delete("/assets/{asset_id}", response_model=MediaAssetRead)
async def delete_media_asset(
    asset_id: UUID,
    data: MediaDeleteRequest,
    principal: MediaManager,
    service: Service,
) -> MediaAssetRead:
    return MediaAssetRead.model_validate(
        await service.delete_asset(principal, asset_id, data)
    )
