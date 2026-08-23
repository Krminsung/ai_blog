"""Agency and portal service with explicit cross-tenant authorization."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.core.context import Principal
from blogops.core.errors import AppError
from blogops.db.session import apply_workspace_scope
from blogops.domain.b2b.enums import (
    AgencyClientState,
    DomainVerificationState,
    PortalGrantState,
    PortalInvitationState,
    ProvisioningState,
)
from blogops.domain.b2b.models import (
    Agency,
    AgencyClient,
    AgencyCreditAllocationPolicy,
    ClientProvisioningRequest,
    PortalAccessGrant,
    PortalInvitation,
    WhiteLabelConfigVersion,
)
from blogops.domain.b2b.providers import (
    AgencyRelationshipAuthority,
    PortalTokenSecrets,
    WorkspaceProvisioner,
)
from blogops.domain.b2b.rules import (
    authorize_portal_scopes,
    ensure_client_isolation,
    issue_portal_token,
    require_portal_target,
    verify_portal_token,
)
from blogops.domain.b2b.schemas import (
    AgencyClientCreate,
    AgencyCreate,
    CreditAllocationPolicyCreate,
    PortalInvitationCreate,
    ProvisionClientCreate,
    WhiteLabelVersionCreate,
)
from blogops.services.audit import append_audit_log
from blogops.services.outbox import add_outbox_event

_SCHEMA_VERSION = "1.0"


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class B2BService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _scope(self, workspace_id: UUID) -> None:
        await apply_workspace_scope(self._session, workspace_id)

    async def _record(
        self,
        *,
        principal: Principal,
        action: str,
        target_type: str,
        target_id: UUID,
        details: dict[str, Any],
        workspace_id: UUID | None = None,
    ) -> None:
        owner_workspace = workspace_id or principal.workspace_id
        await append_audit_log(
            self._session,
            workspace_id=owner_workspace,
            actor_id=principal.subject_id,
            action=action,
            target_type=target_type,
            target_id=str(target_id),
            details=details,
        )
        await add_outbox_event(
            self._session,
            workspace_id=owner_workspace,
            aggregate_type=target_type,
            aggregate_id=str(target_id),
            event_type=action,
            schema_version=_SCHEMA_VERSION,
            payload={
                "workspace_id": str(owner_workspace),
                "actor_id": str(principal.subject_id),
                "aggregate_id": str(target_id),
                **details,
            },
        )

    async def create_agency(self, principal: Principal, data: AgencyCreate) -> Agency:
        await self._scope(principal.workspace_id)
        existing = await self._session.scalar(
            select(Agency).where(Agency.workspace_id == principal.workspace_id)
        )
        if existing is not None:
            return existing
        value = Agency(
            workspace_id=principal.workspace_id,
            name=data.name.strip(),
            consolidated_billing=data.consolidated_billing,
            default_client_permissions=sorted(data.default_client_permissions),
            created_by=principal.subject_id,
        )
        self._session.add(value)
        await self._session.flush()
        await self._record(
            principal=principal,
            action="b2b.agency.created",
            target_type="agency",
            target_id=value.id,
            details={"name": value.name},
        )
        return value

    async def _agency(self, workspace_id: UUID) -> Agency:
        value = await self._session.scalar(
            select(Agency).where(Agency.workspace_id == workspace_id)
        )
        if value is None:
            raise AppError("AGENCY_NOT_FOUND", "대행사 설정을 찾을 수 없습니다.", 404)
        if value.state != "ACTIVE":
            raise AppError("AGENCY_NOT_ACTIVE", "활성 대행사만 고객을 관리할 수 있습니다.", 409)
        return value

    async def add_client(
        self,
        principal: Principal,
        data: AgencyClientCreate,
        *,
        authority: AgencyRelationshipAuthority,
    ) -> AgencyClient:
        await self._scope(principal.workspace_id)
        agency = await self._agency(principal.workspace_id)
        ensure_client_isolation(
            agency_workspace_id=principal.workspace_id,
            client_workspace_id=data.client_workspace_id,
        )
        existing = await self._session.scalar(
            select(AgencyClient).where(
                AgencyClient.agency_id == agency.id,
                AgencyClient.client_workspace_id == data.client_workspace_id,
            )
        )
        if existing is not None:
            return existing
        receipt = await authority.authorize_client_relationship(
            agency_workspace_id=principal.workspace_id,
            client_workspace_id=data.client_workspace_id,
            permissions=frozenset(data.permissions),
        )
        if not receipt:
            raise AppError("AGENCY_CLIENT_CONSENT_REQUIRED", "고객 관계 승인 증명이 필요합니다.", 403)
        metadata = dict(data.metadata)
        metadata["authority_receipt_hash"] = hashlib.sha256(receipt.encode()).hexdigest()
        value = AgencyClient(
            workspace_id=principal.workspace_id,
            agency_id=agency.id,
            client_workspace_id=data.client_workspace_id,
            client_display_name=data.client_display_name,
            state=AgencyClientState.ACTIVE.value,
            permissions=sorted(data.permissions),
            billing_mode=data.billing_mode,
            relationship_metadata=metadata,
            activated_at=datetime.now(UTC),
            created_by=principal.subject_id,
        )
        self._session.add(value)
        await self._session.flush()
        await self._record(
            principal=principal,
            action="b2b.agency_client.activated",
            target_type="agency_client",
            target_id=value.id,
            details={"client_workspace_id": str(value.client_workspace_id)},
        )
        return value

    async def list_clients(self, principal: Principal) -> list[AgencyClient]:
        await self._scope(principal.workspace_id)
        return list(
            await self._session.scalars(
                select(AgencyClient)
                .where(AgencyClient.workspace_id == principal.workspace_id)
                .order_by(AgencyClient.created_at.desc())
            )
        )

    async def issue_portal_invitation(
        self,
        principal: Principal,
        agency_client_id: UUID,
        data: PortalInvitationCreate,
        *,
        secrets_provider: PortalTokenSecrets,
    ) -> tuple[PortalInvitation, str]:
        await self._scope(principal.workspace_id)
        relationship = await self._session.scalar(
            select(AgencyClient).where(
                AgencyClient.workspace_id == principal.workspace_id,
                AgencyClient.id == agency_client_id,
                AgencyClient.state == AgencyClientState.ACTIVE.value,
            )
        )
        if relationship is None:
            raise AppError("AGENCY_CLIENT_NOT_FOUND", "활성 고객 관계를 찾을 수 없습니다.", 404)
        if data.expires_at <= datetime.now(UTC):
            raise AppError("PORTAL_INVITATION_EXPIRY_INVALID", "초대 만료 시각은 미래여야 합니다.", 422)
        scopes = authorize_portal_scopes(
            requested=data.scopes,
            relationship_permissions=relationship.permissions,
        )
        key_version, pepper = await secrets_provider.pepper()
        material = issue_portal_token(pepper=pepper)
        email_hash = hmac.new(
            pepper,
            data.email.strip().casefold().encode(),
            hashlib.sha256,
        ).hexdigest()
        invitation = PortalInvitation(
            workspace_id=principal.workspace_id,
            agency_client_id=relationship.id,
            client_workspace_id=relationship.client_workspace_id,
            invited_email_hash=email_hash,
            token_prefix=material.prefix,
            token_digest=material.digest,
            token_key_version=key_version,
            scopes=sorted(scopes),
            expires_at=data.expires_at,
            invited_by=principal.subject_id,
        )
        self._session.add(invitation)
        await self._session.flush()
        await self._record(
            principal=principal,
            action="b2b.portal_invitation.created",
            target_type="portal_invitation",
            target_id=invitation.id,
            details={
                "client_workspace_id": str(invitation.client_workspace_id),
                "scopes": invitation.scopes,
            },
        )
        return invitation, material.raw

    async def accept_portal_invitation(
        self,
        principal: Principal,
        raw_token: str,
        *,
        secrets_provider: PortalTokenSecrets,
    ) -> PortalAccessGrant:
        """Resolve only the owning tenant before loading the RLS-protected invitation."""

        prefix = raw_token[:12]
        await self._scope(principal.workspace_id)
        invitation_workspace_id = await self._session.scalar(
            text(
                "SELECT app.resolve_portal_invitation_workspace("
                ":token_prefix, :client_workspace_id)"
            ),
            {
                "token_prefix": prefix,
                "client_workspace_id": str(principal.workspace_id),
            },
        )
        if invitation_workspace_id is None:
            raise AppError("PORTAL_INVITATION_INVALID", "포털 초대가 올바르지 않습니다.", 404)
        await self._scope(invitation_workspace_id)
        invitation = await self._session.scalar(
            select(PortalInvitation)
            .where(
                PortalInvitation.workspace_id == invitation_workspace_id,
                PortalInvitation.token_prefix == prefix,
                PortalInvitation.client_workspace_id == principal.workspace_id,
            )
            .with_for_update()
        )
        if invitation is None:
            raise AppError("PORTAL_INVITATION_INVALID", "포털 초대가 올바르지 않습니다.", 404)
        key_version, pepper = await secrets_provider.pepper(invitation.token_key_version)
        if key_version != invitation.token_key_version:
            raise AppError("PORTAL_INVITATION_INVALID", "포털 초대가 올바르지 않습니다.", 404)
        if not verify_portal_token(raw_token, invitation.token_digest, pepper=pepper):
            raise AppError("PORTAL_INVITATION_INVALID", "포털 초대가 올바르지 않습니다.", 404)
        if principal.workspace_id != invitation.client_workspace_id:
            raise AppError("PORTAL_INVITATION_WORKSPACE_MISMATCH", "초대 대상 고객 Workspace가 아닙니다.", 403)
        now = datetime.now(UTC)
        if invitation.state != PortalInvitationState.PENDING.value or invitation.expires_at <= now:
            raise AppError("PORTAL_INVITATION_INACTIVE", "포털 초대가 만료되었거나 사용되었습니다.", 409)
        existing = await self._session.scalar(
            select(PortalAccessGrant).where(
                PortalAccessGrant.workspace_id == invitation.workspace_id,
                PortalAccessGrant.agency_client_id == invitation.agency_client_id,
                PortalAccessGrant.user_id == principal.subject_id,
            )
        )
        if existing is not None:
            invitation.state = PortalInvitationState.ACCEPTED.value
            invitation.accepted_by = principal.subject_id
            invitation.accepted_at = now
            return existing
        grant = PortalAccessGrant(
            workspace_id=invitation.workspace_id,
            agency_client_id=invitation.agency_client_id,
            client_workspace_id=invitation.client_workspace_id,
            user_id=principal.subject_id,
            scopes=invitation.scopes,
            state=PortalGrantState.ACTIVE.value,
            granted_by=invitation.invited_by,
        )
        invitation.state = PortalInvitationState.ACCEPTED.value
        invitation.accepted_by = principal.subject_id
        invitation.accepted_at = now
        self._session.add(grant)
        await self._session.flush()
        await self._record(
            principal=principal,
            workspace_id=invitation.workspace_id,
            action="b2b.portal_invitation.accepted",
            target_type="portal_access_grant",
            target_id=grant.id,
            details={"client_workspace_id": str(grant.client_workspace_id)},
        )
        return grant

    async def create_white_label_version(
        self, principal: Principal, data: WhiteLabelVersionCreate
    ) -> WhiteLabelConfigVersion:
        await self._scope(principal.workspace_id)
        agency = await self._agency(principal.workspace_id)
        payload = data.model_dump(mode="json")
        value = WhiteLabelConfigVersion(
            workspace_id=principal.workspace_id,
            agency_id=agency.id,
            version=data.version,
            custom_domain=data.custom_domain,
            domain_state=DomainVerificationState.UNVERIFIED.value,
            dns_challenge_hash=data.dns_challenge_hash,
            logo_asset_ref=data.logo_asset_ref,
            email_sender_domain=data.email_sender_domain,
            branding=data.branding,
            config_hash=_canonical_hash(payload),
            created_by=principal.subject_id,
        )
        self._session.add(value)
        await self._session.flush()
        await self._record(
            principal=principal,
            action="b2b.white_label_version.created",
            target_type="white_label_version",
            target_id=value.id,
            details={"version": value.version, "domain_state": value.domain_state},
        )
        return value

    async def resolve_portal_principal(
        self,
        *,
        grant_id: UUID,
        user_id: UUID,
        requested_workspace_id: UUID,
    ) -> Principal:
        """Resolve a server-held grant ID, then bind a principal only to its client tenant."""

        await self._scope(requested_workspace_id)
        grant_workspace_id = await self._session.scalar(
            text(
                "SELECT app.resolve_portal_grant_workspace("
                ":grant_id, :user_id, :client_workspace_id)"
            ),
            {
                "grant_id": str(grant_id),
                "user_id": str(user_id),
                "client_workspace_id": str(requested_workspace_id),
            },
        )
        if grant_workspace_id is None:
            raise AppError("PORTAL_RESOURCE_NOT_FOUND", "리소스를 찾을 수 없습니다.", 404)
        await self._scope(grant_workspace_id)
        grant = await self._session.scalar(
            select(PortalAccessGrant).where(
                PortalAccessGrant.workspace_id == grant_workspace_id,
                PortalAccessGrant.id == grant_id,
                PortalAccessGrant.user_id == user_id,
                PortalAccessGrant.client_workspace_id == requested_workspace_id,
            )
        )
        if grant is None:
            raise AppError("PORTAL_RESOURCE_NOT_FOUND", "리소스를 찾을 수 없습니다.", 404)
        require_portal_target(
            grant_client_workspace_id=grant.client_workspace_id,
            requested_workspace_id=requested_workspace_id,
            grant_state=grant.state,
            expires_at=grant.expires_at,
            now=datetime.now(UTC),
        )
        await self._scope(grant.client_workspace_id)
        return Principal(
            subject_id=user_id,
            workspace_id=grant.client_workspace_id,
            session_id=None,
            permissions=frozenset(grant.scopes),
            authentication_method="client_portal_grant",
        )

    async def queue_client_provisioning(
        self, principal: Principal, data: ProvisionClientCreate
    ) -> ClientProvisioningRequest:
        await self._scope(principal.workspace_id)
        agency = await self._agency(principal.workspace_id)
        payload = data.model_dump(mode="json")
        request_hash = _canonical_hash(payload)
        existing = await self._session.scalar(
            select(ClientProvisioningRequest).where(
                ClientProvisioningRequest.workspace_id == principal.workspace_id,
                ClientProvisioningRequest.requested_by == principal.subject_id,
                ClientProvisioningRequest.idempotency_key == data.idempotency_key,
            )
        )
        if existing is not None:
            if existing.request_hash != request_hash:
                raise AppError("IDEMPOTENCY_KEY_REUSED", "같은 Provisioning 멱등키의 요청이 다릅니다.", 409)
            return existing
        value = ClientProvisioningRequest(
            workspace_id=principal.workspace_id,
            agency_id=agency.id,
            requested_by=principal.subject_id,
            idempotency_key=data.idempotency_key,
            request_hash=request_hash,
            workspace_request=data.workspace,
            requested_permissions=sorted(data.permissions),
            state=ProvisioningState.QUEUED.value,
        )
        self._session.add(value)
        await self._session.flush()
        await self._record(
            principal=principal,
            action="b2b.client_provisioning.queued",
            target_type="client_provisioning_request",
            target_id=value.id,
            details={"state": value.state},
        )
        return value

    async def execute_client_provisioning(
        self,
        workspace_id: UUID,
        request_id: UUID,
        *,
        provisioner: WorkspaceProvisioner,
    ) -> ClientProvisioningRequest:
        """Worker boundary; request_id is the provider idempotency key."""

        await self._scope(workspace_id)
        value = await self._session.scalar(
            select(ClientProvisioningRequest)
            .where(
                ClientProvisioningRequest.workspace_id == workspace_id,
                ClientProvisioningRequest.id == request_id,
            )
            .with_for_update()
        )
        if value is None:
            raise AppError(
                "CLIENT_PROVISIONING_NOT_FOUND",
                "고객 Workspace Provisioning 요청을 찾을 수 없습니다.",
                404,
            )
        if value.state in {
            ProvisioningState.SUCCEEDED.value,
            ProvisioningState.FAILED.value,
        }:
            return value
        if value.state not in {
            ProvisioningState.QUEUED.value,
            ProvisioningState.RUNNING.value,
        }:
            raise AppError(
                "CLIENT_PROVISIONING_STATE_INVALID",
                "대기 또는 실행 중인 Provisioning 요청만 처리할 수 있습니다.",
                409,
            )
        value.state = ProvisioningState.RUNNING.value
        await self._session.flush()
        result = await provisioner.provision(
            request_id=value.id,
            workspace_request=value.workspace_request,
        )
        operation_ref = result.operation_ref.strip()
        if (
            not isinstance(result.workspace_id, UUID)
            or result.workspace_id.int == 0
            or result.workspace_id == workspace_id
            or not operation_ref
            or operation_ref != result.operation_ref
            or len(operation_ref) > 500
        ):
            raise AppError(
                "CLIENT_PROVISIONER_RESULT_INVALID",
                "Provisioner 결과가 고객 Workspace 계약과 일치하지 않습니다.",
                503,
            )
        ensure_client_isolation(
            agency_workspace_id=workspace_id,
            client_workspace_id=result.workspace_id,
        )
        value.state = ProvisioningState.SUCCEEDED.value
        value.provisioned_workspace_id = result.workspace_id
        value.provider_operation_ref = operation_ref
        value.error_code = None
        value.error_detail = None
        await add_outbox_event(
            self._session,
            workspace_id=workspace_id,
            aggregate_type="client_provisioning_request",
            aggregate_id=str(value.id),
            event_type="b2b.client_provisioning.succeeded",
            schema_version=_SCHEMA_VERSION,
            payload={
                "workspace_id": str(workspace_id),
                "client_provisioning_request_id": str(value.id),
                "provisioned_workspace_id": str(value.provisioned_workspace_id),
                "provider_operation_ref": value.provider_operation_ref,
            },
        )
        await self._session.flush()
        return value

    async def fail_client_provisioning(
        self,
        workspace_id: UUID,
        request_id: UUID,
        *,
        error_code: str,
    ) -> ClientProvisioningRequest:
        """Persist a terminal worker failure without changing terminal replays."""

        await self._scope(workspace_id)
        value = await self._session.scalar(
            select(ClientProvisioningRequest)
            .where(
                ClientProvisioningRequest.workspace_id == workspace_id,
                ClientProvisioningRequest.id == request_id,
            )
            .with_for_update()
        )
        if value is None:
            raise AppError(
                "CLIENT_PROVISIONING_NOT_FOUND",
                "고객 Workspace Provisioning 요청을 찾을 수 없습니다.",
                404,
            )
        if value.state in {
            ProvisioningState.SUCCEEDED.value,
            ProvisioningState.FAILED.value,
        }:
            return value
        if value.state not in {
            ProvisioningState.QUEUED.value,
            ProvisioningState.RUNNING.value,
        }:
            raise AppError(
                "CLIENT_PROVISIONING_STATE_INVALID",
                "대기 또는 실행 중인 Provisioning 요청만 실패 처리할 수 있습니다.",
                409,
            )
        value.state = ProvisioningState.FAILED.value
        value.error_code = error_code[:120] or "CLIENT_PROVISIONING_FAILED"
        value.error_detail = "Provisioning worker failed; inspect the recorded error code."
        await add_outbox_event(
            self._session,
            workspace_id=workspace_id,
            aggregate_type="client_provisioning_request",
            aggregate_id=str(value.id),
            event_type="b2b.client_provisioning.failed",
            schema_version=_SCHEMA_VERSION,
            payload={
                "workspace_id": str(workspace_id),
                "client_provisioning_request_id": str(value.id),
                "error_code": value.error_code,
            },
        )
        await self._session.flush()
        return value

    async def create_credit_allocation_policy(
        self,
        principal: Principal,
        agency_client_id: UUID,
        data: CreditAllocationPolicyCreate,
    ) -> AgencyCreditAllocationPolicy:
        await self._scope(principal.workspace_id)
        relationship = await self._session.scalar(
            select(AgencyClient).where(
                AgencyClient.workspace_id == principal.workspace_id,
                AgencyClient.id == agency_client_id,
                AgencyClient.state == AgencyClientState.ACTIVE.value,
            )
        )
        if relationship is None:
            raise AppError("AGENCY_CLIENT_NOT_FOUND", "활성 고객 관계를 찾을 수 없습니다.", 404)
        payload = data.model_dump(mode="json")
        value = AgencyCreditAllocationPolicy(
            workspace_id=principal.workspace_id,
            agency_client_id=relationship.id,
            version=data.version,
            monthly_credit_limit=data.monthly_credit_limit,
            overage_policy=data.overage_policy,
            policy_hash=_canonical_hash(payload),
            effective_at=data.effective_at,
            created_by=principal.subject_id,
        )
        self._session.add(value)
        await self._session.flush()
        await self._record(
            principal=principal,
            action="b2b.credit_allocation_policy.created",
            target_type="agency_credit_allocation_policy",
            target_id=value.id,
            details={
                "client_workspace_id": str(relationship.client_workspace_id),
                "version": value.version,
            },
        )
        return value
