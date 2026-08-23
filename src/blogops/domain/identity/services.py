"""Transactional identity, session and organization application services."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import re
import unicodedata
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.core.context import Principal
from blogops.core.errors import AppError
from blogops.core.permissions import Permission
from blogops.db.models.foundation import AuditLog
from blogops.db.session import apply_workspace_scope
from blogops.domain.identity.enums import (
    AgencyClientStatus,
    ChallengePurpose,
    ConnectionStatus,
    CredentialKind,
    InvitationStatus,
    MembershipStatus,
    MFAFactorKind,
    MFAFactorStatus,
    OneTimeTokenPurpose,
    SCIMResourceType,
    SessionStatus,
    UserStatus,
    WorkspaceStatus,
)
from blogops.domain.identity.models import (
    Agency,
    AgencyClient,
    AuthenticationChallenge,
    ExternalIdentity,
    FederatedProviderConnection,
    LoginSession,
    Membership,
    MFAFactor,
    MFARecoveryCode,
    OneTimeToken,
    Role,
    SCIMConfiguration,
    SCIMResourceLink,
    SessionRefreshToken,
    TermsConsent,
    TermsDocumentVersion,
    User,
    UserCredential,
    Workspace,
    WorkspaceAuthenticationPolicy,
    WorkspaceInvitation,
)
from blogops.domain.identity.providers import FederatedIdentityClaims, SCIMUserPayload
from blogops.domain.identity.schemas import (
    AgencyClientCreateRequest,
    AgencyCreateRequest,
    FederatedConnectionCreateRequest,
    InvitationCreateRequest,
    MembershipView,
    RoleCreateRequest,
    RoleUpdateRequest,
    SCIMConfigurationCreateRequest,
    SignupRequest,
    TermsAcceptance,
    UserProfileUpdateRequest,
    WorkspaceAuthenticationPolicyUpdate,
    WorkspaceCreateRequest,
    WorkspaceUpdateRequest,
)
from blogops.domain.identity.security import (
    InvalidAccessToken,
    PasswordManager,
    SecretEnvelope,
    TokenManager,
    generate_totp_secret,
    invitation_workspace_id,
    issue_recovery_codes,
    normalize_email,
    provisioning_uri,
    recovery_code_digest,
    utc_now,
    validate_password,
    verify_totp,
)
from blogops.services.audit import append_audit_log
from blogops.services.outbox import add_outbox_event


DEFAULT_ACCESS_TTL = timedelta(minutes=15)
DEFAULT_SESSION_TTL = timedelta(days=30)
EMAIL_VERIFICATION_TTL = timedelta(hours=24)
PASSWORD_RESET_TTL = timedelta(hours=1)
MFA_CHALLENGE_TTL = timedelta(minutes=5)
LOGIN_FAILURE_LIMIT = 5
LOGIN_LOCKOUT = timedelta(minutes=15)
EMAIL_SECURITY_REQUEST_LIMIT_PER_HOUR = 5


@dataclass(slots=True)
class SignupResult:
    user: User
    workspace: Workspace
    verification_token: str


@dataclass(slots=True)
class TokenPairResult:
    access_token: str
    refresh_token: str
    expires_in: int
    session_id: UUID
    workspace_id: UUID


@dataclass(slots=True)
class LoginResult:
    mfa_required: bool
    challenge_token: str | None = None
    tokens: TokenPairResult | None = None


@dataclass(slots=True)
class MFAEnrollmentResult:
    factor: MFAFactor
    secret: str
    provisioning_uri: str


@dataclass(slots=True)
class InvitationResult:
    invitation: WorkspaceInvitation
    token: str


@dataclass(slots=True)
class SCIMConfigurationResult:
    configuration: SCIMConfiguration
    bearer_token: str


@dataclass(slots=True)
class MembershipRecord:
    membership: Membership
    user: User
    role: Role

    def to_view(self) -> MembershipView:
        return MembershipView(
            id=self.membership.id,
            workspace_id=self.membership.workspace_id,
            user_id=self.user.id,
            email=self.user.email,
            display_name=self.user.display_name,
            role=self.role,
            status=self.membership.status,
            joined_at=self.membership.joined_at,
        )


class IdentityService:
    """Account, credential, MFA and refreshable-session behavior."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        passwords: PasswordManager,
        tokens: TokenManager,
        envelope: SecretEnvelope,
    ) -> None:
        self.session = session
        self.passwords = passwords
        self.tokens = tokens
        self.envelope = envelope

    async def signup(
        self,
        request: SignupRequest,
        *,
        ip_hash: str | None,
    ) -> SignupResult:
        existing = await self.session.scalar(select(User.id).where(User.email == request.email))
        if existing is not None:
            raise AppError(
                code="EMAIL_ALREADY_REGISTERED",
                message="이미 가입된 이메일입니다.",
                status_code=409,
            )

        now = utc_now()
        await self._validate_signup_terms(request.terms, now=now)
        user = User(
            id=uuid4(),
            email=request.email,
            display_name=request.display_name,
            status=UserStatus.PENDING_EMAIL.value,
            locale=request.locale,
            timezone=request.timezone,
        )
        credential = UserCredential(
            id=uuid4(),
            user_id=user.id,
            kind=CredentialKind.PASSWORD.value,
            password_hash=self.passwords.hash(request.password.get_secret_value()),
            password_changed_at=now,
        )
        workspace, roles, policy = _new_workspace_bundle(
            creator_id=user.id,
            name=request.workspace_name,
            industry=request.industry,
            country_code=request.country_code,
            timezone=request.timezone,
            default_locale=request.locale,
            data_region="ap-northeast",
        )
        owner_role = roles["owner"]
        membership = Membership(
            id=uuid4(),
            workspace_id=workspace.id,
            user_id=user.id,
            role_id=owner_role.id,
            status=MembershipStatus.ACTIVE.value,
            joined_at=now,
        )
        consents = [
            TermsConsent(
                id=uuid4(),
                user_id=user.id,
                document_type=item.document_type.casefold(),
                document_version=item.document_version,
                required=item.required,
                accepted_at=now,
                accepted_ip_hash=ip_hash,
            )
            for item in request.terms
        ]
        token_record = OneTimeToken(
            id=uuid4(),
            user_id=user.id,
            purpose=OneTimeTokenPurpose.EMAIL_VERIFICATION.value,
            token_hash="pending",
            expires_at=now + EMAIL_VERIFICATION_TTL,
            requested_ip_hash=ip_hash,
        )
        raw_token, token_hash = self.tokens.derive_opaque("ev", token_record.id)
        token_record.token_hash = token_hash

        await apply_workspace_scope(self.session, workspace.id)
        self.session.add_all(
            [
                user,
                credential,
                workspace,
                *roles.values(),
                policy,
                membership,
                *consents,
                token_record,
            ]
        )
        await self.session.flush()
        await append_audit_log(
            self.session,
            workspace_id=workspace.id,
            actor_id=user.id,
            action="identity.user.signed_up",
            target_type="user",
            target_id=str(user.id),
            details={"owner_workspace_created": True},
            ip_hash=ip_hash,
        )
        await add_outbox_event(
            self.session,
            workspace_id=workspace.id,
            aggregate_type="user",
            aggregate_id=str(user.id),
            event_type="identity.email_verification.requested",
            schema_version="1",
            payload={
                "user_id": str(user.id),
                "email": user.email,
                "token_id": str(token_record.id),
                "token_prefix": "ev",
                "expires_at": token_record.expires_at.isoformat(),
            },
        )
        return SignupResult(user=user, workspace=workspace, verification_token=raw_token)

    async def verify_email(self, raw_token: str) -> User:
        now = utc_now()
        token = await self.session.scalar(
            select(OneTimeToken)
            .where(
                OneTimeToken.purpose == OneTimeTokenPurpose.EMAIL_VERIFICATION.value,
                OneTimeToken.token_hash == self.tokens.digest(raw_token),
            )
            .with_for_update()
        )
        _validate_one_time_token(token, now=now)
        assert token is not None
        user = await self.session.get(User, token.user_id, with_for_update=True)
        if user is None:
            raise _invalid_token_error()
        token.consumed_at = now
        if user.email_verified_at is None:
            user.email_verified_at = now
            user.status = UserStatus.ACTIVE.value
        workspace_id = await self._first_workspace_id(user.id)
        if workspace_id is not None:
            await apply_workspace_scope(self.session, workspace_id)
            await append_audit_log(
                self.session,
                workspace_id=workspace_id,
                actor_id=user.id,
                action="identity.email.verified",
                target_type="user",
                target_id=str(user.id),
            )
            await add_outbox_event(
                self.session,
                workspace_id=workspace_id,
                aggregate_type="user",
                aggregate_id=str(user.id),
                event_type="identity.email.verified",
                schema_version="1",
                payload={"user_id": str(user.id)},
            )
        return user

    async def resend_email_verification(
        self, email: str, *, ip_hash: str | None
    ) -> str | None:
        user = await self.session.scalar(select(User).where(User.email == normalize_email(email)))
        if user is None or user.email_verified_at is not None:
            return None
        now = utc_now()
        recent_count = await self.session.scalar(
            select(func.count())
            .select_from(OneTimeToken)
            .where(
                OneTimeToken.user_id == user.id,
                OneTimeToken.purpose == OneTimeTokenPurpose.EMAIL_VERIFICATION.value,
                OneTimeToken.created_at >= now - timedelta(hours=1),
            )
        )
        if (recent_count or 0) >= EMAIL_SECURITY_REQUEST_LIMIT_PER_HOUR:
            return None
        prior_tokens = list(
            await self.session.scalars(
                select(OneTimeToken)
                .where(
                    OneTimeToken.user_id == user.id,
                    OneTimeToken.purpose == OneTimeTokenPurpose.EMAIL_VERIFICATION.value,
                    OneTimeToken.consumed_at.is_(None),
                    OneTimeToken.superseded_at.is_(None),
                )
                .with_for_update()
            )
        )
        for prior in prior_tokens:
            prior.superseded_at = now
        record = OneTimeToken(
            id=uuid4(),
            user_id=user.id,
            purpose=OneTimeTokenPurpose.EMAIL_VERIFICATION.value,
            token_hash="pending",
            expires_at=now + EMAIL_VERIFICATION_TTL,
            requested_ip_hash=ip_hash,
        )
        raw, record.token_hash = self.tokens.derive_opaque("ev", record.id)
        self.session.add(record)
        workspace_id = await self._first_workspace_id(user.id)
        if workspace_id is not None:
            await apply_workspace_scope(self.session, workspace_id)
            await add_outbox_event(
                self.session,
                workspace_id=workspace_id,
                aggregate_type="user",
                aggregate_id=str(user.id),
                event_type="identity.email_verification.requested",
                schema_version="1",
                payload={
                    "user_id": str(user.id),
                    "email": user.email,
                    "token_id": str(record.id),
                    "token_prefix": "ev",
                    "expires_at": record.expires_at.isoformat(),
                },
            )
        return raw

    async def request_password_reset(self, email: str, *, ip_hash: str | None) -> str | None:
        user = await self.session.scalar(select(User).where(User.email == normalize_email(email)))
        if user is None or user.status not in {
            UserStatus.ACTIVE.value,
            UserStatus.DORMANT.value,
        }:
            return None
        now = utc_now()
        recent_count = await self.session.scalar(
            select(func.count())
            .select_from(OneTimeToken)
            .where(
                OneTimeToken.user_id == user.id,
                OneTimeToken.purpose == OneTimeTokenPurpose.PASSWORD_RESET.value,
                OneTimeToken.created_at >= now - timedelta(hours=1),
            )
        )
        if (recent_count or 0) >= EMAIL_SECURITY_REQUEST_LIMIT_PER_HOUR:
            return None
        await self.session.execute(
            update(OneTimeToken)
            .where(
                OneTimeToken.user_id == user.id,
                OneTimeToken.purpose == OneTimeTokenPurpose.PASSWORD_RESET.value,
                OneTimeToken.consumed_at.is_(None),
                OneTimeToken.superseded_at.is_(None),
            )
            .values(superseded_at=now)
        )
        record = OneTimeToken(
            id=uuid4(),
            user_id=user.id,
            purpose=OneTimeTokenPurpose.PASSWORD_RESET.value,
            token_hash="pending",
            expires_at=now + PASSWORD_RESET_TTL,
            requested_ip_hash=ip_hash,
        )
        raw, record.token_hash = self.tokens.derive_opaque("pr", record.id)
        self.session.add(record)
        workspace_id = await self._first_workspace_id(user.id)
        if workspace_id is not None:
            await apply_workspace_scope(self.session, workspace_id)
            await add_outbox_event(
                self.session,
                workspace_id=workspace_id,
                aggregate_type="user",
                aggregate_id=str(user.id),
                event_type="identity.password_reset.requested",
                schema_version="1",
                payload={
                    "user_id": str(user.id),
                    "email": user.email,
                    "token_id": str(record.id),
                    "token_prefix": "pr",
                    "expires_at": record.expires_at.isoformat(),
                },
            )
        return raw

    async def reset_password(
        self,
        raw_token: str,
        new_password: str,
        *,
        revoke_all_sessions: bool,
        ip_hash: str | None,
    ) -> None:
        now = utc_now()
        token = await self.session.scalar(
            select(OneTimeToken)
            .where(
                OneTimeToken.purpose == OneTimeTokenPurpose.PASSWORD_RESET.value,
                OneTimeToken.token_hash == self.tokens.digest(raw_token),
            )
            .with_for_update()
        )
        _validate_one_time_token(token, now=now)
        assert token is not None
        credential = await self.session.scalar(
            select(UserCredential)
            .where(
                UserCredential.user_id == token.user_id,
                UserCredential.kind == CredentialKind.PASSWORD.value,
            )
            .with_for_update()
        )
        if credential is None:
            raise _invalid_token_error()
        validate_password(new_password)
        credential.password_hash = self.passwords.hash(new_password)
        credential.password_changed_at = now
        credential.failed_attempts = 0
        credential.locked_until = None
        token.consumed_at = now
        if revoke_all_sessions:
            await self._revoke_all_sessions(token.user_id, reason="password_reset", now=now)
        workspace_id = await self._first_workspace_id(token.user_id)
        if workspace_id is not None:
            await apply_workspace_scope(self.session, workspace_id)
            await append_audit_log(
                self.session,
                workspace_id=workspace_id,
                actor_id=token.user_id,
                action="identity.password.reset",
                target_type="user",
                target_id=str(token.user_id),
                details={"all_sessions_revoked": revoke_all_sessions},
                ip_hash=ip_hash,
            )

    async def login(
        self,
        *,
        email: str,
        password: str,
        requested_workspace_id: UUID | None,
        device_name: str | None,
        device_id_hash: str | None,
        user_agent_hash: str | None,
        ip_hash: str | None,
        country_code: str | None,
    ) -> LoginResult:
        now = utc_now()
        user = await self.session.scalar(select(User).where(User.email == normalize_email(email)))
        if user is None:
            # Preserve a password-hash cost for unknown accounts without persisting anything.
            self.passwords.verify(self._dummy_password_hash(), password)
            raise _invalid_credentials_error()
        credential = await self.session.scalar(
            select(UserCredential)
            .where(
                UserCredential.user_id == user.id,
                UserCredential.kind == CredentialKind.PASSWORD.value,
            )
            .with_for_update()
        )
        workspace_id = await self._select_workspace(user.id, requested_workspace_id)
        await apply_workspace_scope(self.session, workspace_id)
        policy = await self._authentication_policy(workspace_id)
        email_domain = user.email.rsplit("@", 1)[-1]
        if policy is not None and (
            not policy.password_login_enabled
            or email_domain in {domain.casefold() for domain in policy.sso_enforced_domains}
        ):
            raise AppError(
                code="PASSWORD_LOGIN_DISABLED",
                message="이 워크스페이스는 SSO 로그인이 필요합니다.",
                status_code=403,
            )
        max_failures = policy.max_login_failures if policy else LOGIN_FAILURE_LIMIT
        lockout = timedelta(seconds=policy.lockout_seconds) if policy else LOGIN_LOCKOUT
        if credential is None or (
            credential.locked_until is not None and credential.locked_until > now
        ):
            await append_audit_log(
                self.session,
                workspace_id=workspace_id,
                actor_id=user.id,
                action="identity.login.blocked",
                target_type="user",
                target_id=str(user.id),
                details={"reason": "locked_or_missing_credential"},
                ip_hash=ip_hash,
            )
            await self._commit_security_failure(_login_locked_error())
        assert credential is not None
        if not self.passwords.verify(credential.password_hash, password):
            credential.failed_attempts += 1
            credential.last_failed_at = now
            locked = credential.failed_attempts >= max_failures
            if locked:
                credential.locked_until = now + lockout
            await append_audit_log(
                self.session,
                workspace_id=workspace_id,
                actor_id=user.id,
                action="identity.login.failed",
                target_type="user",
                target_id=str(user.id),
                details={"attempt_count": credential.failed_attempts, "locked": locked},
                ip_hash=ip_hash,
            )
            await self._commit_security_failure(
                _login_locked_error() if locked else _invalid_credentials_error()
            )

        if user.status != UserStatus.ACTIVE.value:
            code = (
                "EMAIL_VERIFICATION_REQUIRED"
                if user.status == UserStatus.PENDING_EMAIL.value
                else "ACCOUNT_UNAVAILABLE"
            )
            await append_audit_log(
                self.session,
                workspace_id=workspace_id,
                actor_id=user.id,
                action="identity.login.blocked",
                target_type="user",
                target_id=str(user.id),
                details={"reason": user.status},
                ip_hash=ip_hash,
            )
            await self._commit_security_failure(
                AppError(code=code, message="계정 상태를 확인해 주세요.", status_code=403)
            )

        credential.failed_attempts = 0
        credential.last_failed_at = None
        credential.locked_until = None
        if self.passwords.needs_rehash(credential.password_hash):
            credential.password_hash = self.passwords.hash(password)

        active_factor = await self.session.scalar(
            select(MFAFactor).where(
                MFAFactor.user_id == user.id,
                MFAFactor.status == MFAFactorStatus.ACTIVE.value,
            )
        )
        if user.mfa_enabled and active_factor is None:
            raise AppError(
                code="ACCOUNT_SECURITY_STATE_INVALID",
                message="MFA 보안 상태를 복구해야 합니다.",
                status_code=403,
            )
        if user.mfa_enabled:
            challenge = AuthenticationChallenge(
                id=uuid4(),
                user_id=user.id,
                purpose=ChallengePurpose.MFA_LOGIN.value,
                token_hash="pending",
                context={
                    "workspace_id": str(workspace_id),
                    "device_name": device_name,
                    "device_id_hash": device_id_hash,
                    "user_agent_hash": user_agent_hash,
                    "ip_hash": ip_hash,
                    "country_code": country_code,
                },
                expires_at=now + MFA_CHALLENGE_TTL,
            )
            raw, challenge.token_hash = self.tokens.issue_opaque("mc")
            self.session.add(challenge)
            await append_audit_log(
                self.session,
                workspace_id=workspace_id,
                actor_id=user.id,
                action="identity.login.mfa_challenged",
                target_type="authentication_challenge",
                target_id=str(challenge.id),
                ip_hash=ip_hash,
            )
            return LoginResult(mfa_required=True, challenge_token=raw)

        pair = await self._create_session(
            user=user,
            workspace_id=workspace_id,
            device_name=device_name,
            device_id_hash=device_id_hash,
            user_agent_hash=user_agent_hash,
            ip_hash=ip_hash,
            country_code=country_code,
            authentication_methods=["password"],
            mfa_verified_at=None,
            now=now,
        )
        return LoginResult(mfa_required=False, tokens=pair)

    async def verify_mfa_login(self, challenge_token: str, code: str) -> TokenPairResult:
        now = utc_now()
        challenge = await self.session.scalar(
            select(AuthenticationChallenge)
            .where(
                AuthenticationChallenge.purpose == ChallengePurpose.MFA_LOGIN.value,
                AuthenticationChallenge.token_hash == self.tokens.digest(challenge_token),
            )
            .with_for_update()
        )
        if (
            challenge is None
            or challenge.consumed_at is not None
            or challenge.expires_at <= now
        ):
            raise AppError(
                code="MFA_CHALLENGE_INVALID",
                message="MFA 인증 요청이 유효하지 않거나 만료되었습니다.",
                status_code=401,
            )
        user = await self.session.get(User, challenge.user_id)
        if user is None or user.status != UserStatus.ACTIVE.value:
            raise AppError(code="ACCOUNT_UNAVAILABLE", message="계정을 사용할 수 없습니다.", status_code=403)
        workspace_id = UUID(str(challenge.context["workspace_id"]))
        await apply_workspace_scope(self.session, workspace_id)
        await self._verify_second_factor(user.id, code, now=now)
        challenge.consumed_at = now
        return await self._create_session(
            user=user,
            workspace_id=workspace_id,
            device_name=_optional_str(challenge.context.get("device_name")),
            device_id_hash=_optional_str(challenge.context.get("device_id_hash")),
            user_agent_hash=_optional_str(challenge.context.get("user_agent_hash")),
            ip_hash=_optional_str(challenge.context.get("ip_hash")),
            country_code=_optional_str(challenge.context.get("country_code")),
            authentication_methods=["password", "totp_or_recovery"],
            mfa_verified_at=now,
            now=now,
        )

    async def refresh(
        self,
        raw_refresh_token: str,
        *,
        requested_workspace_id: UUID | None,
        ip_hash: str | None,
    ) -> TokenPairResult:
        now = utc_now()
        refresh_token = await self.session.scalar(
            select(SessionRefreshToken)
            .where(SessionRefreshToken.token_hash == self.tokens.digest(raw_refresh_token))
            .with_for_update()
        )
        if refresh_token is None:
            raise AppError(
                code="REFRESH_TOKEN_INVALID",
                message="갱신 토큰이 유효하지 않습니다.",
                status_code=401,
            )
        login_session = await self.session.get(
            LoginSession, refresh_token.session_id, with_for_update=True
        )
        if login_session is None:
            raise AppError(
                code="REFRESH_TOKEN_INVALID",
                message="갱신 토큰이 유효하지 않습니다.",
                status_code=401,
            )
        workspace_id = await self._select_workspace(
            login_session.user_id, requested_workspace_id
        )
        await apply_workspace_scope(self.session, workspace_id)
        if refresh_token.consumed_at is not None:
            login_session.status = SessionStatus.COMPROMISED.value
            login_session.revoked_at = now
            login_session.revoke_reason = "refresh_token_reuse"
            await append_audit_log(
                self.session,
                workspace_id=workspace_id,
                actor_id=login_session.user_id,
                action="identity.refresh_token.reuse_detected",
                target_type="login_session",
                target_id=str(login_session.id),
                ip_hash=ip_hash,
            )
            await add_outbox_event(
                self.session,
                workspace_id=workspace_id,
                aggregate_type="login_session",
                aggregate_id=str(login_session.id),
                event_type="identity.session.compromised",
                schema_version="1",
                payload={"user_id": str(login_session.user_id), "reason": "refresh_token_reuse"},
            )
            await self._commit_security_failure(
                AppError(
                    code="REFRESH_TOKEN_REUSE_DETECTED",
                    message="토큰 재사용이 감지되어 세션을 종료했습니다.",
                    status_code=401,
                )
            )
        if (
            login_session.status != SessionStatus.ACTIVE.value
            or login_session.expires_at <= now
            or refresh_token.expires_at <= now
        ):
            if login_session.status == SessionStatus.ACTIVE.value:
                login_session.status = SessionStatus.EXPIRED.value
                login_session.revoked_at = now
                login_session.revoke_reason = "expired"
                await self.session.flush()
                await self.session.commit()
            raise AppError(
                code="SESSION_EXPIRED",
                message="세션이 만료되었습니다.",
                status_code=401,
            )

        user = await self.session.get(User, login_session.user_id)
        if user is None or user.status != UserStatus.ACTIVE.value:
            raise AppError(code="ACCOUNT_UNAVAILABLE", message="계정을 사용할 수 없습니다.", status_code=403)
        raw_new, new_hash = self.tokens.issue_opaque("rt")
        replacement = SessionRefreshToken(
            id=uuid4(),
            session_id=login_session.id,
            token_hash=new_hash,
            generation=refresh_token.generation + 1,
            expires_at=login_session.expires_at,
        )
        refresh_token.consumed_at = now
        refresh_token.replaced_by_id = replacement.id
        login_session.last_activity_at = now
        if ip_hash:
            login_session.ip_hash = ip_hash
        self.session.add(replacement)
        policy = await self._authentication_policy(workspace_id)
        access_ttl = timedelta(
            seconds=policy.access_token_ttl_seconds if policy else int(DEFAULT_ACCESS_TTL.total_seconds())
        )
        access, expires_in = self.tokens.issue_access_token(
            user_id=user.id,
            workspace_id=workspace_id,
            session_id=login_session.id,
            authentication_methods=login_session.authentication_methods,
            ttl=access_ttl,
            now=now,
        )
        return TokenPairResult(
            access_token=access,
            refresh_token=raw_new,
            expires_in=expires_in,
            session_id=login_session.id,
            workspace_id=workspace_id,
        )

    async def resolve_principal(
        self,
        access_token: str,
        *,
        enforce_terms: bool = True,
        enforce_mfa: bool = True,
    ) -> Principal:
        try:
            claims = self.tokens.decode_access_token(access_token)
        except InvalidAccessToken as exc:
            raise AppError(
                code="ACCESS_TOKEN_INVALID",
                message="액세스 토큰이 유효하지 않습니다.",
                status_code=401,
            ) from exc
        now = utc_now()
        login_session = await self.session.get(LoginSession, claims.session_id)
        if (
            login_session is None
            or login_session.user_id != claims.user_id
            or login_session.status != SessionStatus.ACTIVE.value
            or login_session.expires_at <= now
        ):
            raise AppError(code="SESSION_INVALID", message="세션이 유효하지 않습니다.", status_code=401)
        user = await self.session.get(User, claims.user_id)
        if user is None or user.status != UserStatus.ACTIVE.value:
            raise AppError(code="ACCOUNT_UNAVAILABLE", message="계정을 사용할 수 없습니다.", status_code=403)
        if enforce_terms:
            await self._enforce_required_terms(user.id, now=now)
        await apply_workspace_scope(self.session, claims.workspace_id)
        membership, role, workspace = await self._active_membership(
            claims.user_id, claims.workspace_id
        )
        policy = await self._authentication_policy(claims.workspace_id)
        if (
            enforce_mfa
            and policy is not None
            and role.key in set(policy.require_mfa_role_keys)
            and login_session.mfa_verified_at is None
        ):
            raise AppError(
                code="MFA_REQUIRED_BY_POLICY",
                message="이 역할은 MFA 인증이 필요합니다.",
                status_code=403,
            )
        if workspace.status != WorkspaceStatus.ACTIVE.value:
            raise AppError(
                code="WORKSPACE_UNAVAILABLE",
                message="워크스페이스를 사용할 수 없습니다.",
                status_code=403,
            )
        if now - login_session.last_activity_at >= timedelta(minutes=5):
            login_session.last_activity_at = now
        return Principal(
            subject_id=user.id,
            workspace_id=membership.workspace_id,
            session_id=login_session.id,
            permissions=frozenset(role.permissions),
            authentication_method="+".join(login_session.authentication_methods),
        )

    async def list_sessions(self, user_id: UUID) -> list[LoginSession]:
        return list(
            await self.session.scalars(
                select(LoginSession)
                .where(LoginSession.user_id == user_id)
                .order_by(LoginSession.last_activity_at.desc())
            )
        )

    async def get_user(self, user_id: UUID) -> User:
        user = await self.session.get(User, user_id)
        if user is None:
            raise AppError(code="USER_NOT_FOUND", message="사용자를 찾을 수 없습니다.", status_code=404)
        return user

    async def update_profile(
        self,
        principal: Principal,
        request: UserProfileUpdateRequest,
    ) -> User:
        user = await self.session.get(User, principal.subject_id, with_for_update=True)
        if user is None:
            raise AppError(code="USER_NOT_FOUND", message="사용자를 찾을 수 없습니다.", status_code=404)
        for field_name in request.model_fields_set:
            value = getattr(request, field_name)
            if value is None:
                raise AppError(
                    code="PROFILE_VALUE_REQUIRED",
                    message="프로필 값은 null일 수 없습니다.",
                    status_code=422,
                    fields=[{"path": field_name, "reason": "null_not_allowed"}],
                )
            setattr(user, field_name, value.strip() if isinstance(value, str) else value)
        await apply_workspace_scope(self.session, principal.workspace_id)
        await append_audit_log(
            self.session,
            workspace_id=principal.workspace_id,
            actor_id=principal.subject_id,
            action="identity.profile.updated",
            target_type="user",
            target_id=str(user.id),
            details={"fields": sorted(request.model_fields_set)},
        )
        return user

    async def revoke_session(
        self, *, user_id: UUID, session_id: UUID, workspace_id: UUID
    ) -> None:
        login_session = await self.session.get(LoginSession, session_id, with_for_update=True)
        if login_session is None or login_session.user_id != user_id:
            raise AppError(code="SESSION_NOT_FOUND", message="세션을 찾을 수 없습니다.", status_code=404)
        now = utc_now()
        login_session.status = SessionStatus.REVOKED.value
        login_session.revoked_at = now
        login_session.revoke_reason = "user_revoked"
        await apply_workspace_scope(self.session, workspace_id)
        await append_audit_log(
            self.session,
            workspace_id=workspace_id,
            actor_id=user_id,
            action="identity.session.revoked",
            target_type="login_session",
            target_id=str(session_id),
        )

    async def revoke_all_sessions(
        self, *, user_id: UUID, workspace_id: UUID, except_session_id: UUID | None = None
    ) -> None:
        await self._revoke_all_sessions(
            user_id,
            reason="user_revoked_all",
            now=utc_now(),
            except_session_id=except_session_id,
        )
        await apply_workspace_scope(self.session, workspace_id)
        await append_audit_log(
            self.session,
            workspace_id=workspace_id,
            actor_id=user_id,
            action="identity.sessions.revoked_all",
            target_type="user",
            target_id=str(user_id),
            details={"except_current": except_session_id is not None},
        )

    async def begin_mfa_enrollment(self, *, user_id: UUID, workspace_id: UUID) -> MFAEnrollmentResult:
        await apply_workspace_scope(self.session, workspace_id)
        user = await self.session.get(User, user_id)
        if user is None:
            raise AppError(code="USER_NOT_FOUND", message="사용자를 찾을 수 없습니다.", status_code=404)
        await self.session.execute(
            update(MFAFactor)
            .where(
                MFAFactor.user_id == user_id,
                MFAFactor.status == MFAFactorStatus.PENDING.value,
            )
            .values(status=MFAFactorStatus.DISABLED.value, disabled_at=utc_now())
        )
        factor_id = uuid4()
        secret = generate_totp_secret()
        factor = MFAFactor(
            id=factor_id,
            user_id=user_id,
            kind=MFAFactorKind.TOTP.value,
            label="Authenticator",
            secret_ciphertext=self.envelope.encrypt(
                secret, context=f"mfa:{factor_id}:{user_id}"
            ),
            status=MFAFactorStatus.PENDING.value,
        )
        self.session.add(factor)
        await append_audit_log(
            self.session,
            workspace_id=workspace_id,
            actor_id=user_id,
            action="identity.mfa.enrollment_started",
            target_type="mfa_factor",
            target_id=str(factor.id),
        )
        return MFAEnrollmentResult(
            factor=factor,
            secret=secret,
            provisioning_uri=provisioning_uri(secret=secret, account_name=user.email),
        )

    async def confirm_mfa_enrollment(
        self,
        *,
        user_id: UUID,
        workspace_id: UUID,
        factor_id: UUID,
        code: str,
    ) -> list[str]:
        now = utc_now()
        factor = await self.session.get(MFAFactor, factor_id, with_for_update=True)
        if (
            factor is None
            or factor.user_id != user_id
            or factor.status != MFAFactorStatus.PENDING.value
        ):
            raise AppError(code="MFA_FACTOR_NOT_FOUND", message="MFA 설정을 찾을 수 없습니다.", status_code=404)
        secret = self.envelope.decrypt(
            factor.secret_ciphertext, context=f"mfa:{factor.id}:{factor.user_id}"
        )
        used_step = verify_totp(secret, code, at=now)
        if used_step is None:
            raise AppError(code="MFA_CODE_INVALID", message="MFA 코드가 올바르지 않습니다.", status_code=401)
        await self.session.execute(
            update(MFAFactor)
            .where(
                MFAFactor.user_id == user_id,
                MFAFactor.id != factor.id,
                MFAFactor.status == MFAFactorStatus.ACTIVE.value,
            )
            .values(status=MFAFactorStatus.DISABLED.value, disabled_at=now)
        )
        issued_codes = issue_recovery_codes(self.tokens)
        self.session.add_all(
            [
                MFARecoveryCode(id=uuid4(), factor_id=factor.id, code_hash=code_hash)
                for _raw, code_hash in issued_codes
            ]
        )
        factor.status = MFAFactorStatus.ACTIVE.value
        factor.confirmed_at = now
        factor.last_used_step = used_step
        user = await self.session.get(User, user_id, with_for_update=True)
        assert user is not None
        user.mfa_enabled = True
        await apply_workspace_scope(self.session, workspace_id)
        await append_audit_log(
            self.session,
            workspace_id=workspace_id,
            actor_id=user_id,
            action="identity.mfa.enabled",
            target_type="mfa_factor",
            target_id=str(factor.id),
        )
        await add_outbox_event(
            self.session,
            workspace_id=workspace_id,
            aggregate_type="user",
            aggregate_id=str(user_id),
            event_type="identity.mfa.enabled",
            schema_version="1",
            payload={"user_id": str(user_id)},
        )
        return [raw for raw, _code_hash in issued_codes]

    async def disable_mfa(
        self, *, user_id: UUID, workspace_id: UUID, code: str
    ) -> None:
        await apply_workspace_scope(self.session, workspace_id)
        membership, _role, _workspace = await self._active_membership(user_id, workspace_id)
        if await self._mfa_required_in_any_workspace(user_id):
            raise AppError(
                code="MFA_REQUIRED_BY_POLICY",
                message="참여 중인 워크스페이스 정책상 MFA를 해제할 수 없습니다.",
                status_code=409,
            )
        await apply_workspace_scope(self.session, workspace_id)
        now = utc_now()
        factor = await self._verify_second_factor(user_id, code, now=now)
        factor.status = MFAFactorStatus.DISABLED.value
        factor.disabled_at = now
        user = await self.session.get(User, user_id, with_for_update=True)
        assert user is not None
        user.mfa_enabled = False
        await append_audit_log(
            self.session,
            workspace_id=membership.workspace_id,
            actor_id=user_id,
            action="identity.mfa.disabled",
            target_type="mfa_factor",
            target_id=str(factor.id),
        )

    async def reauthenticate_mfa(self, principal: Principal, code: str) -> None:
        if principal.session_id is None:
            raise AppError(code="SESSION_INVALID", message="세션이 유효하지 않습니다.", status_code=401)
        now = utc_now()
        await self._verify_second_factor(principal.subject_id, code, now=now)
        login_session = await self.session.get(
            LoginSession, principal.session_id, with_for_update=True
        )
        if (
            login_session is None
            or login_session.user_id != principal.subject_id
            or login_session.status != SessionStatus.ACTIVE.value
        ):
            raise AppError(code="SESSION_INVALID", message="세션이 유효하지 않습니다.", status_code=401)
        login_session.mfa_verified_at = now
        if "totp_or_recovery" not in login_session.authentication_methods:
            login_session.authentication_methods = [
                *login_session.authentication_methods,
                "totp_or_recovery",
            ]
        await apply_workspace_scope(self.session, principal.workspace_id)
        await append_audit_log(
            self.session,
            workspace_id=principal.workspace_id,
            actor_id=principal.subject_id,
            action="identity.mfa.reauthenticated",
            target_type="login_session",
            target_id=str(login_session.id),
        )

    async def record_terms_consents(
        self,
        *,
        user_id: UUID,
        workspace_id: UUID,
        consents: list[TermsAcceptance],
        ip_hash: str | None,
    ) -> None:
        now = utc_now()
        for item in consents:
            existing = await self.session.scalar(
                select(TermsConsent).where(
                    TermsConsent.user_id == user_id,
                    TermsConsent.document_type == item.document_type.casefold(),
                    TermsConsent.document_version == item.document_version,
                )
            )
            if existing is None:
                self.session.add(
                    TermsConsent(
                        id=uuid4(),
                        user_id=user_id,
                        document_type=item.document_type.casefold(),
                        document_version=item.document_version,
                        required=item.required,
                        accepted_at=now,
                        accepted_ip_hash=ip_hash,
                    )
                )
        await apply_workspace_scope(self.session, workspace_id)
        await append_audit_log(
            self.session,
            workspace_id=workspace_id,
            actor_id=user_id,
            action="identity.terms.accepted",
            target_type="user",
            target_id=str(user_id),
            details={
                "versions": [
                    {"type": item.document_type, "version": item.document_version}
                    for item in consents
                ]
            },
            ip_hash=ip_hash,
        )

    async def request_account_deletion(self, principal: Principal) -> None:
        await _apply_user_scope(self.session, principal.subject_id)
        owner_count = await self.session.scalar(
            select(func.count())
            .select_from(Membership)
            .join(Role, Role.id == Membership.role_id)
            .where(
                Membership.user_id == principal.subject_id,
                Membership.status == MembershipStatus.ACTIVE.value,
                Role.is_owner.is_(True),
            )
        )
        if owner_count:
            raise AppError(
                code="OWNED_WORKSPACES_REMAIN",
                message="소유한 워크스페이스의 소유권을 먼저 이전하거나 삭제해야 합니다.",
                status_code=409,
            )
        user = await self.session.get(User, principal.subject_id, with_for_update=True)
        if user is None:
            raise AppError(code="USER_NOT_FOUND", message="사용자를 찾을 수 없습니다.", status_code=404)
        now = utc_now()
        user.status = UserStatus.DELETION_PENDING.value
        user.deletion_requested_at = now
        await self._revoke_all_sessions(user.id, reason="account_deletion", now=now)
        await apply_workspace_scope(self.session, principal.workspace_id)
        await append_audit_log(
            self.session,
            workspace_id=principal.workspace_id,
            actor_id=user.id,
            action="identity.account.deletion_requested",
            target_type="user",
            target_id=str(user.id),
        )

    async def _create_session(
        self,
        *,
        user: User,
        workspace_id: UUID,
        device_name: str | None,
        device_id_hash: str | None,
        user_agent_hash: str | None,
        ip_hash: str | None,
        country_code: str | None,
        authentication_methods: list[str],
        mfa_verified_at: datetime | None,
        now: datetime,
    ) -> TokenPairResult:
        await self._active_membership(user.id, workspace_id)
        prior_session = await self.session.scalar(
            select(LoginSession)
            .where(
                LoginSession.user_id == user.id,
                LoginSession.status == SessionStatus.ACTIVE.value,
            )
            .order_by(LoginSession.last_activity_at.desc())
            .limit(1)
        )
        policy = await self._authentication_policy(workspace_id)
        session_ttl = timedelta(
            seconds=policy.session_ttl_seconds if policy else int(DEFAULT_SESSION_TTL.total_seconds())
        )
        access_ttl = timedelta(
            seconds=policy.access_token_ttl_seconds if policy else int(DEFAULT_ACCESS_TTL.total_seconds())
        )
        login_session = LoginSession(
            id=uuid4(),
            user_id=user.id,
            family_id=uuid4(),
            status=SessionStatus.ACTIVE.value,
            device_name=device_name,
            device_id_hash=device_id_hash,
            user_agent_hash=user_agent_hash,
            ip_hash=ip_hash,
            country_code=country_code.upper() if country_code else None,
            authentication_methods=authentication_methods,
            mfa_verified_at=mfa_verified_at,
            last_activity_at=now,
            expires_at=now + session_ttl,
        )
        raw_refresh, refresh_hash = self.tokens.issue_opaque("rt")
        refresh = SessionRefreshToken(
            id=uuid4(),
            session_id=login_session.id,
            token_hash=refresh_hash,
            generation=0,
            expires_at=login_session.expires_at,
        )
        self.session.add_all([login_session, refresh])
        user.last_login_at = now
        access, expires_in = self.tokens.issue_access_token(
            user_id=user.id,
            workspace_id=workspace_id,
            session_id=login_session.id,
            authentication_methods=authentication_methods,
            ttl=access_ttl,
            now=now,
        )
        await append_audit_log(
            self.session,
            workspace_id=workspace_id,
            actor_id=user.id,
            action="identity.login.succeeded",
            target_type="login_session",
            target_id=str(login_session.id),
            details={"authentication_methods": authentication_methods},
            ip_hash=ip_hash,
        )
        await add_outbox_event(
            self.session,
            workspace_id=workspace_id,
            aggregate_type="login_session",
            aggregate_id=str(login_session.id),
            event_type="identity.session.created",
            schema_version="1",
            payload={
                "user_id": str(user.id),
                "country_code": login_session.country_code,
                "device_name": login_session.device_name,
            },
        )
        new_device = bool(
            prior_session is not None
            and device_id_hash
            and prior_session.device_id_hash != device_id_hash
        )
        country_changed = bool(
            prior_session is not None
            and country_code
            and prior_session.country_code
            and prior_session.country_code != country_code.upper()
        )
        if new_device or country_changed:
            await append_audit_log(
                self.session,
                workspace_id=workspace_id,
                actor_id=user.id,
                action="identity.login.anomaly_detected",
                target_type="login_session",
                target_id=str(login_session.id),
                details={"new_device": new_device, "country_changed": country_changed},
                ip_hash=ip_hash,
            )
            await add_outbox_event(
                self.session,
                workspace_id=workspace_id,
                aggregate_type="login_session",
                aggregate_id=str(login_session.id),
                event_type="identity.login.anomaly_detected",
                schema_version="1",
                payload={
                    "user_id": str(user.id),
                    "new_device": new_device,
                    "country_changed": country_changed,
                },
            )
        return TokenPairResult(
            access_token=access,
            refresh_token=raw_refresh,
            expires_in=expires_in,
            session_id=login_session.id,
            workspace_id=workspace_id,
        )

    async def _verify_second_factor(
        self, user_id: UUID, supplied_code: str, *, now: datetime
    ) -> MFAFactor:
        factor = await self.session.scalar(
            select(MFAFactor)
            .where(
                MFAFactor.user_id == user_id,
                MFAFactor.status == MFAFactorStatus.ACTIVE.value,
            )
            .with_for_update()
        )
        if factor is None:
            raise AppError(code="MFA_NOT_CONFIGURED", message="MFA가 설정되지 않았습니다.", status_code=409)
        secret = self.envelope.decrypt(
            factor.secret_ciphertext, context=f"mfa:{factor.id}:{factor.user_id}"
        )
        used_step = verify_totp(
            secret,
            supplied_code,
            at=now,
            last_used_step=factor.last_used_step,
        )
        if used_step is not None:
            factor.last_used_step = used_step
            return factor
        code_hash = recovery_code_digest(self.tokens, supplied_code)
        recovery = await self.session.scalar(
            select(MFARecoveryCode)
            .where(
                MFARecoveryCode.factor_id == factor.id,
                MFARecoveryCode.code_hash == code_hash,
            )
            .with_for_update()
        )
        if recovery is None or recovery.consumed_at is not None:
            raise AppError(code="MFA_CODE_INVALID", message="MFA 코드가 올바르지 않습니다.", status_code=401)
        recovery.consumed_at = now
        return factor

    async def _select_workspace(self, user_id: UUID, requested: UUID | None) -> UUID:
        await _apply_user_scope(self.session, user_id)
        query = (
            select(Membership.workspace_id)
            .join(Workspace, Workspace.id == Membership.workspace_id)
            .where(
                Membership.user_id == user_id,
                Membership.status == MembershipStatus.ACTIVE.value,
                Workspace.status == WorkspaceStatus.ACTIVE.value,
            )
        )
        if requested is not None:
            query = query.where(Membership.workspace_id == requested)
        workspace_id = await self.session.scalar(query.order_by(Membership.joined_at).limit(1))
        if workspace_id is None:
            raise AppError(
                code="WORKSPACE_ACCESS_DENIED",
                message="접근 가능한 워크스페이스가 없습니다.",
                status_code=403,
            )
        return workspace_id

    async def _first_workspace_id(self, user_id: UUID) -> UUID | None:
        await _apply_user_scope(self.session, user_id)
        return await self.session.scalar(
            select(Membership.workspace_id)
            .where(
                Membership.user_id == user_id,
                Membership.status == MembershipStatus.ACTIVE.value,
            )
            .order_by(Membership.joined_at)
            .limit(1)
        )

    async def _active_membership(
        self, user_id: UUID, workspace_id: UUID
    ) -> tuple[Membership, Role, Workspace]:
        row = (
            await self.session.execute(
                select(Membership, Role, Workspace)
                .join(Role, Role.id == Membership.role_id)
                .join(Workspace, Workspace.id == Membership.workspace_id)
                .where(
                    Membership.user_id == user_id,
                    Membership.workspace_id == workspace_id,
                    Membership.status == MembershipStatus.ACTIVE.value,
                )
            )
        ).one_or_none()
        if row is None:
            raise AppError(
                code="WORKSPACE_ACCESS_DENIED",
                message="워크스페이스 접근 권한이 없습니다.",
                status_code=403,
            )
        return row[0], row[1], row[2]

    async def _authentication_policy(
        self, workspace_id: UUID
    ) -> WorkspaceAuthenticationPolicy | None:
        return await self.session.scalar(
            select(WorkspaceAuthenticationPolicy).where(
                WorkspaceAuthenticationPolicy.workspace_id == workspace_id
            )
        )

    async def _validate_signup_terms(
        self, supplied: list[TermsAcceptance], *, now: datetime
    ) -> None:
        requirements = list(
            await self.session.scalars(
                select(TermsDocumentVersion).where(
                    TermsDocumentVersion.required.is_(True),
                    TermsDocumentVersion.effective_at <= now,
                    TermsDocumentVersion.retired_at.is_(None),
                )
            )
        )
        if not requirements:
            return
        accepted = {
            (item.document_type.casefold(), item.document_version)
            for item in supplied
            if item.required
        }
        missing = [
            requirement
            for requirement in requirements
            if (requirement.document_type.casefold(), requirement.document_version)
            not in accepted
        ]
        if missing:
            raise AppError(
                code="TERMS_CONSENT_REQUIRED",
                message="현재 필수 약관 동의가 필요합니다.",
                status_code=422,
                fields=[
                    {
                        "path": "terms",
                        "reason": f"{item.document_type}:{item.document_version}",
                    }
                    for item in missing
                ],
            )

    async def _enforce_required_terms(self, user_id: UUID, *, now: datetime) -> None:
        requirement_rows = list(
            await self.session.execute(
                select(
                    TermsDocumentVersion.document_type,
                    TermsDocumentVersion.document_version,
                ).where(
                    TermsDocumentVersion.required.is_(True),
                    TermsDocumentVersion.effective_at <= now,
                    TermsDocumentVersion.retired_at.is_(None),
                )
            )
        )
        requirements = {(str(row[0]).casefold(), str(row[1])) for row in requirement_rows}
        if not requirements:
            return
        accepted_rows = list(
            await self.session.execute(
                select(TermsConsent.document_type, TermsConsent.document_version).where(
                    TermsConsent.user_id == user_id
                )
            )
        )
        accepted = {(str(row[0]).casefold(), str(row[1])) for row in accepted_rows}
        missing = sorted(requirements.difference(accepted))
        if missing:
            raise AppError(
                code="TERMS_RECONSENT_REQUIRED",
                message="개정된 필수 약관에 다시 동의해야 합니다.",
                status_code=403,
                remediation={
                    "required_terms": [
                        {"document_type": item[0], "document_version": item[1]}
                        for item in missing
                    ]
                },
            )

    async def _revoke_all_sessions(
        self,
        user_id: UUID,
        *,
        reason: str,
        now: datetime,
        except_session_id: UUID | None = None,
    ) -> None:
        query = update(LoginSession).where(
            LoginSession.user_id == user_id,
            LoginSession.status == SessionStatus.ACTIVE.value,
        )
        if except_session_id is not None:
            query = query.where(LoginSession.id != except_session_id)
        await self.session.execute(
            query.values(
                status=SessionStatus.REVOKED.value,
                revoked_at=now,
                revoke_reason=reason,
            )
        )

    async def _mfa_required_in_any_workspace(self, user_id: UUID) -> bool:
        await _apply_user_scope(self.session, user_id)
        workspace_ids = list(
            await self.session.scalars(
                select(Membership.workspace_id).where(
                    Membership.user_id == user_id,
                    Membership.status == MembershipStatus.ACTIVE.value,
                )
            )
        )
        for workspace_id in workspace_ids:
            await apply_workspace_scope(self.session, workspace_id)
            row = (
                await self.session.execute(
                    select(Role.key, WorkspaceAuthenticationPolicy.require_mfa_role_keys)
                    .join(Membership, Membership.role_id == Role.id)
                    .join(
                        WorkspaceAuthenticationPolicy,
                        WorkspaceAuthenticationPolicy.workspace_id == Membership.workspace_id,
                    )
                    .where(
                        Membership.workspace_id == workspace_id,
                        Membership.user_id == user_id,
                        Membership.status == MembershipStatus.ACTIVE.value,
                    )
                )
            ).one_or_none()
            if row is not None and row[0] in set(row[1]):
                return True
        return False

    async def _commit_security_failure(self, error: AppError) -> None:
        """Persist a lockout/reuse transition before returning the expected auth error."""

        await self.session.flush()
        await self.session.commit()
        raise error

    def _dummy_password_hash(self) -> str:
        cached = getattr(self.passwords, "_blogops_dummy_hash", None)
        if isinstance(cached, str):
            return cached
        value = self.passwords.hash("Invalid-account-password-123!")
        setattr(self.passwords, "_blogops_dummy_hash", value)
        return value


