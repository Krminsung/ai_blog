"""Strict platform operations and GA-readiness API contracts."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from blogops.domain.operations.enums import (
    ComponentKind,
    OperationalIncidentEventKind,
    OperationalIncidentState,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ORMModel(StrictModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class ServiceComponentCreate(StrictModel):
    component_key: str = Field(min_length=1, max_length=160)
    display_name: str = Field(min_length=1, max_length=240)
    kind: ComponentKind
    endpoint_ref: str = Field(min_length=1, max_length=1_000)
    public: bool = False
    owner_team: str = Field(min_length=1, max_length=160)
    service_tier: str = Field(min_length=1, max_length=40)
    safe_metadata: dict[str, Any] = Field(default_factory=dict)


class ServiceComponentRead(ORMModel):
    id: UUID
    component_key: str
    display_name: str
    kind: str
    public: bool
    enabled: bool
    owner_team: str
    service_tier: str
    safe_metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class HealthObservationRead(ORMModel):
    id: UUID
    component_id: UUID
    status: str
    checked_at: datetime
    valid_until: datetime
    latency_ms: int | None
    safe_metrics: dict[str, Any]
    evidence_hash: str


class OperationalIncidentCreate(StrictModel):
    external_ref: str = Field(min_length=1, max_length=500)
    title: str = Field(min_length=3, max_length=240)
    safe_summary: str = Field(min_length=3, max_length=10_000)
    severity: str = Field(pattern=r"^(LOW|MEDIUM|HIGH|CRITICAL)$")
    component_ids: list[UUID] = Field(min_length=1)
    affected_workspace_ids: list[UUID] = Field(default_factory=list)
    started_at: datetime
    runbook_version_id: UUID

    @field_validator("started_at")
    @classmethod
    def aware_started_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("started_at must be timezone-aware")
        return value


class OperationalIncidentEventCreate(StrictModel):
    kind: OperationalIncidentEventKind
    state_after: OperationalIncidentState
    safe_summary: str = Field(min_length=3, max_length=10_000)
    evidence_object_refs: list[str] = Field(default_factory=list, max_length=100)
    evidence_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    occurred_at: datetime

    @field_validator("occurred_at")
    @classmethod
    def aware_occurred_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")
        return value


class OperationalIncidentNotify(StrictModel):
    audience: str = Field(min_length=1, max_length=80)
    template_version: str = Field(min_length=1, max_length=80)
    safe_payload: dict[str, Any]


class OperationalIncidentRead(ORMModel):
    id: UUID
    external_ref: str
    title: str
    safe_summary: str
    severity: str
    state: str
    component_ids: list[str]
    affected_workspace_ids: list[str]
    started_at: datetime
    identified_at: datetime | None
    resolved_at: datetime | None
    runbook_version_id: UUID
    created_at: datetime
    updated_at: datetime


class OperationalIncidentEventRead(ORMModel):
    id: UUID
    incident_id: UUID
    sequence: int
    kind: str
    state_after: str
    safe_summary: str
    evidence_hash: str
    event_hash: str
    occurred_at: datetime


class StatusNotificationRead(ORMModel):
    id: UUID
    incident_id: UUID
    audience: str
    template_version: str
    provider_message_ref: str
    evidence_hash: str
    delivered_at: datetime


class RunbookVersionCreate(StrictModel):
    runbook_key: str = Field(min_length=1, max_length=160)
    version: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=240)
    artifact_ref: str = Field(min_length=1, max_length=1_000)
    artifact_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    owner_team: str = Field(min_length=1, max_length=160)
    escalation_policy_ref: str = Field(min_length=1, max_length=1_000)
    exercise_interval_days: int = Field(gt=0, le=365)
    effective_at: datetime
    retired_at: datetime | None = None

    @model_validator(mode="after")
    def valid_window(self) -> "RunbookVersionCreate":
        if self.effective_at.tzinfo is None:
            raise ValueError("effective_at must be timezone-aware")
        if self.retired_at is not None and self.retired_at <= self.effective_at:
            raise ValueError("retired_at must be after effective_at")
        return self


class RunbookVersionRead(ORMModel):
    id: UUID
    runbook_key: str
    version: int
    title: str
    artifact_hash: str
    owner_team: str
    exercise_interval_days: int
    effective_at: datetime
    retired_at: datetime | None


class BackupPolicyCreate(StrictModel):
    policy_key: str = Field(min_length=1, max_length=160)
    version: int = Field(gt=0)
    data_scope: list[str] = Field(min_length=1)
    rpo_minutes: int = Field(gt=0)
    rto_minutes: int = Field(gt=0)
    backup_interval_minutes: int = Field(gt=0)
    pitr_enabled: bool
    encrypted: bool
    encryption_key_policy: dict[str, Any]
    retention_cycles: int = Field(gt=0)
    quarterly_drill_required: bool
    region_policy: dict[str, Any]
    effective_at: datetime
    retired_at: datetime | None = None

    @model_validator(mode="after")
    def valid_window(self) -> "BackupPolicyCreate":
        if self.effective_at.tzinfo is None:
            raise ValueError("effective_at must be timezone-aware")
        if self.retired_at is not None and self.retired_at <= self.effective_at:
            raise ValueError("retired_at must be after effective_at")
        return self


class BackupPolicyRead(ORMModel):
    id: UUID
    policy_key: str
    version: int
    data_scope: list[str]
    rpo_minutes: int
    rto_minutes: int
    backup_interval_minutes: int
    pitr_enabled: bool
    encrypted: bool
    retention_cycles: int
    quarterly_drill_required: bool
    region_policy: dict[str, Any]
    policy_hash: str
    effective_at: datetime
    retired_at: datetime | None


class BackupRunCreate(StrictModel):
    policy_version_id: UUID


class BackupRunRead(ORMModel):
    id: UUID
    policy_version_id: UUID
    state: str
    attempt_count: int
    provider_run_ref: str | None
    requested_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    failure_code: str | None


class BackupEvidenceRead(ORMModel):
    id: UUID
    run_id: UUID
    provider_run_ref: str
    snapshot_hash: str
    encryption_key_version: str
    started_at: datetime
    completed_at: datetime
    restore_point_at: datetime
    size_bytes: int
    verified: bool
    evidence_hash: str


class RecoveryExerciseCreate(StrictModel):
    backup_evidence_id: UUID
    runbook_version_id: UUID


class RecoveryExerciseRead(ORMModel):
    id: UUID
    backup_evidence_id: UUID
    runbook_version_id: UUID
    rpo_minutes: int
    rto_minutes: int
    state: str
    attempt_count: int
    provider_run_ref: str | None
    requested_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    failure_code: str | None


class RecoveryEvidenceRead(ORMModel):
    id: UUID
    exercise_id: UUID
    provider_run_ref: str
    data_loss_minutes: int
    recovery_minutes: int
    objectives_met: bool
    integrity_checks: dict[str, Any]
    started_at: datetime
    completed_at: datetime
    evidence_hash: str


class GAAssessmentCreate(StrictModel):
    release_ref: str = Field(min_length=1, max_length=500)
    artifact_refs: list[str] = Field(min_length=1, max_length=100)


class GAAssessmentRead(ORMModel):
    id: UUID
    release_ref: str
    artifact_refs: list[str]
    state: str
    attempt_count: int
    decision_hash: str | None
    requested_at: datetime
    verified_at: datetime | None
    failure_code: str | None


class GAGateEvidenceRead(ORMModel):
    id: UUID
    assessment_id: UUID
    gate: str
    passed: bool
    verified_at: datetime
    verifier: str
    evidence_hash: str
    metrics: dict[str, Any]
    reason_codes: list[str]
