"""Pydantic request and response contracts for identity APIs."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from blogops.domain.identity.enums import FederationProtocol
from blogops.domain.identity.security import normalize_email


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class TermsAcceptance(BaseModel):
    document_type: str = Field(min_length=1, max_length=80)
    document_version: str = Field(min_length=1, max_length=40)
    required: bool = True


class SignupRequest(BaseModel):
    email: str
    password: SecretStr = Field(min_length=12, max_length=1024)
    display_name: str = Field(min_length=1, max_length=120)
    workspace_name: str = Field(min_length=1, max_length=160)
    industry: str | None = Field(default=None, max_length=120)
    country_code: str = Field(default="KR", min_length=2, max_length=2)
    timezone: str = Field(default="Asia/Seoul", min_length=1, max_length=64)
    locale: str = Field(default="ko-KR", min_length=2, max_length=16)
    terms: list[TermsAcceptance] = Field(min_length=1)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)

    @field_validator("display_name", "workspace_name")
    @classmethod
    def strip_names(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("값을 입력해야 합니다.")
        return stripped

    @field_validator("country_code")
    @classmethod
    def uppercase_country(cls, value: str) -> str:
        if not value.isalpha():
            raise ValueError("국가 코드는 ISO alpha-2 형식이어야 합니다.")
        return value.upper()

    @model_validator(mode="after")
    def validate_required_consents(self) -> "SignupRequest":
        identities = [
            (item.document_type.casefold(), item.document_version) for item in self.terms
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("같은 약관 버전 동의를 중복 제출할 수 없습니다.")
        accepted = {item.document_type.casefold() for item in self.terms if item.required}
        missing = {"terms", "privacy"}.difference(accepted)
        if missing:
            raise ValueError("필수 이용약관 및 개인정보 처리방침 동의가 필요합니다.")
        return self


class UserView(ORMModel):
    id: UUID
    email: str
    display_name: str
    status: str
    locale: str
    timezone: str
    email_verified_at: datetime | None
    mfa_enabled: bool
    created_at: datetime


class UserProfileUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    locale: str | None = Field(default=None, min_length=2, max_length=16)
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    date_format: str | None = Field(default=None, min_length=1, max_length=32)

    @model_validator(mode="after")
    def require_change(self) -> "UserProfileUpdateRequest":
        if not self.model_fields_set:
            raise ValueError("변경할 값을 하나 이상 입력해야 합니다.")
        return self


class WorkspaceView(ORMModel):
    id: UUID
    name: str
    slug: str
    industry: str | None
    country_code: str
    timezone: str
    default_locale: str
    data_region: str
    default_channel_ref: str | None
    status: str
    created_at: datetime
    updated_at: datetime


class SignupResponse(BaseModel):
    user: UserView
    workspace: WorkspaceView
    verification_required: bool = True


class EmailAddressRequest(BaseModel):
    email: str

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)


class OneTimeTokenRequest(BaseModel):
    token: SecretStr = Field(min_length=20, max_length=512)


class PasswordResetConfirmRequest(OneTimeTokenRequest):
    new_password: SecretStr = Field(min_length=12, max_length=1024)
    revoke_all_sessions: bool = True


class LoginRequest(BaseModel):
    email: str
    password: SecretStr = Field(min_length=1, max_length=1024)
    workspace_id: UUID | None = None
    device_name: str | None = Field(default=None, max_length=120)
    device_id: str | None = Field(default=None, max_length=500)
    country_code: str | None = Field(default=None, min_length=2, max_length=2)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    session_id: UUID
    workspace_id: UUID


class LoginResponse(BaseModel):
    mfa_required: bool
    challenge_token: str | None = None
    tokens: TokenPair | None = None

    @model_validator(mode="after")
    def validate_variant(self) -> "LoginResponse":
        if self.mfa_required == (self.tokens is not None):
            raise ValueError("login response must contain either an MFA challenge or tokens")
        if self.mfa_required and self.challenge_token is None:
            raise ValueError("MFA challenge token is required")
        return self


class MFALoginVerifyRequest(BaseModel):
    challenge_token: SecretStr = Field(min_length=20, max_length=512)
    code: SecretStr = Field(min_length=6, max_length=32)


class RefreshRequest(BaseModel):
    refresh_token: SecretStr = Field(min_length=20, max_length=512)
    workspace_id: UUID | None = None


class SessionView(ORMModel):
    id: UUID
    status: str
    device_name: str | None
    country_code: str | None
    authentication_methods: list[str]
    mfa_verified_at: datetime | None
    last_activity_at: datetime
    expires_at: datetime
    created_at: datetime


class MFAEnrollmentResponse(BaseModel):
    factor_id: UUID
    secret: str
    provisioning_uri: str


class MFAConfirmRequest(BaseModel):
    factor_id: UUID
    code: SecretStr = Field(min_length=6, max_length=6)


class MFAConfirmResponse(BaseModel):
    recovery_codes: list[str]


class MFADisableRequest(BaseModel):
    code: SecretStr = Field(min_length=6, max_length=32)


class TermsConsentRequest(BaseModel):
    consents: list[TermsAcceptance] = Field(min_length=1)


class WorkspaceCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    industry: str | None = Field(default=None, max_length=120)
    country_code: str = Field(default="KR", min_length=2, max_length=2)
    timezone: str = Field(default="Asia/Seoul", min_length=1, max_length=64)
    default_locale: str = Field(default="ko-KR", min_length=2, max_length=16)
    data_region: str = Field(default="ap-northeast", min_length=1, max_length=40)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("워크스페이스 이름이 필요합니다.")
        return stripped

    @field_validator("country_code")
    @classmethod
    def uppercase_country(cls, value: str) -> str:
        return value.upper()


class WorkspaceUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    industry: str | None = Field(default=None, max_length=120)
    country_code: str | None = Field(default=None, min_length=2, max_length=2)
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    default_locale: str | None = Field(default=None, min_length=2, max_length=16)
    default_channel_ref: str | None = Field(default=None, max_length=255)
    retention_policy: dict[str, Any] | None = None
    generation_policy: dict[str, Any] | None = None
    approval_policy: dict[str, Any] | None = None

    @model_validator(mode="after")
    def require_change(self) -> "WorkspaceUpdateRequest":
        if not self.model_fields_set:
            raise ValueError("변경할 값을 하나 이상 입력해야 합니다.")
        return self


class RoleView(ORMModel):
    id: UUID
    workspace_id: UUID
    key: str
    name: str
    permissions: list[str]
    is_system: bool
    is_owner: bool


class RoleCreateRequest(BaseModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,62}[a-z0-9]$")
    name: str = Field(min_length=1, max_length=100)
    permissions: list[str] = Field(default_factory=list)


class RoleUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    permissions: list[str] | None = None

    @model_validator(mode="after")
    def require_change(self) -> "RoleUpdateRequest":
        if not self.model_fields_set:
            raise ValueError("변경할 값을 하나 이상 입력해야 합니다.")
        return self


class MembershipView(BaseModel):
    id: UUID
    workspace_id: UUID
    user_id: UUID
    email: str
    display_name: str
    role: RoleView
    status: str
    joined_at: datetime


class InvitationCreateRequest(BaseModel):
    email: str
    role_id: UUID
    expires_in_hours: int = Field(default=72, ge=1, le=720)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)


class InvitationView(ORMModel):
    id: UUID
    workspace_id: UUID
    email: str
    role_id: UUID
    status: str
    expires_at: datetime
    accepted_at: datetime | None
    created_at: datetime


class InvitationAcceptRequest(BaseModel):
    token: SecretStr = Field(min_length=20, max_length=512)


class MembershipRoleUpdateRequest(BaseModel):
    role_id: UUID


class OwnershipTransferRequest(BaseModel):
    new_owner_user_id: UUID
    current_owner_role_id: UUID | None = None


class WorkspaceAuthenticationPolicyUpdate(BaseModel):
    password_min_length: int | None = Field(default=None, ge=12, le=128)
    max_login_failures: int | None = Field(default=None, ge=3, le=20)
    lockout_seconds: int | None = Field(default=None, ge=60, le=86_400)
    access_token_ttl_seconds: int | None = Field(default=None, ge=300, le=3600)
    session_ttl_seconds: int | None = Field(default=None, ge=3600, le=7_776_000)
    require_mfa_role_keys: list[str] | None = None
    password_login_enabled: bool | None = None
    sso_enforced_domains: list[str] | None = None

    @model_validator(mode="after")
    def require_change(self) -> "WorkspaceAuthenticationPolicyUpdate":
        if not self.model_fields_set:
            raise ValueError("변경할 값을 하나 이상 입력해야 합니다.")
        return self


class WorkspaceAuthenticationPolicyView(ORMModel):
    workspace_id: UUID
    password_min_length: int
    max_login_failures: int
    lockout_seconds: int
    access_token_ttl_seconds: int
    session_ttl_seconds: int
    require_mfa_role_keys: list[str]
    password_login_enabled: bool
    sso_enforced_domains: list[str]


class FederatedConnectionCreateRequest(BaseModel):
    provider_key: str = Field(min_length=1, max_length=100)
    display_name: str = Field(min_length=1, max_length=120)
    protocol: FederationProtocol
    issuer: str | None = Field(default=None, max_length=500)
    discovery_url: str | None = Field(default=None, max_length=1000)
    client_id: str | None = Field(default=None, max_length=500)
    secret_ref: str | None = Field(default=None, max_length=500)
    domains: list[str] = Field(default_factory=list)
    attribute_mapping: dict[str, Any] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)
    jit_provisioning_enabled: bool = False


class FederatedConnectionView(ORMModel):
    id: UUID
    workspace_id: UUID
    provider_key: str
    display_name: str
    protocol: str
    issuer: str | None
    discovery_url: str | None
    client_id: str | None
    domains: list[str]
    attribute_mapping: dict[str, Any]
    config: dict[str, Any]
    status: str
    jit_provisioning_enabled: bool


class SCIMConfigurationCreateRequest(BaseModel):
    provider_key: str = Field(min_length=1, max_length=100)
    secret_ref: str | None = Field(default=None, max_length=500)
    attribute_mapping: dict[str, Any] = Field(default_factory=dict)
    group_role_mapping: dict[str, Any] = Field(default_factory=dict)


class SCIMConfigurationCreated(BaseModel):
    id: UUID
    workspace_id: UUID
    bearer_token: str
    provider_key: str


class AgencyCreateRequest(BaseModel):
    white_label_config: dict[str, Any] = Field(default_factory=dict)
    common_template_policy: dict[str, Any] = Field(default_factory=dict)


class AgencyView(ORMModel):
    id: UUID
    workspace_id: UUID
    white_label_config: dict[str, Any]
    common_template_policy: dict[str, Any]


class AgencyClientCreateRequest(BaseModel):
    client_workspace_id: UUID
    permissions: list[str] = Field(default_factory=list)
    billing_allocation: dict[str, Any] = Field(default_factory=dict)
    template_overrides: dict[str, Any] = Field(default_factory=dict)


class AgencyClientView(ORMModel):
    id: UUID
    workspace_id: UUID
    agency_id: UUID
    client_workspace_id: UUID
    status: str
    permissions: list[str]
    billing_allocation: dict[str, Any]
    template_overrides: dict[str, Any]


class AuditLogView(ORMModel):
    id: UUID
    workspace_id: UUID | None
    actor_id: UUID | None
    action: str
    target_type: str
    target_id: str
    details: dict[str, Any]
    request_id: str | None
    occurred_at: datetime


class MessageResponse(BaseModel):
    message: str
