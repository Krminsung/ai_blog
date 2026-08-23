"""Tenant-safe bulk input snapshots, jobs, rows and append-only attempts."""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
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
from blogops.domain.bulk.enums import (
    BulkPriority,
    BulkRowState,
    BulkScheduleState,
)
from blogops.domain.jobs.state import JobState


class BulkInputFile(UUIDPrimaryKeyMixin, Base):
    """Immutable, private object snapshot of CSV/XLSX/Sheet/API/feed input."""

    __tablename__ = "bulk_input_files"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="bulk_input_workspace_id"),
        UniqueConstraint("workspace_id", "content_hash", name="bulk_input_content_hash"),
        CheckConstraint("size_bytes >= 0", name="bulk_input_size_nonnegative"),
        CheckConstraint("row_count > 0", name="bulk_input_rows_positive"),
        CheckConstraint("header_row > 0", name="bulk_input_header_positive"),
        CheckConstraint(
            "malware_scan_status IN ('CLEAN', 'NOT_REQUIRED')",
            name="bulk_input_scan_status_valid",
        ),
        Index("ix_bulk_input_workspace_time", "workspace_id", "created_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    input_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    object_ref: Mapped[str] = mapped_column(String(1_000), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    encoding: Mapped[str | None] = mapped_column(String(40))
    delimiter: Mapped[str | None] = mapped_column(String(8))
    sheet_name: Mapped[str | None] = mapped_column(String(240))
    sheet_range: Mapped[str | None] = mapped_column(String(240))
    header_row: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    headers: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    source_locator: Mapped[str | None] = mapped_column(Text)
    source_locator_hash: Mapped[str | None] = mapped_column(String(64))
    source_connection_ref: Mapped[str | None] = mapped_column(String(512))
    source_secret_ref: Mapped[str | None] = mapped_column(String(512))
    malware_scan_status: Mapped[str] = mapped_column(String(24), nullable=False)
    malware_scanner: Mapped[str | None] = mapped_column(String(120))
    malware_scanner_version: Mapped[str | None] = mapped_column(String(80))
    malware_scan_result_hash: Mapped[str | None] = mapped_column(String(64))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    uploaded_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BulkMapping(UUIDPrimaryKeyMixin, Base):
    """Immutable column-to-template-variable contract."""

    __tablename__ = "bulk_mappings"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="bulk_mapping_workspace_id"),
        UniqueConstraint("workspace_id", "mapping_hash", name="bulk_mapping_hash"),
        Index("ix_bulk_mapping_workspace_name", "workspace_id", "name"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    input_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    column_mapping: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False)
    variable_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    required_variables: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    normalization_rules: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    duplicate_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    mapping_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BulkJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Mutable parent lifecycle with immutable execution snapshots."""

    __tablename__ = "bulk_jobs"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="bulk_job_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "campaign_id"],
            ["campaigns.workspace_id", "campaigns.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "input_file_id"],
            ["bulk_input_files.workspace_id", "bulk_input_files.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "mapping_id"],
            ["bulk_mappings.workspace_id", "bulk_mappings.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "template_version_id"],
            ["template_versions.workspace_id", "template_versions.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id", "requested_by", "operation", "idempotency_key",
            name="bulk_job_idempotency",
        ),
        CheckConstraint("total_rows > 0", name="bulk_job_total_positive"),
        CheckConstraint("processed_rows >= 0", name="bulk_job_processed_nonnegative"),
        CheckConstraint("succeeded_rows >= 0", name="bulk_job_succeeded_nonnegative"),
        CheckConstraint("review_rows >= 0", name="bulk_job_review_nonnegative"),
        CheckConstraint("failed_rows >= 0", name="bulk_job_failed_nonnegative"),
        CheckConstraint("cancelled_rows >= 0", name="bulk_job_cancelled_nonnegative"),
        CheckConstraint("estimated_cost >= 0", name="bulk_job_estimate_nonnegative"),
        CheckConstraint("maximum_cost > 0", name="bulk_job_max_cost_positive"),
        CheckConstraint("authorized_cost >= 0", name="bulk_job_authorized_nonnegative"),
        CheckConstraint("actual_cost >= 0", name="bulk_job_actual_nonnegative"),
        CheckConstraint("held_cost >= 0", name="bulk_job_held_nonnegative"),
        CheckConstraint("max_row_attempts > 0", name="bulk_job_attempts_positive"),
        CheckConstraint("concurrency_limit > 0", name="bulk_job_concurrency_positive"),
        CheckConstraint("daily_throughput_limit > 0", name="bulk_job_daily_positive"),
        CheckConstraint("lock_version > 0", name="bulk_job_lock_positive"),
        Index("ix_bulk_job_state", "workspace_id", "state", "created_at"),
        Index("ix_bulk_job_campaign", "workspace_id", "campaign_id", "created_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    campaign_id: Mapped[UUID | None] = mapped_column(index=True)
    input_file_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    mapping_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    template_version_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    requested_by: Mapped[UUID] = mapped_column(nullable=False, index=True)
    operation: Mapped[str] = mapped_column(String(40), nullable=False, default="GENERATE_CONTENT")
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default=JobState.CREATED.value
    )
    priority: Mapped[str] = mapped_column(
        String(24), nullable=False, default=BulkPriority.NORMAL.value
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    mapping_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    mapping_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    template_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    template_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    brand_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    brand_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    model_policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    model_policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    quality_policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    quality_policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    approval_policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    approval_policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    publishing_policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    publishing_policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    retry_policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    concurrency_policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    callback_endpoint_ref: Mapped[str | None] = mapped_column(String(512))
    callback_secret_ref: Mapped[str | None] = mapped_column(String(512))
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sample_size: Mapped[int | None] = mapped_column(Integer)
    total_rows: Mapped[int] = mapped_column(Integer, nullable=False)
    processed_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    succeeded_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    review_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cancelled_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress_percent: Mapped[Decimal] = mapped_column(
        Numeric(6, 3), nullable=False, default=Decimal("0")
    )
    estimated_cost: Mapped[Decimal] = mapped_column(Numeric(19, 6), nullable=False)
    maximum_cost: Mapped[Decimal] = mapped_column(Numeric(19, 6), nullable=False)
    authorized_cost: Mapped[Decimal] = mapped_column(Numeric(19, 6), nullable=False)
    actual_cost: Mapped[Decimal] = mapped_column(
        Numeric(19, 6), nullable=False, default=Decimal("0")
    )
    held_cost: Mapped[Decimal] = mapped_column(
        Numeric(19, 6), nullable=False, default=Decimal("0")
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    budget_reservation_ref: Mapped[str | None] = mapped_column(String(512))
    budget_kill_switch_triggered: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    pause_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    pause_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paused_by: Mapped[UUID | None]
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_by: Mapped[UUID | None]
    max_row_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    concurrency_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    daily_throughput_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(120))
    error_detail: Mapped[str | None] = mapped_column(Text)
    result_manifest_ref: Mapped[str | None] = mapped_column(String(1_000))
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class BulkRow(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "bulk_rows"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="bulk_row_workspace_id"),
        UniqueConstraint("workspace_id", "job_id", "id", name="bulk_row_job_identity"),
        ForeignKeyConstraint(
            ["workspace_id", "job_id"],
            ["bulk_jobs.workspace_id", "bulk_jobs.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "job_id", "duplicate_of_row_id"],
            ["bulk_rows.workspace_id", "bulk_rows.job_id", "bulk_rows.id"],
            name="fk_bulk_row_duplicate",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "generation_job_id"],
            ["generation_jobs.workspace_id", "generation_jobs.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "content_id", "content_version_id", "content_hash"],
            [
                "content_versions.workspace_id",
                "content_versions.content_id",
                "content_versions.id",
                "content_versions.content_hash",
            ],
            name="fk_bulk_row_exact_content_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "workspace_id",
                "quality_assessment_id",
                "content_id",
                "content_version_id",
                "content_hash",
            ],
            [
                "quality_assessments.workspace_id",
                "quality_assessments.id",
                "quality_assessments.content_id",
                "quality_assessments.content_version_id",
                "quality_assessments.content_hash",
            ],
            name="fk_bulk_row_exact_quality_assessment",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "workspace_id",
                "approval_request_id",
                "content_id",
                "content_version_id",
                "content_hash",
            ],
            [
                "content_approval_requests.workspace_id",
                "content_approval_requests.id",
                "content_approval_requests.content_id",
                "content_approval_requests.content_version_id",
                "content_approval_requests.content_hash",
            ],
            name="fk_bulk_row_exact_approval_request",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("workspace_id", "job_id", "row_no", name="bulk_row_number"),
        UniqueConstraint(
            "workspace_id", "job_id", "row_idempotency_key", name="bulk_row_idempotency"
        ),
        CheckConstraint("row_no > 0", name="bulk_row_number_positive"),
        CheckConstraint("attempt >= 0", name="bulk_row_attempt_nonnegative"),
        CheckConstraint("estimated_cost >= 0", name="bulk_row_estimate_nonnegative"),
        CheckConstraint("actual_cost >= 0", name="bulk_row_actual_nonnegative"),
        CheckConstraint(
            "(content_id IS NULL) = (content_version_id IS NULL) AND "
            "(content_version_id IS NULL) = (content_hash IS NULL)",
            name="bulk_row_content_identity_complete",
        ),
        CheckConstraint(
            "quality_assessment_id IS NULL OR content_version_id IS NOT NULL",
            name="bulk_row_quality_content_required",
        ),
        CheckConstraint(
            "approval_request_id IS NULL OR content_version_id IS NOT NULL",
            name="bulk_row_approval_content_required",
        ),
        CheckConstraint(
            "semantic_duplicate_score IS NULL OR "
            "(semantic_duplicate_score >= 0 AND semantic_duplicate_score <= 1)",
            name="bulk_row_duplicate_score_range",
        ),
        CheckConstraint(
            "value_score IS NULL OR (value_score >= 0 AND value_score <= 100)",
            name="bulk_row_value_score_range",
        ),
        CheckConstraint("lock_version > 0", name="bulk_row_lock_positive"),
        Index("ix_bulk_row_state", "workspace_id", "job_id", "state", "row_no"),
        Index("ix_bulk_row_content", "workspace_id", "content_id"),
        Index("ix_bulk_row_input_hash", "workspace_id", "job_id", "input_hash"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    job_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    row_no: Mapped[int] = mapped_column(Integer, nullable=False)
    row_idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default=BulkRowState.PENDING.value
    )
    validation_errors: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    duplicate_of_row_id: Mapped[UUID | None] = mapped_column(index=True)
    duplicate_action: Mapped[str | None] = mapped_column(String(24))
    semantic_duplicate_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 5))
    keyword_cluster_ref: Mapped[str | None] = mapped_column(String(512))
    existing_content_action: Mapped[str | None] = mapped_column(String(24))
    existing_content_refs: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    generation_job_id: Mapped[UUID | None] = mapped_column(index=True)
    content_id: Mapped[UUID | None] = mapped_column(index=True)
    content_version_id: Mapped[UUID | None] = mapped_column(index=True)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    quality_assessment_id: Mapped[UUID | None] = mapped_column(index=True)
    quality_passed: Mapped[bool | None] = mapped_column(Boolean)
    approval_request_id: Mapped[UUID | None] = mapped_column(index=True)
    approved_content_hash: Mapped[str | None] = mapped_column(String(64))
    hard_blocked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    risk_findings: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    spam_similarity_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 5))
    value_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    estimated_cost: Mapped[Decimal] = mapped_column(Numeric(19, 6), nullable=False)
    actual_cost: Mapped[Decimal] = mapped_column(
        Numeric(19, 6), nullable=False, default=Decimal("0")
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error_code: Mapped[str | None] = mapped_column(String(120))
    last_error_detail: Mapped[str | None] = mapped_column(Text)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    callback_delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[UUID | None]
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class BulkRowAttempt(UUIDPrimaryKeyMixin, Base):
    """Append-only result for one completed row attempt."""

    __tablename__ = "bulk_row_attempts"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="bulk_attempt_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "job_id", "row_id"],
            ["bulk_rows.workspace_id", "bulk_rows.job_id", "bulk_rows.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "generation_job_id"],
            ["generation_jobs.workspace_id", "generation_jobs.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "content_id", "content_version_id", "content_hash"],
            [
                "content_versions.workspace_id",
                "content_versions.content_id",
                "content_versions.id",
                "content_versions.content_hash",
            ],
            name="fk_bulk_attempt_exact_content_version",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id", "row_id", "attempt_number", name="bulk_attempt_number"
        ),
        CheckConstraint("attempt_number > 0", name="bulk_attempt_number_positive"),
        CheckConstraint("actual_cost >= 0", name="bulk_attempt_cost_nonnegative"),
        CheckConstraint(
            "(content_id IS NULL) = (content_version_id IS NULL) AND "
            "(content_version_id IS NULL) = (content_hash IS NULL)",
            name="bulk_attempt_content_identity_complete",
        ),
        Index("ix_bulk_attempt_row", "workspace_id", "row_id", "attempt_number"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    job_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    row_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    generation_job_id: Mapped[UUID | None] = mapped_column(index=True)
    content_id: Mapped[UUID | None] = mapped_column(index=True)
    content_version_id: Mapped[UUID | None] = mapped_column(index=True)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    quality_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    approval_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    actual_cost: Mapped[Decimal] = mapped_column(Numeric(19, 6), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(120))
    error_detail: Mapped[str | None] = mapped_column(Text)
    retryable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BulkJobCommand(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "bulk_job_commands"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="bulk_command_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "job_id"],
            ["bulk_jobs.workspace_id", "bulk_jobs.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id", "job_id", "actor_id", "kind", "idempotency_key",
            name="bulk_command_idempotency",
        ),
        Index("ix_bulk_command_job", "workspace_id", "job_id", "created_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    job_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    actor_id: Mapped[UUID] = mapped_column(nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    details_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BulkExportArtifact(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "bulk_export_artifacts"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="bulk_export_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "job_id"],
            ["bulk_jobs.workspace_id", "bulk_jobs.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id", "job_id", "export_kind", "manifest_hash",
            name="bulk_export_manifest",
        ),
        Index("ix_bulk_export_job", "workspace_id", "job_id", "created_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    job_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    export_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    object_ref: Mapped[str] = mapped_column(String(1_000), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BulkSchedule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Optional recurring snapshot trigger; workers process only changed input hashes."""

    __tablename__ = "bulk_schedules"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="bulk_schedule_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "input_file_id"],
            ["bulk_input_files.workspace_id", "bulk_input_files.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "mapping_id"],
            ["bulk_mappings.workspace_id", "bulk_mappings.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "template_version_id"],
            ["template_versions.workspace_id", "template_versions.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("lock_version > 0", name="bulk_schedule_lock_positive"),
        Index("ix_bulk_schedule_due", "workspace_id", "state", "next_run_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    input_file_id: Mapped[UUID] = mapped_column(nullable=False)
    mapping_id: Mapped[UUID] = mapped_column(nullable=False)
    template_version_id: Mapped[UUID] = mapped_column(nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    schedule_expression: Mapped[str] = mapped_column(String(240), nullable=False)
    state: Mapped[str] = mapped_column(
        String(24), nullable=False, default=BulkScheduleState.ACTIVE.value
    )
    last_input_hash: Mapped[str | None] = mapped_column(String(64))
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    config_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class BulkCallbackDelivery(UUIDPrimaryKeyMixin, Base):
    """Append-only signed row-result callback attempt for replay-safe delivery."""

    __tablename__ = "bulk_callback_deliveries"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="bulk_callback_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "job_id", "row_id"],
            ["bulk_rows.workspace_id", "bulk_rows.job_id", "bulk_rows.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id", "event_id", "attempt", name="bulk_callback_event_attempt"
        ),
        CheckConstraint("attempt > 0", name="bulk_callback_attempt_positive"),
        Index("ix_bulk_callback_state", "workspace_id", "state", "next_attempt_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    job_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    row_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    event_id: Mapped[UUID] = mapped_column(nullable=False)
    endpoint_ref: Mapped[str] = mapped_column(String(512), nullable=False)
    secret_ref: Mapped[str] = mapped_column(String(512), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    signature_version: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    response_status: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(120))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


def _reject_immutable_bulk_row(
    _mapper: object, _connection: object, target: object
) -> None:
    raise ValueError(f"{type(target).__name__} rows are immutable")


for _immutable_model in (
    BulkInputFile,
    BulkMapping,
    BulkRowAttempt,
    BulkJobCommand,
    BulkExportArtifact,
    BulkCallbackDelivery,
):
    event.listen(_immutable_model, "before_update", _reject_immutable_bulk_row)
    event.listen(_immutable_model, "before_delete", _reject_immutable_bulk_row)


def _reject_changed_bulk_fields(
    _mapper: object,
    _connection: object,
    target: object,
    frozen_fields: frozenset[str],
) -> None:
    state = inspect(target)
    changed = sorted(
        field for field in frozen_fields if state.attrs[field].history.has_changes()
    )
    if changed:
        raise ValueError(
            f"{type(target).__name__} immutable fields changed: {', '.join(changed)}"
        )


_BULK_JOB_FROZEN = frozenset(
    {
        "workspace_id",
        "campaign_id",
        "input_file_id",
        "mapping_id",
        "template_version_id",
        "requested_by",
        "operation",
        "priority",
        "idempotency_key",
        "request_hash",
        "input_snapshot_hash",
        "mapping_snapshot",
        "mapping_snapshot_hash",
        "template_snapshot",
        "template_snapshot_hash",
        "brand_snapshot",
        "brand_snapshot_hash",
        "model_policy_snapshot",
        "model_policy_hash",
        "quality_policy_snapshot",
        "quality_policy_hash",
        "approval_policy_snapshot",
        "approval_policy_hash",
        "publishing_policy_snapshot",
        "publishing_policy_hash",
        "retry_policy_snapshot",
        "concurrency_policy_snapshot",
        "callback_endpoint_ref",
        "callback_secret_ref",
        "dry_run",
        "sample_size",
        "total_rows",
        "estimated_cost",
        "maximum_cost",
        "authorized_cost",
        "currency",
        "budget_reservation_ref",
        "max_row_attempts",
        "concurrency_limit",
        "daily_throughput_limit",
    }
)
_BULK_ROW_FROZEN = frozenset(
    {
        "workspace_id",
        "job_id",
        "row_no",
        "row_idempotency_key",
        "input_hash",
        "input_json",
        "estimated_cost",
    }
)


@event.listens_for(BulkJob, "before_update")
def _guard_bulk_job_snapshot(
    mapper: object, connection: object, target: BulkJob
) -> None:
    _reject_changed_bulk_fields(mapper, connection, target, _BULK_JOB_FROZEN)


@event.listens_for(BulkRow, "before_update")
def _guard_bulk_row_input(
    mapper: object, connection: object, target: BulkRow
) -> None:
    _reject_changed_bulk_fields(mapper, connection, target, _BULK_ROW_FROZEN)