class WorkspaceService:
    """Server-authorized workspace, role, membership and invitation operations."""

    def __init__(self, session: AsyncSession, *, tokens: TokenManager) -> None:
        self.session = session
        self.tokens = tokens

    async def list_workspaces(self, user_id: UUID) -> list[Workspace]:
        await _apply_user_scope(self.session, user_id)
        return list(
            await self.session.scalars(
                select(Workspace)
                .join(Membership, Membership.workspace_id == Workspace.id)
                .where(
                    Membership.user_id == user_id,
                    Membership.status == MembershipStatus.ACTIVE.value,
                    Workspace.status != WorkspaceStatus.DELETED.value,
                )
                .order_by(Workspace.name, Workspace.id)
            )
        )

    async def create_workspace(
        self, actor_user_id: UUID, request: WorkspaceCreateRequest
    ) -> Workspace:
        now = utc_now()
        workspace, roles, policy = _new_workspace_bundle(
            creator_id=actor_user_id,
            name=request.name,
            industry=request.industry,
            country_code=request.country_code,
            timezone=request.timezone,
            default_locale=request.default_locale,
            data_region=request.data_region,
        )
        membership = Membership(
            id=uuid4(),
            workspace_id=workspace.id,
            user_id=actor_user_id,
            role_id=roles["owner"].id,
            status=MembershipStatus.ACTIVE.value,
            joined_at=now,
        )
        await apply_workspace_scope(self.session, workspace.id)
        self.session.add_all([workspace, *roles.values(), policy, membership])
        await self.session.flush()
        await append_audit_log(
            self.session,
            workspace_id=workspace.id,
            actor_id=actor_user_id,
            action="organization.workspace.created",
            target_type="workspace",
            target_id=str(workspace.id),
        )
        await add_outbox_event(
            self.session,
            workspace_id=workspace.id,
            aggregate_type="workspace",
            aggregate_id=str(workspace.id),
            event_type="organization.workspace.created",
            schema_version="1",
            payload={"owner_user_id": str(actor_user_id)},
        )
        return workspace

    async def get_workspace(self, actor_user_id: UUID, workspace_id: UUID) -> Workspace:
        _membership, _role, workspace = await self.authorize(
            actor_user_id, workspace_id, Permission.WORKSPACE_READ
        )
        return workspace

    async def update_workspace(
        self,
        actor_user_id: UUID,
        workspace_id: UUID,
        request: WorkspaceUpdateRequest,
    ) -> Workspace:
        _membership, _role, workspace = await self.authorize(
            actor_user_id, workspace_id, Permission.WORKSPACE_MANAGE
        )
        for field_name in request.model_fields_set:
            value = getattr(request, field_name)
            if value is None and field_name not in {"industry", "default_channel_ref"}:
                raise AppError(
                    code="WORKSPACE_VALUE_REQUIRED",
                    message="워크스페이스 설정 값은 null일 수 없습니다.",
                    status_code=422,
                    fields=[{"path": field_name, "reason": "null_not_allowed"}],
                )
            if field_name == "country_code" and isinstance(value, str):
                value = value.upper()
            setattr(workspace, field_name, value)
        await append_audit_log(
            self.session,
            workspace_id=workspace_id,
            actor_id=actor_user_id,
            action="organization.workspace.updated",
            target_type="workspace",
            target_id=str(workspace_id),
            details={"fields": sorted(request.model_fields_set)},
        )
        return workspace

    async def schedule_workspace_deletion(
        self, actor_user_id: UUID, workspace_id: UUID, *, grace_days: int = 14
    ) -> Workspace:
        membership, role, workspace = await self.authorize(
            actor_user_id, workspace_id, Permission.WORKSPACE_MANAGE, lock=True
        )
        if not role.is_owner:
            raise AppError(
                code="OWNER_REQUIRED",
                message="워크스페이스 Owner만 삭제를 예약할 수 있습니다.",
                status_code=403,
            )
        now = utc_now()
        workspace.status = WorkspaceStatus.DELETION_SCHEDULED.value
        workspace.deletion_scheduled_at = now + timedelta(days=grace_days)
        await append_audit_log(
            self.session,
            workspace_id=workspace_id,
            actor_id=membership.user_id,
            action="organization.workspace.deletion_scheduled",
            target_type="workspace",
            target_id=str(workspace_id),
            details={"delete_after": workspace.deletion_scheduled_at.isoformat()},
        )
        await add_outbox_event(
            self.session,
            workspace_id=workspace_id,
            aggregate_type="workspace",
            aggregate_id=str(workspace_id),
            event_type="organization.workspace.deletion_scheduled",
            schema_version="1",
            payload={"delete_after": workspace.deletion_scheduled_at.isoformat()},
        )
        return workspace

    async def list_roles(self, actor_user_id: UUID, workspace_id: UUID) -> list[Role]:
        await self.authorize(actor_user_id, workspace_id, Permission.WORKSPACE_READ)
        return list(
            await self.session.scalars(
                select(Role).where(Role.workspace_id == workspace_id).order_by(Role.name, Role.id)
            )
        )

    async def create_role(
        self, actor_user_id: UUID, workspace_id: UUID, request: RoleCreateRequest
    ) -> Role:
        await self.authorize(actor_user_id, workspace_id, Permission.WORKSPACE_MANAGE)
        _validate_permissions(request.permissions)
        existing = await self.session.scalar(
            select(Role.id).where(
                Role.workspace_id == workspace_id,
                (Role.key == request.key) | (Role.name == request.name),
            )
        )
        if existing is not None:
            raise AppError(code="ROLE_ALREADY_EXISTS", message="같은 역할이 이미 있습니다.", status_code=409)
        role = Role(
            id=uuid4(),
            workspace_id=workspace_id,
            key=request.key,
            name=request.name.strip(),
            permissions=sorted(set(request.permissions)),
            is_system=False,
            is_owner=False,
        )
        self.session.add(role)
        await append_audit_log(
            self.session,
            workspace_id=workspace_id,
            actor_id=actor_user_id,
            action="organization.role.created",
            target_type="role",
            target_id=str(role.id),
            details={"permissions": role.permissions},
        )
        return role

    async def update_role(
        self,
        actor_user_id: UUID,
        workspace_id: UUID,
        role_id: UUID,
        request: RoleUpdateRequest,
    ) -> Role:
        await self.authorize(actor_user_id, workspace_id, Permission.WORKSPACE_MANAGE)
        role = await self.session.get(Role, role_id, with_for_update=True)
        if role is None or role.workspace_id != workspace_id:
            raise AppError(code="ROLE_NOT_FOUND", message="역할을 찾을 수 없습니다.", status_code=404)
        if role.is_system:
            raise AppError(
                code="SYSTEM_ROLE_IMMUTABLE",
                message="기본 역할은 변경할 수 없습니다.",
                status_code=409,
            )
        if request.permissions is not None:
            _validate_permissions(request.permissions)
            role.permissions = sorted(set(request.permissions))
        if request.name is not None:
            role.name = request.name.strip()
        if all(getattr(request, field_name) is None for field_name in request.model_fields_set):
            raise AppError(
                code="ROLE_VALUE_REQUIRED",
                message="역할 변경 값은 null일 수 없습니다.",
                status_code=422,
            )
        await append_audit_log(
            self.session,
            workspace_id=workspace_id,
            actor_id=actor_user_id,
            action="organization.role.updated",
            target_type="role",
            target_id=str(role.id),
        )
        return role

    async def list_members(
        self, actor_user_id: UUID, workspace_id: UUID
    ) -> list[MembershipRecord]:
        await self.authorize(actor_user_id, workspace_id, Permission.WORKSPACE_READ)
        rows = (
            await self.session.execute(
                select(Membership, User, Role)
                .join(User, User.id == Membership.user_id)
                .join(Role, Role.id == Membership.role_id)
                .where(
                    Membership.workspace_id == workspace_id,
                    Membership.status != MembershipStatus.REMOVED.value,
                )
                .order_by(User.display_name, User.id)
            )
        ).all()
        return [MembershipRecord(membership=row[0], user=row[1], role=row[2]) for row in rows]

    async def invite_member(
        self,
        actor_user_id: UUID,
        workspace_id: UUID,
        request: InvitationCreateRequest,
    ) -> InvitationResult:
        await self.authorize(actor_user_id, workspace_id, Permission.WORKSPACE_MANAGE)
        role = await self.session.get(Role, request.role_id)
        if role is None or role.workspace_id != workspace_id:
            raise AppError(code="ROLE_NOT_FOUND", message="역할을 찾을 수 없습니다.", status_code=404)
        if role.is_owner:
            raise AppError(
                code="OWNER_INVITATION_NOT_ALLOWED",
                message="Owner 권한은 소유권 이전 절차로만 부여할 수 있습니다.",
                status_code=409,
            )
        existing_member = await self.session.scalar(
            select(Membership.id)
            .join(User, User.id == Membership.user_id)
            .where(
                Membership.workspace_id == workspace_id,
                Membership.status == MembershipStatus.ACTIVE.value,
                User.email == request.email,
            )
        )
        if existing_member is not None:
            raise AppError(code="ALREADY_A_MEMBER", message="이미 참여 중인 사용자입니다.", status_code=409)
        now = utc_now()
        await self.session.execute(
            update(WorkspaceInvitation)
            .where(
                WorkspaceInvitation.workspace_id == workspace_id,
                WorkspaceInvitation.email == request.email,
                WorkspaceInvitation.status == InvitationStatus.PENDING.value,
            )
            .values(status=InvitationStatus.CANCELLED.value, cancelled_at=now)
        )
        invitation = WorkspaceInvitation(
            id=uuid4(),
            workspace_id=workspace_id,
            email=request.email,
            role_id=role.id,
            invited_by_user_id=actor_user_id,
            token_hash="pending",
            status=InvitationStatus.PENDING.value,
            expires_at=now + timedelta(hours=request.expires_in_hours),
        )
        raw, invitation.token_hash = self.tokens.derive_opaque(
            f"inv.{workspace_id}", invitation.id
        )
        self.session.add(invitation)
        await append_audit_log(
            self.session,
            workspace_id=workspace_id,
            actor_id=actor_user_id,
            action="organization.invitation.created",
            target_type="workspace_invitation",
            target_id=str(invitation.id),
            details={"role_id": str(role.id)},
        )
        await add_outbox_event(
            self.session,
            workspace_id=workspace_id,
            aggregate_type="workspace_invitation",
            aggregate_id=str(invitation.id),
            event_type="organization.invitation.requested",
            schema_version="1",
            payload={
                "email": invitation.email,
                "token_id": str(invitation.id),
                "token_prefix": f"inv.{workspace_id}",
                "expires_at": invitation.expires_at.isoformat(),
            },
        )
        return InvitationResult(invitation=invitation, token=raw)

    async def accept_invitation(self, user_id: UUID, raw_token: str) -> Membership:
        try:
            workspace_id = invitation_workspace_id(raw_token)
        except ValueError as exc:
            raise _invalid_invitation_error() from exc
        await apply_workspace_scope(self.session, workspace_id)
        now = utc_now()
        invitation = await self.session.scalar(
            select(WorkspaceInvitation)
            .where(
                WorkspaceInvitation.workspace_id == workspace_id,
                WorkspaceInvitation.token_hash == self.tokens.digest(raw_token),
            )
            .with_for_update()
        )
        if (
            invitation is None
            or invitation.status != InvitationStatus.PENDING.value
            or invitation.expires_at <= now
        ):
            raise _invalid_invitation_error()
        user = await self.session.get(User, user_id)
        if user is None or user.email != invitation.email:
            raise AppError(
                code="INVITATION_EMAIL_MISMATCH",
                message="초대받은 이메일 계정으로 로그인해야 합니다.",
                status_code=403,
            )
        membership = await self.session.scalar(
            select(Membership)
            .where(
                Membership.workspace_id == workspace_id,
                Membership.user_id == user_id,
            )
            .with_for_update()
        )
        if membership is None:
            membership = Membership(
                id=uuid4(),
                workspace_id=workspace_id,
                user_id=user_id,
                role_id=invitation.role_id,
                status=MembershipStatus.ACTIVE.value,
                joined_at=now,
            )
            self.session.add(membership)
        else:
            membership.role_id = invitation.role_id
            membership.status = MembershipStatus.ACTIVE.value
            membership.joined_at = now
            membership.removed_at = None
        invitation.status = InvitationStatus.ACCEPTED.value
        invitation.accepted_by_user_id = user_id
        invitation.accepted_at = now
        await append_audit_log(
            self.session,
            workspace_id=workspace_id,
            actor_id=user_id,
            action="organization.invitation.accepted",
            target_type="membership",
            target_id=str(membership.id),
            details={"invitation_id": str(invitation.id)},
        )
        return membership

    async def cancel_invitation(
        self, actor_user_id: UUID, workspace_id: UUID, invitation_id: UUID
    ) -> WorkspaceInvitation:
        await self.authorize(actor_user_id, workspace_id, Permission.WORKSPACE_MANAGE)
        invitation = await self.session.get(
            WorkspaceInvitation, invitation_id, with_for_update=True
        )
        if invitation is None or invitation.workspace_id != workspace_id:
            raise AppError(code="INVITATION_NOT_FOUND", message="초대를 찾을 수 없습니다.", status_code=404)
        if invitation.status != InvitationStatus.PENDING.value:
            raise AppError(code="INVITATION_NOT_PENDING", message="대기 중인 초대가 아닙니다.", status_code=409)
        invitation.status = InvitationStatus.CANCELLED.value
        invitation.cancelled_at = utc_now()
        await append_audit_log(
            self.session,
            workspace_id=workspace_id,
            actor_id=actor_user_id,
            action="organization.invitation.cancelled",
            target_type="workspace_invitation",
            target_id=str(invitation.id),
        )
        return invitation

    async def change_member_role(
        self,
        actor_user_id: UUID,
        workspace_id: UUID,
        target_user_id: UUID,
        role_id: UUID,
    ) -> Membership:
        await self.authorize(actor_user_id, workspace_id, Permission.WORKSPACE_MANAGE, lock=True)
        membership = await self._locked_membership(workspace_id, target_user_id)
        old_role = await self.session.get(Role, membership.role_id, with_for_update=True)
        new_role = await self.session.get(Role, role_id)
        if new_role is None or new_role.workspace_id != workspace_id:
            raise AppError(code="ROLE_NOT_FOUND", message="역할을 찾을 수 없습니다.", status_code=404)
        assert old_role is not None
        if new_role.is_owner and not old_role.is_owner:
            raise AppError(
                code="OWNER_TRANSFER_REQUIRED",
                message="Owner 지정은 소유권 이전 절차를 사용해야 합니다.",
                status_code=409,
            )
        if old_role.is_owner and not new_role.is_owner:
            await self._protect_last_owner(workspace_id, excluding_user_id=target_user_id)
        membership.role_id = new_role.id
        await append_audit_log(
            self.session,
            workspace_id=workspace_id,
            actor_id=actor_user_id,
            action="organization.membership.role_changed",
            target_type="membership",
            target_id=str(membership.id),
            details={"old_role_id": str(old_role.id), "new_role_id": str(new_role.id)},
        )
        return membership

    async def remove_member(
        self, actor_user_id: UUID, workspace_id: UUID, target_user_id: UUID
    ) -> Membership:
        await self.authorize(actor_user_id, workspace_id, Permission.WORKSPACE_MANAGE, lock=True)
        membership = await self._locked_membership(workspace_id, target_user_id)
        role = await self.session.get(Role, membership.role_id, with_for_update=True)
        assert role is not None
        if role.is_owner:
            await self._protect_last_owner(workspace_id, excluding_user_id=target_user_id)
        membership.status = MembershipStatus.REMOVED.value
        membership.removed_at = utc_now()
        await append_audit_log(
            self.session,
            workspace_id=workspace_id,
            actor_id=actor_user_id,
            action="organization.membership.removed",
            target_type="membership",
            target_id=str(membership.id),
            details={"target_user_id": str(target_user_id)},
        )
        return membership

    async def transfer_ownership(
        self,
        principal: Principal,
        workspace_id: UUID,
        new_owner_user_id: UUID,
    ) -> None:
        current, current_role, _workspace = await self.authorize(
            principal.subject_id, workspace_id, Permission.WORKSPACE_MANAGE, lock=True
        )
        if not current_role.is_owner:
            raise AppError(code="OWNER_REQUIRED", message="Owner만 소유권을 이전할 수 있습니다.", status_code=403)
        if principal.session_id is None:
            raise AppError(code="MFA_REAUTH_REQUIRED", message="MFA 재인증이 필요합니다.", status_code=403)
        login_session = await self.session.get(LoginSession, principal.session_id)
        now = utc_now()
        if (
            login_session is None
            or login_session.mfa_verified_at is None
            or login_session.mfa_verified_at < now - timedelta(minutes=5)
        ):
            raise AppError(
                code="MFA_REAUTH_REQUIRED",
                message="최근 5분 이내 MFA 재인증이 필요합니다.",
                status_code=403,
            )
        target = await self._locked_membership(workspace_id, new_owner_user_id)
        owner_role = current_role
        admin_role = await self.session.scalar(
            select(Role).where(Role.workspace_id == workspace_id, Role.key == "admin")
        )
        if admin_role is None:
            raise AppError(code="ROLE_CONFIGURATION_INVALID", message="Admin 역할이 없습니다.", status_code=500)
        target.role_id = owner_role.id
        current.role_id = admin_role.id
        await append_audit_log(
            self.session,
            workspace_id=workspace_id,
            actor_id=principal.subject_id,
            action="organization.ownership.transferred",
            target_type="workspace",
            target_id=str(workspace_id),
            details={
                "previous_owner_user_id": str(principal.subject_id),
                "new_owner_user_id": str(new_owner_user_id),
            },
        )
        await add_outbox_event(
            self.session,
            workspace_id=workspace_id,
            aggregate_type="workspace",
            aggregate_id=str(workspace_id),
            event_type="organization.ownership.transferred",
            schema_version="1",
            payload={
                "previous_owner_user_id": str(principal.subject_id),
                "new_owner_user_id": str(new_owner_user_id),
            },
        )

    async def get_authentication_policy(
        self, actor_user_id: UUID, workspace_id: UUID
    ) -> WorkspaceAuthenticationPolicy:
        await self.authorize(actor_user_id, workspace_id, Permission.WORKSPACE_READ)
        policy = await self.session.scalar(
            select(WorkspaceAuthenticationPolicy).where(
                WorkspaceAuthenticationPolicy.workspace_id == workspace_id
            )
        )
        if policy is None:
            raise AppError(code="AUTH_POLICY_NOT_FOUND", message="인증 정책을 찾을 수 없습니다.", status_code=404)
        return policy

    async def update_authentication_policy(
        self,
        actor_user_id: UUID,
        workspace_id: UUID,
        request: WorkspaceAuthenticationPolicyUpdate,
    ) -> WorkspaceAuthenticationPolicy:
        _membership, actor_role, _workspace = await self.authorize(
            actor_user_id, workspace_id, Permission.WORKSPACE_MANAGE
        )
        if not actor_role.is_owner and actor_role.key != "admin":
            raise AppError(code="ADMIN_REQUIRED", message="Owner 또는 Admin 권한이 필요합니다.", status_code=403)
        policy = await self.get_authentication_policy(actor_user_id, workspace_id)
        for field_name in request.model_fields_set:
            value = getattr(request, field_name)
            if value is not None:
                if field_name == "sso_enforced_domains":
                    value = sorted({str(domain).casefold() for domain in value})
                setattr(policy, field_name, value)
        if all(getattr(request, field_name) is None for field_name in request.model_fields_set):
            raise AppError(
                code="AUTH_POLICY_VALUE_REQUIRED",
                message="인증 정책 변경 값은 null일 수 없습니다.",
                status_code=422,
            )
        role_keys = set(await self.session.scalars(select(Role.key).where(Role.workspace_id == workspace_id)))
        missing = set(policy.require_mfa_role_keys).difference(role_keys)
        if missing:
            raise AppError(
                code="ROLE_NOT_FOUND",
                message="MFA 정책에 존재하지 않는 역할이 포함되어 있습니다.",
                status_code=422,
                fields=[{"path": "require_mfa_role_keys", "reason": key} for key in sorted(missing)],
            )
        await append_audit_log(
            self.session,
            workspace_id=workspace_id,
            actor_id=actor_user_id,
            action="organization.authentication_policy.updated",
            target_type="workspace_authentication_policy",
            target_id=str(policy.id),
            details={"fields": sorted(request.model_fields_set)},
        )
        return policy

    async def list_audit_logs(
        self, actor_user_id: UUID, workspace_id: UUID, *, limit: int = 100
    ) -> list[AuditLog]:
        await self.authorize(actor_user_id, workspace_id, Permission.AUDIT_READ)
        return list(
            await self.session.scalars(
                select(AuditLog)
                .where(AuditLog.workspace_id == workspace_id)
                .order_by(AuditLog.occurred_at.desc(), AuditLog.id.desc())
                .limit(min(max(limit, 1), 200))
            )
        )

    async def authorize(
        self,
        actor_user_id: UUID,
        workspace_id: UUID,
        permission: Permission,
        *,
        lock: bool = False,
    ) -> tuple[Membership, Role, Workspace]:
        await apply_workspace_scope(self.session, workspace_id)
        query = (
            select(Membership, Role, Workspace)
            .join(Role, Role.id == Membership.role_id)
            .join(Workspace, Workspace.id == Membership.workspace_id)
            .where(
                Membership.workspace_id == workspace_id,
                Membership.user_id == actor_user_id,
                Membership.status == MembershipStatus.ACTIVE.value,
                Workspace.status != WorkspaceStatus.DELETED.value,
            )
        )
        if lock:
            query = query.with_for_update()
        row = (await self.session.execute(query)).one_or_none()
        if row is None:
            raise AppError(
                code="WORKSPACE_ACCESS_DENIED",
                message="워크스페이스 접근 권한이 없습니다.",
                status_code=403,
            )
        membership, role, workspace = row
        if permission.value not in set(role.permissions):
            raise AppError(
                code="PERMISSION_DENIED",
                message="이 작업을 수행할 권한이 없습니다.",
                status_code=403,
                fields=[{"path": "permissions", "reason": permission.value}],
            )
        return membership, role, workspace

    async def _locked_membership(self, workspace_id: UUID, user_id: UUID) -> Membership:
        membership = await self.session.scalar(
            select(Membership)
            .where(
                Membership.workspace_id == workspace_id,
                Membership.user_id == user_id,
                Membership.status == MembershipStatus.ACTIVE.value,
            )
            .with_for_update()
        )
        if membership is None:
            raise AppError(code="MEMBER_NOT_FOUND", message="멤버를 찾을 수 없습니다.", status_code=404)
        return membership

    async def _protect_last_owner(
        self, workspace_id: UUID, *, excluding_user_id: UUID
    ) -> None:
        other_owner = await self.session.scalar(
            select(Membership.id)
            .join(Role, Role.id == Membership.role_id)
            .where(
                Membership.workspace_id == workspace_id,
                Membership.user_id != excluding_user_id,
                Membership.status == MembershipStatus.ACTIVE.value,
                Role.is_owner.is_(True),
            )
            .limit(1)
            .with_for_update()
        )
        if other_owner is None:
            raise AppError(
                code="LAST_OWNER_PROTECTED",
                message="마지막 Owner는 역할 변경 또는 제거할 수 없습니다.",
                status_code=409,
            )


