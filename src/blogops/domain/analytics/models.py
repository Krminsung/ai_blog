"""Tenant-isolated analytics facts, lineage, recommendations and reporting models."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
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
from blogops.domain.analytics.enums import (
    AnalyticsConnectionState,
    ExperimentState,
)
from blogops.domain.jobs.state import JobState


class AnalyticsConnection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "analytics_connections"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="analytics_connection_workspace_id"),
        UniqueConstraint(
            "workspace_id", "provider", "external_property_id", name="analytics_connection_key"
        ),
        CheckConstraint("lock_version > 0", name="analytics_connection_lock_positive"),
        Index("ix_analytics_connection_state", "workspace_id", "provider", "state"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    external_property_id: Mapped[str] = mapped_column(String(500), nullable=False)
    site_url: Mapped[str | None] = mapped_column(String(2_048))
    official_contract: Mapped[str] = mapped_column(String(160), nullable=False)
    api_version: Mapped[str] = mapped_column(String(80), nullable=False)
    credential_secret_ref: Mapped[str] = mapped_column(String(512), nullable=False)
    capabilities: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    safe_config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    source_delay: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    state: Mapped[str] = mapped_column(
        String(24), nullable=False, default=AnalyticsConnectionState.PENDING.value
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(120))
    disconnected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class AnalyticsMetricDefinition(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "analytics_metric_definitions"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="analytics_metric_workspace_id"),
        UniqueConstraint("workspace_id", "key", "version", name="analytics_metric_version"),
        CheckConstraint("version > 0", name="analytics_metric_version_positive"),
        Index("ix_analytics_metric_effective", "workspace_id", "key", "effective_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(160), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[str] = mapped_column(String(24), nullable=False)
    unit: Mapped[str] = mapped_column(String(40), nullable=False)
    value_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    formula: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    source_provider: Mapped[str] = mapped_column(String(40), nullable=False)
    source_field: Mapped[str] = mapped_column(String(240), nullable=False)
    source_contract_version: Mapped[str] = mapped_column(String(80), nullable=False)
    latency: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    supported_dimensions: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    caveats: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deprecated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    definition_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AnalyticsSyncInputSnapshot(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "analytics_sync_input_snapshots"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="analytics_sync_input_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            ["analytics_connections.workspace_id", "analytics_connections.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id", "connection_id", "request_hash", name="analytics_sync_input_hash"
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    connection_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    connection_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    metric_definition_snapshots: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False
    )
    date_from: Mapped[date] = mapped_column(Date, nullable=False)
    date_to: Mapped[date] = mapped_column(Date, nullable=False)
    dimensions: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    request_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AnalyticsSyncRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "analytics_sync_runs"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="analytics_sync_run_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            ["analytics_connections.workspace_id", "analytics_connections.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "input_snapshot_id"],
            ["analytics_sync_input_snapshots.workspace_id", "analytics_sync_input_snapshots.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id",
            "requested_by",
            "operation",
            "idempotency_key",
            name="analytics_sync_idempotency",
        ),
        CheckConstraint("attempt >= 0", name="analytics_sync_attempt_nonnegative"),
        CheckConstraint(
            "max_attempts IS NULL OR max_attempts > 0", name="analytics_sync_max_attempts_positive"
        ),
        Index("ix_analytics_sync_state", "workspace_id", "state", "created_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    connection_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    input_snapshot_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    requested_by: Mapped[UUID] = mapped_column(nullable=False)
    operation: Mapped[str] = mapped_column(String(24), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default=JobState.QUEUED.value
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int | None] = mapped_column(Integer)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(120))
    error_detail: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class AnalyticsProviderCall(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "analytics_provider_calls"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="analytics_call_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "sync_run_id"],
            ["analytics_sync_runs.workspace_id", "analytics_sync_runs.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            ["analytics_connections.workspace_id", "analytics_connections.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id", "sync_run_id", "request_hash", name="analytics_call_request"
        ),
        Index("ix_analytics_calls_run", "workspace_id", "sync_run_id", "started_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    sync_run_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    connection_id: Mapped[UUID] = mapped_column(nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    adapter_name: Mapped[str] = mapped_column(String(120), nullable=False)
    adapter_version: Mapped[str] = mapped_column(String(80), nullable=False)
    api_version: Mapped[str] = mapped_column(String(80), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    raw_object_ref: Mapped[str | None] = mapped_column(String(1_000))
    raw_response_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    source_delay: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ContentMetricDailyFact(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "analytics_content_daily_facts"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="analytics_content_fact_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "content_id"],
            ["contents.workspace_id", "contents.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "published_post_id"],
            ["published_posts.workspace_id", "published_posts.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "metric_definition_id"],
            ["analytics_metric_definitions.workspace_id", "analytics_metric_definitions.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "provider_call_id"],
            ["analytics_provider_calls.workspace_id", "analytics_provider_calls.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "evidence_batch_id"],
            ["analytics_evidence_batches.workspace_id", "analytics_evidence_batches.id"],
            ondelete="RESTRICT",
            use_alter=True,
        ),
        UniqueConstraint(
            "workspace_id", "source", "external_fact_id", name="analytics_content_fact_source"
        ),
        CheckConstraint(
            "(provider_call_id IS NOT NULL AND evidence_batch_id IS NULL) OR "
            "(provider_call_id IS NULL AND evidence_batch_id IS NOT NULL)",
            name="analytics_content_fact_one_evidence",
        ),
        Index("ix_analytics_content_fact_day", "workspace_id", "content_id", "fact_date"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    content_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    published_post_id: Mapped[UUID | None] = mapped_column(index=True)
    metric_definition_id: Mapped[UUID] = mapped_column(nullable=False)
    provider_call_id: Mapped[UUID | None] = mapped_column(index=True)
    evidence_batch_id: Mapped[UUID | None] = mapped_column(index=True)
    fact_date: Mapped[date] = mapped_column(Date, nullable=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    external_fact_id: Mapped[str] = mapped_column(String(500), nullable=False)
    dimensions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    dimensions_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    value_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_delay: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ChannelMetricDailyFact(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "analytics_channel_daily_facts"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="analytics_channel_fact_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            ["analytics_connections.workspace_id", "analytics_connections.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "metric_definition_id"],
            ["analytics_metric_definitions.workspace_id", "analytics_metric_definitions.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "provider_call_id"],
            ["analytics_provider_calls.workspace_id", "analytics_provider_calls.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "evidence_batch_id"],
            ["analytics_evidence_batches.workspace_id", "analytics_evidence_batches.id"],
            ondelete="RESTRICT",
            use_alter=True,
        ),
        UniqueConstraint(
            "workspace_id", "source", "external_fact_id", name="analytics_channel_fact_source"
        ),
        CheckConstraint(
            "(provider_call_id IS NOT NULL AND evidence_batch_id IS NULL) OR "
            "(provider_call_id IS NULL AND evidence_batch_id IS NOT NULL)",
            name="analytics_channel_fact_one_evidence",
        ),
        Index("ix_analytics_channel_fact_day", "workspace_id", "channel", "fact_date"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    connection_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    metric_definition_id: Mapped[UUID] = mapped_column(nullable=False)
    provider_call_id: Mapped[UUID | None] = mapped_column(index=True)
    evidence_batch_id: Mapped[UUID | None] = mapped_column(index=True)
    channel: Mapped[str] = mapped_column(String(80), nullable=False)
    fact_date: Mapped[date] = mapped_column(Date, nullable=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    external_fact_id: Mapped[str] = mapped_column(String(500), nullable=False)
    dimensions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    dimensions_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    value_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_delay: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class QueryMetricDailyFact(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "analytics_query_daily_facts"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="analytics_query_fact_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "content_id"],
            ["contents.workspace_id", "contents.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "metric_definition_id"],
            ["analytics_metric_definitions.workspace_id", "analytics_metric_definitions.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "provider_call_id"],
            ["analytics_provider_calls.workspace_id", "analytics_provider_calls.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "evidence_batch_id"],
            ["analytics_evidence_batches.workspace_id", "analytics_evidence_batches.id"],
            ondelete="RESTRICT",
            use_alter=True,
        ),
        UniqueConstraint(
            "workspace_id", "source", "external_fact_id", name="analytics_query_fact_source"
        ),
        CheckConstraint(
            "(provider_call_id IS NOT NULL AND evidence_batch_id IS NULL) OR "
            "(provider_call_id IS NULL AND evidence_batch_id IS NOT NULL)",
            name="analytics_query_fact_one_evidence",
        ),
        Index("ix_analytics_query_fact_day", "workspace_id", "query_hash", "fact_date"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    content_id: Mapped[UUID | None] = mapped_column(index=True)
    metric_definition_id: Mapped[UUID] = mapped_column(nullable=False)
    provider_call_id: Mapped[UUID | None] = mapped_column(index=True)
    evidence_batch_id: Mapped[UUID | None] = mapped_column(index=True)
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    query_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    fact_date: Mapped[date] = mapped_column(Date, nullable=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    external_fact_id: Mapped[str] = mapped_column(String(500), nullable=False)
    dimensions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    dimensions_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    value_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_delay: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AnalyticsEvidenceBatch(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "analytics_evidence_batches"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="analytics_evidence_workspace_id"),
        UniqueConstraint(
            "workspace_id", "source", "external_batch_id", name="analytics_evidence_source"
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(24), nullable=False)
    external_batch_id: Mapped[str] = mapped_column(String(500), nullable=False)
    object_ref: Mapped[str | None] = mapped_column(String(1_000))
    object_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    mapping_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    evidence_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    submitted_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TrackingLink(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "analytics_tracking_links"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="analytics_tracking_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "content_id"],
            ["contents.workspace_id", "contents.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "campaign_id"],
            ["campaigns.workspace_id", "campaigns.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("token_hash", name="analytics_tracking_token_hash"),
        CheckConstraint("lock_version > 0", name="analytics_tracking_lock_positive"),
        Index("ix_analytics_tracking_content", "workspace_id", "content_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    content_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    campaign_id: Mapped[UUID | None] = mapped_column(index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    destination_url: Mapped[str] = mapped_column(String(2_048), nullable=False)
    tracking_parameters: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class TrackingClickEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "analytics_tracking_clicks"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="analytics_click_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "tracking_link_id"],
            ["analytics_tracking_links.workspace_id", "analytics_tracking_links.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id", "tracking_link_id", "external_event_id", name="analytics_click_event"
        ),
        Index("ix_analytics_click_time", "workspace_id", "tracking_link_id", "clicked_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    tracking_link_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    external_event_id: Mapped[str] = mapped_column(String(500), nullable=False)
    clicked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    referrer_origin: Mapped[str | None] = mapped_column(String(500))
    user_agent_hash: Mapped[str | None] = mapped_column(String(64))
    ip_network_hash: Mapped[str | None] = mapped_column(String(64))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class ConversionEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "analytics_conversion_events"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="analytics_conversion_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "content_id"],
            ["contents.workspace_id", "contents.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "published_post_id"],
            ["published_posts.workspace_id", "published_posts.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "tracking_link_id"],
            ["analytics_tracking_links.workspace_id", "analytics_tracking_links.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "evidence_batch_id"],
            ["analytics_evidence_batches.workspace_id", "analytics_evidence_batches.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id", "source", "external_event_id", name="analytics_conversion_source"
        ),
        CheckConstraint(
            "amount IS NULL OR amount >= 0", name="analytics_conversion_amount_nonnegative"
        ),
        CheckConstraint(
            "currency IS NULL OR char_length(currency) = 3",
            name="analytics_conversion_currency_length",
        ),
        Index("ix_analytics_conversion_content", "workspace_id", "content_id", "occurred_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    content_id: Mapped[UUID | None] = mapped_column(index=True)
    published_post_id: Mapped[UUID | None] = mapped_column(index=True)
    tracking_link_id: Mapped[UUID | None] = mapped_column(index=True)
    evidence_batch_id: Mapped[UUID | None] = mapped_column(index=True)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    external_event_id: Mapped[str] = mapped_column(String(500), nullable=False)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(19, 4))
    currency: Mapped[str | None] = mapped_column(String(3))
    attribution_model: Mapped[str] = mapped_column(String(24), nullable=False)
    attribution_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    is_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    evidence_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    evidence_ref: Mapped[str | None] = mapped_column(String(1_000))
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_delay: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    raw_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    recorded_by: Mapped[UUID | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class OperationalMetricSnapshot(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "analytics_operational_snapshots"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="analytics_ops_workspace_id"),
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
            "workspace_id", "snapshot_kind", "scope_hash", "period_end", name="analytics_ops_key"
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    content_id: Mapped[UUID | None] = mapped_column(index=True)
    content_version_id: Mapped[UUID | None] = mapped_column(index=True)
    snapshot_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    scope: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    scope_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    metric_definition_snapshots: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False
    )
    sample_size: Mapped[int | None] = mapped_column(Integer)
    completeness: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    caveats: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ContentROISnapshot(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "analytics_content_roi_snapshots"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="analytics_roi_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "content_id"],
            ["contents.workspace_id", "contents.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id", "content_id", "period_start", "period_end", "snapshot_hash",
            name="analytics_roi_key",
        ),
        CheckConstraint("production_cost >= 0", name="analytics_roi_cost_nonnegative"),
        CheckConstraint("char_length(currency) = 3", name="analytics_roi_currency_length"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    content_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attributed_revenue: Mapped[Decimal] = mapped_column(Numeric(19, 4), nullable=False)
    production_cost: Mapped[Decimal] = mapped_column(Numeric(19, 4), nullable=False)
    net_return: Mapped[Decimal] = mapped_column(Numeric(19, 4), nullable=False)
    roi_ratio: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    revenue_status: Mapped[str] = mapped_column(String(24), nullable=False)
    cost_status: Mapped[str] = mapped_column(String(24), nullable=False)
    attribution_model: Mapped[str] = mapped_column(String(24), nullable=False)
    formula_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    evidence_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    caveats: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AnalyticsComparisonSnapshot(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "analytics_comparison_snapshots"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="analytics_compare_workspace_id"),
        UniqueConstraint(
            "workspace_id", "comparison_kind", "scope_hash", "snapshot_hash",
            name="analytics_compare_key",
        ),
        CheckConstraint("sample_size >= 0", name="analytics_compare_sample_nonnegative"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    comparison_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    scope: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    scope_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    results: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    metric_compatibility: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    definition_snapshots: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    caveats: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AnalyticsRecommendation(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "analytics_recommendations"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="analytics_recommend_workspace_id"),
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
            "workspace_id", "content_version_id", "kind", "proposal_hash",
            name="analytics_recommend_key",
        ),
        Index("ix_analytics_recommend_content", "workspace_id", "content_id", "created_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    content_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    content_version_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    rule_name: Mapped[str] = mapped_column(String(160), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(80), nullable=False)
    model_name: Mapped[str | None] = mapped_column(String(160))
    model_version: Mapped[str | None] = mapped_column(String(120))
    metric_definition_snapshots: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False
    )
    evidence_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    proposed_actions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    limitations: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    proposal_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AnalyticsRecommendationDecision(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "analytics_recommendation_decisions"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="analytics_rec_decision_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "recommendation_id"],
            ["analytics_recommendations.workspace_id", "analytics_recommendations.id"],
            ondelete="RESTRICT",
        ),
        Index("ix_analytics_rec_decision", "workspace_id", "recommendation_id", "created_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    recommendation_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    decision: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    decided_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AnalyticsExperiment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "analytics_experiments"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="analytics_experiment_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "content_id"],
            ["contents.workspace_id", "contents.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "metric_definition_id"],
            ["analytics_metric_definitions.workspace_id", "analytics_metric_definitions.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("required_sample_size > 0", name="analytics_experiment_sample_positive"),
        CheckConstraint("lock_version > 0", name="analytics_experiment_lock_positive"),
        Index("ix_analytics_experiment_state", "workspace_id", "state"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    content_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    metric_definition_id: Mapped[UUID] = mapped_column(nullable=False)
    variants: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    allocation_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    required_sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    analysis_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    caveats: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    state: Mapped[str] = mapped_column(
        String(24), nullable=False, default=ExperimentState.DRAFT.value
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class AnalyticsExperimentResult(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "analytics_experiment_results"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="analytics_exp_result_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "experiment_id"],
            ["analytics_experiments.workspace_id", "analytics_experiments.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id", "experiment_id", "result_hash", name="analytics_exp_result_hash"
        ),
        CheckConstraint("sample_size >= 0", name="analytics_exp_result_sample_nonnegative"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    experiment_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    variant_results: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    analysis: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    conclusion: Mapped[str | None] = mapped_column(Text)
    caveats: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    result_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AnalyticsReportDefinition(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "analytics_report_definitions"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="analytics_report_def_workspace_id"),
        UniqueConstraint("workspace_id", "name", name="analytics_report_def_name"),
        CheckConstraint("lock_version > 0", name="analytics_report_def_lock_positive"),
        Index("ix_analytics_reports_due", "workspace_id", "enabled", "next_run_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    cadence: Mapped[str] = mapped_column(String(24), nullable=False)
    timezone: Mapped[str] = mapped_column(String(80), nullable=False)
    scope: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    formats: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    delivery_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    branding_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    caveats: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    definition_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class AnalyticsReportMetric(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "analytics_report_metrics"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="analytics_report_metric_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "report_definition_id"],
            ["analytics_report_definitions.workspace_id", "analytics_report_definitions.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "metric_definition_id"],
            ["analytics_metric_definitions.workspace_id", "analytics_metric_definitions.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id",
            "report_definition_id",
            "metric_definition_id",
            name="analytics_report_metric_key",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    report_definition_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    metric_definition_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    definition_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AnalyticsReportRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "analytics_report_runs"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="analytics_report_run_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "definition_id"],
            ["analytics_report_definitions.workspace_id", "analytics_report_definitions.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id", "requested_by", "operation", "idempotency_key",
            name="analytics_report_run_idempotency",
        ),
        CheckConstraint("attempt >= 0", name="analytics_report_run_attempt_nonnegative"),
        Index("ix_analytics_report_run_state", "workspace_id", "state", "created_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    definition_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    requested_by: Mapped[UUID] = mapped_column(nullable=False)
    operation: Mapped[str] = mapped_column(String(24), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    definition_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    definition_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default=JobState.QUEUED.value
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(120))
    error_detail: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AnalyticsReportArtifact(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "analytics_report_artifacts"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="analytics_report_artifact_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "report_run_id"],
            ["analytics_report_runs.workspace_id", "analytics_report_runs.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id", "report_run_id", "format", name="analytics_report_artifact_format"
        ),
        CheckConstraint("size_bytes >= 0", name="analytics_report_artifact_size_nonnegative"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    report_run_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    format: Mapped[str] = mapped_column(String(16), nullable=False)
    object_ref: Mapped[str] = mapped_column(String(1_000), nullable=False)
    object_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    media_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AnalyticsJobCommand(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "analytics_job_commands"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="analytics_command_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "sync_run_id"],
            ["analytics_sync_runs.workspace_id", "analytics_sync_runs.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "report_run_id"],
            ["analytics_report_runs.workspace_id", "analytics_report_runs.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id",
            "target_id",
            "actor_id",
            "command",
            "idempotency_key",
            name="analytics_command_idempotency",
        ),
        CheckConstraint(
            "(sync_run_id IS NOT NULL AND report_run_id IS NULL) OR "
            "(sync_run_id IS NULL AND report_run_id IS NOT NULL)",
            name="analytics_command_one_job",
        ),
        CheckConstraint(
            "target_id = COALESCE(sync_run_id, report_run_id)",
            name="analytics_command_target_matches",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    sync_run_id: Mapped[UUID | None] = mapped_column(index=True)
    report_run_id: Mapped[UUID | None] = mapped_column(index=True)
    target_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
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


def _reject_immutable_analytics_row(
    _mapper: object, _connection: object, target: object
) -> None:
    raise ValueError(f"{type(target).__name__} rows are immutable")


for _immutable_model in (
    AnalyticsMetricDefinition,
    AnalyticsSyncInputSnapshot,
    AnalyticsProviderCall,
    ContentMetricDailyFact,
    ChannelMetricDailyFact,
    QueryMetricDailyFact,
    AnalyticsEvidenceBatch,
    TrackingClickEvent,
    ConversionEvent,
    OperationalMetricSnapshot,
    ContentROISnapshot,
    AnalyticsComparisonSnapshot,
    AnalyticsRecommendation,
    AnalyticsRecommendationDecision,
    AnalyticsExperimentResult,
    AnalyticsReportMetric,
    AnalyticsReportArtifact,
    AnalyticsJobCommand,
):
    event.listen(_immutable_model, "before_update", _reject_immutable_analytics_row)
    event.listen(_immutable_model, "before_delete", _reject_immutable_analytics_row)
