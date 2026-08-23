"""SQLAlchemy persistence models for identity and tenant ownership.

Secret-bearing tokens are represented only by keyed hashes. TOTP seeds are stored as
authenticated ciphertext, while external IdP/SCIM credentials are represented by an opaque
secret-manager reference.
"""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from blogops.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from blogops.domain.identity.enums import (
    AgencyClientStatus,
    ChallengePurpose,
    ConnectionStatus,
    CredentialKind,
    FederationProtocol,
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


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=UserStatus.PENDING_EMAIL.value, index=True
    )
    locale: Mapped[str] = mapped_column(String(16), nullable=False, default="ko-KR")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    date_format: Mapped[str] = mapped_column(String(32), nullable=False, default="YYYY-MM-DD")
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    inactive_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deletion_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserCredential(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "user_credentials"
    __table_args__ = (
        UniqueConstraint("user_id", "kind", name="uq_user_credentials_user_kind"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(
        String(24), nullable=False, default=CredentialKind.PASSWORD.value
    )
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class OneTimeToken(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "identity_one_time_tokens"
    __table_args__ = (
        Index("ix_identity_one_time_tokens_lookup", "purpose", "token_hash"),
        Index("ix_identity_one_time_tokens_expiry", "expires_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    purpose: Mapped[str] = mapped_column(String(40), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    requested_ip_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TermsConsent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "terms_consents"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "document_type", "document_version", name="uq_terms_consents_version"
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_type: Mapped[str] = mapped_column(String(80), nullable=False)
    document_version: Mapped[str] = mapped_column(String(40), nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    accepted_ip_hash: Mapped[str | None] = mapped_column(String(64))


class TermsDocumentVersion(UUIDPrimaryKeyMixin, Base):
    """Server-owned terms catalog used to enforce re-consent after required revisions."""

    __tablename__ = "terms_document_versions"
    __table_args__ = (
        UniqueConstraint(
            "document_type", "document_version", name="uq_terms_document_versions_identity"
        ),
        Index("ix_terms_document_versions_effective", "required", "effective_at"),
    )

    document_type: Mapped[str] = mapped_column(String(80), nullable=False)
    document_version: Mapped[str] = mapped_column(String(40), nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AuthenticationChallenge(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "authentication_challenges"
    __table_args__ = (
        Index("ix_authentication_challenges_lookup", "purpose", "token_hash"),
        Index("ix_authentication_challenges_expiry", "expires_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    purpose: Mapped[str] = mapped_column(
        String(40), nullable=False, default=ChallengePurpose.MFA_LOGIN.value
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    context: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class LoginSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "login_sessions"
    __table_args__ = (
        Index("ix_login_sessions_user_status", "user_id", "status"),
        Index("ix_login_sessions_expiry", "expires_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    family_id: Mapped[UUID] = mapped_column(nullable=False, default=uuid4, index=True)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=SessionStatus.ACTIVE.value
    )
    device_name: Mapped[str | None] = mapped_column(String(120))
    device_id_hash: Mapped[str | None] = mapped_column(String(64))
    user_agent_hash: Mapped[str | None] = mapped_column(String(64))
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    country_code: Mapped[str | None] = mapped_column(String(2))
    authentication_methods: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    mfa_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoke_reason: Mapped[str | None] = mapped_column(String(120))


class SessionRefreshToken(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "session_refresh_tokens"
    __table_args__ = (
        UniqueConstraint("session_id", "generation", name="uq_session_refresh_generation"),
        Index("ix_session_refresh_tokens_expiry", "expires_at"),
    )

    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("login_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replaced_by_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("session_refresh_tokens.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MFAFactor(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "mfa_factors"
    __table_args__ = (
        Index("ix_mfa_factors_user_status", "user_id", "status"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(
        String(24), nullable=False, default=MFAFactorKind.TOTP.value
    )
    label: Mapped[str] = mapped_column(String(120), nullable=False, default="Authenticator")
    secret_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=MFAFactorStatus.PENDING.value
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_step: Mapped[int | None] = mapped_column(Integer)


class MFARecoveryCode(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "mfa_recovery_codes"

    factor_id: Mapped[UUID] = mapped_column(
        ForeignKey("mfa_factors.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Workspace(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workspaces"

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    industry: Mapped[str | None] = mapped_column(String(120))
    country_code: Mapped[str] = mapped_column(String(2), nullable=False, default="KR")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Seoul")
    default_locale: Mapped[str] = mapped_column(String(16), nullable=False, default="ko-KR")
    data_region: Mapped[str] = mapped_column(String(40), nullable=False, default="ap-northeast")
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=WorkspaceStatus.ACTIVE.value, index=True
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    retention_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    generation_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    approval_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    default_channel_ref: Mapped[str | None] = mapped_column(String(255))
    deletion_scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Role(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "roles"
    __table_args__ = (
        UniqueConstraint("workspace_id", "key", name="uq_roles_workspace_key"),
        UniqueConstraint("workspace_id", "name", name="uq_roles_workspace_name"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    permissions: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_owner: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Membership(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", name="uq_memberships_workspace_user"),
        Index("ix_memberships_user_status", "user_id", "status"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    role_id: Mapped[UUID] = mapped_column(
        ForeignKey("roles.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=MembershipStatus.ACTIVE.value
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkspaceInvitation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workspace_invitations"
    __table_args__ = (
        Index("ix_workspace_invitations_pending", "workspace_id", "email", "status"),
        Index("ix_workspace_invitations_expiry", "expires_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    role_id: Mapped[UUID] = mapped_column(
        ForeignKey("roles.id", ondelete="RESTRICT"), nullable=False
    )
    invited_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=InvitationStatus.PENDING.value
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkspaceAuthenticationPolicy(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workspace_authentication_policies"
    __table_args__ = (
        CheckConstraint(
            "jsonb_typeof(require_mfa_role_keys) = 'array' "
            "AND require_mfa_role_keys @> '[\"owner\", \"admin\"]'::jsonb",
            name="auth_policy_privileged_mfa",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    password_min_length: Mapped[int] = mapped_column(Integer, nullable=False, default=12)
    max_login_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    lockout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=900)
    access_token_ttl_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=900)
    session_ttl_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=2_592_000)
    require_mfa_role_keys: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=lambda: ["owner", "admin"],
    )
    password_login_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sso_enforced_domains: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)


class FederatedProviderConnection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Provider-neutral OAuth/OIDC/SAML connection metadata."""

    __tablename__ = "federated_provider_connections"
    __table_args__ = (
        UniqueConstraint("workspace_id", "provider_key", name="uq_federated_provider_key"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider_key: Mapped[str] = mapped_column(String(100), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    protocol: Mapped[str] = mapped_column(
        String(20), nullable=False, default=FederationProtocol.OIDC.value
    )
    issuer: Mapped[str | None] = mapped_column(String(500))
    discovery_url: Mapped[str | None] = mapped_column(String(1000))
    client_id: Mapped[str | None] = mapped_column(String(500))
    secret_ref: Mapped[str | None] = mapped_column(String(500))
    domains: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    attribute_mapping: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=ConnectionStatus.DRAFT.value
    )
    jit_provisioning_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class ExternalIdentity(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "external_identities"
    __table_args__ = (
        UniqueConstraint("issuer", "subject", name="uq_external_identities_subject"),
        UniqueConstraint(
            "user_id", "connection_id", name="uq_external_identities_user_connection"
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    connection_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("federated_provider_connections.id", ondelete="CASCADE"), index=True
    )
    provider_key: Mapped[str] = mapped_column(String(100), nullable=False)
    issuer: Mapped[str] = mapped_column(String(500), nullable=False)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    email_at_link: Mapped[str | None] = mapped_column(String(320))
    profile: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SCIMConfiguration(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "scim_configurations"

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    provider_key: Mapped[str] = mapped_column(String(100), nullable=False)
    bearer_token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    secret_ref: Mapped[str | None] = mapped_column(String(500))
    attribute_mapping: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    group_role_mapping: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=ConnectionStatus.ACTIVE.value
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SCIMResourceLink(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "scim_resource_links"
    __table_args__ = (
        UniqueConstraint(
            "configuration_id",
            "resource_type",
            "external_id",
            name="uq_scim_resource_external_id",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    configuration_id: Mapped[UUID] = mapped_column(
        ForeignKey("scim_configurations.id", ondelete="CASCADE"), nullable=False
    )
    resource_type: Mapped[str] = mapped_column(
        String(24), nullable=False, default=SCIMResourceType.USER.value
    )
    external_id: Mapped[str] = mapped_column(String(500), nullable=False)
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    role_id: Mapped[UUID | None] = mapped_column(ForeignKey("roles.id", ondelete="SET NULL"))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class Agency(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agencies"

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    white_label_config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    common_template_policy: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )


class AgencyClient(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agency_clients"
    __table_args__ = (
        UniqueConstraint("agency_id", "client_workspace_id", name="uq_agency_client_workspace"),
        UniqueConstraint("client_workspace_id", name="uq_agency_clients_client_workspace"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agency_id: Mapped[UUID] = mapped_column(
        ForeignKey("agencies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=AgencyClientStatus.ACTIVE.value
    )
    permissions: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    billing_allocation: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    template_overrides: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
