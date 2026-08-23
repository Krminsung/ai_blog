"""Strict platform operations and notification API contracts."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from blogops.domain.admin.enums import (
    AdminApprovalDecision,
    AdminCommandKind,
    NotificationChannel,
    NotificationFrequency,
)
from blogops.domain.admin.rules import validate_notification_preference


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ORMModel(StrictModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class SupportAccessCreate(StrictModel):
    target_workspace_id: UUID
    reason: str = Field(min_length=10, max_length=4_000)
    ticket_ref: str = Field(min_length=1, max_length=500)
    scopes: set[str] = Field(min_length=1, max_length=20)
    requested_minutes: int = Field(gt=0, le=480)
    content_access_requested: bool = False
    idempotency_key: str = Field(min_length=8, max_length=255)


class SupportAccessDecision(StrictModel):
    approve: bool
    approved_scopes: set[str] = Field(default_factory=set, max_length=20)
    approve_masked_content: bool = False
    reason: str = Field(min_length=3, max_length=2_000)

    @model_validator(mode="after")
    def approved_requires_scope(self) -> "SupportAccessDecision":
        if self.approve and not self.approved_scopes:
            raise ValueError("approved_scopes are required")
        if not self.approve and (self.approved_scopes or self.approve_masked_content):
            raise ValueError("denied requests cannot grant access")
        return self


class SupportAccessRead(ORMModel):
    id: UUID
    target_workspace_id: UUID
    requested_by: UUID
    reason: str
    ticket_ref: str
    requested_scopes: list[str]
    requested_minutes: int
    content_access_requested: bool
    state: str
    customer_approved_by: UUID | None
    customer_approved_scopes: list[str]
    customer_approved_content: bool
    decided_at: datetime | None
    expires_at: datetime | None
    created_at: datetime


class AdminElevationSessionRead(ORMModel):
    id: UUID
    access_request_id: UUID
    target_workspace_id: UUID
    operator_id: UUID
    scopes: list[str]
    content_is_masked: bool
    state: str
    expires_at: datetime
    revoked_at: datetime | None
    created_at: datetime


class AdminCommandCreate(StrictModel):
    target_workspace_id: UUID | None = None
    kind: AdminCommandKind
    target_type: str = Field(min_length=1, max_length=80)
    target_id: str = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=10, max_length=4_000)
    idempotency_key: str = Field(min_length=8, max_length=255)
    parameters: dict[str, Any] = Field(default_factory=dict)
    secure_parameters_ref: str | None = Field(default=None, max_length=1_000)


class AdminCommandDecision(StrictModel):
    decision: AdminApprovalDecision
    reason: str = Field(min_length=3, max_length=2_000)
    mfa_verified: bool


class AdminCommandRead(ORMModel):
    id: UUID
    target_workspace_id: UUID | None
    requested_by: UUID
    kind: str
    target_type: str
    target_id: str
    reason: str
    required_approvals: int
    approval_count: int
    state: str
    dispatched_at: datetime | None
    completed_at: datetime | None
    result_ref: str | None
    error_code: str | None
    created_at: datetime


class NotificationPreferenceUpsert(StrictModel):
    event_type: str = Field(min_length=1, max_length=160)
    channel: NotificationChannel
    frequency: NotificationFrequency
    digest_hour: int | None = Field(default=None, ge=0, le=23)
    timezone: str = Field(min_length=1, max_length=64)
    quiet_hours: dict[str, Any] | None = None

    @model_validator(mode="after")
    def enforce_mandatory(self) -> "NotificationPreferenceUpsert":
        validate_notification_preference(
            event_type=self.event_type,
            frequency=self.frequency.value,
        )
        if self.frequency == NotificationFrequency.DIGEST and self.digest_hour is None:
            raise ValueError("digest_hour is required for digest notifications")
        return self


class NotificationPreferenceRead(ORMModel):
    id: UUID
    event_type: str
    channel: str
    frequency: str
    digest_hour: int | None
    timezone: str
    quiet_hours: dict[str, Any] | None
    updated_at: datetime


class NotificationRead(ORMModel):
    id: UUID
    event_type: str
    priority: str
    title: str
    safe_summary: str
    action_url: str | None
    mandatory: bool
    read_at: datetime | None
    archived_at: datetime | None
    snoozed_until: datetime | None
    created_at: datetime


class NotificationSnooze(StrictModel):
    until: datetime
