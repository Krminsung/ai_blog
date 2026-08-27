"""Platform operations, customer approval and notification APIs."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.core.context import Principal
from blogops.core.permissions import get_principal, require_permission_value
from blogops.db.session import get_platform_session, get_tenant_session
from blogops.domain.admin.enums import AdminCommandState
from blogops.domain.admin.providers import AdminOperationPolicy, FailClosedAdminAdapters
from blogops.domain.admin.schemas import (
    AdminCommandCreate,
    AdminCommandDecision,
    AdminCommandRead,
    AdminElevationSessionRead,
    NotificationPreferenceRead,
    NotificationPreferenceUpsert,
    NotificationRead,
    NotificationSnooze,
    SupportAccessCreate,
    SupportAccessDecision,
    SupportAccessRead,
)
from blogops.domain.admin.service import AdminService
from blogops.domain.admin.tasks import enqueue_admin_command

router = APIRouter(tags=["admin", "notifications"])
PlatformSession = Annotated[AsyncSession, Depends(get_platform_session)]
TenantSession = Annotated[AsyncSession, Depends(get_tenant_session)]

PlatformOperator = Annotated[Principal, Depends(require_permission_value("platform:operate"))]
PlatformApprover = Annotated[Principal, Depends(require_permission_value("platform:approve"))]
WorkspaceManager = Annotated[Principal, Depends(require_permission_value("workspace:manage"))]
Authenticated = Annotated[Principal, Depends(get_principal)]


def admin_service(session: PlatformSession) -> AdminService:
    return AdminService(session)


def tenant_admin_service(session: TenantSession) -> AdminService:
    return AdminService(session)


def admin_adapters() -> FailClosedAdminAdapters:
    """Production must override with controlled policy and execution adapters."""

    return FailClosedAdminAdapters()


AdminSvc = Annotated[AdminService, Depends(admin_service)]
TenantSvc = Annotated[AdminService, Depends(tenant_admin_service)]
AdminPolicy = Annotated[AdminOperationPolicy, Depends(admin_adapters)]


@router.post(
    "/admin/support-access-requests",
    response_model=SupportAccessRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_support_access_request(
    data: SupportAccessCreate,
    principal: PlatformOperator,
    service: AdminSvc,
    policy: AdminPolicy,
) -> SupportAccessRead:
    return SupportAccessRead.model_validate(
        await service.create_support_access_request(principal, data, policy=policy)
    )


@router.post(
    "/admin/support-access-requests/{request_id}/customer-decision",
    response_model=SupportAccessRead,
)
async def decide_support_access_request(
    request_id: UUID,
    data: SupportAccessDecision,
    principal: WorkspaceManager,
    service: TenantSvc,
) -> SupportAccessRead:
    return SupportAccessRead.model_validate(
        await service.decide_support_access(principal, request_id, data)
    )


@router.post(
    "/admin/support-access-requests/{request_id}/sessions",
    response_model=AdminElevationSessionRead,
    status_code=status.HTTP_201_CREATED,
)
async def start_support_session(
    request_id: UUID,
    principal: PlatformOperator,
    service: AdminSvc,
) -> AdminElevationSessionRead:
    return AdminElevationSessionRead.model_validate(
        await service.start_elevation_session(principal, request_id)
    )


@router.post(
    "/admin/commands",
    response_model=AdminCommandRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_admin_command(
    data: AdminCommandCreate,
    principal: PlatformOperator,
    service: AdminSvc,
    policy: AdminPolicy,
) -> AdminCommandRead:
    return AdminCommandRead.model_validate(
        await service.create_command(principal, data, policy=policy)
    )


@router.post("/admin/commands/{command_id}/decision", response_model=AdminCommandRead)
async def decide_admin_command(
    command_id: UUID,
    data: AdminCommandDecision,
    principal: PlatformApprover,
    service: AdminSvc,
    background_tasks: BackgroundTasks,
) -> AdminCommandRead:
    value = await service.decide_command(principal, command_id, data)
    if value.state == AdminCommandState.READY.value:
        background_tasks.add_task(
            enqueue_admin_command,
            value.target_workspace_id,
            value.id,
        )
    return AdminCommandRead.model_validate(value)


@router.put("/notifications/preferences", response_model=NotificationPreferenceRead)
async def upsert_notification_preference(
    data: NotificationPreferenceUpsert,
    principal: Authenticated,
    service: TenantSvc,
) -> NotificationPreferenceRead:
    return NotificationPreferenceRead.model_validate(
        await service.upsert_notification_preference(principal, data)
    )


@router.get("/notifications", response_model=list[NotificationRead])
async def list_notifications(
    principal: Authenticated,
    service: TenantSvc,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[NotificationRead]:
    return [
        NotificationRead.model_validate(value)
        for value in await service.list_notifications(principal, limit=limit, offset=offset)
    ]


@router.post("/notifications/{notification_id}/read", response_model=NotificationRead)
async def mark_notification_read(
    notification_id: UUID,
    principal: Authenticated,
    service: TenantSvc,
) -> NotificationRead:
    return NotificationRead.model_validate(
        await service.mark_notification_read(principal, notification_id)
    )


@router.post("/notifications/{notification_id}/snooze", response_model=NotificationRead)
async def snooze_notification(
    notification_id: UUID,
    data: NotificationSnooze,
    principal: Authenticated,
    service: TenantSvc,
) -> NotificationRead:
    return NotificationRead.model_validate(
        await service.snooze_notification(principal, notification_id, until=data.until)
    )
