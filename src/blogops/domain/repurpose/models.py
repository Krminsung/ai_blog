"""Tenant-isolated repurposing jobs, immutable lineage, and approval artifacts."""

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
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from blogops.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from blogops.domain.jobs.state import JobState, StepState
from blogops.domain.repurpose.enums import ChannelTemplateStatus, DeliveryState


class ChannelTemplate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "repurpose_channel_templates"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="repurpose_template_workspace_id"),
        UniqueConstraint("workspace_id", "name", name="repurpose_template_name"),
        ForeignKeyConstraint(
            ["workspace_id", "current_version_id"],
            [
                "repurpose_channel_template_versions.workspace_id",
                "repurpose_channel_template_versions.id",
            ],
            name="fk_repurpose_template_current_version",
            ondelete="RESTRICT",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint("lock_version > 0", name="repurpose_template_lock_positive"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    channel: Mapped[str] = mapped_column(String(80), nullable=False)
    current_version_id: Mapped[UUID | None] = mapped_column(index=True)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class ChannelTemplateVersion(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "repurpose_channel_template_versions"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "id", name="repurpose_template_version_workspace_id"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "template_id"],
            ["repurpose_channel_templates.workspace_id", "repurpose_channel_templates.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id", "template_id", "version", name="repurpose_template_version_no"
        ),
        CheckConstraint("version > 0", name="version_positive"),
        Index(
            "ix_repurpose_template_version_status", "workspace_id", "template_id", "status"
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    template_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=ChannelTemplateStatus.DRAFT.value
    )
    prompt_blocks: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    output_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    platform_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    disclosure_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    safety_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    pii_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    approval_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    model_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RepurposeInputSnapshot(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "repurpose_input_snapshots"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="repurpose_snapshot_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "content_id"],
            ["contents.workspace_id", "contents.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "content_version_id"],
            ["content_versions.workspace_id", "content_versions.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "template_version_id"],
            [
                "repurpose_channel_template_versions.workspace_id",
                "repurpose_channel_template_versions.id",
            ],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id",
            "content_version_id",
            "template_version_id",
            "snapshot_hash",
            name="repurpose_snapshot_hash",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    content_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    content_version_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    template_version_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    source_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_title: Mapped[str] = mapped_column(String(500), nullable=False)
    source_document: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    source_plain_text: Mapped[str] = mapped_column(Text, nullable=False)
    template_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    platform_policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    disclosure_policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    safety_policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    pii_policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    approval_policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    claim_lineage_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    citation_lineage_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RepurposeSnapshotClaim(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "repurpose_snapshot_claims"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="repurpose_claim_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "snapshot_id"],
            ["repurpose_input_snapshots.workspace_id", "repurpose_input_snapshots.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "claim_id"],
            ["claims.workspace_id", "claims.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id", "snapshot_id", "claim_id", name="repurpose_snapshot_claim_key"
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    snapshot_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    claim_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    claim_key: Mapped[str] = mapped_column(String(160), nullable=False)
    claim_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    lineage_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class RepurposeSnapshotCitation(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "repurpose_snapshot_citations"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="repurpose_citation_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "snapshot_claim_id"],
            ["repurpose_snapshot_claims.workspace_id", "repurpose_snapshot_claims.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "citation_id"],
            ["citations.workspace_id", "citations.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id",
            "snapshot_claim_id",
            "citation_id",
            name="repurpose_snapshot_citation_key",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    snapshot_claim_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    citation_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    locator_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    citation_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class RepurposeJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "repurpose_jobs"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="repurpose_job_workspace_id"),
        UniqueConstraint(
            "workspace_id",
            "requested_by",
            "operation",
            "idempotency_key",
            name="repurpose_job_idempotency",
        ),
        CheckConstraint("attempt >= 0", name="repurpose_job_attempt_nonnegative"),
        CheckConstraint("item_count > 0", name="repurpose_job_items_positive"),
        CheckConstraint("variant_count > 0", name="repurpose_job_variants_positive"),
        CheckConstraint(
            "estimated_cost >= 0 AND reserved_cost >= 0 AND actual_cost >= 0",
            name="repurpose_job_cost_nonnegative",
        ),
        CheckConstraint(
            "actual_cost <= reserved_cost", name="repurpose_job_cost_within_reservation"
        ),
        Index("ix_repurpose_job_state", "workspace_id", "state", "created_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    requested_by: Mapped[UUID] = mapped_column(nullable=False)
    operation: Mapped[str] = mapped_column(String(24), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default=JobState.QUEUED.value
    )
    item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    variant_count: Mapped[int] = mapped_column(Integer, nullable=False)
    budget_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    estimated_cost: Mapped[Decimal] = mapped_column(Numeric(19, 4), nullable=False)
    reserved_cost: Mapped[Decimal] = mapped_column(Numeric(19, 4), nullable=False)
    actual_cost: Mapped[Decimal] = mapped_column(Numeric(19, 4), nullable=False, default=0)
    budget_reservation_ref: Mapped[str] = mapped_column(String(500), nullable=False)
    model_provider: Mapped[str] = mapped_column(String(120), nullable=False)
    model_name: Mapped[str] = mapped_column(String(160), nullable=False)
    model_version: Mapped[str] = mapped_column(String(120), nullable=False)
    model_config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(120))
    error_detail: Mapped[str | None] = mapped_column(Text)


class RepurposeJobItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "repurpose_job_items"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="repurpose_item_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "job_id"],
            ["repurpose_jobs.workspace_id", "repurpose_jobs.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "snapshot_id"],
            ["repurpose_input_snapshots.workspace_id", "repurpose_input_snapshots.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("workspace_id", "job_id", "position", name="repurpose_item_position"),
        CheckConstraint("position >= 0", name="repurpose_item_position_nonnegative"),
        CheckConstraint("variant_count > 0", name="repurpose_item_variants_positive"),
        Index("ix_repurpose_item_state", "workspace_id", "job_id", "state"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    job_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    snapshot_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    channel: Mapped[str] = mapped_column(String(80), nullable=False)
    variant_count: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default=StepState.PENDING.value
    )
    error_code: Mapped[str | None] = mapped_column(String(120))
    error_detail: Mapped[str | None] = mapped_column(Text)


class RepurposeVariant(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "repurpose_variants"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="repurpose_variant_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "job_item_id"],
            ["repurpose_job_items.workspace_id", "repurpose_job_items.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "snapshot_id"],
            ["repurpose_input_snapshots.workspace_id", "repurpose_input_snapshots.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id", "job_item_id", "variant_no", name="repurpose_variant_no"
        ),
        CheckConstraint("variant_no > 0", name="repurpose_variant_no_positive"),
        CheckConstraint("character_count >= 0", name="repurpose_variant_chars_nonnegative"),
        Index("ix_repurpose_variant_item", "workspace_id", "job_item_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    job_item_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    snapshot_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    variant_no: Mapped[int] = mapped_column(Integer, nullable=False)
    document: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    plain_text: Mapped[str] = mapped_column(Text, nullable=False)
    character_count: Mapped[int] = mapped_column(Integer, nullable=False)
    source_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    template_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    claim_lineage: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    citation_lineage: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    validation_result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    disclosure_result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    safety_result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    pii_result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    model_provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    raw_object_ref: Mapped[str | None] = mapped_column(String(1_000))
    raw_response_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RepurposeApproval(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "repurpose_approvals"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="repurpose_approval_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "variant_id"],
            ["repurpose_variants.workspace_id", "repurpose_variants.id"],
            ondelete="RESTRICT",
        ),
        Index("ix_repurpose_approval_variant", "workspace_id", "variant_id", "created_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    variant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    variant_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    decision: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    decided_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RepurposeExportArtifact(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "repurpose_export_artifacts"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="repurpose_export_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "variant_id"],
            ["repurpose_variants.workspace_id", "repurpose_variants.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "approval_id"],
            ["repurpose_approvals.workspace_id", "repurpose_approvals.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id", "variant_id", "variant_hash", "format", name="repurpose_export_key"
        ),
        CheckConstraint("size_bytes >= 0", name="repurpose_export_size_nonnegative"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    variant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    approval_id: Mapped[UUID | None] = mapped_column(index=True)
    variant_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    format: Mapped[str] = mapped_column(String(16), nullable=False)
    object_ref: Mapped[str] = mapped_column(String(1_000), nullable=False)
    object_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    media_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RepurposeDeliveryRequest(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "repurpose_delivery_requests"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="repurpose_delivery_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "variant_id"],
            ["repurpose_variants.workspace_id", "repurpose_variants.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "approval_id"],
            ["repurpose_approvals.workspace_id", "repurpose_approvals.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id",
            "requested_by",
            "idempotency_key",
            name="repurpose_delivery_idempotency",
        ),
        Index("ix_repurpose_delivery_state", "workspace_id", "state", "created_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    variant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    approval_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    variant_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    official_provider: Mapped[str] = mapped_column(String(120), nullable=False)
    connection_secret_ref: Mapped[str] = mapped_column(String(512), nullable=False)
    destination: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    requested_by: Mapped[UUID] = mapped_column(nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(
        String(24), nullable=False, default=DeliveryState.REQUESTED.value
    )
    external_post_id: Mapped[str | None] = mapped_column(String(500))
    response_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RepurposeDeliveryResult(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "repurpose_delivery_results"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "id", name="repurpose_delivery_result_workspace_id"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "delivery_request_id"],
            [
                "repurpose_delivery_requests.workspace_id",
                "repurpose_delivery_requests.id",
            ],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id",
            "delivery_request_id",
            "result_hash",
            name="repurpose_delivery_result_hash",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    delivery_request_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    succeeded: Mapped[bool] = mapped_column(Boolean, nullable=False)
    external_post_id: Mapped[str | None] = mapped_column(String(500))
    response_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(120))
    result_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RepurposeJobCommand(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "repurpose_job_commands"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="repurpose_command_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "job_id"],
            ["repurpose_jobs.workspace_id", "repurpose_jobs.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id",
            "job_id",
            "actor_id",
            "command",
            "idempotency_key",
            name="repurpose_command_idempotency",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    job_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    actor_id: Mapped[UUID] = mapped_column(nullable=False)
    command: Mapped[str] = mapped_column(String(24), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    from_state: Mapped[str] = mapped_column(String(32), nullable=False)
    to_state: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


def _reject_immutable_repurpose_row(
    _mapper: object, _connection: object, target: object
) -> None:
    raise ValueError(f"{type(target).__name__} rows are immutable")


for _immutable_model in (
    ChannelTemplateVersion,
    RepurposeInputSnapshot,
    RepurposeSnapshotClaim,
    RepurposeSnapshotCitation,
    RepurposeVariant,
    RepurposeApproval,
    RepurposeExportArtifact,
    RepurposeDeliveryResult,
    RepurposeJobCommand,
):
    event.listen(_immutable_model, "before_update", _reject_immutable_repurpose_row)
    event.listen(_immutable_model, "before_delete", _reject_immutable_repurpose_row)
