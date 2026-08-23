"""Strict agency and client-portal API contracts."""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from blogops.domain.b2b.rules import normalize_white_label_domain


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ORMModel(StrictModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


def _secret_paths(value: Any, path: str = "payload") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for raw_key, nested in value.items():
            key = str(raw_key)
            normalized = key.casefold().replace("-", "_").replace(".", "_")
            current = f"{path}.{key}"
            if frozenset(normalized.split("_")).intersection(
                {"secret", "password", "token", "authorization", "cookie"}
            ) or normalized in {"api_key", "access_key", "private_key"}:
                found.append(current)
            else:
                found.extend(_secret_paths(nested, current))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            found.extend(_secret_paths(nested, f"{path}[{index}]"))
    return found


class AgencyCreate(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    consolidated_billing: bool = False
    default_client_permissions: set[str] = Field(min_length=1, max_length=100)


class AgencyRead(ORMModel):
    id: UUID
    workspace_id: UUID
    name: str
    state: str
    consolidated_billing: bool
    default_client_permissions: list[str]
    created_at: datetime


class AgencyClientCreate(StrictModel):
    client_workspace_id: UUID
    client_display_name: str = Field(min_length=1, max_length=200)
    permissions: set[str] = Field(min_length=1, max_length=100)
    billing_mode: str = Field(pattern=r"^(SEPARATE|CONSOLIDATED)$")
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def metadata_has_no_secrets(self) -> "AgencyClientCreate":
        if _secret_paths(self.metadata, "metadata"):
            raise ValueError("metadata must not contain plaintext secrets")
        return self


class AgencyClientRead(ORMModel):
    id: UUID
    agency_id: UUID
    client_workspace_id: UUID
    client_display_name: str
    state: str
    permissions: list[str]
    billing_mode: str
    activated_at: datetime | None
    created_at: datetime


class PortalInvitationCreate(StrictModel):
    email: str = Field(min_length=3, max_length=320)
    scopes: set[str] = Field(min_length=1, max_length=20)
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def aware_expiry(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("expires_at must be timezone-aware")
        return value


class PortalInvitationIssued(StrictModel):
    invitation_id: UUID
    token: str = Field(description="Returned once and never persisted")
    expires_at: datetime


class PortalInvitationAccept(StrictModel):
    token: SecretStr = Field(min_length=30, max_length=512)


class PortalGrantRead(ORMModel):
    id: UUID
    agency_client_id: UUID
    client_workspace_id: UUID
    user_id: UUID
    scopes: list[str]
    state: str
    expires_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class WhiteLabelVersionCreate(StrictModel):
    version: int = Field(gt=0)
    custom_domain: str | None = None
    dns_challenge_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    logo_asset_ref: str | None = Field(default=None, max_length=1_000)
    email_sender_domain: str | None = Field(default=None, max_length=253)
    branding: dict[str, Any]

    @field_validator("custom_domain", "email_sender_domain")
    @classmethod
    def normalize_domain(cls, value: str | None) -> str | None:
        return normalize_white_label_domain(value) if value else None


class WhiteLabelVersionRead(ORMModel):
    id: UUID
    agency_id: UUID
    version: int
    custom_domain: str | None
    domain_state: str
    dns_verified_at: datetime | None
    logo_asset_ref: str | None
    email_sender_domain: str | None
    email_sender_verified: bool
    branding: dict[str, Any]
    created_at: datetime


class ProvisionClientCreate(StrictModel):
    workspace: dict[str, Any]
    permissions: set[str] = Field(min_length=1, max_length=100)
    idempotency_key: str = Field(min_length=8, max_length=255)

    @model_validator(mode="after")
    def workspace_has_no_secrets(self) -> "ProvisionClientCreate":
        if _secret_paths(self.workspace, "workspace"):
            raise ValueError("workspace request must not contain plaintext secrets")
        return self


class ProvisionClientRead(ORMModel):
    id: UUID
    state: str
    provisioned_workspace_id: UUID | None
    provider_operation_ref: str | None
    error_code: str | None
    created_at: datetime


class CreditAllocationPolicyCreate(StrictModel):
    version: int = Field(gt=0)
    monthly_credit_limit: Decimal = Field(ge=0, max_digits=19, decimal_places=6)
    overage_policy: str = Field(pattern=r"^(BLOCK|USE_CREDITS|POSTPAID)$")
    effective_at: datetime

    @field_validator("effective_at")
    @classmethod
    def aware_effective_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("effective_at must be timezone-aware")
        return value


class CreditAllocationPolicyRead(ORMModel):
    id: UUID
    agency_client_id: UUID
    version: int
    monthly_credit_limit: Decimal
    overage_policy: str
    effective_at: datetime
    retired_at: datetime | None
    created_at: datetime
