"""Tenant-isolated persistence for content generation, provenance and editing.

Mutable roots point at immutable versions. Every cross-tenant-capable reference includes
``workspace_id`` so an accidental UUID from another tenant cannot satisfy a foreign key.
"""

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
from blogops.domain.generation.enums import (
    ContentChangeKind,
    GenerationQuality,
    ModelCatalogStatus,
    TemplateScope,
    VersionStatus,
)
from blogops.domain.jobs.state import JobState, StepState


class ContentItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "contents"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="content_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "brief_id"],
            ["content_briefs.workspace_id", "content_briefs.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "brand_id"],
            ["brands.workspace_id", "brands.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "current_version_id"],
            ["content_versions.workspace_id", "content_versions.id"],
            name="fk_content_current_version",
            ondelete="RESTRICT",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint("lock_version > 0", name="content_lock_positive"),
        Index("ix_contents_workspace_state", "workspace_id", "state", "updated_at"),
        Index("ix_contents_workspace_type", "workspace_id", "content_type"),
        Index("ix_contents_workspace_author", "workspace_id", "created_by"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    brief_id: Mapped[UUID | None] = mapped_column(index=True)
    brand_id: Mapped[UUID | None] = mapped_column(index=True)
    content_type: Mapped[str] = mapped_column(String(64), nullable=False)
    channel: Mapped[str] = mapped_column(String(80), nullable=False)
    language: Mapped[str] = mapped_column(String(16), nullable=False, default="ko")
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default=JobState.CREATED.value
    )
    current_version_id: Mapped[UUID | None] = mapped_column(index=True)
    folder_path: Mapped[str | None] = mapped_column(String(1_000))
    tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    retention_hold: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[UUID] = mapped_column(nullable=False, index=True)
    updated_by: Mapped[UUID] = mapped_column(nullable=False)
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class PromptDefinition(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "prompts"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="prompt_workspace_id"),
        UniqueConstraint("workspace_id", "key", name="prompt_workspace_key"),
        ForeignKeyConstraint(
            ["workspace_id", "current_version_id"],
            ["prompt_versions.workspace_id", "prompt_versions.id"],
            name="fk_prompt_current_version",
            ondelete="RESTRICT",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(160), nullable=False)
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    current_version_id: Mapped[UUID | None] = mapped_column(index=True)
    created_by: Mapped[UUID] = mapped_column(nullable=False)


class PromptVersion(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "prompt_versions"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="prompt_version_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "prompt_id"],
            ["prompts.workspace_id", "prompts.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("workspace_id", "prompt_id", "version", name="prompt_version_no"),
        CheckConstraint("version > 0", name="prompt_version_positive"),
        Index("ix_prompt_versions_status", "workspace_id", "status"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    prompt_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=VersionStatus.DRAFT.value
    )
    system_blocks: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    task_blocks: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    output_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    safety_policy_version: Mapped[str] = mapped_column(String(80), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ContentTemplate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "templates"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="template_workspace_id"),
        UniqueConstraint("workspace_id", "scope", "owner_id", "name", name="template_name"),
        ForeignKeyConstraint(
            ["workspace_id", "current_version_id"],
            ["template_versions.workspace_id", "template_versions.id"],
            name="fk_template_current_version",
            ondelete="RESTRICT",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        Index("ix_templates_workspace_type", "workspace_id", "content_type"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    scope: Mapped[str] = mapped_column(
        String(24), nullable=False, default=TemplateScope.WORKSPACE.value
    )
    owner_id: Mapped[UUID | None] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(String(64), nullable=False)
    industry: Mapped[str | None] = mapped_column(String(120))
    current_version_id: Mapped[UUID | None] = mapped_column(index=True)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[UUID] = mapped_column(nullable=False)


class TemplateVersion(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "template_versions"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="template_version_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "template_id"],
            ["templates.workspace_id", "templates.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "prompt_version_id"],
            ["prompt_versions.workspace_id", "prompt_versions.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("workspace_id", "template_id", "version", name="template_version_no"),
        CheckConstraint("version > 0", name="template_version_positive"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    template_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    prompt_version_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=VersionStatus.DRAFT.value
    )
    input_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    prompt_blocks: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    structure_blocks: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    quality_rules: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    channel_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ModelCatalogEntry(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "model_catalog"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="model_catalog_workspace_id"),
        UniqueConstraint(
            "workspace_id",
            "provider",
            "model",
            "model_version",
            "region",
            name="model_catalog_identity",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "replacement_entry_id"],
            ["model_catalog.workspace_id", "model_catalog.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "context_limit IS NULL OR context_limit > 0", name="model_context_limit_positive"
        ),
        Index("ix_model_catalog_status", "workspace_id", "status"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(120), nullable=False)
    model: Mapped[str] = mapped_column(String(160), nullable=False)
    model_version: Mapped[str] = mapped_column(String(120), nullable=False)
    region: Mapped[str] = mapped_column(String(80), nullable=False)
    quality_grades: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    capabilities: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    context_limit: Mapped[int | None] = mapped_column(Integer)
    parameter_policy: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    data_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=ModelCatalogStatus.ACTIVE.value
    )
    retirement_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replacement_entry_id: Mapped[UUID | None] = mapped_column(index=True)
    customer_managed_key: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    secret_ref: Mapped[str | None] = mapped_column(String(512))
    configured_by: Mapped[UUID] = mapped_column(nullable=False)


class ModelPricingVersion(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "model_pricing_versions"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="model_pricing_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "model_entry_id"],
            ["model_catalog.workspace_id", "model_catalog.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id", "model_entry_id", "effective_at", name="model_pricing_effective"
        ),
        CheckConstraint("char_length(currency) = 3", name="model_pricing_currency_length"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    model_entry_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    rates: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GenerationInputSnapshot(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "generation_input_snapshots"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="generation_snapshot_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "brief_id"],
            ["content_briefs.workspace_id", "content_briefs.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "brief_version_id"],
            ["content_brief_versions.workspace_id", "content_brief_versions.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "template_version_id"],
            ["template_versions.workspace_id", "template_versions.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "prompt_version_id"],
            ["prompt_versions.workspace_id", "prompt_versions.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "model_entry_id"],
            ["model_catalog.workspace_id", "model_catalog.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "pricing_version_id"],
            ["model_pricing_versions.workspace_id", "model_pricing_versions.id"],
            ondelete="RESTRICT",
        ),
        Index("ix_generation_snapshot_brief", "workspace_id", "brief_version_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    brief_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    brief_version_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    template_version_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    prompt_version_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    model_entry_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    pricing_version_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    content_type: Mapped[str] = mapped_column(String(64), nullable=False)
    brief_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    brand_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    product_snapshots: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    persona_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    source_version_snapshots: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    keyword_metric_snapshots: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    template_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    prompt_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    model_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    pricing_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    type_input_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    variables_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    generation_policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    generation_policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    approval_policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    approval_policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    safety_policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    safety_policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    contract_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    request_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GenerationSnapshotSource(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "generation_snapshot_sources"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="generation_source_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "snapshot_id"],
            ["generation_input_snapshots.workspace_id", "generation_input_snapshots.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "source_id"],
            ["knowledge_sources.workspace_id", "knowledge_sources.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "source_version_id"],
            ["source_versions.workspace_id", "source_versions.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id", "snapshot_id", "source_version_id", name="generation_source_once"
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    snapshot_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    source_id: Mapped[UUID] = mapped_column(nullable=False)
    source_version_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GenerationSnapshotKeywordMetric(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "generation_snapshot_keyword_metrics"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="generation_metric_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "snapshot_id"],
            ["generation_input_snapshots.workspace_id", "generation_input_snapshots.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "keyword_id"],
            ["keywords.workspace_id", "keywords.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "metric_snapshot_id"],
            ["keyword_metrics.workspace_id", "keyword_metrics.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id", "snapshot_id", "metric_snapshot_id", name="generation_metric_once"
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    snapshot_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    keyword_id: Mapped[UUID] = mapped_column(nullable=False)
    metric_snapshot_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GenerationJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "generation_jobs"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="generation_job_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "content_id"],
            ["contents.workspace_id", "contents.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "input_snapshot_id"],
            ["generation_input_snapshots.workspace_id", "generation_input_snapshots.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id",
            "requested_by",
            "operation",
            "idempotency_key",
            name="generation_job_idempotency",
        ),
        CheckConstraint("attempt >= 0", name="generation_job_attempt_nonnegative"),
        CheckConstraint(
            "max_attempts IS NULL OR max_attempts > 0", name="generation_job_max_attempts_positive"
        ),
        CheckConstraint("estimated_cost >= 0", name="generation_estimated_cost_nonnegative"),
        CheckConstraint(
            "actual_cost IS NULL OR actual_cost >= 0", name="generation_actual_cost_nonnegative"
        ),
        Index("ix_generation_jobs_state", "workspace_id", "state", "created_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    requested_by: Mapped[UUID] = mapped_column(nullable=False, index=True)
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    content_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    input_snapshot_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    quality: Mapped[str] = mapped_column(
        String(24), nullable=False, default=GenerationQuality.BALANCED.value
    )
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default=JobState.CREATED.value
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    budget_reservation_ref: Mapped[str] = mapped_column(String(512), nullable=False)
    entitlement_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    budget_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    estimated_cost: Mapped[Decimal] = mapped_column(Numeric(19, 6), nullable=False)
    actual_cost: Mapped[Decimal | None] = mapped_column(Numeric(19, 6))
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    estimate_breakdown: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int | None] = mapped_column(Integer)
    cancel_requested_by: Mapped[UUID | None]
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(120))
    error_detail: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class GenerationJobStep(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "generation_job_steps"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="generation_step_workspace_id"),
        UniqueConstraint(
            "workspace_id", "job_id", "id", name="generation_step_job_identity"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "job_id"],
            ["generation_jobs.workspace_id", "generation_jobs.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "partial_content_version_id"],
            ["content_versions.workspace_id", "content_versions.id"],
            name="fk_generation_step_partial_version",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        UniqueConstraint(
            "workspace_id",
            "job_id",
            "step_key",
            "input_snapshot_hash",
            name="generation_step_idempotency",
        ),
        CheckConstraint("ordinal >= 0", name="generation_step_ordinal_nonnegative"),
        CheckConstraint("attempt >= 0", name="generation_step_attempt_nonnegative"),
        Index("ix_generation_steps_state", "workspace_id", "job_id", "state", "ordinal"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    job_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    step_key: Mapped[str] = mapped_column(String(240), nullable=False)
    step_kind: Mapped[str] = mapped_column(String(48), nullable=False)
    section_key: Mapped[str | None] = mapped_column(String(160))
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default=StepState.PENDING.value
    )
    input_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int | None] = mapped_column(Integer)
    partial_content_version_id: Mapped[UUID | None] = mapped_column(index=True)
    output_ref: Mapped[str | None] = mapped_column(String(1_000))
    output_hash: Mapped[str | None] = mapped_column(String(64))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(120))
    error_detail: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ModelRun(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "model_runs"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="model_run_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "job_id", "step_id"],
            [
                "generation_job_steps.workspace_id",
                "generation_job_steps.job_id",
                "generation_job_steps.id",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "model_entry_id"],
            ["model_catalog.workspace_id", "model_catalog.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "pricing_version_id"],
            ["model_pricing_versions.workspace_id", "model_pricing_versions.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "prompt_version_id"],
            ["prompt_versions.workspace_id", "prompt_versions.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("input_tokens >= 0", name="model_run_input_tokens_nonnegative"),
        CheckConstraint("output_tokens >= 0", name="model_run_output_tokens_nonnegative"),
        CheckConstraint("cost >= 0", name="model_run_cost_nonnegative"),
        Index("ix_model_runs_job", "workspace_id", "job_id", "started_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    job_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    step_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    model_entry_id: Mapped[UUID] = mapped_column(nullable=False)
    pricing_version_id: Mapped[UUID] = mapped_column(nullable=False)
    prompt_version_id: Mapped[UUID] = mapped_column(nullable=False)
    provider: Mapped[str] = mapped_column(String(120), nullable=False)
    model: Mapped[str] = mapped_column(String(160), nullable=False)
    model_version: Mapped[str] = mapped_column(String(120), nullable=False)
    region: Mapped[str] = mapped_column(String(80), nullable=False)
    routing_reason: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    fallback_from: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    prompt_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_object_ref: Mapped[str | None] = mapped_column(String(1_000))
    response_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    provider_transmission_log: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    tool_usage: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    cost: Mapped[Decimal] = mapped_column(Numeric(19, 6), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GenerationJobCommand(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "generation_job_commands"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="generation_command_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "job_id"],
            ["generation_jobs.workspace_id", "generation_jobs.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id",
            "job_id",
            "actor_id",
            "command_kind",
            "idempotency_key",
            name="generation_command_idempotency",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    job_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    actor_id: Mapped[UUID] = mapped_column(nullable=False)
    command_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    from_state: Mapped[str] = mapped_column(String(32), nullable=False)
    to_state: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ContentVersion(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "content_versions"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="content_version_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "content_id"],
            ["contents.workspace_id", "contents.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "parent_version_id"],
            ["content_versions.workspace_id", "content_versions.id"],
            name="fk_content_version_parent",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "restored_from_version_id"],
            ["content_versions.workspace_id", "content_versions.id"],
            name="fk_content_version_restored",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "generation_job_id"],
            ["generation_jobs.workspace_id", "generation_jobs.id"],
            name="fk_content_version_generation_job",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ["workspace_id", "generation_snapshot_id"],
            ["generation_input_snapshots.workspace_id", "generation_input_snapshots.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id", "content_id", "version_number", name="content_version_no"
        ),
        CheckConstraint("version_number > 0", name="content_version_positive"),
        Index("ix_content_versions_content", "workspace_id", "content_id", "version_number"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    content_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    parent_version_id: Mapped[UUID | None] = mapped_column(index=True)
    restored_from_version_id: Mapped[UUID | None] = mapped_column(index=True)
    generation_job_id: Mapped[UUID | None] = mapped_column(index=True)
    generation_snapshot_id: Mapped[UUID | None] = mapped_column(index=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    document: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    plain_text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_snapshot_hash: Mapped[str | None] = mapped_column(String(64))
    change_kind: Mapped[str] = mapped_column(
        String(24), nullable=False, default=ContentChangeKind.MANUAL.value
    )
    change_note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ContentBlock(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "content_blocks"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="content_block_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "content_version_id"],
            ["content_versions.workspace_id", "content_versions.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id", "content_version_id", "block_key", name="content_block_key"
        ),
        CheckConstraint("position >= 0", name="content_block_position_nonnegative"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    content_version_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    block_key: Mapped[UUID] = mapped_column(nullable=False)
    block_type: Mapped[str] = mapped_column(String(40), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    plain_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    locked_facts: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    source_anchors: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ContentLineage(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "content_lineage"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="content_lineage_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "parent_content_version_id"],
            ["content_versions.workspace_id", "content_versions.id"],
            name="fk_content_lineage_parent",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "child_content_version_id"],
            ["content_versions.workspace_id", "content_versions.id"],
            name="fk_content_lineage_child",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id",
            "parent_content_version_id",
            "child_content_version_id",
            "lineage_kind",
            name="content_lineage_identity",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    parent_content_version_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    child_content_version_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    lineage_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ContentCollaborationEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "content_collaboration_events"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="content_event_workspace_id"),
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
        UniqueConstraint(
            "workspace_id", "actor_id", "client_operation_id", name="content_event_client_op"
        ),
        Index("ix_content_events_timeline", "workspace_id", "content_id", "created_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    content_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    content_version_id: Mapped[UUID | None] = mapped_column(index=True)
    actor_id: Mapped[UUID] = mapped_column(nullable=False)
    client_operation_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    block_key: Mapped[UUID | None]
    text_range: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ContentComment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "content_comments"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="content_comment_workspace_id"),
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
            ["workspace_id", "parent_comment_id"],
            ["content_comments.workspace_id", "content_comments.id"],
            ondelete="RESTRICT",
        ),
        Index("ix_content_comments_target", "workspace_id", "content_id", "resolved_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    content_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    content_version_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    parent_comment_id: Mapped[UUID | None] = mapped_column(index=True)
    block_key: Mapped[UUID | None]
    text_range: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    mentions: Mapped[list[UUID]] = mapped_column(JSONB, nullable=False, default=list)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[UUID | None]


class ContentEditLease(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "content_edit_leases"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="content_lease_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "content_id"],
            ["contents.workspace_id", "contents.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("workspace_id", "content_id", name="content_edit_active_lease"),
        Index("ix_content_edit_lease_expiry", "workspace_id", "expires_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    content_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    holder_id: Mapped[UUID] = mapped_column(nullable=False)
    lease_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ContentLibraryMarker(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "content_library_markers"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="content_marker_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "content_id"],
            ["contents.workspace_id", "contents.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "workspace_id", "content_id", "actor_id", "marker", name="content_marker_identity"
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    content_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    actor_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    marker: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ContentFeedback(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "content_feedback"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="content_feedback_workspace_id"),
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
            ["workspace_id", "generation_job_id"],
            ["generation_jobs.workspace_id", "generation_jobs.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id", "content_version_id", "actor_id", "kind", name="content_feedback_once"
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    content_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    content_version_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    generation_job_id: Mapped[UUID | None] = mapped_column(index=True)
    actor_id: Mapped[UUID] = mapped_column(nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


def _reject_immutable_generation_row(
    _mapper: object, _connection: object, target: object
) -> None:
    raise ValueError(f"{type(target).__name__} rows are immutable")


for _immutable_model in (
    PromptVersion,
    TemplateVersion,
    ModelPricingVersion,
    GenerationInputSnapshot,
    GenerationSnapshotSource,
    GenerationSnapshotKeywordMetric,
    ModelRun,
    GenerationJobCommand,
    ContentVersion,
    ContentBlock,
    ContentLineage,
    ContentCollaborationEvent,
    ContentLibraryMarker,
    ContentFeedback,
):
    event.listen(_immutable_model, "before_update", _reject_immutable_generation_row)
    event.listen(_immutable_model, "before_delete", _reject_immutable_generation_row)
