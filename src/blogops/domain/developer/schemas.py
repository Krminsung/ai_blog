"""Strict developer-product API contracts."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from blogops.domain.developer.enums import ApiEnvironment
from blogops.domain.developer.security import validate_ip_allowlist


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ORMModel(StrictModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class ApiKeyCreate(StrictModel):
    name: str = Field(min_length=1, max_length=160)
    environment: ApiEnvironment = ApiEnvironment.PRODUCTION
    scopes: set[str] = Field(min_length=1, max_length=100)
    ip_allowlist: list[str] = Field(default_factory=list, max_length=100)
    endpoint_allowlist: list[str] = Field(default_factory=list, max_length=200)
    expires_at: datetime | None = None

    @field_validator("ip_allowlist")
    @classmethod
    def valid_networks(cls, value: list[str]) -> list[str]:
        return list(validate_ip_allowlist(value))

    @field_validator("expires_at")
    @classmethod
    def aware_expiry(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("expires_at must be timezone-aware")
        return value


class ApiKeyRotate(StrictModel):
    scopes: set[str] | None = Field(default=None, min_length=1, max_length=100)
    expires_at: datetime | None = None
    reason: str = Field(min_length=3, max_length=500)

    @field_validator("expires_at")
    @classmethod
    def aware_expiry(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("expires_at must be timezone-aware")
        return value


class ApiKeyRevoke(StrictModel):
    reason: str = Field(min_length=3, max_length=2_000)


class ApiKeyRead(ORMModel):
    id: UUID
    name: str
    prefix: str
    environment: str
    scopes: list[str]
    ip_allowlist: list[str]
    endpoint_allowlist: list[str]
    state: str
    generation: int
    rotated_from_id: UUID | None
    rotated_to_id: UUID | None
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class ApiKeyIssued(StrictModel):
    key: ApiKeyRead
    raw_key: str = Field(description="Returned once and never persisted")


class RateLimitPolicyCreate(StrictModel):
    scope_kind: str = Field(pattern=r"^(WORKSPACE|ENDPOINT|KEY)$")
    scope_ref: str = Field(min_length=1, max_length=500)
    endpoint_pattern: str = Field(min_length=1, max_length=500)
    version: int = Field(gt=0)
    request_limit: int = Field(gt=0)
    window_seconds: int = Field(gt=0)
    burst: int = Field(ge=0)
    concurrent_limit: int | None = Field(default=None, gt=0)
    active_from: datetime
    active_until: datetime | None = None

    @model_validator(mode="after")
    def valid_window(self) -> "RateLimitPolicyCreate":
        if self.active_from.tzinfo is None or (
            self.active_until is not None and self.active_until.tzinfo is None
        ):
            raise ValueError("policy timestamps must be timezone-aware")
        if self.active_until is not None and self.active_until <= self.active_from:
            raise ValueError("active_until must be later than active_from")
        return self


class RateLimitPolicyRead(ORMModel):
    id: UUID
    scope_kind: str
    scope_ref: str
    endpoint_pattern: str
    version: int
    request_limit: int
    window_seconds: int
    burst: int
    concurrent_limit: int | None
    active_from: datetime
    active_until: datetime | None
    created_at: datetime


class WebhookEndpointCreate(StrictModel):
    name: str = Field(min_length=1, max_length=160)
    url: str = Field(min_length=8, max_length=2_048)
    event_types: set[str] = Field(min_length=1, max_length=100)
    secret_ref: str = Field(min_length=3, max_length=512)
    secret_version: str = Field(min_length=1, max_length=80)
    failure_disable_threshold: int = Field(gt=0)

    @field_validator("secret_ref")
    @classmethod
    def opaque_secret_reference(cls, value: str) -> str:
        if not value.startswith(("kms://", "secret-manager://", "vault://")):
            raise ValueError("secret_ref must be an opaque managed-secret reference")
        return value


class WebhookEndpointRead(ORMModel):
    id: UUID
    name: str
    normalized_url: str
    hostname: str
    event_types: list[str]
    secret_version: str
    state: str
    verified_at: datetime | None
    failure_count: int
    disabled_at: datetime | None
    disabled_reason: str | None
    created_at: datetime


class WebhookEventCreate(StrictModel):
    source_event_id: str = Field(min_length=8, max_length=500)
    event_type: str = Field(min_length=3, max_length=160)
    schema_version: str = Field(min_length=1, max_length=80)
    payload_object_ref: str = Field(min_length=3, max_length=1_000)
    payload_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    payload_preview: dict[str, Any]
    occurred_at: datetime
    max_attempts: int = Field(gt=0)
    manual_replay_limit: int = Field(ge=0)
    retry_policy: dict[str, Any]

    @field_validator("occurred_at")
    @classmethod
    def aware_occurred_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def complete_retry_policy(self) -> "WebhookEventCreate":
        required = {
            "base_delay_seconds",
            "max_delay_seconds",
            "jitter_ratio",
            "success_status_min",
            "success_status_max",
            "retryable_statuses",
        }
        if not required <= self.retry_policy.keys():
            raise ValueError("retry_policy is incomplete")
        base = int(self.retry_policy["base_delay_seconds"])
        maximum = int(self.retry_policy["max_delay_seconds"])
        jitter = float(self.retry_policy["jitter_ratio"])
        if base <= 0 or maximum < base or not 0 <= jitter <= 1:
            raise ValueError("retry_policy backoff values are invalid")
        minimum_status = int(self.retry_policy["success_status_min"])
        maximum_status = int(self.retry_policy["success_status_max"])
        if not 100 <= minimum_status <= maximum_status <= 599:
            raise ValueError("retry_policy success status range is invalid")
        if not isinstance(self.retry_policy["retryable_statuses"], list):
            raise ValueError("retryable_statuses must be a list")
        return self


class WebhookDeliveryRead(ORMModel):
    id: UUID
    endpoint_id: UUID
    event_id: UUID
    state: str
    attempt_count: int
    cycle_attempt_count: int
    max_attempts: int
    manual_replay_count: int
    manual_replay_limit: int
    next_attempt_at: datetime
    delivered_at: datetime | None
    dead_lettered_at: datetime | None
    last_error_code: str | None
    created_at: datetime


class WebhookReplayRequest(StrictModel):
    reason: str = Field(min_length=3, max_length=1_000)
