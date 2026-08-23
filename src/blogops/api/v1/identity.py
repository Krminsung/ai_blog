"""Versioned HTTP routes for authentication and workspace administration.

The router intentionally has no `/v1` prefix; the central v1 registry owns that concern.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status

from blogops.core.context import Principal
from blogops.domain.identity.dependencies import (
    IdentitySecurity,
    get_current_principal,
    get_enterprise_identity_service,
    get_identity_security,
    get_identity_service,
    get_mfa_setup_principal,
    get_reconsent_principal,
    get_workspace_service,
    request_ip_hash,
    request_user_agent_hash,
)
from blogops.domain.identity.schemas import (
    AuditLogView,
    EmailAddressRequest,
    FederatedConnectionCreateRequest,
    FederatedConnectionView,
    InvitationAcceptRequest,
    InvitationCreateRequest,
    InvitationView,
    LoginRequest,
    LoginResponse,
    MembershipRoleUpdateRequest,
    MembershipView,
    MFAConfirmRequest,
    MFAConfirmResponse,
    MFADisableRequest,
    MFAEnrollmentResponse,
    MFALoginVerifyRequest,
    MessageResponse,
    OneTimeTokenRequest,
    OwnershipTransferRequest,
    PasswordResetConfirmRequest,
    RefreshRequest,
    RoleCreateRequest,
    RoleUpdateRequest,
    RoleView,
    SCIMConfigurationCreated,
    SCIMConfigurationCreateRequest,
    SessionView,
    SignupRequest,
    SignupResponse,
    TermsConsentRequest,
    TokenPair,
    UserProfileUpdateRequest,
    UserView,
    WorkspaceAuthenticationPolicyUpdate,
    WorkspaceAuthenticationPolicyView,
    WorkspaceCreateRequest,
    WorkspaceUpdateRequest,
    WorkspaceView,
)
from blogops.domain.identity.services import (
    EnterpriseIdentityService,
    IdentityService,
    TokenPairResult,
    WorkspaceService,
)


router = APIRouter()
AuthenticatedPrincipal = Annotated[Principal, Depends(get_current_principal)]
ReconsentPrincipal = Annotated[Principal, Depends(get_reconsent_principal)]
MFASetupPrincipal = Annotated[Principal, Depends(get_mfa_setup_principal)]
IdentityDependency = Annotated[IdentityService, Depends(get_identity_service)]
WorkspaceDependency = Annotated[WorkspaceService, Depends(get_workspace_service)]
EnterpriseDependency = Annotated[
    EnterpriseIdentityService, Depends(get_enterprise_identity_service)
]
SecurityDependency = Annotated[IdentitySecurity, Depends(get_identity_security)]


@router.post(
    "/auth/signup",
    response_model=SignupResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["authentication"],
)
async def signup(
    body: SignupRequest,
    request: Request,
    identity: IdentityDependency,
    security: SecurityDependency,
) -> SignupResponse:
    result = await identity.signup(body, ip_hash=request_ip_hash(request, security))
    # The raw token is deliberately not serialized. The mail dispatcher reconstructs it from
    # the token id carried by the transactional outbox event.
    return SignupResponse(user=result.user, workspace=result.workspace)


@router.post("/auth/email/verify", response_model=UserView, tags=["authentication"])
async def verify_email(body: OneTimeTokenRequest, identity: IdentityDependency) -> UserView:
    user = await identity.verify_email(body.token.get_secret_value())
    return UserView.model_validate(user)


@router.post("/auth/email/resend", response_model=MessageResponse, tags=["authentication"])
async def resend_email_verification(
    body: EmailAddressRequest,
    request: Request,
    identity: IdentityDependency,
    security: SecurityDependency,
) -> MessageResponse:
    await identity.resend_email_verification(
        body.email, ip_hash=request_ip_hash(request, security)
    )
    return MessageResponse(message="해당 계정이 있다면 인증 메일을 발송했습니다.")


@router.post("/auth/password/forgot", response_model=MessageResponse, tags=["authentication"])
async def request_password_reset(
    body: EmailAddressRequest,
    request: Request,
    identity: IdentityDependency,
    security: SecurityDependency,
) -> MessageResponse:
    await identity.request_password_reset(body.email, ip_hash=request_ip_hash(request, security))
    return MessageResponse(message="해당 계정이 있다면 비밀번호 재설정 메일을 발송했습니다.")


@router.post("/auth/password/reset", response_model=MessageResponse, tags=["authentication"])
async def reset_password(
    body: PasswordResetConfirmRequest,
    request: Request,
    identity: IdentityDependency,
    security: SecurityDependency,
) -> MessageResponse:
    await identity.reset_password(
        body.token.get_secret_value(),
        body.new_password.get_secret_value(),
        revoke_all_sessions=body.revoke_all_sessions,
        ip_hash=request_ip_hash(request, security),
    )
    return MessageResponse(message="비밀번호가 변경되었습니다.")


@router.post("/auth/login", response_model=LoginResponse, tags=["authentication"])
async def login(
    body: LoginRequest,
    request: Request,
    identity: IdentityDependency,
    security: SecurityDependency,
) -> LoginResponse:
    result = await identity.login(
        email=body.email,
        password=body.password.get_secret_value(),
        requested_workspace_id=body.workspace_id,
        device_name=body.device_name,
        device_id_hash=security.tokens.identifier_digest(body.device_id),
        user_agent_hash=request_user_agent_hash(request, security),
        ip_hash=request_ip_hash(request, security),
        country_code=body.country_code,
    )
    return LoginResponse(
        mfa_required=result.mfa_required,
        challenge_token=result.challenge_token,
        tokens=_token_pair(result.tokens) if result.tokens else None,
    )


@router.post("/auth/mfa/verify", response_model=TokenPair, tags=["authentication"])
async def verify_mfa_login(
    body: MFALoginVerifyRequest,
    identity: IdentityDependency,
) -> TokenPair:
    result = await identity.verify_mfa_login(
        body.challenge_token.get_secret_value(), body.code.get_secret_value()
    )
    return _token_pair(result)


@router.post("/auth/token/refresh", response_model=TokenPair, tags=["authentication"])
async def refresh_tokens(
    body: RefreshRequest,
    request: Request,
    identity: IdentityDependency,
    security: SecurityDependency,
) -> TokenPair:
    result = await identity.refresh(
        body.refresh_token.get_secret_value(),
        requested_workspace_id=body.workspace_id,
        ip_hash=request_ip_hash(request, security),
    )
    return _token_pair(result)


@router.get("/auth/sessions", response_model=list[SessionView], tags=["authentication"])
async def list_sessions(
    principal: AuthenticatedPrincipal,
    identity: IdentityDependency,
) -> list[SessionView]:
    sessions = await identity.list_sessions(principal.subject_id)
    return [SessionView.model_validate(item) for item in sessions]


@router.delete(
    "/auth/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["authentication"],
)
async def revoke_session(
    session_id: UUID,
    principal: AuthenticatedPrincipal,
    identity: IdentityDependency,
) -> Response:
    await identity.revoke_session(
        user_id=principal.subject_id,
        session_id=session_id,
        workspace_id=principal.workspace_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/auth/sessions", response_model=MessageResponse, tags=["authentication"])
async def revoke_all_sessions(
    principal: AuthenticatedPrincipal,
    identity: IdentityDependency,
    keep_current: bool = Query(default=False),
) -> MessageResponse:
    await identity.revoke_all_sessions(
        user_id=principal.subject_id,
        workspace_id=principal.workspace_id,
        except_session_id=principal.session_id if keep_current else None,
    )
    return MessageResponse(message="세션을 종료했습니다.")


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT, tags=["authentication"])
async def logout(
    principal: AuthenticatedPrincipal,
    identity: IdentityDependency,
) -> Response:
    assert principal.session_id is not None
    await identity.revoke_session(
        user_id=principal.subject_id,
        session_id=principal.session_id,
        workspace_id=principal.workspace_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/auth/mfa/enroll", response_model=MFAEnrollmentResponse, tags=["authentication"])
async def begin_mfa_enrollment(
    principal: MFASetupPrincipal,
    identity: IdentityDependency,
) -> MFAEnrollmentResponse:
    result = await identity.begin_mfa_enrollment(
        user_id=principal.subject_id, workspace_id=principal.workspace_id
    )
    return MFAEnrollmentResponse(
        factor_id=result.factor.id,
        secret=result.secret,
        provisioning_uri=result.provisioning_uri,
    )


@router.post("/auth/mfa/confirm", response_model=MFAConfirmResponse, tags=["authentication"])
async def confirm_mfa_enrollment(
    body: MFAConfirmRequest,
    principal: MFASetupPrincipal,
    identity: IdentityDependency,
) -> MFAConfirmResponse:
    recovery_codes = await identity.confirm_mfa_enrollment(
        user_id=principal.subject_id,
        workspace_id=principal.workspace_id,
        factor_id=body.factor_id,
        code=body.code.get_secret_value(),
    )
    return MFAConfirmResponse(recovery_codes=recovery_codes)


@router.post("/auth/mfa/reauthenticate", response_model=MessageResponse, tags=["authentication"])
async def reauthenticate_mfa(
    body: MFADisableRequest,
    principal: MFASetupPrincipal,
    identity: IdentityDependency,
) -> MessageResponse:
    await identity.reauthenticate_mfa(principal, body.code.get_secret_value())
    return MessageResponse(message="MFA 재인증을 완료했습니다.")


@router.delete("/auth/mfa", response_model=MessageResponse, tags=["authentication"])
async def disable_mfa(
    body: MFADisableRequest,
    principal: AuthenticatedPrincipal,
    identity: IdentityDependency,
) -> MessageResponse:
    await identity.disable_mfa(
        user_id=principal.subject_id,
        workspace_id=principal.workspace_id,
        code=body.code.get_secret_value(),
    )
    return MessageResponse(message="MFA를 해제했습니다.")


@router.post("/auth/terms/consents", response_model=MessageResponse, tags=["authentication"])
async def accept_terms(
    body: TermsConsentRequest,
    request: Request,
    principal: ReconsentPrincipal,
    identity: IdentityDependency,
    security: SecurityDependency,
) -> MessageResponse:
    await identity.record_terms_consents(
        user_id=principal.subject_id,
        workspace_id=principal.workspace_id,
        consents=body.consents,
        ip_hash=request_ip_hash(request, security),
    )
    return MessageResponse(message="약관 동의를 기록했습니다.")


@router.get("/auth/me", response_model=UserView, tags=["authentication"])
async def get_profile(
    principal: AuthenticatedPrincipal,
    identity: IdentityDependency,
) -> UserView:
    user = await identity.get_user(principal.subject_id)
    return UserView.model_validate(user)


@router.patch("/auth/me", response_model=UserView, tags=["authentication"])
async def update_profile(
    body: UserProfileUpdateRequest,
    principal: AuthenticatedPrincipal,
    identity: IdentityDependency,
) -> UserView:
    user = await identity.update_profile(principal, body)
    return UserView.model_validate(user)


@router.delete("/auth/account", response_model=MessageResponse, tags=["authentication"])
async def request_account_deletion(
    principal: AuthenticatedPrincipal,
    identity: IdentityDependency,
) -> MessageResponse:
    await identity.request_account_deletion(principal)
    return MessageResponse(message="계정 삭제를 요청했습니다.")


@router.post(
    "/auth/invitations/accept", response_model=MessageResponse, tags=["authentication"]
)
async def accept_invitation(
    body: InvitationAcceptRequest,
    principal: AuthenticatedPrincipal,
    workspaces: WorkspaceDependency,
) -> MessageResponse:
    await workspaces.accept_invitation(principal.subject_id, body.token.get_secret_value())
    return MessageResponse(message="초대를 수락했습니다.")


@router.get("/workspaces", response_model=list[WorkspaceView], tags=["workspaces"])
async def list_workspaces(
    principal: AuthenticatedPrincipal,
    workspaces: WorkspaceDependency,
) -> list[WorkspaceView]:
    items = await workspaces.list_workspaces(principal.subject_id)
    return [WorkspaceView.model_validate(item) for item in items]


@router.post(
    "/workspaces",
    response_model=WorkspaceView,
    status_code=status.HTTP_201_CREATED,
    tags=["workspaces"],
)
async def create_workspace(
    body: WorkspaceCreateRequest,
    principal: AuthenticatedPrincipal,
    workspaces: WorkspaceDependency,
) -> WorkspaceView:
    workspace = await workspaces.create_workspace(principal.subject_id, body)
    return WorkspaceView.model_validate(workspace)


@router.get("/workspaces/{workspace_id}", response_model=WorkspaceView, tags=["workspaces"])
async def get_workspace(
    workspace_id: UUID,
    principal: AuthenticatedPrincipal,
    workspaces: WorkspaceDependency,
) -> WorkspaceView:
    workspace = await workspaces.get_workspace(principal.subject_id, workspace_id)
    return WorkspaceView.model_validate(workspace)


@router.patch("/workspaces/{workspace_id}", response_model=WorkspaceView, tags=["workspaces"])
async def update_workspace(
    workspace_id: UUID,
    body: WorkspaceUpdateRequest,
    principal: AuthenticatedPrincipal,
    workspaces: WorkspaceDependency,
) -> WorkspaceView:
    workspace = await workspaces.update_workspace(principal.subject_id, workspace_id, body)
    return WorkspaceView.model_validate(workspace)


@router.delete(
    "/workspaces/{workspace_id}", response_model=WorkspaceView, tags=["workspaces"]
)
async def schedule_workspace_deletion(
    workspace_id: UUID,
    principal: AuthenticatedPrincipal,
    workspaces: WorkspaceDependency,
) -> WorkspaceView:
    workspace = await workspaces.schedule_workspace_deletion(
        principal.subject_id, workspace_id
    )
    return WorkspaceView.model_validate(workspace)


@router.get(
    "/workspaces/{workspace_id}/roles", response_model=list[RoleView], tags=["workspaces"]
)
async def list_roles(
    workspace_id: UUID,
    principal: AuthenticatedPrincipal,
    workspaces: WorkspaceDependency,
) -> list[RoleView]:
    roles = await workspaces.list_roles(principal.subject_id, workspace_id)
    return [RoleView.model_validate(item) for item in roles]


@router.post(
    "/workspaces/{workspace_id}/roles",
    response_model=RoleView,
    status_code=status.HTTP_201_CREATED,
    tags=["workspaces"],
)
async def create_role(
    workspace_id: UUID,
    body: RoleCreateRequest,
    principal: AuthenticatedPrincipal,
    workspaces: WorkspaceDependency,
) -> RoleView:
    role = await workspaces.create_role(principal.subject_id, workspace_id, body)
    return RoleView.model_validate(role)


@router.patch(
    "/workspaces/{workspace_id}/roles/{role_id}",
    response_model=RoleView,
    tags=["workspaces"],
)
async def update_role(
    workspace_id: UUID,
    role_id: UUID,
    body: RoleUpdateRequest,
    principal: AuthenticatedPrincipal,
    workspaces: WorkspaceDependency,
) -> RoleView:
    role = await workspaces.update_role(principal.subject_id, workspace_id, role_id, body)
    return RoleView.model_validate(role)


@router.get(
    "/workspaces/{workspace_id}/members",
    response_model=list[MembershipView],
    tags=["workspaces"],
)
async def list_members(
    workspace_id: UUID,
    principal: AuthenticatedPrincipal,
    workspaces: WorkspaceDependency,
) -> list[MembershipView]:
    members = await workspaces.list_members(principal.subject_id, workspace_id)
    return [item.to_view() for item in members]


@router.post(
    "/workspaces/{workspace_id}/members/invite",
    response_model=InvitationView,
    status_code=status.HTTP_201_CREATED,
    tags=["workspaces"],
)
async def invite_member(
    workspace_id: UUID,
    body: InvitationCreateRequest,
    principal: AuthenticatedPrincipal,
    workspaces: WorkspaceDependency,
) -> InvitationView:
    result = await workspaces.invite_member(principal.subject_id, workspace_id, body)
    return InvitationView.model_validate(result.invitation)


@router.delete(
    "/workspaces/{workspace_id}/invitations/{invitation_id}",
    response_model=InvitationView,
    tags=["workspaces"],
)
async def cancel_invitation(
    workspace_id: UUID,
    invitation_id: UUID,
    principal: AuthenticatedPrincipal,
    workspaces: WorkspaceDependency,
) -> InvitationView:
    invitation = await workspaces.cancel_invitation(
        principal.subject_id, workspace_id, invitation_id
    )
    return InvitationView.model_validate(invitation)


@router.patch(
    "/workspaces/{workspace_id}/members/{user_id}",
    response_model=MessageResponse,
    tags=["workspaces"],
)
async def change_member_role(
    workspace_id: UUID,
    user_id: UUID,
    body: MembershipRoleUpdateRequest,
    principal: AuthenticatedPrincipal,
    workspaces: WorkspaceDependency,
) -> MessageResponse:
    await workspaces.change_member_role(
        principal.subject_id, workspace_id, user_id, body.role_id
    )
    return MessageResponse(message="멤버 역할을 변경했습니다.")


@router.delete(
    "/workspaces/{workspace_id}/members/{user_id}",
    response_model=MessageResponse,
    tags=["workspaces"],
)
async def remove_member(
    workspace_id: UUID,
    user_id: UUID,
    principal: AuthenticatedPrincipal,
    workspaces: WorkspaceDependency,
) -> MessageResponse:
    await workspaces.remove_member(principal.subject_id, workspace_id, user_id)
    return MessageResponse(message="멤버를 제거했습니다.")


@router.post(
    "/workspaces/{workspace_id}/ownership/transfer",
    response_model=MessageResponse,
    tags=["workspaces"],
)
async def transfer_ownership(
    workspace_id: UUID,
    body: OwnershipTransferRequest,
    principal: AuthenticatedPrincipal,
    workspaces: WorkspaceDependency,
) -> MessageResponse:
    await workspaces.transfer_ownership(principal, workspace_id, body.new_owner_user_id)
    return MessageResponse(message="워크스페이스 소유권을 이전했습니다.")


@router.get(
    "/workspaces/{workspace_id}/authentication-policy",
    response_model=WorkspaceAuthenticationPolicyView,
    tags=["workspaces"],
)
async def get_authentication_policy(
    workspace_id: UUID,
    principal: AuthenticatedPrincipal,
    workspaces: WorkspaceDependency,
) -> WorkspaceAuthenticationPolicyView:
    policy = await workspaces.get_authentication_policy(principal.subject_id, workspace_id)
    return WorkspaceAuthenticationPolicyView.model_validate(policy)


@router.patch(
    "/workspaces/{workspace_id}/authentication-policy",
    response_model=WorkspaceAuthenticationPolicyView,
    tags=["workspaces"],
)
async def update_authentication_policy(
    workspace_id: UUID,
    body: WorkspaceAuthenticationPolicyUpdate,
    principal: AuthenticatedPrincipal,
    workspaces: WorkspaceDependency,
) -> WorkspaceAuthenticationPolicyView:
    policy = await workspaces.update_authentication_policy(
        principal.subject_id, workspace_id, body
    )
    return WorkspaceAuthenticationPolicyView.model_validate(policy)


@router.get(
    "/workspaces/{workspace_id}/audit-logs",
    response_model=list[AuditLogView],
    tags=["workspaces"],
)
async def list_audit_logs(
    workspace_id: UUID,
    principal: AuthenticatedPrincipal,
    workspaces: WorkspaceDependency,
    limit: int = Query(default=100, ge=1, le=200),
) -> list[AuditLogView]:
    logs = await workspaces.list_audit_logs(
        principal.subject_id, workspace_id, limit=limit
    )
    return [AuditLogView.model_validate(item) for item in logs]


@router.post(
    "/workspaces/{workspace_id}/federation/connections",
    response_model=FederatedConnectionView,
    status_code=status.HTTP_201_CREATED,
    tags=["enterprise-identity"],
)
async def create_federated_connection(
    workspace_id: UUID,
    body: FederatedConnectionCreateRequest,
    principal: AuthenticatedPrincipal,
    enterprise: EnterpriseDependency,
) -> FederatedConnectionView:
    connection = await enterprise.create_federated_connection(
        principal.subject_id, workspace_id, body
    )
    return FederatedConnectionView.model_validate(connection)


@router.post(
    "/workspaces/{workspace_id}/scim/configuration",
    response_model=SCIMConfigurationCreated,
    status_code=status.HTTP_201_CREATED,
    tags=["enterprise-identity"],
)
async def configure_scim(
    workspace_id: UUID,
    body: SCIMConfigurationCreateRequest,
    principal: AuthenticatedPrincipal,
    enterprise: EnterpriseDependency,
) -> SCIMConfigurationCreated:
    result = await enterprise.configure_scim(principal.subject_id, workspace_id, body)
    return SCIMConfigurationCreated(
        id=result.configuration.id,
        workspace_id=result.configuration.workspace_id,
        bearer_token=result.bearer_token,
        provider_key=result.configuration.provider_key,
    )


def _token_pair(result: TokenPairResult) -> TokenPair:
    return TokenPair(
        access_token=result.access_token,
        refresh_token=result.refresh_token,
        expires_in=result.expires_in,
        session_id=result.session_id,
        workspace_id=result.workspace_id,
    )
