"""Canonical publishing API; all remote mutations are queued for durable workers."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Body, Depends, Header, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.core.context import Principal
from blogops.core.permissions import Permission, require_permissions
from blogops.db.session import get_tenant_session
from blogops.domain.billing.adapters import create_publishing_entitlement_resolver
from blogops.domain.jobs.state import JobState
from blogops.domain.publishing.enums import (
    ConnectionOperation,
    ConnectionState,
    PublishingProvider,
)
from blogops.domain.publishing.references import SQLAlchemyPublishingReadinessResolver
from blogops.domain.publishing.schemas import (
    CancelPublishCreate,
    ConnectionCommandCreate,
    ConnectionJobRead,
    NaverChecklistEventRead,
    NaverChecklistUpdate,
    NaverManualConfirm,
    NaverManualConfirmationRead,
    NaverPackageCreate,
    NaverPackageRead,
    PublicationPolicyCreate,
    PublicationPolicyRead,
    PublishedPostDelete,
    PublishedPostRead,
    PublishedPostUpdate,
    PublishingConnectionCreate,
    PublishingConnectionRead,
    PublishingNotificationRead,
    PublishAttemptRead,
    PublishCreate,
    PublishJobRead,
    PublishPreviewCreate,
    PublishPreviewRead,
    PublishSagaStepRead,
    ReconcileCreate,
    RemoteSnapshotRead,
    RetryPublishCreate,
    RollbackCreate,
)
from blogops.domain.publishing.service import PublishingService
from blogops.domain.publishing.tasks import (
    enqueue_connection_job,
    enqueue_publish_job,
)


router = APIRouter(tags=["publishing"])
TenantSession = Annotated[AsyncSession, Depends(get_tenant_session)]
PublishingReader = Annotated[
    Principal, Depends(require_permissions(Permission.CONTENT_READ))
]
PublishingWriter = Annotated[
    Principal, Depends(require_permissions(Permission.CONTENT_PUBLISH))
]
PublishingManager = Annotated[
    Principal, Depends(require_permissions(Permission.WORKSPACE_MANAGE))
]
IdempotencyKey = Annotated[
    str, Header(alias="Idempotency-Key", min_length=1, max_length=255)
]


def publishing_service(session: TenantSession) -> PublishingService:
    return PublishingService(
        session,
        readiness=SQLAlchemyPublishingReadinessResolver(session),
        entitlements=create_publishing_entitlement_resolver(session),
    )


Service = Annotated[PublishingService, Depends(publishing_service)]


@router.post(
    "/publishing/policies",
    response_model=PublicationPolicyRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_publication_policy(
    data: PublicationPolicyCreate,
    principal: PublishingManager,
    service: Service,
) -> PublicationPolicyRead:
    return PublicationPolicyRead.model_validate(
        await service.create_policy(principal, data)
    )


@router.get("/publishing/policies", response_model=list[PublicationPolicyRead])
async def list_publication_policies(
    principal: PublishingReader, service: Service
) -> list[PublicationPolicyRead]:
    items = await service.list_policies(principal)
    return [PublicationPolicyRead.model_validate(item) for item in items]


@router.post(
    "/publishing/connections",
    response_model=PublishingConnectionRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_publishing_connection(
    data: PublishingConnectionCreate,
    principal: PublishingManager,
    service: Service,
) -> PublishingConnectionRead:
    return PublishingConnectionRead.model_validate(
        await service.create_connection(principal, data)
    )


@router.get(
    "/publishing/connections", response_model=list[PublishingConnectionRead]
)
async def list_publishing_connections(
    principal: PublishingReader,
    service: Service,
    provider: PublishingProvider | None = None,
    connection_state: ConnectionState | None = Query(default=None, alias="state"),
) -> list[PublishingConnectionRead]:
    items = await service.list_connections(
        principal, provider=provider, state=connection_state
    )
    return [PublishingConnectionRead.model_validate(item) for item in items]


@router.get(
    "/publishing/connections/{connection_id}",
    response_model=PublishingConnectionRead,
)
async def get_publishing_connection(
    connection_id: UUID,
    principal: PublishingReader,
    service: Service,
) -> PublishingConnectionRead:
    return PublishingConnectionRead.model_validate(
        await service.get_connection(principal, connection_id)
    )


async def _queue_connection_command(
    *,
    connection_id: UUID,
    operation: ConnectionOperation,
    data: ConnectionCommandCreate,
    principal: Principal,
    service: PublishingService,
    background_tasks: BackgroundTasks,
) -> ConnectionJobRead:
    result = await service.create_connection_job(
        principal, connection_id, operation, data
    )
    if result.created:
        background_tasks.add_task(
            enqueue_connection_job, principal.workspace_id, result.job.id
        )
    return ConnectionJobRead.model_validate(result.job)


@router.post(
    "/publishing/connections/{connection_id}/diagnose",
    response_model=ConnectionJobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def diagnose_publishing_connection(
    connection_id: UUID,
    data: ConnectionCommandCreate,
    background_tasks: BackgroundTasks,
    principal: PublishingManager,
    service: Service,
) -> ConnectionJobRead:
    return await _queue_connection_command(
        connection_id=connection_id,
        operation=ConnectionOperation.DIAGNOSE,
        data=data,
        principal=principal,
        service=service,
        background_tasks=background_tasks,
    )


@router.post(
    "/publishing/connections/{connection_id}/refresh",
    response_model=ConnectionJobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def refresh_publishing_connection(
    connection_id: UUID,
    data: ConnectionCommandCreate,
    background_tasks: BackgroundTasks,
    principal: PublishingManager,
    service: Service,
) -> ConnectionJobRead:
    return await _queue_connection_command(
        connection_id=connection_id,
        operation=ConnectionOperation.REFRESH,
        data=data,
        principal=principal,
        service=service,
        background_tasks=background_tasks,
    )


@router.post(
    "/publishing/connections/{connection_id}/sync-settings",
    response_model=ConnectionJobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def sync_publishing_connection_settings(
    connection_id: UUID,
    data: ConnectionCommandCreate,
    background_tasks: BackgroundTasks,
    principal: PublishingManager,
    service: Service,
) -> ConnectionJobRead:
    return await _queue_connection_command(
        connection_id=connection_id,
        operation=ConnectionOperation.SYNC_SETTINGS,
        data=data,
        principal=principal,
        service=service,
        background_tasks=background_tasks,
    )


@router.delete(
    "/publishing/connections/{connection_id}",
    response_model=ConnectionJobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def disconnect_publishing_connection(
    connection_id: UUID,
    data: Annotated[ConnectionCommandCreate, Body()],
    background_tasks: BackgroundTasks,
    principal: PublishingManager,
    service: Service,
) -> ConnectionJobRead:
    return await _queue_connection_command(
        connection_id=connection_id,
        operation=ConnectionOperation.DISCONNECT,
        data=data,
        principal=principal,
        service=service,
        background_tasks=background_tasks,
    )


@router.get(
    "/publishing/connection-jobs/{job_id}", response_model=ConnectionJobRead
)
async def get_publishing_connection_job(
    job_id: UUID, principal: PublishingReader, service: Service
) -> ConnectionJobRead:
    return ConnectionJobRead.model_validate(
        await service.get_connection_job(principal, job_id)
    )


@router.get(
    "/publishing/connection-jobs", response_model=list[ConnectionJobRead]
)
async def list_publishing_connection_jobs(
    principal: PublishingReader,
    service: Service,
    connection_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ConnectionJobRead]:
    items = await service.list_connection_jobs(
        principal,
        connection_id=connection_id,
        limit=limit,
        offset=offset,
    )
    return [ConnectionJobRead.model_validate(item) for item in items]


@router.post(
    "/content/{content_id}/publishing-preview",
    response_model=PublishPreviewRead,
)
async def preview_content_publish(
    content_id: UUID,
    data: PublishPreviewCreate,
    principal: PublishingReader,
    service: Service,
) -> PublishPreviewRead:
    return PublishPreviewRead.model_validate(
        await service.preview_publish(principal, content_id, data)
    )


@router.post(
    "/content/{content_id}/publish",
    response_model=PublishJobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def publish_content(
    content_id: UUID,
    data: PublishCreate,
    background_tasks: BackgroundTasks,
    idempotency_key: IdempotencyKey,
    principal: PublishingWriter,
    service: Service,
) -> PublishJobRead:
    result = await service.create_publish_job(
        principal, content_id, data, idempotency_key=idempotency_key
    )
    if result.created:
        background_tasks.add_task(
            enqueue_publish_job, principal.workspace_id, result.job.id, None
        )
    return PublishJobRead.model_validate(result.job)


@router.get("/publishing/jobs", response_model=list[PublishJobRead])
async def list_publish_jobs(
    principal: PublishingReader,
    service: Service,
    content_id: UUID | None = None,
    job_state: JobState | None = Query(default=None, alias="state"),
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[PublishJobRead]:
    items = await service.list_publish_jobs(
        principal,
        content_id=content_id,
        state=job_state,
        limit=limit,
        offset=offset,
    )
    return [PublishJobRead.model_validate(item) for item in items]


@router.get("/publishing/jobs/{job_id}", response_model=PublishJobRead)
async def get_publish_job(
    job_id: UUID, principal: PublishingReader, service: Service
) -> PublishJobRead:
    return PublishJobRead.model_validate(await service.get_publish_job(principal, job_id))


@router.get(
    "/publishing/jobs/{job_id}/steps", response_model=list[PublishSagaStepRead]
)
async def get_publish_job_steps(
    job_id: UUID, principal: PublishingReader, service: Service
) -> list[PublishSagaStepRead]:
    items = await service.publish_job_steps(principal, job_id)
    return [PublishSagaStepRead.model_validate(item) for item in items]


@router.get(
    "/publishing/jobs/{job_id}/attempts", response_model=list[PublishAttemptRead]
)
async def get_publish_job_attempts(
    job_id: UUID, principal: PublishingReader, service: Service
) -> list[PublishAttemptRead]:
    items = await service.publish_job_attempts(principal, job_id)
    return [PublishAttemptRead.model_validate(item) for item in items]


@router.post(
    "/publishing/jobs/{job_id}/cancel",
    response_model=PublishJobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def cancel_publish_job(
    job_id: UUID,
    data: CancelPublishCreate,
    background_tasks: BackgroundTasks,
    principal: PublishingWriter,
    service: Service,
) -> PublishJobRead:
    job = await service.cancel_publish_job(principal, job_id, data)
    background_tasks.add_task(
        enqueue_publish_job, principal.workspace_id, job.id, None
    )
    return PublishJobRead.model_validate(job)


@router.post(
    "/publishing/jobs/{job_id}/retry",
    response_model=PublishJobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_publish_job(
    job_id: UUID,
    data: RetryPublishCreate,
    background_tasks: BackgroundTasks,
    principal: PublishingWriter,
    service: Service,
) -> PublishJobRead:
    job = await service.retry_publish_job(principal, job_id, data)
    background_tasks.add_task(
        enqueue_publish_job, principal.workspace_id, job.id, None
    )
    return PublishJobRead.model_validate(job)


@router.get("/published-posts", response_model=list[PublishedPostRead])
async def list_published_posts(
    principal: PublishingReader,
    service: Service,
    content_id: UUID | None = None,
    provider: PublishingProvider | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[PublishedPostRead]:
    items = await service.list_published_posts(
        principal,
        content_id=content_id,
        provider=provider,
        limit=limit,
        offset=offset,
    )
    return [PublishedPostRead.model_validate(item) for item in items]


@router.get("/published-posts/{post_id}", response_model=PublishedPostRead)
async def get_published_post(
    post_id: UUID, principal: PublishingReader, service: Service
) -> PublishedPostRead:
    return PublishedPostRead.model_validate(
        await service.get_published_post(principal, post_id)
    )


@router.patch(
    "/published-posts/{post_id}",
    response_model=PublishJobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def update_published_post(
    post_id: UUID,
    data: PublishedPostUpdate,
    background_tasks: BackgroundTasks,
    idempotency_key: IdempotencyKey,
    principal: PublishingWriter,
    service: Service,
) -> PublishJobRead:
    result = await service.update_published_post(
        principal, post_id, data, idempotency_key=idempotency_key
    )
    if result.created:
        background_tasks.add_task(
            enqueue_publish_job, principal.workspace_id, result.job.id, None
        )
    return PublishJobRead.model_validate(result.job)


@router.delete(
    "/published-posts/{post_id}",
    response_model=PublishJobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def delete_published_post(
    post_id: UUID,
    data: Annotated[PublishedPostDelete, Body()],
    background_tasks: BackgroundTasks,
    idempotency_key: IdempotencyKey,
    principal: PublishingWriter,
    service: Service,
) -> PublishJobRead:
    result = await service.delete_published_post(
        principal, post_id, data, idempotency_key=idempotency_key
    )
    if result.created:
        background_tasks.add_task(
            enqueue_publish_job, principal.workspace_id, result.job.id, None
        )
    return PublishJobRead.model_validate(result.job)


@router.post(
    "/published-posts/{post_id}/reconcile",
    response_model=PublishJobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def reconcile_published_post(
    post_id: UUID,
    data: ReconcileCreate,
    background_tasks: BackgroundTasks,
    idempotency_key: IdempotencyKey,
    principal: PublishingWriter,
    service: Service,
) -> PublishJobRead:
    result = await service.reconcile_published_post(
        principal, post_id, data, idempotency_key=idempotency_key
    )
    if result.created:
        background_tasks.add_task(
            enqueue_publish_job, principal.workspace_id, result.job.id, None
        )
    return PublishJobRead.model_validate(result.job)


@router.post(
    "/published-posts/{post_id}/rollback",
    response_model=PublishJobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def rollback_published_post(
    post_id: UUID,
    data: RollbackCreate,
    background_tasks: BackgroundTasks,
    idempotency_key: IdempotencyKey,
    principal: PublishingWriter,
    service: Service,
) -> PublishJobRead:
    result = await service.rollback_published_post(
        principal, post_id, data, idempotency_key=idempotency_key
    )
    if result.created:
        background_tasks.add_task(
            enqueue_publish_job, principal.workspace_id, result.job.id, None
        )
    return PublishJobRead.model_validate(result.job)


@router.get(
    "/published-posts/{post_id}/snapshots",
    response_model=list[RemoteSnapshotRead],
)
async def list_published_post_snapshots(
    post_id: UUID, principal: PublishingReader, service: Service
) -> list[RemoteSnapshotRead]:
    items = await service.list_remote_snapshots(principal, post_id)
    return [RemoteSnapshotRead.model_validate(item) for item in items]


@router.post(
    "/content/{content_id}/naver-package",
    response_model=NaverPackageRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_naver_package(
    content_id: UUID,
    data: NaverPackageCreate,
    principal: PublishingWriter,
    service: Service,
) -> NaverPackageRead:
    return NaverPackageRead.model_validate(
        await service.create_naver_package(principal, content_id, data)
    )


@router.get("/publishing/naver-packages/{package_id}", response_model=NaverPackageRead)
async def get_naver_package(
    package_id: UUID, principal: PublishingReader, service: Service
) -> NaverPackageRead:
    return NaverPackageRead.model_validate(
        await service.get_naver_package(principal, package_id)
    )


@router.get("/publishing/naver-packages", response_model=list[NaverPackageRead])
async def list_naver_packages(
    principal: PublishingReader,
    service: Service,
    content_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[NaverPackageRead]:
    items = await service.list_naver_packages(
        principal, content_id=content_id, limit=limit, offset=offset
    )
    return [NaverPackageRead.model_validate(item) for item in items]


@router.post(
    "/publishing/naver-packages/{package_id}/checklist",
    response_model=NaverChecklistEventRead,
    status_code=status.HTTP_201_CREATED,
)
async def update_naver_package_checklist(
    package_id: UUID,
    data: NaverChecklistUpdate,
    principal: PublishingWriter,
    service: Service,
) -> NaverChecklistEventRead:
    return NaverChecklistEventRead.model_validate(
        await service.update_naver_checklist(principal, package_id, data)
    )


@router.get(
    "/publishing/naver-packages/{package_id}/checklist",
    response_model=list[NaverChecklistEventRead],
)
async def list_naver_package_checklist(
    package_id: UUID,
    principal: PublishingReader,
    service: Service,
) -> list[NaverChecklistEventRead]:
    items = await service.list_naver_checklist_events(principal, package_id)
    return [NaverChecklistEventRead.model_validate(item) for item in items]


@router.post(
    "/publishing/naver-packages/{package_id}/confirm",
    response_model=NaverManualConfirmationRead,
    status_code=status.HTTP_201_CREATED,
)
async def confirm_naver_manual_publish(
    package_id: UUID,
    data: NaverManualConfirm,
    principal: PublishingWriter,
    service: Service,
) -> NaverManualConfirmationRead:
    return NaverManualConfirmationRead.model_validate(
        await service.confirm_naver_manual_publish(principal, package_id, data)
    )


@router.get(
    "/publishing/notifications", response_model=list[PublishingNotificationRead]
)
async def list_publishing_notifications(
    principal: PublishingReader,
    service: Service,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[PublishingNotificationRead]:
    items = await service.list_notifications(principal, limit=limit, offset=offset)
    return [PublishingNotificationRead.model_validate(item) for item in items]
