"""Tenant-isolated media, provenance, license and image-plan persistence."""

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
from blogops.domain.jobs.state import JobState
from blogops.domain.media.enums import (
    ImagePlanStatus,
    ImageSelectionState,
    InspectionStatus,
    LicenseState,
    MalwareScanStatus,
    MediaAssetState,
    MediaProviderState,
    MediaVersionKind,
)


class MediaProviderConnection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Provider configuration containing only an opaque secret reference."""

    __tablename__ = "media_provider_connections"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="media_provider_workspace_id"),
        UniqueConstraint("workspace_id", "provider", "name", name="media_provider_name"),
        CheckConstraint(
            "daily_quota IS NULL OR daily_quota > 0", name="media_provider_quota_positive"
        ),
        CheckConstraint(
            "quota_remaining IS NULL OR quota_remaining >= 0",
            name="media_provider_quota_remaining",
        ),
        CheckConstraint(
            "consecutive_failures >= 0",
            name="provider_failures_nonnegative",
        ),
        Index("ix_media_provider_state", "workspace_id", "state"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False, default="default")
    secret_ref: Mapped[str] = mapped_column(String(512), nullable=False)
    license_ref: Mapped[str | None] = mapped_column(String(512))
    capabilities: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    allowed_regions: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default=MediaProviderState.ACTIVE.value
    )
    daily_quota: Mapped[int | None] = mapped_column(Integer)
    quota_remaining: Mapped[int | None] = mapped_column(Integer)
    quota_reset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    circuit_open_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(120))
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by: Mapped[UUID] = mapped_column(nullable=False)


class MediaAsset(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Mutable library root; bytes live only in immutable version objects."""

    __tablename__ = "media_assets"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="media_asset_workspace_id"),
        Index("ix_media_asset_original_hash", "workspace_id", "original_content_hash"),
        ForeignKeyConstraint(
            ["workspace_id", "id", "original_version_id"],
            [
                "media_versions.workspace_id",
                "media_versions.asset_id",
                "media_versions.id",
            ],
            name="fk_media_asset_original_version",
            ondelete="RESTRICT",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "id", "current_version_id"],
            [
                "media_versions.workspace_id",
                "media_versions.asset_id",
                "media_versions.id",
            ],
            name="fk_media_asset_current_version",
            ondelete="RESTRICT",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint("declared_size_bytes > 0", name="media_asset_size_positive"),
        CheckConstraint("lock_version > 0", name="media_asset_lock_positive"),
        Index("ix_media_asset_state", "workspace_id", "state", "created_at"),
        Index("ix_media_asset_folder", "workspace_id", "folder_path"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    media_type: Mapped[str] = mapped_column(String(32), nullable=False, default="IMAGE")
    origin: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default=MediaAssetState.AWAITING_UPLOAD.value
    )
    declared_mime_type: Mapped[str] = mapped_column(String(120), nullable=False)
    declared_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    quarantine_object_ref: Mapped[str | None] = mapped_column(String(1_000))
    quarantine_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    original_content_hash: Mapped[str | None] = mapped_column(String(64))
    original_version_id: Mapped[UUID | None] = mapped_column(index=True)
    current_version_id: Mapped[UUID | None] = mapped_column(index=True)
    folder_path: Mapped[str | None] = mapped_column(String(1_000))
    tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    ai_generated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ai_disclosure_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    review_reason: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_by: Mapped[UUID] = mapped_column(nullable=False, index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class MediaVersion(UUIDPrimaryKeyMixin, Base):
    """Immutable original or non-destructive derived image version."""

    __tablename__ = "media_versions"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="media_version_workspace_id"),
        UniqueConstraint(
            "workspace_id", "asset_id", "id", name="media_version_asset_identity"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "asset_id"],
            ["media_assets.workspace_id", "media_assets.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "asset_id", "parent_version_id"],
            ["media_versions.workspace_id", "media_versions.asset_id", "media_versions.id"],
            name="fk_media_version_parent",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("workspace_id", "asset_id", "version_number", name="media_version_no"),
        CheckConstraint("version_number > 0", name="media_version_number_positive"),
        CheckConstraint("size_bytes > 0", name="media_version_size_positive"),
        CheckConstraint("width > 0", name="media_version_width_positive"),
        CheckConstraint("height > 0", name="media_version_height_positive"),
        CheckConstraint("actual_cost >= 0", name="media_version_cost_nonnegative"),
        Index("ix_media_version_asset", "workspace_id", "asset_id", "version_number"),
        Index("ix_media_version_hash", "workspace_id", "content_hash"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    asset_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    parent_version_id: Mapped[UUID | None] = mapped_column(index=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    version_kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default=MediaVersionKind.DERIVED.value
    )
    operation: Mapped[str] = mapped_column(String(48), nullable=False)
    object_ref: Mapped[str] = mapped_column(String(1_000), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    prompt_hash: Mapped[str | None] = mapped_column(String(64))
    model_run_id: Mapped[UUID | None] = mapped_column(index=True)
    provider: Mapped[str | None] = mapped_column(String(120))
    provider_version: Mapped[str | None] = mapped_column(String(80))
    model: Mapped[str | None] = mapped_column(String(160))
    model_version: Mapped[str | None] = mapped_column(String(120))
    edit_parameters: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    provenance_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    sanitized_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    removed_metadata_paths: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    pii_detected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    face_detected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    trademark_detected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    safety_labels: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    ai_generated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    disclosure_text: Mapped[str | None] = mapped_column(Text)
    actual_cost: Mapped[Decimal] = mapped_column(
        Numeric(19, 6), nullable=False, default=Decimal("0")
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="KRW")
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MediaLicense(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Mutable pointer to append-only license revisions."""

    __tablename__ = "media_licenses"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="media_license_workspace_id"),
        UniqueConstraint(
            "workspace_id", "id", "asset_id", name="media_license_asset_identity"
        ),
        UniqueConstraint("workspace_id", "asset_id", name="media_license_asset_once"),
        ForeignKeyConstraint(
            ["workspace_id", "asset_id"],
            ["media_assets.workspace_id", "media_assets.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "asset_id", "current_revision_id"],
            [
                "media_license_revisions.workspace_id",
                "media_license_revisions.asset_id",
                "media_license_revisions.id",
            ],
            name="fk_media_license_current_revision",
            ondelete="RESTRICT",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint("lock_version > 0", name="media_license_lock_positive"),
        Index("ix_media_license_expiry", "workspace_id", "state", "valid_until"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    asset_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    current_revision_id: Mapped[UUID | None] = mapped_column(index=True)
    state: Mapped[str] = mapped_column(
        String(24), nullable=False, default=LicenseState.NEEDS_REVIEW.value
    )
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class MediaLicenseRevision(UUIDPrimaryKeyMixin, Base):
    """Immutable rights evidence pinned by every content usage."""

    __tablename__ = "media_license_revisions"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="media_license_revision_workspace_id"),
        UniqueConstraint(
            "workspace_id", "asset_id", "id", name="media_license_revision_asset_id"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "license_id", "asset_id"],
            ["media_licenses.workspace_id", "media_licenses.id", "media_licenses.asset_id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id", "license_id", "revision", name="media_license_revision_no"
        ),
        UniqueConstraint(
            "workspace_id", "license_id", "snapshot_hash", name="media_license_revision_hash"
        ),
        CheckConstraint("revision > 0", name="media_license_revision_positive"),
        Index("ix_media_license_revision_asset", "workspace_id", "asset_id", "revision"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    license_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    asset_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    license_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    source_asset_ref: Mapped[str | None] = mapped_column(String(1_000))
    author: Mapped[str | None] = mapped_column(String(500))
    downloaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    commercial_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    editorial_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    allowed_channels: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    allowed_regions: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    derivative_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    attribution_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    attribution_text: Mapped[str | None] = mapped_column(Text)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    terms_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    evidence_object_ref: Mapped[str | None] = mapped_column(String(1_000))
    model_name: Mapped[str | None] = mapped_column(String(160))
    model_version: Mapped[str | None] = mapped_column(String(120))
    prompt_hash: Mapped[str | None] = mapped_column(String(64))
    confirmed_by: Mapped[UUID] = mapped_column(nullable=False)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MediaScanResult(UUIDPrimaryKeyMixin, Base):
    """Append-only malware result for one exact quarantined byte hash."""

    __tablename__ = "media_scan_results"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="media_scan_workspace_id"),
        UniqueConstraint(
            "workspace_id", "asset_id", "id", name="media_scan_asset_identity"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "asset_id"],
            ["media_assets.workspace_id", "media_assets.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id",
            "asset_id",
            "content_hash",
            "scanner",
            "scanner_version",
            name="media_scan_exact_file",
        ),
        CheckConstraint("size_bytes > 0", name="media_scan_size_positive"),
        Index("ix_media_scan_asset", "workspace_id", "asset_id", "scanned_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    asset_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    quarantine_object_ref: Mapped[str] = mapped_column(String(1_000), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    detected_mime_type: Mapped[str] = mapped_column(String(120), nullable=False)
    scanner: Mapped[str] = mapped_column(String(120), nullable=False)
    scanner_version: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=MalwareScanStatus.UNAVAILABLE.value
    )
    signature: Mapped[str | None] = mapped_column(String(500))
    details_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    scanned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MediaInspection(UUIDPrimaryKeyMixin, Base):
    """Immutable EXIF/PII/face/safety inspection for scanned bytes."""

    __tablename__ = "media_inspections"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="media_inspection_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "asset_id", "scan_result_id"],
            ["media_scan_results.workspace_id", "media_scan_results.asset_id", "media_scan_results.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id", "scan_result_id", "inspector", "inspector_version",
            name="media_inspection_scan_version",
        ),
        CheckConstraint("width > 0", name="media_inspection_width_positive"),
        CheckConstraint("height > 0", name="media_inspection_height_positive"),
        CheckConstraint("sanitized_size_bytes > 0", name="media_inspection_size_positive"),
        Index("ix_media_inspection_asset", "workspace_id", "asset_id", "inspected_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    asset_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    scan_result_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    inspector: Mapped[str] = mapped_column(String(120), nullable=False)
    inspector_version: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=InspectionStatus.UNAVAILABLE.value
    )
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    sanitized_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    removed_metadata_paths: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    pii_findings: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    face_findings: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    trademark_findings: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    safety_findings: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    transformation_log: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    sanitized_object_ref: Mapped[str | None] = mapped_column(String(1_000))
    sanitized_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    sanitized_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    inspected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MediaOperationJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Durable image job using the shared authoritative parent lifecycle."""

    __tablename__ = "media_operation_jobs"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="media_job_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "provider_connection_id"],
            ["media_provider_connections.workspace_id", "media_provider_connections.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "source_asset_id", "source_version_id"],
            ["media_versions.workspace_id", "media_versions.asset_id", "media_versions.id"],
            name="fk_media_job_source_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "result_asset_id", "result_version_id"],
            ["media_versions.workspace_id", "media_versions.asset_id", "media_versions.id"],
            name="fk_media_job_result_version",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id", "requested_by", "operation", "idempotency_key",
            name="media_job_idempotency",
        ),
        CheckConstraint("estimated_cost >= 0", name="media_job_estimate_nonnegative"),
        CheckConstraint(
            "(source_asset_id IS NULL) = (source_version_id IS NULL)",
            name="media_job_source_pair_complete",
        ),
        CheckConstraint(
            "(result_asset_id IS NULL) = (result_version_id IS NULL)",
            name="media_job_result_pair_complete",
        ),
        CheckConstraint(
            "actual_cost IS NULL OR actual_cost >= 0", name="media_job_actual_nonnegative"
        ),
        CheckConstraint("attempt >= 0", name="media_job_attempt_nonnegative"),
        CheckConstraint("max_attempts > 0", name="media_job_max_attempts_positive"),
        CheckConstraint(
            "NOT provider_quota_released OR provider_quota_reserved",
            name="quota_release_requires_reservation",
        ),
        Index("ix_media_job_state", "workspace_id", "state", "created_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    requested_by: Mapped[UUID] = mapped_column(nullable=False, index=True)
    operation: Mapped[str] = mapped_column(String(48), nullable=False)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default=JobState.CREATED.value
    )
    provider_connection_id: Mapped[UUID | None] = mapped_column(index=True)
    source_asset_id: Mapped[UUID | None] = mapped_column(index=True)
    source_version_id: Mapped[UUID | None] = mapped_column(index=True)
    result_asset_id: Mapped[UUID | None] = mapped_column(index=True)
    result_version_id: Mapped[UUID | None] = mapped_column(index=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    input_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    policy_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    prompt_hash: Mapped[str | None] = mapped_column(String(64))
    estimated_cost: Mapped[Decimal] = mapped_column(Numeric(19, 6), nullable=False)
    actual_cost: Mapped[Decimal | None] = mapped_column(Numeric(19, 6))
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    budget_reservation_ref: Mapped[str | None] = mapped_column(String(512))
    budget_limit: Mapped[Decimal | None] = mapped_column(Numeric(19, 6))
    budget_kill_switch_triggered: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    provider_quota_reserved: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    provider_quota_released: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(120))
    error_detail: Mapped[str | None] = mapped_column(Text)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class MediaJobCommand(UUIDPrimaryKeyMixin, Base):
    """Append-only idempotent control command for a durable media job."""

    __tablename__ = "media_job_commands"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="media_command_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "job_id"],
            ["media_operation_jobs.workspace_id", "media_operation_jobs.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id",
            "job_id",
            "actor_id",
            "command_kind",
            "idempotency_key",
            name="media_command_idempotency",
        ),
        Index("ix_media_command_job", "workspace_id", "job_id", "created_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    job_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    actor_id: Mapped[UUID] = mapped_column(nullable=False)
    command_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    from_state: Mapped[str] = mapped_column(String(32), nullable=False)
    to_state: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MediaPlanVersion(UUIDPrimaryKeyMixin, Base):
    """Immutable image plan pinned to an exact content version and hash."""

    __tablename__ = "media_plan_versions"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="media_plan_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "content_id"],
            ["contents.workspace_id", "contents.id"],
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
            name="fk_media_plan_exact_content_version",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id", "content_version_id", "plan_hash", name="media_plan_version_hash"
        ),
        CheckConstraint("recommended_count >= 0", name="media_plan_count_nonnegative"),
        Index("ix_media_plan_content", "workspace_id", "content_id", "created_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    content_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    content_version_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    channel: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=ImagePlanStatus.DRAFT.value
    )
    recommended_count: Mapped[int] = mapped_column(Integer, nullable=False)
    count_policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    brand_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    generation_policy_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False
    )
    prohibited_elements: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    plan_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MediaPlanItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "media_plan_items"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="media_plan_item_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "plan_id"],
            ["media_plan_versions.workspace_id", "media_plan_versions.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "selected_asset_id", "selected_version_id"],
            ["media_versions.workspace_id", "media_versions.asset_id", "media_versions.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("workspace_id", "plan_id", "sequence", name="media_plan_item_sequence"),
        CheckConstraint("sequence > 0", name="media_plan_item_sequence_positive"),
        CheckConstraint("lock_version > 0", name="media_plan_item_lock_positive"),
        Index("ix_media_plan_item_plan", "workspace_id", "plan_id", "sequence"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    plan_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    section_key: Mapped[str | None] = mapped_column(String(160))
    need_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    requires_real_photo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    generation_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    generation_prompt: Mapped[str | None] = mapped_column(Text)
    prohibited_elements: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    alt_text_plan: Mapped[str] = mapped_column(Text, nullable=False)
    caption_plan: Mapped[str | None] = mapped_column(Text)
    aspect_ratio: Mapped[str | None] = mapped_column(String(32))
    placement_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    candidate_asset_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    selection_state: Mapped[str] = mapped_column(
        String(24), nullable=False, default=ImageSelectionState.PROPOSED.value
    )
    selected_asset_id: Mapped[UUID | None] = mapped_column(index=True)
    selected_version_id: Mapped[UUID | None] = mapped_column(index=True)
    selected_by: Mapped[UUID | None]
    selected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duplicate_warning: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    performance_ref: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class MediaUsage(UUIDPrimaryKeyMixin, Base):
    """Append-only placement and exact license evidence used by publishing gates."""

    __tablename__ = "media_usages"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="media_usage_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "content_id"],
            ["contents.workspace_id", "contents.id"],
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
            name="fk_media_usage_exact_content_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "asset_id", "media_version_id"],
            ["media_versions.workspace_id", "media_versions.asset_id", "media_versions.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "asset_id", "license_revision_id"],
            [
                "media_license_revisions.workspace_id",
                "media_license_revisions.asset_id",
                "media_license_revisions.id",
            ],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id", "content_version_id", "placement_key", name="media_usage_placement"
        ),
        Index("ix_media_usage_asset", "workspace_id", "asset_id", "created_at"),
        Index("ix_media_usage_content", "workspace_id", "content_version_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    content_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    content_version_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    asset_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    media_version_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    license_revision_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    placement_key: Mapped[str] = mapped_column(String(160), nullable=False)
    channel: Mapped[str] = mapped_column(String(80), nullable=False)
    region: Mapped[str | None] = mapped_column(String(80))
    usage_mode: Mapped[str] = mapped_column(String(24), nullable=False)
    alt_text: Mapped[str] = mapped_column(Text, nullable=False)
    caption: Mapped[str | None] = mapped_column(Text)
    attribution_text: Mapped[str | None] = mapped_column(Text)
    rights_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    rights_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


def _reject_immutable_media_row(
    _mapper: object, _connection: object, target: object
) -> None:
    raise ValueError(f"{type(target).__name__} rows are immutable")


for _immutable_model in (
    MediaVersion,
    MediaLicenseRevision,
    MediaScanResult,
    MediaInspection,
    MediaJobCommand,
    MediaPlanVersion,
    MediaUsage,
):
    event.listen(_immutable_model, "before_update", _reject_immutable_media_row)
    event.listen(_immutable_model, "before_delete", _reject_immutable_media_row)


def _reject_changed_media_fields(
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


_MEDIA_JOB_FROZEN = frozenset(
    {
        "workspace_id",
        "requested_by",
        "operation",
        "provider_connection_id",
        "source_asset_id",
        "source_version_id",
        "idempotency_key",
        "request_hash",
        "input_snapshot",
        "input_snapshot_hash",
        "policy_snapshot",
        "policy_snapshot_hash",
        "prompt_snapshot",
        "prompt_hash",
        "estimated_cost",
        "currency",
        "budget_reservation_ref",
        "budget_limit",
        "provider_quota_reserved",
        "max_attempts",
    }
)
_MEDIA_PLAN_ITEM_FROZEN = frozenset(
    {
        "workspace_id",
        "plan_id",
        "sequence",
        "section_key",
        "need_kind",
        "reason",
        "requires_real_photo",
        "generation_allowed",
        "generation_prompt",
        "prohibited_elements",
        "alt_text_plan",
        "caption_plan",
        "aspect_ratio",
        "placement_json",
        "candidate_asset_ids",
        "duplicate_warning",
        "performance_ref",
    }
)


@event.listens_for(MediaOperationJob, "before_update")
def _guard_media_job_snapshot(
    mapper: object, connection: object, target: MediaOperationJob
) -> None:
    _reject_changed_media_fields(mapper, connection, target, _MEDIA_JOB_FROZEN)


@event.listens_for(MediaPlanItem, "before_update")
def _guard_media_plan_item(
    mapper: object, connection: object, target: MediaPlanItem
) -> None:
    _reject_changed_media_fields(mapper, connection, target, _MEDIA_PLAN_ITEM_FROZEN)
