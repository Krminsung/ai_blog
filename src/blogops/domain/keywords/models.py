"""Tenant-isolated persistence for keyword collection, lineage and strategy signals."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from blogops.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from blogops.domain.keywords.enums import (
    ClusterDecisionState,
    ClusterKind,
    ClusterMethod,
    CredentialOwner,
    IntentSource,
    KeywordIntent,
    ProviderCallState,
    ProviderConnectionState,
    ProviderSourceClass,
    ResearchItemState,
    ResearchJobState,
)


class KeywordProviderConnection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Provider metadata; ``secret_ref`` is opaque and never contains a credential."""

    __tablename__ = "keyword_provider_connections"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="keyword_provider_connection_workspace_id"),
        UniqueConstraint(
            "workspace_id", "provider", "name", name="keyword_provider_connection_name"
        ),
        CheckConstraint("ttl_seconds > 0", name="keyword_provider_ttl_positive"),
        CheckConstraint(
            "daily_quota IS NULL OR daily_quota > 0", name="keyword_provider_quota_positive"
        ),
        CheckConstraint(
            "quota_remaining IS NULL OR quota_remaining >= 0",
            name="provider_remaining_nonnegative",
        ),
        Index("ix_keyword_provider_workspace_state", "workspace_id", "state"),
        Index("ix_keyword_provider_quota_reset", "workspace_id", "quota_reset_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(48), nullable=False)
    source_class: Mapped[str] = mapped_column(
        String(24), nullable=False, default=ProviderSourceClass.OFFICIAL.value
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False, default="default")
    credential_owner: Mapped[str] = mapped_column(
        String(16), nullable=False, default=CredentialOwner.CUSTOMER.value
    )
    secret_ref: Mapped[str | None] = mapped_column(String(512))
    license_ref: Mapped[str | None] = mapped_column(String(512))
    license_valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ProviderConnectionState.ACTIVE.value
    )
    capabilities_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    ttl_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=86_400)
    daily_quota: Mapped[int | None] = mapped_column(Integer)
    quota_remaining: Mapped[int | None] = mapped_column(Integer)
    quota_reset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    circuit_open_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(120))
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Keyword(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "keywords"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="keyword_workspace_id"),
        UniqueConstraint(
            "workspace_id",
            "language",
            "region",
            "normalized",
            name="keyword_workspace_language_region_normalized",
        ),
        CheckConstraint(
            "intent_confidence >= 0 AND intent_confidence <= 1",
            name="keyword_intent_confidence_range",
        ),
        CheckConstraint(
            "brand_alignment >= 0 AND brand_alignment <= 1",
            name="keyword_brand_alignment_range",
        ),
        Index("ix_keywords_workspace_intent", "workspace_id", "intent"),
        Index("ix_keywords_workspace_normalized", "workspace_id", "normalized"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    display_text: Mapped[str] = mapped_column(String(1_000), nullable=False)
    normalized: Mapped[str] = mapped_column(String(1_000), nullable=False)
    language: Mapped[str] = mapped_column(String(16), nullable=False, default="ko")
    region: Mapped[str] = mapped_column(String(32), nullable=False, default="KR")
    intent: Mapped[str] = mapped_column(
        String(24), nullable=False, default=KeywordIntent.UNKNOWN.value
    )
    intent_source: Mapped[str] = mapped_column(
        String(24), nullable=False, default=IntentSource.RULE.value
    )
    intent_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    intent_signals_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    brand_alignment: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    risk_tags_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    is_excluded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    exclusion_reason: Mapped[str | None] = mapped_column(String(240))
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536))
    embedding_model: Mapped[str | None] = mapped_column(String(160))
    embedding_version: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[UUID] = mapped_column(nullable=False, index=True)


class KeywordResearchJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "keyword_research_jobs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "score_profile_id"],
            ["keyword_score_profiles.workspace_id", "keyword_score_profiles.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("workspace_id", "id", name="keyword_research_job_workspace_id"),
        UniqueConstraint(
            "workspace_id",
            "input_kind",
            "requested_by",
            "idempotency_key",
            name="keyword_research_idempotency",
        ),
        CheckConstraint("total_items >= 0", name="keyword_job_total_nonnegative"),
        CheckConstraint("processed_items >= 0", name="keyword_job_processed_nonnegative"),
        CheckConstraint("failed_items >= 0", name="keyword_job_failed_nonnegative"),
        CheckConstraint("attempt >= 0", name="keyword_job_attempt_nonnegative"),
        CheckConstraint("max_attempts > 0", name="keyword_job_max_attempts_positive"),
        Index("ix_keyword_jobs_workspace_state", "workspace_id", "state", "created_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    requested_by: Mapped[UUID] = mapped_column(nullable=False, index=True)
    input_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ResearchJobState.QUEUED.value
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    provider_keys_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    requested_capabilities_json: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    score_profile_id: Mapped[UUID | None] = mapped_column(index=True)
    total_items: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    processed_items: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_items: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    excluded_items: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress_percent: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retry_after_seconds: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(120))
    error_detail: Mapped[str | None] = mapped_column(Text)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class KeywordResearchItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "keyword_research_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "job_id"],
            ["keyword_research_jobs.workspace_id", "keyword_research_jobs.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "keyword_id"],
            ["keywords.workspace_id", "keywords.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "job_id", "duplicate_of_item_id"],
            [
                "keyword_research_items.workspace_id",
                "keyword_research_items.job_id",
                "keyword_research_items.id",
            ],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("workspace_id", "id", name="keyword_research_item_workspace_id"),
        UniqueConstraint(
            "workspace_id", "job_id", "id", name="keyword_research_item_job_identity"
        ),
        UniqueConstraint("workspace_id", "job_id", "row_no", name="keyword_research_job_row"),
        CheckConstraint("row_no > 0", name="keyword_research_row_positive"),
        CheckConstraint("attempt >= 0", name="research_item_attempt_nonnegative"),
        Index("ix_keyword_items_job_state", "workspace_id", "job_id", "state"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    job_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    row_no: Mapped[int] = mapped_column(Integer, nullable=False)
    original_text_masked: Mapped[str] = mapped_column(String(1_000), nullable=False)
    normalized: Mapped[str] = mapped_column(String(1_000), nullable=False)
    state: Mapped[str] = mapped_column(
        String(24), nullable=False, default=ResearchItemState.PENDING.value
    )
    keyword_id: Mapped[UUID | None] = mapped_column(index=True)
    duplicate_of_item_id: Mapped[UUID | None] = mapped_column(index=True)
    expansion_reason: Mapped[str | None] = mapped_column(String(120))
    input_metrics_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    provider_status_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(120))
    error_detail: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class KeywordMetricSnapshot(UUIDPrimaryKeyMixin, Base):
    """Immutable metric and provenance snapshot."""

    __tablename__ = "keyword_metrics"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "keyword_id"],
            ["keywords.workspace_id", "keywords.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "provider_connection_id"],
            [
                "keyword_provider_connections.workspace_id",
                "keyword_provider_connections.id",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "job_id"],
            ["keyword_research_jobs.workspace_id", "keyword_research_jobs.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("workspace_id", "id", name="keyword_metric_snapshot_workspace_id"),
        UniqueConstraint(
            "workspace_id",
            "keyword_id",
            "provider",
            "dimensions_hash",
            "measured_at",
            name="keyword_metric_snapshot_identity",
        ),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="keyword_metric_confidence"),
        Index(
            "ix_keyword_metrics_latest",
            "workspace_id",
            "keyword_id",
            "provider",
            "measured_at",
        ),
        Index("ix_keyword_metrics_expiry", "workspace_id", "expires_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    keyword_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    job_id: Mapped[UUID | None] = mapped_column(index=True)
    provider_connection_id: Mapped[UUID | None] = mapped_column(index=True)
    provider: Mapped[str] = mapped_column(String(48), nullable=False)
    source_class: Mapped[str] = mapped_column(String(24), nullable=False)
    source_label: Mapped[str] = mapped_column(String(160), nullable=False)
    value_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dimensions_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    dimensions_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    trend_points_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    demographics_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    serp_samples_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    limitations_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    adapter_name: Mapped[str] = mapped_column(String(120), nullable=False)
    adapter_version: Mapped[str] = mapped_column(String(32), nullable=False)
    transform_version: Mapped[str] = mapped_column(String(32), nullable=False)
    raw_object_ref: Mapped[str | None] = mapped_column(Text)
    raw_response_hash: Mapped[str | None] = mapped_column(String(64))
    is_cached: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_stale: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class KeywordProviderCall(UUIDPrimaryKeyMixin, Base):
    """Append-only external-call lineage suitable for audit and provider health views."""

    __tablename__ = "keyword_provider_calls"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            [
                "keyword_provider_connections.workspace_id",
                "keyword_provider_connections.id",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "job_id"],
            ["keyword_research_jobs.workspace_id", "keyword_research_jobs.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("workspace_id", "id", name="keyword_provider_call_workspace_id"),
        Index("ix_keyword_provider_calls_status", "workspace_id", "provider", "state"),
        Index("ix_keyword_provider_calls_started", "workspace_id", "started_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    connection_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    job_id: Mapped[UUID | None] = mapped_column(index=True)
    provider: Mapped[str] = mapped_column(String(48), nullable=False)
    capability: Mapped[str] = mapped_column(String(48), nullable=False)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ProviderCallState.STARTED.value
    )
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    cache_hit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    stale_returned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    quota_remaining_before: Mapped[int | None] = mapped_column(Integer)
    quota_remaining_after: Mapped[int | None] = mapped_column(Integer)
    quota_reset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    http_status: Mapped[int | None] = mapped_column(Integer)
    retry_after_seconds: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(120))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latency_ms: Mapped[int | None] = mapped_column(Integer)


class KeywordScoreProfile(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "keyword_score_profiles"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="keyword_score_profile_workspace_id"),
        UniqueConstraint("workspace_id", "name", "version", name="keyword_score_profile_version"),
        CheckConstraint("version > 0", name="score_profile_version_positive"),
        Index("ix_keyword_score_profiles_active", "workspace_id", "is_active"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    formula_version: Mapped[str] = mapped_column(String(32), nullable=False, default="1")
    weights_json: Mapped[dict[str, float]] = mapped_column(JSONB, nullable=False)
    thresholds_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class KeywordScoreSnapshot(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "keyword_score_snapshots"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "keyword_id"],
            ["keywords.workspace_id", "keywords.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "profile_id"],
            ["keyword_score_profiles.workspace_id", "keyword_score_profiles.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "metric_snapshot_id"],
            ["keyword_metrics.workspace_id", "keyword_metrics.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("workspace_id", "id", name="keyword_score_snapshot_workspace_id"),
        CheckConstraint(
            "opportunity_score IS NULL OR (opportunity_score >= 0 AND opportunity_score <= 100)",
            name="keyword_opportunity_score_range",
        ),
        CheckConstraint("coverage >= 0 AND coverage <= 1", name="keyword_score_coverage_range"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="keyword_score_confidence"),
        Index("ix_keyword_scores_rank", "workspace_id", "opportunity_score", "id"),
        Index("ix_keyword_scores_latest", "workspace_id", "keyword_id", "scored_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    keyword_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    metric_snapshot_id: Mapped[UUID | None] = mapped_column(index=True)
    profile_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    opportunity_score: Mapped[float | None] = mapped_column(Float)
    components_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    coverage: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    saturation_score: Mapped[float | None] = mapped_column(Float)
    difficulty_lower: Mapped[float | None] = mapped_column(Float)
    difficulty_upper: Mapped[float | None] = mapped_column(Float)
    difficulty_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    commerciality_score: Mapped[float | None] = mapped_column(Float)
    freshness_score: Mapped[float | None] = mapped_column(Float)
    risk_score: Mapped[float | None] = mapped_column(Float)
    score_version: Mapped[str] = mapped_column(String(32), nullable=False)
    scored_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class KeywordIntentRevision(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "keyword_intent_revisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "keyword_id"],
            ["keywords.workspace_id", "keywords.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("workspace_id", "id", name="keyword_intent_revision_workspace_id"),
        Index("ix_keyword_intent_history", "workspace_id", "keyword_id", "changed_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    keyword_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    previous_intent: Mapped[str] = mapped_column(String(24), nullable=False)
    next_intent: Mapped[str] = mapped_column(String(24), nullable=False)
    previous_source: Mapped[str] = mapped_column(String(24), nullable=False)
    next_source: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    changed_by: Mapped[UUID] = mapped_column(nullable=False)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class KeywordCluster(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "keyword_clusters"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "primary_keyword_id"],
            ["keywords.workspace_id", "keywords.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("workspace_id", "id", name="keyword_cluster_workspace_id"),
        CheckConstraint("version > 0", name="keyword_cluster_version_positive"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="keyword_cluster_confidence"),
        Index("ix_keyword_clusters_workspace_kind", "workspace_id", "kind"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False, default=ClusterKind.KEYWORD.value)
    method: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ClusterMethod.SEMANTIC_INTENT.value
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    primary_keyword_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    intent: Mapped[str] = mapped_column(String(24), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    decision_state: Mapped[str] = mapped_column(
        String(24), nullable=False, default=ClusterDecisionState.PROPOSED.value
    )
    decision_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    signals_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_by: Mapped[UUID] = mapped_column(nullable=False)


class KeywordClusterMember(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "keyword_cluster_members"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "cluster_id"],
            ["keyword_clusters.workspace_id", "keyword_clusters.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "keyword_id"],
            ["keywords.workspace_id", "keywords.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "workspace_id", "cluster_id", "keyword_id", name="keyword_cluster_member_identity"
        ),
        CheckConstraint(
            "similarity_score >= 0 AND similarity_score <= 1",
            name="keyword_cluster_similarity_range",
        ),
        Index("ix_keyword_cluster_members_keyword", "workspace_id", "keyword_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    cluster_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    keyword_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    similarity_score: Mapped[float] = mapped_column(Float, nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    signals_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class KeywordSavedView(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "keyword_saved_views"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="keyword_saved_view_workspace_id"),
        UniqueConstraint("workspace_id", "owner_id", "name", name="keyword_saved_view_owner_name"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    owner_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    filters_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    sort_json: Mapped[list[dict[str, str]]] = mapped_column(JSONB, nullable=False)


class KeywordCollection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "keyword_collections"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="keyword_collection_workspace_id"),
        UniqueConstraint("workspace_id", "kind", "name", name="keyword_collection_kind_name"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    # Planning is a separate bounded context; this opaque reference is resolved by its
    # service and deliberately has no cross-domain database FK.
    campaign_opaque_ref: Mapped[UUID | None] = mapped_column(index=True)
    created_by: Mapped[UUID] = mapped_column(nullable=False)


class KeywordCollectionMember(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "keyword_collection_members"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "collection_id"],
            ["keyword_collections.workspace_id", "keyword_collections.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "keyword_id"],
            ["keywords.workspace_id", "keywords.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "workspace_id", "collection_id", "keyword_id", name="keyword_collection_member_identity"
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    collection_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    keyword_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    added_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class KeywordContentLink(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "keyword_content_links"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "keyword_id"],
            ["keywords.workspace_id", "keywords.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("workspace_id", "id", name="keyword_content_link_workspace_id"),
        UniqueConstraint(
            "workspace_id",
            "keyword_id",
            "target_kind",
            "target_ref",
            name="keyword_target_identity",
        ),
        CheckConstraint("similarity >= 0 AND similarity <= 1", name="keyword_target_similarity"),
        Index("ix_keyword_content_links_target", "workspace_id", "target_kind", "target_ref"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    keyword_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    target_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    target_ref: Mapped[str] = mapped_column(String(2_048), nullable=False)
    target_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str | None] = mapped_column(String(500))
    mapped_intent: Mapped[str] = mapped_column(String(24), nullable=False)
    similarity: Mapped[float] = mapped_column(Float, nullable=False)
    recommendation: Mapped[str] = mapped_column(String(24), nullable=False)
    evidence_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    last_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class KeywordAlertRule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "keyword_alert_rules"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "keyword_id"],
            ["keywords.workspace_id", "keywords.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("workspace_id", "id", name="keyword_alert_rule_workspace_id"),
        UniqueConstraint("workspace_id", "keyword_id", "owner_id", name="keyword_alert_owner"),
        Index("ix_keyword_alerts_due", "workspace_id", "enabled", "next_evaluate_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    keyword_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    owner_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    kinds_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    thresholds_json: Mapped[dict[str, float]] = mapped_column(JSONB, nullable=False)
    channels_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    cadence_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=1_440)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_evaluate_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


def _reject_immutable_keyword_row(
    _mapper: object, _connection: object, target: object
) -> None:
    raise ValueError(f"{type(target).__name__} rows are immutable")


for _immutable_model in (
    KeywordMetricSnapshot,
    KeywordScoreSnapshot,
    KeywordIntentRevision,
):
    event.listen(_immutable_model, "before_update", _reject_immutable_keyword_row)
    event.listen(_immutable_model, "before_delete", _reject_immutable_keyword_row)

# A provider call is inserted as STARTED and finalized in the same transaction. Its lifecycle
# fields may therefore be updated, but the lineage row must never be removed.
event.listen(KeywordProviderCall, "before_delete", _reject_immutable_keyword_row)
