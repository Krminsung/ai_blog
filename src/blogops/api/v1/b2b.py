"""Agency hierarchy, client portal and white-label APIs."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.core.context import Principal
from blogops.core.permissions import Permission, get_principal, require_permissions
from blogops.db.session import get_session, get_tenant_session
from blogops.domain.b2b.providers import (
    AgencyRelationshipAuthority,
    FailClosedB2BAdapters,
    PortalTokenSecrets,
)
from blogops.domain.b2b.schemas import (
    AgencyClientCreate,
    AgencyClientRead,
    AgencyCreate,
    AgencyRead,
    CreditAllocationPolicyCreate,
    CreditAllocationPolicyRead,
    PortalGrantRead,
    PortalInvitationAccept,
    PortalInvitationCreate,
    PortalInvitationIssued,
    ProvisionClientCreate,
    ProvisionClientRead,
    WhiteLabelVersionCreate,
    WhiteLabelVersionRead,
)
from blogops.domain.b2b.service import B2BService
from blogops.domain.b2b.tasks import enqueue_client_provisioning

router = APIRouter(prefix="/b2b", tags=["b2b"])
TenantSession = Annotated[AsyncSession, Depends(get_tenant_session)]
UnscopedSession = Annotated[AsyncSession, Depends(get_session)]


AgencyReader = Annotated[Principal, Depends(require_permissions(Permission.AGENCY_READ))]
AgencyManager = Annotated[Principal, Depends(require_permissions(Permission.AGENCY_MANAGE))]
PortalManager = Annotated[Principal, Depends(require_permissions(Permission.PORTAL_MANAGE))]
Authenticated = Annotated[Principal, Depends(get_principal)]


def b2b_service(session: TenantSession) -> B2BService:
    return B2BService(session)


def b2b_redemption_service(session: UnscopedSession) -> B2BService:
    return B2BService(session)


def b2b_adapters() -> FailClosedB2BAdapters:
    """Production must override with entitlement/consent and KMS adapters."""

    return FailClosedB2BAdapters()


Service = Annotated[B2BService, Depends(b2b_service)]
RedemptionService = Annotated[B2BService, Depends(b2b_redemption_service)]
RelationshipAuthority = Annotated[AgencyRelationshipAuthority, Depends(b2b_adapters)]
PortalSecrets = Annotated[PortalTokenSecrets, Depends(b2b_adapters)]


@router.post("/agencies", response_model=AgencyRead, status_code=status.HTTP_201_CREATED)
async def create_agency(
    data: AgencyCreate,
    principal: AgencyManager,
    service: Service,
) -> AgencyRead:
    return AgencyRead.model_validate(await service.create_agency(principal, data))


@router.post(
    "/clients",
    response_model=AgencyClientRead,
    status_code=status.HTTP_201_CREATED,
)
async def add_agency_client(
    data: AgencyClientCreate,
    principal: AgencyManager,
    service: Service,
    authority: RelationshipAuthority,
) -> AgencyClientRead:
    return AgencyClientRead.model_validate(
        await service.add_client(principal, data, authority=authority)
    )


@router.get("/clients", response_model=list[AgencyClientRead])
async def list_agency_clients(
    principal: AgencyReader,
    service: Service,
) -> list[AgencyClientRead]:
    return [
        AgencyClientRead.model_validate(value)
        for value in await service.list_clients(principal)
    ]


@router.post(
    "/clients/{agency_client_id}/portal-invitations",
    response_model=PortalInvitationIssued,
    status_code=status.HTTP_201_CREATED,
)
async def issue_portal_invitation(
    agency_client_id: UUID,
    data: PortalInvitationCreate,
    principal: PortalManager,
    service: Service,
    secrets_provider: PortalSecrets,
) -> PortalInvitationIssued:
    invitation, raw = await service.issue_portal_invitation(
        principal,
        agency_client_id,
        data,
        secrets_provider=secrets_provider,
    )
    return PortalInvitationIssued(
        invitation_id=invitation.id,
        token=raw,
        expires_at=invitation.expires_at,
    )


@router.post("/portal/invitations/accept", response_model=PortalGrantRead)
async def accept_portal_invitation(
    data: PortalInvitationAccept,
    principal: Authenticated,
    service: RedemptionService,
    secrets_provider: PortalSecrets,
) -> PortalGrantRead:
    return PortalGrantRead.model_validate(
        await service.accept_portal_invitation(
            principal,
            data.token.get_secret_value(),
            secrets_provider=secrets_provider,
        )
    )


@router.post(
    "/white-label/versions",
    response_model=WhiteLabelVersionRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_white_label_version(
    data: WhiteLabelVersionCreate,
    principal: AgencyManager,
    service: Service,
) -> WhiteLabelVersionRead:
    return WhiteLabelVersionRead.model_validate(
        await service.create_white_label_version(principal, data)
    )


@router.post(
    "/clients/provision",
    response_model=ProvisionClientRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def queue_client_provisioning(
    data: ProvisionClientCreate,
    principal: AgencyManager,
    service: Service,
    background_tasks: BackgroundTasks,
) -> ProvisionClientRead:
    value = await service.queue_client_provisioning(principal, data)
    background_tasks.add_task(
        enqueue_client_provisioning,
        principal.workspace_id,
        value.id,
    )
    return ProvisionClientRead.model_validate(value)


@router.post(
    "/clients/{agency_client_id}/credit-allocation-policies",
    response_model=CreditAllocationPolicyRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_credit_allocation_policy(
    agency_client_id: UUID,
    data: CreditAllocationPolicyCreate,
    principal: AgencyManager,
    service: Service,
) -> CreditAllocationPolicyRead:
    return CreditAllocationPolicyRead.model_validate(
        await service.create_credit_allocation_policy(
            principal,
            agency_client_id,
            data,
        )
    )