class EnterpriseIdentityService:
    """C2 federation, SCIM and agency hierarchy persistence/service boundary."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        tokens: TokenManager,
        workspaces: WorkspaceService,
    ) -> None:
        self.session = session
        self.tokens = tokens
        self.workspaces = workspaces

    async def create_federated_connection(
        self,
        actor_user_id: UUID,
        workspace_id: UUID,
        request: FederatedConnectionCreateRequest,
    ) -> FederatedProviderConnection:
        await self.workspaces.authorize(
            actor_user_id, workspace_id, Permission.WORKSPACE_MANAGE
        )
        connection = FederatedProviderConnection(
            id=uuid4(),
            workspace_id=workspace_id,
            provider_key=request.provider_key,
            display_name=request.display_name,
            protocol=request.protocol.value,
            issuer=request.issuer,
            discovery_url=request.discovery_url,
            client_id=request.client_id,
            secret_ref=request.secret_ref,
            domains=sorted({domain.casefold() for domain in request.domains}),
            attribute_mapping=request.attribute_mapping,
            config=request.config,
            status=ConnectionStatus.DRAFT.value,
            jit_provisioning_enabled=request.jit_provisioning_enabled,
        )
        self.session.add(connection)
        await append_audit_log(
            self.session,
            workspace_id=workspace_id,
            actor_id=actor_user_id,
            action="identity.federation.connection_created",
            target_type="federated_provider_connection",
            target_id=str(connection.id),
            details={"protocol": connection.protocol, "provider_key": connection.provider_key},
        )
        return connection

    async def link_federated_identity(
        self,
        *,
        connection: FederatedProviderConnection,
        user: User,
        claims: FederatedIdentityClaims,
    ) -> ExternalIdentity:
        existing_subject = await self.session.scalar(
            select(ExternalIdentity).where(
                ExternalIdentity.issuer == claims.issuer,
                ExternalIdentity.subject == claims.subject,
            )
        )
        if existing_subject is not None and existing_subject.user_id != user.id:
            raise AppError(
                code="FEDERATED_IDENTITY_CONFLICT",
                message="외부 계정이 다른 사용자와 연결되어 있습니다.",
                status_code=409,
            )
        if claims.email is not None and normalize_email(claims.email) != user.email:
            if not claims.email_verified:
                raise AppError(
                    code="FEDERATED_EMAIL_UNVERIFIED",
                    message="확인되지 않은 외부 이메일은 계정에 연결할 수 없습니다.",
                    status_code=403,
                )
        identity = existing_subject or ExternalIdentity(
            id=uuid4(),
            user_id=user.id,
            connection_id=connection.id,
            provider_key=connection.provider_key,
            issuer=claims.issuer,
            subject=claims.subject,
        )
        identity.email_at_link = normalize_email(claims.email) if claims.email else None
        identity.profile = {
            "display_name": claims.display_name,
            "groups": list(claims.groups),
            "attributes": claims.attributes,
        }
        identity.last_login_at = utc_now()
        if existing_subject is None:
            self.session.add(identity)
        return identity

    async def configure_scim(
        self,
        actor_user_id: UUID,
        workspace_id: UUID,
        request: SCIMConfigurationCreateRequest,
    ) -> SCIMConfigurationResult:
        await self.workspaces.authorize(
            actor_user_id, workspace_id, Permission.WORKSPACE_MANAGE
        )
        existing = await self.session.scalar(
            select(SCIMConfiguration).where(SCIMConfiguration.workspace_id == workspace_id)
        )
        if existing is not None:
            raise AppError(code="SCIM_ALREADY_CONFIGURED", message="SCIM이 이미 설정되어 있습니다.", status_code=409)
        raw, token_hash = self.tokens.issue_opaque(f"scim.{workspace_id}")
        configuration = SCIMConfiguration(
            id=uuid4(),
            workspace_id=workspace_id,
            provider_key=request.provider_key,
            bearer_token_hash=token_hash,
            secret_ref=request.secret_ref,
            attribute_mapping=request.attribute_mapping,
            group_role_mapping=request.group_role_mapping,
            status=ConnectionStatus.ACTIVE.value,
        )
        self.session.add(configuration)
        await append_audit_log(
            self.session,
            workspace_id=workspace_id,
            actor_id=actor_user_id,
            action="identity.scim.configured",
            target_type="scim_configuration",
            target_id=str(configuration.id),
        )
        return SCIMConfigurationResult(configuration=configuration, bearer_token=raw)

    async def authenticate_scim_token(self, raw_token: str) -> SCIMConfiguration:
        try:
            marker, raw_workspace_id, _secret = raw_token.split(".", 2)
            if marker != "scim":
                raise ValueError
            workspace_id = UUID(raw_workspace_id)
        except (ValueError, TypeError) as exc:
            raise AppError(code="SCIM_TOKEN_INVALID", message="SCIM 토큰이 유효하지 않습니다.", status_code=401) from exc
        await apply_workspace_scope(self.session, workspace_id)
        configuration = await self.session.scalar(
            select(SCIMConfiguration).where(
                SCIMConfiguration.workspace_id == workspace_id,
                SCIMConfiguration.bearer_token_hash == self.tokens.digest(raw_token),
                SCIMConfiguration.status == ConnectionStatus.ACTIVE.value,
            )
        )
        if configuration is None:
            raise AppError(code="SCIM_TOKEN_INVALID", message="SCIM 토큰이 유효하지 않습니다.", status_code=401)
        return configuration

    async def provision_scim_user(
        self,
        configuration: SCIMConfiguration,
        payload: SCIMUserPayload,
    ) -> tuple[User, Membership, SCIMResourceLink]:
        email = normalize_email(payload.user_name)
        user = await self.session.scalar(select(User).where(User.email == email))
        now = utc_now()
        if user is None:
            user = User(
                id=uuid4(),
                email=email,
                display_name=payload.display_name,
                status=UserStatus.ACTIVE.value if payload.active else UserStatus.DISABLED.value,
                email_verified_at=now,
                locale="ko-KR",
                timezone="UTC",
            )
            self.session.add(user)
        role_key = _role_key_from_scim_groups(configuration.group_role_mapping, payload.groups)
        role = await self.session.scalar(
            select(Role).where(
                Role.workspace_id == configuration.workspace_id,
                Role.key == role_key,
            )
        )
        if role is None or role.is_owner:
            role = await self.session.scalar(
                select(Role).where(
                    Role.workspace_id == configuration.workspace_id,
                    Role.key == "viewer",
                )
            )
        if role is None:
            raise AppError(code="ROLE_CONFIGURATION_INVALID", message="SCIM 기본 역할이 없습니다.", status_code=500)
        membership = await self.session.scalar(
            select(Membership)
            .where(
                Membership.workspace_id == configuration.workspace_id,
                Membership.user_id == user.id,
            )
            .with_for_update()
        )
        if membership is None:
            membership = Membership(
                id=uuid4(),
                workspace_id=configuration.workspace_id,
                user_id=user.id,
                role_id=role.id,
                status=(
                    MembershipStatus.ACTIVE.value
                    if payload.active
                    else MembershipStatus.SUSPENDED.value
                ),
                joined_at=now,
            )
            self.session.add(membership)
        else:
            membership.role_id = role.id
            membership.status = (
                MembershipStatus.ACTIVE.value
                if payload.active
                else MembershipStatus.SUSPENDED.value
            )
        link = await self.session.scalar(
            select(SCIMResourceLink).where(
                SCIMResourceLink.configuration_id == configuration.id,
                SCIMResourceLink.resource_type == SCIMResourceType.USER.value,
                SCIMResourceLink.external_id == payload.external_id,
            )
        )
        if link is None:
            link = SCIMResourceLink(
                id=uuid4(),
                workspace_id=configuration.workspace_id,
                configuration_id=configuration.id,
                resource_type=SCIMResourceType.USER.value,
                external_id=payload.external_id,
                user_id=user.id,
            )
            self.session.add(link)
        link.active = payload.active
        link.attributes = payload.attributes
        configuration.last_synced_at = now
        await append_audit_log(
            self.session,
            workspace_id=configuration.workspace_id,
            actor_id=None,
            action="identity.scim.user_provisioned",
            target_type="user",
            target_id=str(user.id),
            details={"active": payload.active, "external_id": payload.external_id},
        )
        return user, membership, link

    async def create_agency(
        self,
        actor_user_id: UUID,
        workspace_id: UUID,
        request: AgencyCreateRequest,
    ) -> Agency:
        _membership, role, _workspace = await self.workspaces.authorize(
            actor_user_id, workspace_id, Permission.WORKSPACE_MANAGE
        )
        if not role.is_owner:
            raise AppError(code="OWNER_REQUIRED", message="Owner 권한이 필요합니다.", status_code=403)
        existing = await self.session.scalar(
            select(Agency).where(Agency.workspace_id == workspace_id)
        )
        if existing is not None:
            raise AppError(code="AGENCY_ALREADY_EXISTS", message="대행사가 이미 설정되어 있습니다.", status_code=409)
        agency = Agency(
            id=uuid4(),
            workspace_id=workspace_id,
            white_label_config=request.white_label_config,
            common_template_policy=request.common_template_policy,
        )
        self.session.add(agency)
        await append_audit_log(
            self.session,
            workspace_id=workspace_id,
            actor_id=actor_user_id,
            action="organization.agency.created",
            target_type="agency",
            target_id=str(agency.id),
        )
        return agency

    async def add_agency_client(
        self,
        actor_user_id: UUID,
        workspace_id: UUID,
        request: AgencyClientCreateRequest,
    ) -> AgencyClient:
        await self.workspaces.authorize(
            actor_user_id, workspace_id, Permission.WORKSPACE_MANAGE
        )
        if request.client_workspace_id == workspace_id:
            raise AppError(code="INVALID_AGENCY_CLIENT", message="대행사 자체를 고객으로 연결할 수 없습니다.", status_code=422)
        agency = await self.session.scalar(select(Agency).where(Agency.workspace_id == workspace_id))
        if agency is None:
            raise AppError(code="AGENCY_NOT_FOUND", message="대행사 설정을 찾을 수 없습니다.", status_code=404)
        client_workspace = await self.session.get(Workspace, request.client_workspace_id)
        if client_workspace is None or client_workspace.status != WorkspaceStatus.ACTIVE.value:
            raise AppError(code="CLIENT_WORKSPACE_NOT_FOUND", message="고객 워크스페이스를 찾을 수 없습니다.", status_code=404)
        link = AgencyClient(
            id=uuid4(),
            workspace_id=workspace_id,
            agency_id=agency.id,
            client_workspace_id=request.client_workspace_id,
            status=AgencyClientStatus.ACTIVE.value,
            permissions=sorted(set(request.permissions)),
            billing_allocation=request.billing_allocation,
            template_overrides=request.template_overrides,
        )
        self.session.add(link)
        await append_audit_log(
            self.session,
            workspace_id=workspace_id,
            actor_id=actor_user_id,
            action="organization.agency.client_added",
            target_type="agency_client",
            target_id=str(link.id),
            details={"client_workspace_id": str(request.client_workspace_id)},
        )
        return link


def _new_workspace_bundle(
    *,
    creator_id: UUID,
    name: str,
    industry: str | None,
    country_code: str,
    timezone: str,
    default_locale: str,
    data_region: str,
) -> tuple[Workspace, dict[str, Role], WorkspaceAuthenticationPolicy]:
    workspace_id = uuid4()
    workspace = Workspace(
        id=workspace_id,
        name=name.strip(),
        slug=_workspace_slug(name, workspace_id),
        industry=industry,
        country_code=country_code.upper(),
        timezone=timezone,
        default_locale=default_locale,
        data_region=data_region,
        status=WorkspaceStatus.ACTIVE.value,
        created_by_user_id=creator_id,
        retention_policy={},
        generation_policy={},
        approval_policy={},
    )
    roles = {
        key: Role(
            id=uuid4(),
            workspace_id=workspace_id,
            key=key,
            name=name_value,
            permissions=permissions,
            is_system=True,
            is_owner=key == "owner",
        )
        for key, (name_value, permissions) in _default_roles().items()
    }
    policy = WorkspaceAuthenticationPolicy(
        id=uuid4(),
        workspace_id=workspace_id,
        password_min_length=12,
        max_login_failures=LOGIN_FAILURE_LIMIT,
        lockout_seconds=int(LOGIN_LOCKOUT.total_seconds()),
        access_token_ttl_seconds=int(DEFAULT_ACCESS_TTL.total_seconds()),
        session_ttl_seconds=int(DEFAULT_SESSION_TTL.total_seconds()),
        require_mfa_role_keys=[],
        password_login_enabled=True,
        sso_enforced_domains=[],
    )
    return workspace, roles, policy


def _default_roles() -> dict[str, tuple[str, list[str]]]:
    all_permissions = sorted(permission.value for permission in Permission)
    read = [Permission.WORKSPACE_READ.value]
    return {
        "owner": ("Owner", all_permissions),
        "admin": ("Admin", all_permissions),
        "strategist": (
            "Strategist",
            read
            + [
                Permission.BRAND_READ.value,
                Permission.BRAND_WRITE.value,
                Permission.KNOWLEDGE_READ.value,
                Permission.KNOWLEDGE_WRITE.value,
                Permission.KEYWORD_READ.value,
                Permission.KEYWORD_WRITE.value,
                Permission.KEYWORD_EXPORT.value,
                Permission.PLANNING_READ.value,
                Permission.PLANNING_WRITE.value,
                Permission.PLANNING_APPROVE.value,
                Permission.PLANNING_EXPORT.value,
                Permission.CONTENT_READ.value,
                Permission.CONTENT_WRITE.value,
                Permission.CONTENT_APPROVE.value,
                Permission.MEDIA_READ.value,
                Permission.MEDIA_WRITE.value,
                Permission.MEDIA_MANAGE.value,
                Permission.BULK_READ.value,
                Permission.BULK_WRITE.value,
                Permission.BULK_APPROVE.value,
                Permission.BULK_EXPORT.value,
                Permission.BULK_MANAGE.value,
            ],
        ),
        "writer": (
            "Writer",
            read
            + [
                Permission.BRAND_READ.value,
                Permission.KNOWLEDGE_READ.value,
                Permission.KNOWLEDGE_WRITE.value,
                Permission.KEYWORD_READ.value,
                Permission.PLANNING_READ.value,
                Permission.CONTENT_READ.value,
                Permission.CONTENT_WRITE.value,
                Permission.MEDIA_READ.value,
                Permission.MEDIA_WRITE.value,
                Permission.BULK_READ.value,
                Permission.BULK_WRITE.value,
            ],
        ),
        "reviewer": (
            "Reviewer",
            read
            + [
                Permission.BRAND_READ.value,
                Permission.KNOWLEDGE_READ.value,
                Permission.KEYWORD_READ.value,
                Permission.PLANNING_READ.value,
                Permission.CONTENT_READ.value,
                Permission.MEDIA_READ.value,
                Permission.BULK_READ.value,
            ],
        ),
        "approver": (
            "Approver",
            read
            + [
                Permission.KEYWORD_READ.value,
                Permission.PLANNING_READ.value,
                Permission.PLANNING_APPROVE.value,
                Permission.CONTENT_READ.value,
                Permission.CONTENT_APPROVE.value,
                Permission.MEDIA_READ.value,
                Permission.BULK_READ.value,
                Permission.BULK_APPROVE.value,
            ],
        ),
        "publisher": (
            "Publisher",
            read
            + [
                Permission.KEYWORD_READ.value,
                Permission.PLANNING_READ.value,
                Permission.CONTENT_READ.value,
                Permission.CONTENT_PUBLISH.value,
                Permission.MEDIA_READ.value,
                Permission.BULK_READ.value,
            ],
        ),
        "analyst": (
            "Analyst",
            read
            + [
                Permission.KEYWORD_READ.value,
                Permission.PLANNING_READ.value,
                Permission.CONTENT_READ.value,
                Permission.MEDIA_READ.value,
                Permission.BULK_READ.value,
                Permission.BULK_EXPORT.value,
            ],
        ),
        "billing": (
            "Billing",
            read
            + [
                Permission.BILLING_READ.value,
                Permission.BILLING_MANAGE.value,
            ],
        ),
        "developer": ("Developer", read + [Permission.API_MANAGE.value]),
        "agency_manager": (
            "Agency Manager",
            read
            + [
                Permission.AGENCY_READ.value,
                Permission.AGENCY_MANAGE.value,
                Permission.PORTAL_MANAGE.value,
            ],
        ),
        "viewer": (
            "Viewer",
            read
            + [
                Permission.BRAND_READ.value,
                Permission.KNOWLEDGE_READ.value,
                Permission.KEYWORD_READ.value,
                Permission.PLANNING_READ.value,
                Permission.CONTENT_READ.value,
                Permission.MEDIA_READ.value,
                Permission.BULK_READ.value,
            ],
        ),
    }


def _workspace_slug(name: str, workspace_id: UUID) -> str:
    normalized = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    base = re.sub(r"[^a-z0-9]+", "-", normalized.casefold()).strip("-")
    base = base[:80].strip("-") or "workspace"
    return f"{base}-{workspace_id.hex[:8]}"


def _validate_one_time_token(token: OneTimeToken | None, *, now: datetime) -> None:
    if (
        token is None
        or token.consumed_at is not None
        or token.superseded_at is not None
        or token.expires_at <= now
    ):
        raise _invalid_token_error()


def _validate_permissions(permissions: list[str]) -> None:
    known = {permission.value for permission in Permission}
    unknown = sorted(set(permissions).difference(known))
    if unknown:
        raise AppError(
            code="UNKNOWN_PERMISSION",
            message="지원하지 않는 권한이 포함되어 있습니다.",
            status_code=422,
            fields=[{"path": "permissions", "reason": value} for value in unknown],
        )


def _role_key_from_scim_groups(mapping: dict[str, Any], groups: tuple[str, ...]) -> str:
    for group in groups:
        role_key = mapping.get(group)
        if isinstance(role_key, str):
            return role_key
    default = mapping.get("__default__")
    return default if isinstance(default, str) else "viewer"


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


async def _apply_user_scope(session: AsyncSession, user_id: UUID) -> None:
    """Bind a server-resolved user for safe cross-workspace membership discovery.

    The value comes from an internal user lookup or a verified access token, never a user-id
    request header. RLS still limits membership discovery to rows owned by this user.
    """

    await session.execute(
        text("SELECT set_config('app.current_user_id', :user_id, true)"),
        {"user_id": str(user_id)},
    )


def _invalid_token_error() -> AppError:
    return AppError(
        code="ONE_TIME_TOKEN_INVALID",
        message="토큰이 유효하지 않거나 만료되었습니다.",
        status_code=400,
    )


def _invalid_invitation_error() -> AppError:
    return AppError(
        code="INVITATION_INVALID",
        message="초대가 유효하지 않거나 만료되었습니다.",
        status_code=400,
    )


def _invalid_credentials_error() -> AppError:
    return AppError(
        code="INVALID_CREDENTIALS",
        message="이메일 또는 비밀번호가 올바르지 않습니다.",
        status_code=401,
    )


def _login_locked_error() -> AppError:
    return AppError(
        code="LOGIN_TEMPORARILY_LOCKED",
        message="로그인 시도가 제한되었습니다. 잠시 후 다시 시도해 주세요.",
        status_code=429,
    )
