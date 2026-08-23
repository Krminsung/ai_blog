"""Strict API contracts for security, privacy, and copyright workflows."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

from blogops.domain.security.enums import (
    ComplianceAssessmentKind,
    ConsentDecision,
    ConsentPurpose,
    CopyrightEventKind,
    DataClass,
    LegalHoldEventKind,
    NotificationAudience,
    PrivacyRequestKind,
    SecurityIncidentEventKind,
    SecurityIncidentSeverity,
    SecurityIncidentState,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ORMModel(StrictModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class RetentionPolicyCreate(StrictModel):
    version: int = Field(gt=0)
    rules: dict[str, dict[str, Any]]
    data_region: str = Field(min_length=2, max_length=80)
    cross_border_policy: dict[str, Any]
    backup_erasure_policy: dict[str, Any]
    legal_basis_snapshot: dict[str, Any]
    effective_at: datetime
    retired_at: datetime | None = None

    @model_validator(mode="after")
    def valid_window(self) -> "RetentionPolicyCreate":
        if self.effective_at.tzinfo is None:
            raise ValueError("effective_at must be timezone-aware")
        if self.retired_at is not None:
            if self.retired_at.tzinfo is None or self.retired_at <= self.effective_at:
                raise ValueError("retired_at must be aware and after effective_at")
        return self


class RetentionPolicyRead(ORMModel):
    id: UUID
    version: int
    rules: dict[str, Any]
    data_region: str
    cross_border_policy: dict[str, Any]
    backup_erasure_policy: dict[str, Any]
    legal_basis_snapshot: dict[str, Any]
    policy_hash: str
    effective_at: datetime
    retired_at: datetime | None
    created_at: datetime


class RetentionSweepRead(ORMModel):
    id: UUID
    policy_version_id: UUID
    policy_snapshot_hash: str
    legal_hold_snapshot_hash: str
    state: str
    started_at: datetime | None
    completed_at: datetime | None
    failure_code: str | None
    created_at: datetime
    updated_at: datetime


class RetentionDispositionRead(ORMModel):
    id: UUID
    sweep_id: UUID
    data_class: str
    target_system: str
    cutoff_at: datetime
    disposition: str
    affected_records: int
    held_records: int
    passed: bool
    evidence_object_ref: str
    evidence_hash: str
    completed_at: datetime
    recorded_at: datetime


class LegalHoldCreate(StrictModel):
    external_matter_ref: str = Field(min_length=1, max_length=500)
    title: str = Field(min_length=3, max_length=240)
    reason: str = Field(min_length=10, max_length=5_000)
    scope_snapshot: dict[str, Any]
    evidence_object_refs: list[str] = Field(min_length=1, max_length=100)
    expires_at: datetime | None = None

    @field_validator("expires_at")
    @classmethod
    def aware_expiry(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("expires_at must be timezone-aware")
        return value


class LegalHoldRelease(StrictModel):
    reason: str = Field(min_length=10, max_length=5_000)
    evidence_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class LegalHoldRead(ORMModel):
    id: UUID
    external_matter_ref: str
    title: str
    reason: str
    scope_snapshot: dict[str, Any]
    scope_hash: str
    evidence_object_refs: list[str]
    state: str
    activated_by: UUID
    activated_at: datetime
    expires_at: datetime | None
    released_by: UUID | None
    released_at: datetime | None
    release_reason: str | None


class PrivacyRequestCreate(StrictModel):
    kind: PrivacyRequestKind
    subject_locator_ref: str = Field(min_length=10, max_length=1_000)
    data_classes: list[DataClass] = Field(min_length=1)
    requester_relationship: str = Field(min_length=2, max_length=40)
    requested_correction_ref: str | None = Field(default=None, max_length=1_000)

    @model_validator(mode="after")
    def correction_scope(self) -> "PrivacyRequestCreate":
        if self.kind == PrivacyRequestKind.CORRECT and self.requested_correction_ref is None:
            raise ValueError("requested_correction_ref is required for correction")
        if self.kind != PrivacyRequestKind.CORRECT and self.requested_correction_ref is not None:
            raise ValueError("requested_correction_ref is only allowed for correction")
        return self


class PrivacyScopeCreate(StrictModel):
    subject_locator_ref: str = Field(min_length=10, max_length=1_000)
    data_classes: list[DataClass] = Field(min_length=1)
    requester_relationship: str = Field(min_length=2, max_length=40)


class PrivacyVerificationSubmit(StrictModel):
    verification_token: SecretStr = Field(min_length=8, max_length=2_048)


class PrivacyRequestReject(StrictModel):
    rejection_code: str = Field(min_length=3, max_length=120)
    reason: str = Field(min_length=10, max_length=2_000)


class ReasonRequest(StrictModel):
    reason: str = Field(min_length=3, max_length=2_000)


class PrivacyRequestRead(ORMModel):
    id: UUID
    kind: str
    source: str
    data_classes: list[str]
    requester_relationship: str
    state: str
    due_at: datetime
    verified_at: datetime | None
    processing_started_at: datetime | None
    completed_at: datetime | None
    rejection_code: str | None
    failure_code: str | None
    created_at: datetime
    updated_at: datetime


class PrivacyActionRead(ORMModel):
    id: UUID
    request_id: UUID
    sequence: int
    kind: str
    data_classes: list[str]
    target_system: str
    state: str
    attempt_count: int
    affected_records: int | None
    backup_erasure_due_at: datetime | None
    last_error_code: str | None
    started_at: datetime | None
    completed_at: datetime | None


class PrivacyAccessEventRead(ORMModel):
    id: UUID
    actor_id: UUID
    action: str
    subject_type: str
    subject_id: str
    data_classes: list[str]
    purpose: str
    bulk: bool
    watermark_reference: str | None
    delivery_reference: str | None
    request_id: str | None
    ip_hash: str | None
    occurred_at: datetime


class PrivacyExportRead(ORMModel):
    id: UUID
    request_id: UUID
    content_hash: str
    size_bytes: int
    manifest: dict[str, Any]
    maximum_downloads: int
    expires_at: datetime
    created_at: datetime


class PrivacyDownloadGrantRead(StrictModel):
    url: str
    expires_at: datetime


class DeletionCertificateRead(ORMModel):
    id: UUID
    request_id: UUID
    completed_data_classes: list[str]
    held_data_classes: list[str]
    system_results: list[dict[str, Any]]
    manifest_hash: str
    backup_erasure_due_at: datetime | None
    certificate_code: str
    issued_at: datetime


class BackupErasureEvidenceCreate(StrictModel):
    evidence_object_ref: str = Field(min_length=1, max_length=1_000)
    evidence_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class BackupErasureEvidenceRead(ORMModel):
    id: UUID
    certificate_id: UUID
    provider_reference: str
    verifier: str
    submitted_evidence_hash: str
    verified_evidence_hash: str
    completed_at: datetime
    recorded_at: datetime


class PrivacyConsentCreate(StrictModel):
    subject_id: UUID
    purpose: ConsentPurpose
    decision: ConsentDecision
    policy_version: str = Field(min_length=1, max_length=80)
    policy_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    scope_snapshot: dict[str, Any]
    transfer_countries: list[str] = Field(default_factory=list)
    evidence_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    supersedes_id: UUID | None = None
    occurred_at: datetime

    @field_validator("occurred_at")
    @classmethod
    def aware_occurred_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")
        return value


class PrivacyConsentRead(ORMModel):
    id: UUID
    subject_id: UUID
    purpose: str
    decision: str
    policy_version: str
    scope_snapshot: dict[str, Any]
    transfer_countries: list[str]
    supersedes_id: UUID | None
    occurred_at: datetime


class SubprocessorVersionCreate(StrictModel):
    vendor_key: str = Field(min_length=1, max_length=120)
    vendor_name: str = Field(min_length=1, max_length=240)
    version: int = Field(gt=0)
    purposes: list[str] = Field(min_length=1)
    data_classes: list[DataClass] = Field(min_length=1)
    processing_countries: list[str] = Field(min_length=1)
    transfer_mechanism: str | None = Field(default=None, max_length=240)
    retention_summary: dict[str, Any]
    security_measures: list[str] = Field(min_length=1)
    contract_artifact_ref: str = Field(min_length=1, max_length=1_000)
    contract_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    notice_required: bool
    notice_at: datetime | None = None
    effective_at: datetime
    retired_at: datetime | None = None

    @model_validator(mode="after")
    def valid_transfer_and_window(self) -> "SubprocessorVersionCreate":
        if len(self.processing_countries) > 1 and not self.transfer_mechanism:
            raise ValueError("transfer_mechanism is required for cross-border processing")
        if self.notice_required and self.notice_at is None:
            raise ValueError("notice_at is required when notice_required is true")
        if self.effective_at.tzinfo is None:
            raise ValueError("effective_at must be timezone-aware")
        if self.retired_at is not None and self.retired_at <= self.effective_at:
            raise ValueError("retired_at must be after effective_at")
        return self


class SubprocessorVersionRead(ORMModel):
    id: UUID
    vendor_key: str
    vendor_name: str
    version: int
    purposes: list[str]
    data_classes: list[str]
    processing_countries: list[str]
    transfer_mechanism: str | None
    retention_summary: dict[str, Any]
    security_measures: list[str]
    contract_hash: str
    notice_required: bool
    notice_at: datetime | None
    effective_at: datetime
    retired_at: datetime | None


class CopyrightTarget(StrictModel):
    target_type: str = Field(min_length=1, max_length=80)
    target_id: str = Field(min_length=1, max_length=500)
    public_url: str | None = Field(default=None, max_length=2_048)


class CopyrightNoticeCreate(StrictModel):
    claimant_contact_ref: str = Field(min_length=10, max_length=1_000)
    work_description: str = Field(min_length=20, max_length=10_000)
    target_refs: list[CopyrightTarget] = Field(min_length=1, max_length=100)
    evidence_object_refs: list[str] = Field(min_length=1, max_length=100)
    sworn_statement: bool

    @field_validator("sworn_statement")
    @classmethod
    def sworn_required(cls, value: bool) -> bool:
        if not value:
            raise ValueError("sworn_statement must be true")
        return value


class CopyrightCounterNoticeCreate(StrictModel):
    respondent_contact_ref: str = Field(min_length=10, max_length=1_000)
    statement_object_ref: str = Field(min_length=1, max_length=1_000)
    statement_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    sworn_statement: bool

    @field_validator("sworn_statement")
    @classmethod
    def sworn_required(cls, value: bool) -> bool:
        if not value:
            raise ValueError("sworn_statement must be true")
        return value


class CopyrightDecision(StrictModel):
    action: str = Field(pattern=r"^(RESTORE|REMOVE|REJECT|CLOSE)$")
    reason: str = Field(min_length=10, max_length=5_000)
    evidence_object_ref: str = Field(min_length=1, max_length=1_000)
    evidence_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class CopyrightCaseRead(ORMModel):
    id: UUID
    work_description: str
    target_refs: list[dict[str, Any]]
    state: str
    response_due_at: datetime | None
    temporary_action: str | None
    policy_version: str | None
    counter_notice_received_at: datetime | None
    resolved_at: datetime | None
    failure_code: str | None
    created_at: datetime
    updated_at: datetime


class SecurityIncidentCreate(StrictModel):
    external_ref: str = Field(min_length=1, max_length=500)
    title: str = Field(min_length=3, max_length=240)
    incident_type: str = Field(min_length=2, max_length=120)
    severity: SecurityIncidentSeverity
    detected_at: datetime
    detection_source: str = Field(min_length=2, max_length=160)
    runbook_version: str = Field(min_length=1, max_length=80)
    impact_snapshot: dict[str, Any]
    affected_data_classes: list[DataClass]
    affected_subject_count: int | None = Field(default=None, ge=0)

    @field_validator("detected_at")
    @classmethod
    def aware_detected_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("detected_at must be timezone-aware")
        return value


class SecurityIncidentEventCreate(StrictModel):
    kind: SecurityIncidentEventKind
    state_after: SecurityIncidentState
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


class SecurityIncidentNotify(StrictModel):
    audience: NotificationAudience
    destination_ref: str = Field(min_length=10, max_length=1_000)
    template_version: str = Field(min_length=1, max_length=80)
    safe_payload: dict[str, Any]


class SecurityIncidentRead(ORMModel):
    id: UUID
    external_ref: str
    title: str
    incident_type: str
    severity: str
    state: str
    detected_at: datetime
    runbook_version: str
    incident_policy_version: str
    impact_snapshot: dict[str, Any]
    affected_data_classes: list[str]
    affected_subject_count: int | None
    containment_due_at: datetime
    notification_due_at: datetime | None
    contained_at: datetime | None
    resolved_at: datetime | None
    created_at: datetime
    updated_at: datetime


class SecurityIncidentEventRead(ORMModel):
    id: UUID
    incident_id: UUID
    sequence: int
    kind: str
    state_after: str
    safe_summary: str
    evidence_hash: str
    event_hash: str
    occurred_at: datetime


class BreachNotificationRead(ORMModel):
    id: UUID
    incident_id: UUID
    audience: str
    template_version: str
    provider_message_ref: str
    delivered_at: datetime


class ComplianceAssessmentCreate(StrictModel):
    kind: ComplianceAssessmentKind
    artifact_ref: str = Field(min_length=1, max_length=1_000)
    artifact_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    control_version: str = Field(min_length=1, max_length=80)


class ComplianceAssessmentRead(ORMModel):
    id: UUID
    kind: str
    artifact_hash: str
    control_version: str
    decision: str
    verifier: str
    findings: list[dict[str, Any]]
    evidence_hash: str
    verified_at: datetime
    expires_at: datetime | None
    created_at: datetime


class LegalHoldEventRead(ORMModel):
    id: UUID
    hold_id: UUID
    sequence: int
    kind: LegalHoldEventKind | str
    actor_id: UUID
    reason: str
    evidence_hash: str
    event_hash: str
    occurred_at: datetime


class CopyrightEventRead(ORMModel):
    id: UUID
    case_id: UUID
    sequence: int
    kind: CopyrightEventKind | str
    reason: str
    metadata_safe: dict[str, Any]
    evidence_hash: str
    event_hash: str
    occurred_at: datetime
