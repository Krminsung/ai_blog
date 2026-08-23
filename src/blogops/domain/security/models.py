"""Tenant-isolated security, privacy, and copyright persistence."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
    inspect,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from blogops.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from blogops.domain.security.enums import (
    CopyrightCaseState,
    LegalHoldState,
    PrivacyActionState,
    PrivacyRequestState,
    RetentionSweepState,
    SecurityIncidentState,
)


class RetentionPolicyVersion(UUIDPrimaryKeyMixin, Base):
    """Immutable, server-validated retention and residency policy."""

    __tablename__ = "security_retention_policy_versions"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="security_retention_workspace_id"),
        UniqueConstraint("workspace_id", "version", name="security_retention_version"),
        CheckConstraint("version > 0", name="version_positive"),
        Index("ix_security_retention_effective", "workspace_id", "effective_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    rules: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    data_region: Mapped[str] = mapped_column(String(80), nullable=False)
    cross_border_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    backup_erasure_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    legal_basis_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class LegalHold(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "security_legal_holds"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="security_legal_hold_workspace_id"),
        UniqueConstraint(
            "workspace_id", "external_matter_ref", name="security_legal_hold_matter"
        ),
        CheckConstraint("lock_version > 0", name="lock_positive"),
        Index("ix_security_legal_hold_active", "workspace_id", "state", "expires_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    external_matter_ref: Mapped[str] = mapped_column(String(500), nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    scope_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    scope_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_object_refs: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, default=LegalHoldState.ACTIVE.value
    )
    activated_by: Mapped[UUID] = mapped_column(nullable=False)
    activated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    released_by: Mapped[UUID | None] = mapped_column()
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    release_reason: Mapped[str | None] = mapped_column(Text)
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class LegalHoldEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "security_legal_hold_events"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="security_legal_event_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "hold_id"],
            ["security_legal_holds.workspace_id", "security_legal_holds.id"],
            ondelete="RESTRICT",
            name="fk_sec_legal_event_hold",
        ),
        UniqueConstraint("workspace_id", "hold_id", "sequence", name="security_legal_sequence"),
        CheckConstraint("sequence > 0", name="sequence_positive"),
        Index("ix_security_legal_event_hold", "workspace_id", "hold_id", "sequence"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    hold_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[UUID] = mapped_column(nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    previous_event_hash: Mapped[str | None] = mapped_column(String(64))
    event_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RetentionSweep(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "security_retention_sweeps"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="security_retention_sweep_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "policy_version_id"],
            [
                "security_retention_policy_versions.workspace_id",
                "security_retention_policy_versions.id",
            ],
            ondelete="RESTRICT",
            name="fk_sec_ret_sweep_policy",
        ),
        UniqueConstraint(
            "workspace_id", "idempotency_key", name="security_retention_sweep_idempotency"
        ),
        CheckConstraint("lock_version > 0", name="lock_positive"),
        Index("ix_security_retention_sweep_queue", "workspace_id", "state", "created_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    policy_version_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    policy_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    legal_hold_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    legal_hold_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    requested_by: Mapped[UUID] = mapped_column(nullable=False)
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, default=RetentionSweepState.QUEUED.value
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_code: Mapped[str | None] = mapped_column(String(120))
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class RetentionDispositionEvidence(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "security_retention_disposition_evidence"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="security_retention_result_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "sweep_id"],
            ["security_retention_sweeps.workspace_id", "security_retention_sweeps.id"],
            ondelete="RESTRICT",
            name="fk_sec_ret_evidence_sweep",
        ),
        UniqueConstraint(
            "workspace_id", "sweep_id", "data_class", "target_system",
            name="security_retention_result_target",
        ),
        CheckConstraint("affected_records >= 0", name="affected_nonnegative"),
        CheckConstraint("held_records >= 0", name="held_nonnegative"),
        Index("ix_security_retention_result_sweep", "workspace_id", "sweep_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    sweep_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    data_class: Mapped[str] = mapped_column(String(40), nullable=False)
    target_system: Mapped[str] = mapped_column(String(120), nullable=False)
    cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    disposition: Mapped[str] = mapped_column(String(24), nullable=False)
    affected_records: Mapped[int] = mapped_column(Integer, nullable=False)
    held_records: Mapped[int] = mapped_column(Integer, nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    evidence_object_ref: Mapped[str] = mapped_column(String(1_000), nullable=False)
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PrivacyRequest(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "security_privacy_requests"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="security_privacy_request_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "retention_policy_version_id"],
            [
                "security_retention_policy_versions.workspace_id",
                "security_retention_policy_versions.id",
            ],
            ondelete="RESTRICT",
            name="fk_sec_priv_request_policy",
        ),
        UniqueConstraint(
            "workspace_id",
            "requested_by",
            "kind",
            "idempotency_key",
            name="security_privacy_request_idempotency",
        ),
        UniqueConstraint(
            "workspace_id",
            "source",
            "external_request_ref",
            name="security_privacy_request_external",
        ),
        CheckConstraint("lock_version > 0", name="lock_positive"),
        Index("ix_security_privacy_request_sla", "workspace_id", "state", "due_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    requested_by: Mapped[UUID] = mapped_column(nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="USER")
    external_request_ref: Mapped[str | None] = mapped_column(String(500))
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_locator_ref: Mapped[str] = mapped_column(String(1_000), nullable=False)
    subject_locator_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    data_classes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    requested_correction_ref: Mapped[str | None] = mapped_column(String(1_000))
    requester_relationship: Mapped[str] = mapped_column(String(40), nullable=False)
    retention_policy_version_id: Mapped[UUID] = mapped_column(nullable=False)
    retention_policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default=PrivacyRequestState.IDENTITY_PENDING.value
    )
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_code: Mapped[str | None] = mapped_column(String(120))
    failure_code: Mapped[str | None] = mapped_column(String(120))
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class ProviderDeletionEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "security_provider_deletion_events"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="security_provider_delete_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "privacy_request_id"],
            ["security_privacy_requests.workspace_id", "security_privacy_requests.id"],
            ondelete="RESTRICT",
            name="fk_sec_provider_delete_request",
        ),
        UniqueConstraint("provider", "provider_event_id", name="security_provider_delete_once"),
        Index("ix_security_provider_delete_workspace", "workspace_id", "occurred_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    privacy_request_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    provider_event_id: Mapped[str] = mapped_column(String(500), nullable=False)
    raw_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    signature_key_version: Mapped[str] = mapped_column(String(80), nullable=False)
    subject_locator_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    data_classes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    assurance_level: Mapped[str] = mapped_column(String(80), nullable=False)
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PrivacyVerificationEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "security_privacy_verification_events"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="security_privacy_verify_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "request_id"],
            ["security_privacy_requests.workspace_id", "security_privacy_requests.id"],
            ondelete="RESTRICT",
            name="fk_sec_priv_verify_request",
        ),
        UniqueConstraint(
            "workspace_id", "provider_reference", name="security_privacy_verify_provider"
        ),
        Index("ix_security_privacy_verify_request", "workspace_id", "request_id", "verified_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    request_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    provider_reference: Mapped[str] = mapped_column(String(500), nullable=False)
    assurance_level: Mapped[str] = mapped_column(String(80), nullable=False)
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    failure_code: Mapped[str | None] = mapped_column(String(120))
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PrivacyAction(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "security_privacy_actions"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="security_privacy_action_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "request_id"],
            ["security_privacy_requests.workspace_id", "security_privacy_requests.id"],
            ondelete="RESTRICT",
            name="fk_sec_priv_action_request",
        ),
        UniqueConstraint(
            "workspace_id", "request_id", "sequence", name="security_privacy_action_sequence"
        ),
        CheckConstraint("sequence > 0", name="sequence_positive"),
        CheckConstraint("attempt_count >= 0", name="attempt_nonnegative"),
        CheckConstraint("lock_version > 0", name="lock_positive"),
        Index("ix_security_privacy_action_queue", "workspace_id", "state", "sequence"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    request_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    data_classes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    target_system: Mapped[str] = mapped_column(String(120), nullable=False)
    target_locator_ref: Mapped[str] = mapped_column(String(1_000), nullable=False)
    plan_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    plan_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default=PrivacyActionState.PENDING.value
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    provider_operation_ref: Mapped[str | None] = mapped_column(String(500))
    affected_records: Mapped[int | None] = mapped_column(Integer)
    result_manifest_hash: Mapped[str | None] = mapped_column(String(64))
    evidence_object_ref: Mapped[str | None] = mapped_column(String(1_000))
    backup_erasure_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(120))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class PrivacyActionAttempt(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "security_privacy_action_attempts"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="security_privacy_attempt_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "action_id"],
            ["security_privacy_actions.workspace_id", "security_privacy_actions.id"],
            ondelete="RESTRICT",
            name="fk_sec_priv_attempt_action",
        ),
        UniqueConstraint(
            "workspace_id", "action_id", "attempt_no", name="security_privacy_action_attempt"
        ),
        CheckConstraint("attempt_no > 0", name="attempt_positive"),
        Index("ix_security_privacy_attempt_action", "workspace_id", "action_id", "attempt_no"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    action_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    provider_operation_ref: Mapped[str | None] = mapped_column(String(500))
    result_manifest_hash: Mapped[str | None] = mapped_column(String(64))
    evidence_object_ref: Mapped[str | None] = mapped_column(String(1_000))
    error_code: Mapped[str | None] = mapped_column(String(120))
    error_class: Mapped[str | None] = mapped_column(String(120))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PrivacyExportArtifact(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "security_privacy_export_artifacts"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="security_privacy_export_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "request_id"],
            ["security_privacy_requests.workspace_id", "security_privacy_requests.id"],
            ondelete="RESTRICT",
            name="fk_sec_priv_export_request",
        ),
        UniqueConstraint("workspace_id", "request_id", name="security_privacy_export_request"),
        CheckConstraint("size_bytes >= 0", name="size_nonnegative"),
        CheckConstraint("maximum_downloads > 0", name="downloads_positive"),
        Index("ix_security_privacy_export_expiry", "workspace_id", "expires_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    request_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    object_ref: Mapped[str] = mapped_column(String(1_000), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    watermark_policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    maximum_downloads: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DeletionCertificate(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "security_deletion_certificates"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="security_deletion_cert_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "request_id"],
            ["security_privacy_requests.workspace_id", "security_privacy_requests.id"],
            ondelete="RESTRICT",
            name="fk_sec_delete_cert_request",
        ),
        UniqueConstraint("workspace_id", "request_id", name="security_deletion_cert_request"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    request_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    completed_data_classes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    held_data_classes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    system_results: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    backup_erasure_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    certificate_code: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BackupErasureEvidence(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "security_backup_erasure_evidence"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="security_backup_erasure_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "certificate_id"],
            ["security_deletion_certificates.workspace_id", "security_deletion_certificates.id"],
            ondelete="RESTRICT",
            name="fk_sec_backup_erase_cert",
        ),
        UniqueConstraint(
            "workspace_id", "certificate_id", name="security_backup_erasure_certificate"
        ),
        UniqueConstraint(
            "workspace_id", "provider_reference", name="security_backup_erasure_provider"
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    certificate_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    provider_reference: Mapped[str] = mapped_column(String(500), nullable=False)
    verifier: Mapped[str] = mapped_column(String(160), nullable=False)
    evidence_object_ref: Mapped[str] = mapped_column(String(1_000), nullable=False)
    submitted_evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    verified_evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PrivacyAccessEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "security_privacy_access_events"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="security_privacy_access_workspace_id"),
        Index("ix_security_privacy_access_subject", "workspace_id", "subject_type", "subject_id"),
        Index("ix_security_privacy_access_time", "workspace_id", "occurred_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    actor_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(80), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(500), nullable=False)
    data_classes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    purpose: Mapped[str] = mapped_column(String(240), nullable=False)
    bulk: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    watermark_reference: Mapped[str | None] = mapped_column(String(500))
    delivery_reference: Mapped[str | None] = mapped_column(String(500))
    request_id: Mapped[str | None] = mapped_column(String(120))
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PrivacyConsentEvidence(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "security_privacy_consent_evidence"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="security_consent_workspace_id"),
        UniqueConstraint(
            "workspace_id", "subject_id", "purpose", "policy_version", "idempotency_key",
            name="security_consent_idempotency",
        ),
        UniqueConstraint(
            "workspace_id", "supersedes_id", name="security_consent_single_successor"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "supersedes_id"],
            [
                "security_privacy_consent_evidence.workspace_id",
                "security_privacy_consent_evidence.id",
            ],
            ondelete="RESTRICT",
            use_alter=True,
            name="fk_sec_consent_supersedes",
            deferrable=True,
            initially="DEFERRED",
        ),
        Index(
            "ix_security_consent_subject",
            "workspace_id",
            "subject_id",
            "purpose",
            "occurred_at",
        ),
        Index(
            "uq_security_consent_root",
            "workspace_id",
            "subject_id",
            "purpose",
            unique=True,
            postgresql_where=text("supersedes_id IS NULL"),
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    subject_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    purpose: Mapped[str] = mapped_column(String(40), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(80), nullable=False)
    policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    transfer_countries: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    supersedes_id: Mapped[UUID | None] = mapped_column(index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SubprocessorVersion(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "security_subprocessor_versions"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="security_subprocessor_workspace_id"),
        UniqueConstraint(
            "workspace_id", "vendor_key", "version", name="security_subprocessor_version"
        ),
        CheckConstraint("version > 0", name="version_positive"),
        Index("ix_security_subprocessor_effective", "workspace_id", "effective_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    vendor_key: Mapped[str] = mapped_column(String(120), nullable=False)
    vendor_name: Mapped[str] = mapped_column(String(240), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    purposes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    data_classes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    processing_countries: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    transfer_mechanism: Mapped[str | None] = mapped_column(String(240))
    retention_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    security_measures: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    contract_artifact_ref: Mapped[str] = mapped_column(String(1_000), nullable=False)
    contract_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    notice_required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    notice_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CopyrightCase(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "security_copyright_cases"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="security_copyright_case_workspace_id"),
        UniqueConstraint(
            "workspace_id", "reported_by", "idempotency_key", name="security_copyright_idempotency"
        ),
        CheckConstraint("lock_version > 0", name="lock_positive"),
        Index("ix_security_copyright_sla", "workspace_id", "state", "response_due_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    reported_by: Mapped[UUID] = mapped_column(nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    claimant_contact_ref: Mapped[str] = mapped_column(String(1_000), nullable=False)
    claimant_contact_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    work_description: Mapped[str] = mapped_column(Text, nullable=False)
    target_refs: Mapped[list[dict[str, str]]] = mapped_column(JSONB, nullable=False)
    evidence_object_refs: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    sworn_statement: Mapped[bool] = mapped_column(Boolean, nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default=CopyrightCaseState.RECEIVED.value
    )
    response_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    temporary_action: Mapped[str | None] = mapped_column(String(80))
    policy_version: Mapped[str | None] = mapped_column(String(80))
    counter_notice_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_code: Mapped[str | None] = mapped_column(String(120))
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class CopyrightCounterNotice(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "security_copyright_counter_notices"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="security_counter_notice_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "case_id"],
            ["security_copyright_cases.workspace_id", "security_copyright_cases.id"],
            ondelete="RESTRICT",
            name="fk_sec_counter_notice_case",
        ),
        UniqueConstraint("workspace_id", "case_id", name="security_counter_notice_case"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    case_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    submitted_by: Mapped[UUID] = mapped_column(nullable=False)
    respondent_contact_ref: Mapped[str] = mapped_column(String(1_000), nullable=False)
    respondent_contact_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    statement_object_ref: Mapped[str] = mapped_column(String(1_000), nullable=False)
    statement_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    sworn_statement: Mapped[bool] = mapped_column(Boolean, nullable=False)
    verification_reference: Mapped[str] = mapped_column(String(500), nullable=False)
    verification_assurance: Mapped[str] = mapped_column(String(80), nullable=False)
    verification_evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CopyrightCaseEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "security_copyright_case_events"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="security_copyright_event_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "case_id"],
            ["security_copyright_cases.workspace_id", "security_copyright_cases.id"],
            ondelete="RESTRICT",
            name="fk_sec_copyright_event_case",
        ),
        UniqueConstraint(
            "workspace_id", "case_id", "sequence", name="security_copyright_event_sequence"
        ),
        CheckConstraint("sequence > 0", name="sequence_positive"),
        Index("ix_security_copyright_event_case", "workspace_id", "case_id", "sequence"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    case_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    actor_id: Mapped[UUID | None] = mapped_column()
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_safe: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    evidence_object_ref: Mapped[str | None] = mapped_column(String(1_000))
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    previous_event_hash: Mapped[str | None] = mapped_column(String(64))
    event_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SecurityIncident(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "security_incidents"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="security_incident_workspace_id"),
        UniqueConstraint("workspace_id", "external_ref", name="security_incident_external"),
        CheckConstraint("lock_version > 0", name="lock_positive"),
        Index("ix_security_incident_state", "workspace_id", "severity", "state"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    external_ref: Mapped[str] = mapped_column(String(500), nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    incident_type: Mapped[str] = mapped_column(String(120), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(
        String(24), nullable=False, default=SecurityIncidentState.DETECTED.value
    )
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    detection_source: Mapped[str] = mapped_column(String(160), nullable=False)
    runbook_version: Mapped[str] = mapped_column(String(80), nullable=False)
    incident_policy_version: Mapped[str] = mapped_column(String(80), nullable=False)
    impact_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    affected_data_classes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    affected_subject_count: Mapped[int | None] = mapped_column(Integer)
    containment_due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    notification_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    contained_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    opened_by: Mapped[UUID] = mapped_column(nullable=False)
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class SecurityIncidentEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "security_incident_events"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="security_incident_event_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "incident_id"],
            ["security_incidents.workspace_id", "security_incidents.id"],
            ondelete="RESTRICT",
            name="fk_sec_incident_event_incident",
        ),
        UniqueConstraint(
            "workspace_id", "incident_id", "sequence", name="security_incident_event_sequence"
        ),
        CheckConstraint("sequence > 0", name="sequence_positive"),
        Index("ix_security_incident_event", "workspace_id", "incident_id", "sequence"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    incident_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[UUID | None] = mapped_column()
    state_after: Mapped[str] = mapped_column(String(24), nullable=False)
    safe_summary: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_object_refs: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    previous_event_hash: Mapped[str | None] = mapped_column(String(64))
    event_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BreachNotification(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "security_breach_notifications"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="security_breach_notice_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "incident_id"],
            ["security_incidents.workspace_id", "security_incidents.id"],
            ondelete="RESTRICT",
            name="fk_sec_breach_notice_incident",
        ),
        UniqueConstraint(
            "workspace_id",
            "incident_id",
            "audience",
            "destination_hash",
            "template_version",
            "payload_hash",
            name="security_breach_notice_destination",
        ),
        Index("ix_security_breach_notice_incident", "workspace_id", "incident_id", "delivered_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    incident_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    audience: Mapped[str] = mapped_column(String(32), nullable=False)
    destination_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    template_version: Mapped[str] = mapped_column(String(80), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_message_ref: Mapped[str] = mapped_column(String(500), nullable=False)
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    delivered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ComplianceAssessment(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "security_compliance_assessments"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="security_assessment_workspace_id"),
        UniqueConstraint(
            "workspace_id", "kind", "artifact_hash", "control_version",
            name="security_assessment_evidence",
        ),
        Index("ix_security_assessment_expiry", "workspace_id", "kind", "expires_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    artifact_ref: Mapped[str] = mapped_column(String(1_000), nullable=False)
    artifact_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    control_version: Mapped[str] = mapped_column(String(80), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    verifier: Mapped[str] = mapped_column(String(160), nullable=False)
    findings: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    requested_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


def _reject_immutable_security_row(
    _mapper: object, _connection: object, target: object
) -> None:
    raise ValueError(f"{type(target).__name__} rows are immutable")


for _immutable_model in (
    RetentionPolicyVersion,
    LegalHoldEvent,
    RetentionDispositionEvidence,
    ProviderDeletionEvent,
    PrivacyVerificationEvent,
    PrivacyActionAttempt,
    PrivacyExportArtifact,
    DeletionCertificate,
    BackupErasureEvidence,
    PrivacyAccessEvent,
    PrivacyConsentEvidence,
    SubprocessorVersion,
    CopyrightCounterNotice,
    CopyrightCaseEvent,
    SecurityIncidentEvent,
    BreachNotification,
    ComplianceAssessment,
):
    event.listen(_immutable_model, "before_update", _reject_immutable_security_row)
    event.listen(_immutable_model, "before_delete", _reject_immutable_security_row)

for _history_root in (
    LegalHold,
    PrivacyRequest,
    PrivacyAction,
    RetentionSweep,
    CopyrightCase,
    SecurityIncident,
):
    event.listen(_history_root, "before_delete", _reject_immutable_security_row)


def _reject_frozen_security_fields(target: object, names: tuple[str, ...]) -> None:
    state = inspect(target)
    changed = [name for name in names if state.attrs[name].history.has_changes()]
    if changed:
        raise ValueError(
            f"{type(target).__name__} immutable fields changed: {', '.join(changed)}"
        )


@event.listens_for(LegalHold, "before_update")
def _legal_hold_frozen(_mapper: object, _connection: object, target: LegalHold) -> None:
    _reject_frozen_security_fields(
        target,
        (
            "workspace_id",
            "external_matter_ref",
            "title",
            "reason",
            "scope_snapshot",
            "scope_hash",
            "evidence_object_refs",
            "activated_by",
            "activated_at",
            "expires_at",
        ),
    )


@event.listens_for(PrivacyRequest, "before_update")
def _privacy_request_frozen(
    _mapper: object, _connection: object, target: PrivacyRequest
) -> None:
    _reject_frozen_security_fields(
        target,
        (
            "workspace_id",
            "requested_by",
            "kind",
            "source",
            "external_request_ref",
            "idempotency_key",
            "request_hash",
            "subject_locator_ref",
            "subject_locator_hash",
            "data_classes",
            "requested_correction_ref",
            "requester_relationship",
            "retention_policy_version_id",
            "retention_policy_snapshot",
            "due_at",
        ),
    )


@event.listens_for(PrivacyAction, "before_update")
def _privacy_action_frozen(
    _mapper: object, _connection: object, target: PrivacyAction
) -> None:
    _reject_frozen_security_fields(
        target,
        (
            "workspace_id",
            "request_id",
            "sequence",
            "kind",
            "data_classes",
            "target_system",
            "target_locator_ref",
            "plan_metadata",
            "plan_hash",
            "idempotency_key",
        ),
    )


@event.listens_for(RetentionSweep, "before_update")
def _retention_sweep_frozen(
    _mapper: object, _connection: object, target: RetentionSweep
) -> None:
    _reject_frozen_security_fields(
        target,
        (
            "workspace_id",
            "policy_version_id",
            "policy_snapshot",
            "policy_snapshot_hash",
            "legal_hold_snapshot",
            "legal_hold_snapshot_hash",
            "idempotency_key",
            "requested_by",
        ),
    )


@event.listens_for(CopyrightCase, "before_update")
def _copyright_case_frozen(
    _mapper: object, _connection: object, target: CopyrightCase
) -> None:
    _reject_frozen_security_fields(
        target,
        (
            "workspace_id",
            "reported_by",
            "idempotency_key",
            "claimant_contact_ref",
            "claimant_contact_hash",
            "work_description",
            "target_refs",
            "evidence_object_refs",
            "sworn_statement",
            "request_hash",
        ),
    )


@event.listens_for(SecurityIncident, "before_update")
def _security_incident_frozen(
    _mapper: object, _connection: object, target: SecurityIncident
) -> None:
    _reject_frozen_security_fields(
        target,
        (
            "workspace_id",
            "external_ref",
            "title",
            "incident_type",
            "severity",
            "detected_at",
            "detection_source",
            "runbook_version",
            "incident_policy_version",
            "impact_snapshot",
            "affected_data_classes",
            "containment_due_at",
            "notification_due_at",
            "opened_by",
        ),
    )
