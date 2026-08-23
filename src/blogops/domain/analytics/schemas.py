"""Analytics API contracts with explicit evidence and definition snapshots."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

from blogops.domain.analytics.enums import (
    AnalyticsCommandKind,
    AnalyticsProvider,
    AttributionModel,
    ComparisonKind,
    ConversionSource,
    EvidenceKind,
    ExperimentKind,
    MetricSubject,
    MetricValueKind,
    OperationalMetricKind,
    RecommendationDecisionKind,
    RecommendationKind,
    ReportCadence,
    ReportFormat,
)


class AnalyticsConnectionCreate(BaseModel):
    provider: AnalyticsProvider
    name: str = Field(min_length=1, max_length=160)
    external_property_id: str = Field(min_length=1, max_length=500)
    site_url: HttpUrl | None = None
    official_contract: str = Field(min_length=1, max_length=160)
    api_version: str = Field(min_length=1, max_length=80)
    credential_secret_ref: str = Field(min_length=1, max_length=512)
    capabilities: list[str] = Field(min_length=1)
    safe_config: dict[str, Any] = Field(default_factory=dict)
    source_delay: dict[str, Any]


class AnalyticsConnectionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    provider: str
    name: str
    external_property_id: str
    site_url: str | None
    official_contract: str
    api_version: str
    capabilities: list[str]
    safe_config: dict[str, Any]
    source_delay: dict[str, Any]
    state: str
    last_synced_at: datetime | None
    last_error_code: str | None
    created_at: datetime
    updated_at: datetime


class MetricDefinitionCreate(BaseModel):
    key: str = Field(min_length=1, max_length=160)
    version: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=240)
    description: str = Field(min_length=1)
    subject: MetricSubject
    unit: str = Field(min_length=1, max_length=40)
    value_kind: MetricValueKind
    formula: dict[str, Any]
    source_provider: AnalyticsProvider | str
    source_field: str = Field(min_length=1, max_length=240)
    source_contract_version: str = Field(min_length=1, max_length=80)
    latency: dict[str, Any]
    supported_dimensions: list[str]
    caveats: list[str] = Field(default_factory=list)
    effective_at: datetime
    deprecated_at: datetime | None = None


class MetricDefinitionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    key: str
    version: int
    name: str
    description: str
    subject: str
    unit: str
    value_kind: str
    formula: dict[str, Any]
    source_provider: str
    source_field: str
    source_contract_version: str
    latency: dict[str, Any]
    supported_dimensions: list[str]
    caveats: list[str]
    effective_at: datetime
    deprecated_at: datetime | None
    definition_hash: str
    created_at: datetime


class AnalyticsSyncCreate(BaseModel):
    connection_id: UUID
    metric_definition_ids: list[UUID] = Field(min_length=1)
    date_from: date
    date_to: date
    dimensions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_window(self) -> "AnalyticsSyncCreate":
        if self.date_from > self.date_to:
            raise ValueError("date_from must not be after date_to")
        return self


class AnalyticsSyncRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    connection_id: UUID
    input_snapshot_id: UUID
    operation: str
    state: str
    attempt: int
    row_count: int
    started_at: datetime | None
    finished_at: datetime | None
    error_code: str | None
    error_detail: str | None
    result: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


class EvidenceBatchCreate(BaseModel):
    source: EvidenceKind
    external_batch_id: str = Field(min_length=1, max_length=500)
    object_ref: str | None = Field(default=None, max_length=1_000)
    object_hash: str = Field(min_length=64, max_length=64)
    mapping_snapshot: dict[str, Any]
    evidence_metadata: dict[str, Any]


class EvidenceBatchRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source: str
    external_batch_id: str
    object_ref: str | None
    object_hash: str
    mapping_snapshot: dict[str, Any]
    evidence_metadata: dict[str, Any]
    submitted_by: UUID
    created_at: datetime


class ManualMetricFactCreate(BaseModel):
    subject: MetricSubject
    evidence_batch_id: UUID
    metric_definition_id: UUID
    external_fact_id: str = Field(min_length=1, max_length=500)
    fact_date: date
    value: Decimal
    value_kind: MetricValueKind
    dimensions: dict[str, Any] = Field(default_factory=dict)
    observed_at: datetime
    source_delay: dict[str, Any]
    content_id: UUID | None = None
    published_post_id: UUID | None = None
    connection_id: UUID | None = None
    channel: str | None = Field(default=None, max_length=80)
    query_text: str | None = None


class TrackingLinkCreate(BaseModel):
    content_id: UUID
    campaign_id: UUID | None = None
    destination_url: HttpUrl
    tracking_parameters: dict[str, str] = Field(default_factory=dict)
    expires_at: datetime | None = None


class TrackingLinkCreated(BaseModel):
    id: UUID
    token: str
    redirect_url: str
    expires_at: datetime | None


class TrackingLinkRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    content_id: UUID
    campaign_id: UUID | None
    destination_url: str
    tracking_parameters: dict[str, str]
    expires_at: datetime | None
    disabled_at: datetime | None
    created_at: datetime


class TrackingClickCreate(BaseModel):
    external_event_id: str = Field(min_length=1, max_length=500)
    clicked_at: datetime
    referrer_origin: str | None = Field(default=None, max_length=500)
    user_agent_hash: str | None = Field(default=None, min_length=64, max_length=64)
    ip_network_hash: str | None = Field(default=None, min_length=64, max_length=64)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConversionCreate(BaseModel):
    source: ConversionSource
    external_event_id: str = Field(min_length=1, max_length=500)
    event_type: str = Field(min_length=1, max_length=120)
    occurred_at: datetime
    content_id: UUID | None = None
    published_post_id: UUID | None = None
    tracking_link_id: UUID | None = None
    evidence_batch_id: UUID | None = None
    amount: Decimal | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    attribution_model: AttributionModel
    attribution_snapshot: dict[str, Any]
    is_confirmed: bool
    evidence_kind: EvidenceKind
    evidence_ref: str | None = Field(default=None, max_length=1_000)
    evidence_hash: str = Field(min_length=64, max_length=64)
    source_delay: dict[str, Any]
    raw_metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def amount_currency_pair(self) -> "ConversionCreate":
        if (self.amount is None) != (self.currency is None):
            raise ValueError("amount and currency must be supplied together")
        return self


class ConversionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source: str
    external_event_id: str
    event_type: str
    occurred_at: datetime
    content_id: UUID | None
    published_post_id: UUID | None
    tracking_link_id: UUID | None
    evidence_batch_id: UUID | None
    amount: Decimal | None
    currency: str | None
    attribution_model: str
    is_confirmed: bool
    evidence_kind: str
    evidence_hash: str
    created_at: datetime


class OperationalSnapshotCreate(BaseModel):
    content_id: UUID | None = None
    content_version_id: UUID | None = None
    snapshot_kind: OperationalMetricKind
    scope: dict[str, Any]
    period_start: datetime
    period_end: datetime
    metrics: dict[str, Any]
    metric_definition_snapshots: list[dict[str, Any]]
    sample_size: int | None = Field(default=None, ge=0)
    completeness: dict[str, Any]
    caveats: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_window(self) -> "OperationalSnapshotCreate":
        if self.period_start >= self.period_end:
            raise ValueError("period_start must be before period_end")
        return self


class ROISnapshotCreate(BaseModel):
    content_id: UUID
    period_start: datetime
    period_end: datetime
    attributed_revenue: Decimal = Field(ge=0)
    production_cost: Decimal = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)
    revenue_status: MetricValueKind
    cost_status: MetricValueKind
    attribution_model: AttributionModel
    formula_snapshot: dict[str, Any]
    evidence_snapshot: dict[str, Any]
    caveats: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_window(self) -> "ROISnapshotCreate":
        if self.period_start >= self.period_end:
            raise ValueError("period_start must be before period_end")
        return self


class ComparisonSnapshotCreate(BaseModel):
    comparison_kind: ComparisonKind
    scope: dict[str, Any]
    period_start: datetime
    period_end: datetime
    results: dict[str, Any]
    metric_definition_ids: list[UUID] = Field(min_length=1)
    sample_size: int = Field(ge=0)
    caveats: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_window(self) -> "ComparisonSnapshotCreate":
        if self.period_start >= self.period_end:
            raise ValueError("period_start must be before period_end")
        return self


class RecommendationCreate(BaseModel):
    content_id: UUID
    content_version_id: UUID
    kind: RecommendationKind
    rule_name: str = Field(min_length=1, max_length=160)
    rule_version: str = Field(min_length=1, max_length=80)
    model_name: str | None = Field(default=None, max_length=160)
    model_version: str | None = Field(default=None, max_length=120)
    metric_definition_ids: list[UUID] = Field(default_factory=list)
    evidence_snapshot: dict[str, Any]
    explanation: str = Field(min_length=1)
    proposed_actions: list[dict[str, Any]] = Field(min_length=1)
    limitations: list[str] = Field(default_factory=list)


class RecommendationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    content_id: UUID
    content_version_id: UUID
    content_hash: str
    kind: str
    rule_name: str
    rule_version: str
    evidence_snapshot: dict[str, Any]
    explanation: str
    proposed_actions: list[dict[str, Any]]
    limitations: list[str]
    proposal_hash: str
    created_at: datetime


class RecommendationDecisionCreate(BaseModel):
    decision: RecommendationDecisionKind
    reason: str = Field(min_length=1)


class ExperimentCreate(BaseModel):
    content_id: UUID
    kind: ExperimentKind
    metric_definition_id: UUID
    variants: list[dict[str, Any]] = Field(min_length=2)
    allocation_policy: dict[str, Any]
    required_sample_size: int = Field(gt=0)
    analysis_policy: dict[str, Any]
    caveats: list[str] = Field(default_factory=list)


class ExperimentResultCreate(BaseModel):
    window_start: datetime
    window_end: datetime
    variant_results: list[dict[str, Any]]
    sample_size: int = Field(ge=0)
    analysis: dict[str, Any]
    conclusion: str | None = None
    caveats: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_window(self) -> "ExperimentResultCreate":
        if self.window_start >= self.window_end:
            raise ValueError("window_start must be before window_end")
        return self


class ReportDefinitionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=240)
    cadence: ReportCadence
    timezone: str = Field(min_length=1, max_length=80)
    metric_definition_ids: list[UUID] = Field(min_length=1)
    scope: dict[str, Any]
    formats: list[ReportFormat] = Field(min_length=1)
    delivery_policy: dict[str, Any]
    branding_snapshot: dict[str, Any]
    caveats: list[str] = Field(default_factory=list)
    next_run_at: datetime | None = None


class ReportRunCreate(BaseModel):
    period_start: datetime
    period_end: datetime

    @model_validator(mode="after")
    def valid_window(self) -> "ReportRunCreate":
        if self.period_start >= self.period_end:
            raise ValueError("period_start must be before period_end")
        return self


class ReportRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    definition_id: UUID
    state: str
    period_start: datetime
    period_end: datetime
    error_code: str | None
    error_detail: str | None
    created_at: datetime
    updated_at: datetime


class JobCommandCreate(BaseModel):
    command: AnalyticsCommandKind
    reason: str | None = None
