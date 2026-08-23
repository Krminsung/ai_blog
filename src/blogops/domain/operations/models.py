"""Platform-owned health, incident, backup, recovery, and GA evidence models."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
    inspect,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from blogops.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from blogops.domain.operations.enums import (
    BackupRunState,
    GAAssessmentState,
    HealthStatus,
    OperationalIncidentState,
    RecoveryExerciseState,
)


class ServiceComponent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "operations_service_components"
    __table_args__ = (
        UniqueConstraint("component_key", name="operations_component_key"),
        CheckConstraint("lock_version > 0", name="lock_positive"),
        Index("ix_operations_component_public", "public", "enabled"),
    )

    component_key: Mapped[str] = mapped_column(String(160), nullable=False)
    display_name: Mapped[str] = mapped_column(String(240), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    endpoint_ref: Mapped[str] = mapped_column(String(1_000), nullable=False)
    public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    owner_team: Mapped[str] = mapped_column(String(160), nullable=False)
    service_tier: Mapped[str] = mapped_column(String(40), nullable=False)
    safe_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class HealthObservation(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "operations_health_observations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["component_id"],
            ["operations_service_components.id"],
            ondelete="RESTRICT",
            name="fk_ops_health_component",
        ),
        UniqueConstraint("component_id", "checked_at", name="operations_health_check_once"),
        CheckConstraint(
            "latency_ms IS NULL OR latency_ms >= 0", name="latency_nonnegative"
        ),
        Index("ix_operations_health_component", "component_id", "checked_at"),
        Index("ix_operations_health_expiry", "valid_until"),
    )

    component_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=HealthStatus.UNKNOWN.value
    )
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    safe_metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class OperationalIncident(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "operations_incidents"
    __table_args__ = (
        UniqueConstraint("external_ref", name="operations_incident_external"),
        CheckConstraint("lock_version > 0", name="lock_positive"),
        Index("ix_operations_incident_state", "severity", "state", "started_at"),
    )

    external_ref: Mapped[str] = mapped_column(String(500), nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    safe_summary: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(
        String(24), nullable=False, default=OperationalIncidentState.INVESTIGATING.value
    )
    component_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    affected_workspace_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    identified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    runbook_version_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "operations_runbook_versions.id",
            ondelete="RESTRICT",
            name="fk_ops_incident_runbook",
        ),
        nullable=False,
        index=True,
    )
    opened_by: Mapped[UUID] = mapped_column(nullable=False)
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class OperationalIncidentEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "operations_incident_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["incident_id"],
            ["operations_incidents.id"],
            ondelete="RESTRICT",
            name="fk_ops_incident_event_incident",
        ),
        UniqueConstraint("incident_id", "sequence", name="operations_incident_event_sequence"),
        CheckConstraint("sequence > 0", name="sequence_positive"),
        Index("ix_operations_incident_event", "incident_id", "sequence"),
    )

    incident_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    state_after: Mapped[str] = mapped_column(String(24), nullable=False)
    actor_id: Mapped[UUID | None] = mapped_column()
    safe_summary: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_object_refs: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    previous_event_hash: Mapped[str | None] = mapped_column(String(64))
    event_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class StatusNotificationEvidence(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "operations_status_notification_evidence"
    __table_args__ = (
        ForeignKeyConstraint(
            ["incident_id"],
            ["operations_incidents.id"],
            ondelete="RESTRICT",
            name="fk_ops_status_notice_incident",
        ),
        UniqueConstraint(
            "incident_id",
            "audience",
            "template_version",
            "payload_hash",
            name="operations_status_notice_once",
        ),
        Index("ix_operations_status_notice", "incident_id", "delivered_at"),
    )

    incident_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    audience: Mapped[str] = mapped_column(String(80), nullable=False)
    template_version: Mapped[str] = mapped_column(String(80), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_message_ref: Mapped[str] = mapped_column(String(500), nullable=False)
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    delivered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RunbookVersion(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "operations_runbook_versions"
    __table_args__ = (
        UniqueConstraint("runbook_key", "version", name="operations_runbook_version"),
        CheckConstraint("version > 0", name="version_positive"),
        Index("ix_operations_runbook_effective", "runbook_key", "effective_at"),
    )

    runbook_key: Mapped[str] = mapped_column(String(160), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    artifact_ref: Mapped[str] = mapped_column(String(1_000), nullable=False)
    artifact_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    owner_team: Mapped[str] = mapped_column(String(160), nullable=False)
    escalation_policy_ref: Mapped[str] = mapped_column(String(1_000), nullable=False)
    exercise_interval_days: Mapped[int] = mapped_column(Integer, nullable=False)
    approved_by: Mapped[UUID] = mapped_column(nullable=False)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BackupPolicyVersion(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "operations_backup_policy_versions"
    __table_args__ = (
        UniqueConstraint("policy_key", "version", name="operations_backup_policy_version"),
        CheckConstraint("version > 0", name="version_positive"),
        CheckConstraint("rpo_minutes > 0", name="rpo_positive"),
        CheckConstraint("rto_minutes > 0", name="rto_positive"),
        CheckConstraint(
            "backup_interval_minutes > 0", name="interval_positive"
        ),
        Index("ix_operations_backup_policy_effective", "policy_key", "effective_at"),
    )

    policy_key: Mapped[str] = mapped_column(String(160), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    data_scope: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    rpo_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    rto_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    backup_interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    pitr_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    encrypted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    encryption_key_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    retention_cycles: Mapped[int] = mapped_column(Integer, nullable=False)
    quarterly_drill_required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    region_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BackupRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "operations_backup_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["policy_version_id"],
            ["operations_backup_policy_versions.id"],
            ondelete="RESTRICT",
            name="fk_ops_backup_run_policy",
        ),
        UniqueConstraint("idempotency_key", name="operations_backup_idempotency"),
        CheckConstraint("attempt_count >= 0", name="attempt_nonnegative"),
        CheckConstraint("lock_version > 0", name="lock_positive"),
        Index("ix_operations_backup_run_queue", "state", "requested_at"),
    )

    policy_version_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    policy_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    requested_by: Mapped[UUID] = mapped_column(nullable=False)
    state: Mapped[str] = mapped_column(
        String(24), nullable=False, default=BackupRunState.QUEUED.value
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    provider_run_ref: Mapped[str | None] = mapped_column(String(500))
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_code: Mapped[str | None] = mapped_column(String(120))
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class BackupEvidence(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "operations_backup_evidence"
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id"],
            ["operations_backup_runs.id"],
            ondelete="RESTRICT",
            name="fk_ops_backup_evidence_run",
        ),
        UniqueConstraint("run_id", name="operations_backup_evidence_run"),
        CheckConstraint("size_bytes >= 0", name="size_nonnegative"),
        Index("ix_operations_backup_restore_point", "restore_point_at"),
    )

    run_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    provider_run_ref: Mapped[str] = mapped_column(String(500), nullable=False)
    snapshot_ref: Mapped[str] = mapped_column(String(1_000), nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    encryption_key_version: Mapped[str] = mapped_column(String(80), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    restore_point_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False)
    evidence_object_ref: Mapped[str] = mapped_column(String(1_000), nullable=False)
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RecoveryExercise(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "operations_recovery_exercises"
    __table_args__ = (
        ForeignKeyConstraint(
            ["backup_evidence_id"],
            ["operations_backup_evidence.id"],
            ondelete="RESTRICT",
            name="fk_ops_recovery_backup_evidence",
        ),
        ForeignKeyConstraint(
            ["runbook_version_id"],
            ["operations_runbook_versions.id"],
            ondelete="RESTRICT",
            name="fk_ops_recovery_runbook",
        ),
        UniqueConstraint("idempotency_key", name="operations_recovery_idempotency"),
        CheckConstraint("attempt_count >= 0", name="attempt_nonnegative"),
        CheckConstraint("lock_version > 0", name="lock_positive"),
        Index("ix_operations_recovery_queue", "state", "requested_at"),
    )

    backup_evidence_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    runbook_version_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    rpo_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    rto_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    requested_by: Mapped[UUID] = mapped_column(nullable=False)
    state: Mapped[str] = mapped_column(
        String(24), nullable=False, default=RecoveryExerciseState.QUEUED.value
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    provider_run_ref: Mapped[str | None] = mapped_column(String(500))
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_code: Mapped[str | None] = mapped_column(String(120))
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class RecoveryEvidence(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "operations_recovery_evidence"
    __table_args__ = (
        ForeignKeyConstraint(
            ["exercise_id"],
            ["operations_recovery_exercises.id"],
            ondelete="RESTRICT",
            name="fk_ops_recovery_evidence_exercise",
        ),
        UniqueConstraint("exercise_id", name="operations_recovery_evidence_exercise"),
        CheckConstraint("data_loss_minutes >= 0", name="loss_nonnegative"),
        CheckConstraint("recovery_minutes >= 0", name="time_nonnegative"),
    )

    exercise_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    provider_run_ref: Mapped[str] = mapped_column(String(500), nullable=False)
    isolated_environment_ref: Mapped[str] = mapped_column(String(1_000), nullable=False)
    data_loss_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    recovery_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    objectives_met: Mapped[bool] = mapped_column(Boolean, nullable=False)
    integrity_checks: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evidence_object_ref: Mapped[str] = mapped_column(String(1_000), nullable=False)
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GAAssessment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "operations_ga_assessments"
    __table_args__ = (
        UniqueConstraint("release_ref", name="operations_ga_release_once"),
        UniqueConstraint("idempotency_key", name="operations_ga_idempotency"),
        CheckConstraint("attempt_count >= 0", name="attempt_nonnegative"),
        CheckConstraint("lock_version > 0", name="lock_positive"),
        Index("ix_operations_ga_state", "state", "requested_at"),
    )

    release_ref: Mapped[str] = mapped_column(String(500), nullable=False)
    artifact_refs: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    requested_by: Mapped[UUID] = mapped_column(nullable=False)
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, default=GAAssessmentState.QUEUED.value
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    decision_hash: Mapped[str | None] = mapped_column(String(64))
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_code: Mapped[str | None] = mapped_column(String(120))
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class GAGateEvidence(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "operations_ga_gate_evidence"
    __table_args__ = (
        ForeignKeyConstraint(
            ["assessment_id"],
            ["operations_ga_assessments.id"],
            ondelete="RESTRICT",
            name="fk_ops_ga_gate_assessment",
        ),
        UniqueConstraint("assessment_id", "gate", name="operations_ga_gate_once"),
        Index("ix_operations_ga_gate_assessment", "assessment_id", "gate"),
    )

    assessment_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    gate: Mapped[str] = mapped_column(String(64), nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verifier: Mapped[str] = mapped_column(String(160), nullable=False)
    source_artifact_ref: Mapped[str] = mapped_column(String(1_000), nullable=False)
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


def _reject_immutable_operations_row(
    _mapper: object, _connection: object, target: object
) -> None:
    raise ValueError(f"{type(target).__name__} rows are immutable")


for _immutable_model in (
    HealthObservation,
    OperationalIncidentEvent,
    StatusNotificationEvidence,
    RunbookVersion,
    BackupPolicyVersion,
    BackupEvidence,
    RecoveryEvidence,
    GAGateEvidence,
):
    event.listen(_immutable_model, "before_update", _reject_immutable_operations_row)
    event.listen(_immutable_model, "before_delete", _reject_immutable_operations_row)

for _history_root in (
    ServiceComponent,
    OperationalIncident,
    BackupRun,
    RecoveryExercise,
    GAAssessment,
):
    event.listen(_history_root, "before_delete", _reject_immutable_operations_row)


def _reject_frozen_operations_fields(target: object, names: tuple[str, ...]) -> None:
    state = inspect(target)
    changed = [name for name in names if state.attrs[name].history.has_changes()]
    if changed:
        raise ValueError(
            f"{type(target).__name__} immutable fields changed: {', '.join(changed)}"
        )


@event.listens_for(ServiceComponent, "before_update")
def _service_component_frozen(
    _mapper: object, _connection: object, target: ServiceComponent
) -> None:
    _reject_frozen_operations_fields(
        target,
        (
            "component_key",
            "kind",
            "created_by",
        ),
    )


@event.listens_for(OperationalIncident, "before_update")
def _operations_incident_frozen(
    _mapper: object, _connection: object, target: OperationalIncident
) -> None:
    _reject_frozen_operations_fields(
        target,
        (
            "external_ref",
            "title",
            "severity",
            "component_ids",
            "affected_workspace_ids",
            "started_at",
            "runbook_version_id",
            "opened_by",
        ),
    )


@event.listens_for(BackupRun, "before_update")
def _backup_run_frozen(_mapper: object, _connection: object, target: BackupRun) -> None:
    _reject_frozen_operations_fields(
        target,
        (
            "policy_version_id",
            "policy_snapshot",
            "policy_snapshot_hash",
            "idempotency_key",
            "requested_by",
            "requested_at",
        ),
    )


@event.listens_for(RecoveryExercise, "before_update")
def _recovery_exercise_frozen(
    _mapper: object, _connection: object, target: RecoveryExercise
) -> None:
    _reject_frozen_operations_fields(
        target,
        (
            "backup_evidence_id",
            "runbook_version_id",
            "rpo_minutes",
            "rto_minutes",
            "idempotency_key",
            "requested_by",
            "requested_at",
        ),
    )


@event.listens_for(GAAssessment, "before_update")
def _ga_assessment_frozen(
    _mapper: object, _connection: object, target: GAAssessment
) -> None:
    _reject_frozen_operations_fields(
        target,
        (
            "release_ref",
            "artifact_refs",
            "request_hash",
            "idempotency_key",
            "requested_by",
            "requested_at",
        ),
    )
