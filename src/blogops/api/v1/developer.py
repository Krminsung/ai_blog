"""API-key, rate-policy and outbound webhook management APIs."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.core.context import Principal
from blogops.core.permissions import Permission, require_permissions
from blogops.db.session import get_tenant_session
from blogops.domain.developer.dependencies import get_developer_adapters
from blogops.domain.developer.providers import (
    ApiKeySecrets,
    DnsResolver,
    WebhookOwnershipVerifier,
    WorkspaceApiPolicy,
)
from blogops.domain.developer.schemas import (
    ApiKeyCreate,
    ApiKeyIssued,
    ApiKeyRead,
    ApiKeyRevoke,
    ApiKeyRotate,
    RateLimitPolicyCreate,
    RateLimitPolicyRead,
    WebhookDeliveryRead,
    WebhookEndpointCreate,
    WebhookEndpointRead,
    WebhookReplayRequest,
)
from blogops.domain.developer.service import DeveloperService
from blogops.domain.developer.tasks import enqueue_webhook_delivery

router = APIRouter(prefix="/developer", tags=["developer"])
TenantSession = Annotated[AsyncSession, Depends(get_tenant_session)]
DeveloperManager = Annotated[Principal, Depends(require_permissions(Permission.API_MANAGE))]


def developer_service(session: TenantSession) -> DeveloperService:
    return DeveloperService(session)


Service = Annotated[DeveloperService, Depends(developer_service)]
Secrets = Annotated[ApiKeySecrets, Depends(get_developer_adapters)]
Policy = Annotated[WorkspaceApiPolicy, Depends(get_developer_adapters)]
DNS = Annotated[DnsResolver, Depends(get_developer_adapters)]
OwnershipVerifier = Annotated[WebhookOwnershipVerifier, Depends(get_developer_adapters)]


@router.post("/api-keys", response_model=ApiKeyIssued, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    data: ApiKeyCreate,
    principal: DeveloperManager,
    service: Service,
    secrets_provider: Secrets,
    policy: Policy,
) -> ApiKeyIssued:
    value, raw = await service.create_api_key(
        principal,
        data,
        secrets_provider=secrets_provider,
        policy=policy,
    )
    return ApiKeyIssued(key=ApiKeyRead.model_validate(value), raw_key=raw)


@router.get("/api-keys", response_model=list[ApiKeyRead])
async def list_api_keys(
    principal: DeveloperManager,
    service: Service,
) -> list[ApiKeyRead]:
    return [ApiKeyRead.model_validate(value) for value in await service.list_api_keys(principal)]


@router.post("/api-keys/{key_id}/rotate", response_model=ApiKeyIssued)
async def rotate_api_key(
    key_id: UUID,
    data: ApiKeyRotate,
    principal: DeveloperManager,
    service: Service,
    secrets_provider: Secrets,
    policy: Policy,
) -> ApiKeyIssued:
    value, raw = await service.rotate_api_key(
        principal,
        key_id,
        data,
        secrets_provider=secrets_provider,
        policy=policy,
    )
    return ApiKeyIssued(key=ApiKeyRead.model_validate(value), raw_key=raw)


@router.post("/api-keys/{key_id}/revoke", response_model=ApiKeyRead)
async def revoke_api_key(
    key_id: UUID,
    data: ApiKeyRevoke,
    principal: DeveloperManager,
    service: Service,
) -> ApiKeyRead:
    return ApiKeyRead.model_validate(
        await service.revoke_api_key(principal, key_id, reason=data.reason)
    )


@router.post(
    "/rate-limit-policies",
    response_model=RateLimitPolicyRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_rate_limit_policy(
    data: RateLimitPolicyCreate,
    principal: DeveloperManager,
    service: Service,
) -> RateLimitPolicyRead:
    return RateLimitPolicyRead.model_validate(
        await service.create_rate_limit_policy(principal, data)
    )


@router.post(
    "/webhooks",
    response_model=WebhookEndpointRead,
    status_code=status.HTTP_201_CREATED,
)
async def register_webhook(
    data: WebhookEndpointCreate,
    principal: DeveloperManager,
    service: Service,
    policy: Policy,
    dns: DNS,
    verifier: OwnershipVerifier,
) -> WebhookEndpointRead:
    return WebhookEndpointRead.model_validate(
        await service.register_webhook(
            principal,
            data,
            policy=policy,
            dns=dns,
            verifier=verifier,
        )
    )


@router.get("/webhooks", response_model=list[WebhookEndpointRead])
async def list_webhooks(
    principal: DeveloperManager,
    service: Service,
) -> list[WebhookEndpointRead]:
    return [
        WebhookEndpointRead.model_validate(value)
        for value in await service.list_webhooks(principal)
    ]


@router.post("/webhook-deliveries/{delivery_id}/replay", response_model=WebhookDeliveryRead)
async def replay_webhook_delivery(
    delivery_id: UUID,
    data: WebhookReplayRequest,
    principal: DeveloperManager,
    service: Service,
    background_tasks: BackgroundTasks,
) -> WebhookDeliveryRead:
    value = await service.replay_delivery(principal, delivery_id, reason=data.reason)
    background_tasks.add_task(
        enqueue_webhook_delivery,
        principal.workspace_id,
        value.id,
        value.next_attempt_at,
    )
    return WebhookDeliveryRead.model_validate(value)
