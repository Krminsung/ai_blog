"""Public DTOs for keyword research and planning consumers."""

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from blogops.domain.keywords.enums import (
    AlertKind,
    ClusterKind,
    CollectionKind,
    CredentialOwner,
    ContentLinkTarget,
    ContentRecommendation,
    KeywordIntent,
    ProviderCapability,
    ProviderKind,
    ProviderSourceClass,
    ResearchInputKind,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ORMModel(StrictModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


def _plaintext_credential_paths(value: Any, path: str = "config") -> list[str]:
    """Find credential-shaped keys recursively without reading secret values."""

    forbidden = {
        "secret",
        "secret_key",
        "api_key",
        "access_token",
        "refresh_token",
        "client_secret",
        "password",
    }
    found: list[str] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            key_text = str(key)
            nested_path = f"{path}.{key_text}"
            if key_text.casefold() in forbidden:
                found.append(nested_path)
            found.extend(_plaintext_credential_paths(nested, nested_path))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            found.extend(_plaintext_credential_paths(nested, f"{path}[{index}]"))
    return found


class ProviderConnectionCreate(StrictModel):
    provider: ProviderKind
    source_class: ProviderSourceClass
    name: str = Field(default="default", min_length=1, max_length=120)
    credential_owner: CredentialOwner
    secret_ref: str | None = Field(default=None, min_length=3, max_length=512)
    license_ref: str | None = Field(default=None, max_length=512)
    license_valid_until: datetime | None = None
    capabilities: set[ProviderCapability] = Field(min_length=1)
    config: dict[str, Any] = Field(default_factory=dict)
    ttl_seconds: int = Field(default=86_400, gt=0, le=2_592_000)
    daily_quota: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_provenance_and_secret(self) -> "ProviderConnectionCreate":
        exposed = _plaintext_credential_paths(self.config)
        if exposed:
            raise ValueError("config must not contain plaintext credentials; use secret_ref")
        if self.provider != ProviderKind.USER_CSV and not self.secret_ref:
            raise ValueError("external providers require an opaque secret_ref")
        if self.source_class == ProviderSourceClass.LICENSED and not self.license_ref:
            raise ValueError("licensed providers require license_ref")
        if self.provider in {
            ProviderKind.GOOGLE_TRENDS_LICENSED,
            ProviderKind.CONTRACT_DATA,
        } and not self.license_ref:
            raise ValueError("approval/contract provider requires license_ref")
        if self.provider in {
            ProviderKind.NAVER_DATALAB,
            ProviderKind.NAVER_SHOPPING_INSIGHT,
        } and self.daily_quota not in {None, 1_000}:
            raise ValueError("this official Naver API has a credential-level daily quota of 1000")
        return self


class ProviderConnectionView(ORMModel):
    id: UUID
    provider: str
    source_class: str
    name: str
    credential_owner: str
    license_ref: str | None
    license_valid_until: datetime | None
    state: str
    capabilities_json: list[str]
    ttl_seconds: int
    daily_quota: int | None
    quota_remaining: int | None
    quota_reset_at: datetime | None
    circuit_open_until: datetime | None
    last_requested_at: datetime | None
    last_success_at: datetime | None
    last_error_code: str | None


class ResearchRequest(StrictModel):
    input_kind: ResearchInputKind = ResearchInputKind.SEED
    seed: str | None = Field(default=None, min_length=1, max_length=1_000)
    keywords: list[str] = Field(default_factory=list, max_length=10_000)
    competitor_urls: list[str] = Field(default_factory=list, max_length=20)
    provider_connection_ids: list[UUID] = Field(default_factory=list, max_length=10)
    capabilities: set[ProviderCapability] = Field(default_factory=set)
    language: str = Field(default="ko", min_length=2, max_length=16)
    region: str = Field(default="KR", min_length=2, max_length=32)
    start_date: date | None = None
    end_date: date | None = None
    time_unit: Literal["date", "week", "month"] = "month"
    dimensions: dict[str, Any] = Field(default_factory=dict)
    excluded_terms: list[str] = Field(default_factory=list, max_length=500)
    banned_terms: list[str] = Field(default_factory=list, max_length=500)
    brand_terms: list[str] = Field(default_factory=list, max_length=500)
    require_metrics: bool = False
    allow_stale: bool = False
    score_profile_id: UUID | None = None
    idempotency_key: str = Field(min_length=8, max_length=255)

    @model_validator(mode="after")
    def validate_input(self) -> "ResearchRequest":
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date must be on or before end_date")
        if self.input_kind == ResearchInputKind.SEED and not self.seed:
            raise ValueError("seed is required for seed research")
        if self.input_kind == ResearchInputKind.PASTE and not self.keywords:
            raise ValueError("keywords are required for pasted research")
        if self.input_kind == ResearchInputKind.COMPETITOR:
            if not self.seed:
                raise ValueError("seed is required for competitor gap research")
            if not self.competitor_urls:
                raise ValueError("competitor_urls are required")
            if not self.provider_connection_ids:
                raise ValueError("competitor research requires an approved provider connection")
            if ProviderCapability.LICENSED_SERP not in self.capabilities:
                raise ValueError("competitor research requires LICENSED_SERP capability")
        if self.require_metrics and not self.provider_connection_ids:
            raise ValueError("require_metrics needs at least one provider connection")
        return self


class KeywordImportRequest(StrictModel):
    csv_content: str = Field(min_length=1, max_length=2_000_000)
    mapping: dict[str, str]
    language: str = Field(default="ko", min_length=2, max_length=16)
    region: str = Field(default="KR", min_length=2, max_length=32)
    excluded_terms: list[str] = Field(default_factory=list, max_length=500)
    banned_terms: list[str] = Field(default_factory=list, max_length=500)
    brand_terms: list[str] = Field(default_factory=list, max_length=500)
    score_profile_id: UUID | None = None
    idempotency_key: str = Field(min_length=8, max_length=255)


class KeywordJobView(ORMModel):
    job_id: UUID = Field(validation_alias="id")
    state: str
    input_kind: str
    total_items: int
    processed_items: int
    failed_items: int
    excluded_items: int
    progress_percent: float
    attempt: int
    max_attempts: int
    next_retry_at: datetime | None
    retry_after_seconds: int | None
    error_code: str | None
    result: dict[str, Any] | None = Field(validation_alias="result_json")
    created_at: datetime
    finished_at: datetime | None


class KeywordJobItemView(ORMModel):
    id: UUID
    row_no: int
    original_text_masked: str
    normalized: str
    state: str
    keyword_id: UUID | None
    duplicate_of_item_id: UUID | None
    expansion_reason: str | None
    provider_status_json: dict[str, Any]
    error_code: str | None
    error_detail: str | None


class KeywordView(ORMModel):
    id: UUID
    display_text: str
    normalized: str
    language: str
    region: str
    intent: str
    intent_source: str
    intent_confidence: float
    brand_alignment: float
    risk_tags_json: list[str]
    is_excluded: bool
    created_at: datetime


class KeywordListResponse(StrictModel):
    items: list[KeywordView]
    next_cursor: UUID | None = None


class MetricSnapshotView(ORMModel):
    id: UUID
    keyword_id: UUID
    provider: str
    source_class: str
    source_label: str
    value_kind: str
    measured_at: datetime
    retrieved_at: datetime
    expires_at: datetime
    dimensions_json: dict[str, Any]
    metrics_json: dict[str, Any]
    trend_points_json: list[dict[str, Any]]
    demographics_json: dict[str, Any]
    serp_samples_json: list[dict[str, Any]]
    confidence: float
    limitations_json: list[str]
    adapter_name: str
    adapter_version: str
    transform_version: str
    is_cached: bool
    is_stale: bool
    quota_remaining: int | None = None
    quota_reset_at: datetime | None = None


class KeywordMetricsResponse(StrictModel):
    keyword: KeywordView
    snapshots: list[MetricSnapshotView]


class KeywordCompareRequest(StrictModel):
    keyword_ids: list[UUID] = Field(min_length=2, max_length=10)
    provider: ProviderKind | None = None
    allow_stale: bool = False

    @field_validator("keyword_ids")
    @classmethod
    def distinct_ids(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("keyword_ids must be distinct")
        return value


class KeywordCompareItem(StrictModel):
    keyword: KeywordView
    metric: MetricSnapshotView | None
    score: dict[str, Any] | None


class KeywordCompareResponse(StrictModel):
    items: list[KeywordCompareItem]
    common_axis: dict[str, Any]


class ClusterRequest(StrictModel):
    keyword_ids: list[UUID] = Field(min_length=2, max_length=5_000)
    kind: ClusterKind = ClusterKind.KEYWORD
    similarity_threshold: float = Field(default=0.72, ge=0.0, le=1.0)
    use_serp_when_licensed: bool = True
    idempotency_key: str = Field(min_length=8, max_length=255)

    @field_validator("keyword_ids")
    @classmethod
    def distinct_ids(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("keyword_ids must be distinct")
        return value


class ClusterMemberView(StrictModel):
    keyword_id: UUID
    text: str
    similarity_score: float
    is_primary: bool
    signals: dict[str, Any]


class ClusterView(StrictModel):
    id: UUID
    name: str
    kind: str
    method: str
    version: int
    primary_keyword_id: UUID
    intent: str
    confidence: float
    decision_state: str
    decision_required: bool
    signals: dict[str, Any]
    members: list[ClusterMemberView]


class ClusterResponse(StrictModel):
    clusters: list[ClusterView]
    input_count: int
    cluster_count: int
    duplicate_count: int
    generation_gate: Literal["OPEN", "DECISION_REQUIRED"]


class IntentUpdateRequest(StrictModel):
    intent: KeywordIntent
    reason: str = Field(min_length=3, max_length=500)


class ScoreProfileCreate(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    weights: dict[str, float]
    thresholds: dict[str, Any] = Field(default_factory=dict)


class ScoreProfileView(ORMModel):
    id: UUID
    name: str
    version: int
    formula_version: str
    weights_json: dict[str, float]
    thresholds_json: dict[str, Any]
    is_active: bool
    created_at: datetime


class SavedViewCreate(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    filters: dict[str, Any]
    sort: list[dict[str, str]] = Field(default_factory=list, max_length=5)


class CollectionCreate(StrictModel):
    name: str = Field(min_length=1, max_length=160)
    kind: CollectionKind = CollectionKind.FAVORITES
    campaign_opaque_ref: UUID | None = None


class CollectionMemberRequest(StrictModel):
    keyword_id: UUID


class AlertRuleCreate(StrictModel):
    keyword_id: UUID
    kinds: set[AlertKind] = Field(min_length=1)
    thresholds: dict[str, float]
    channels: set[Literal["EMAIL", "IN_APP"]] = Field(min_length=1)
    cadence_minutes: int = Field(default=1_440, ge=60, le=43_200)


class ExportRequest(StrictModel):
    keyword_ids: list[UUID] = Field(min_length=1, max_length=10_000)
    format: Literal["CSV", "XLSX"] = "CSV"


class ContentLinkCreate(StrictModel):
    keyword_id: UUID
    target_kind: ContentLinkTarget
    target_ref: str = Field(min_length=1, max_length=2_048)
    title: str | None = Field(default=None, max_length=500)
    mapped_intent: KeywordIntent
    similarity: float = Field(ge=0.0, le=1.0)
    recommendation: ContentRecommendation
    evidence: dict[str, Any] = Field(default_factory=dict)
    observed_at: datetime


class ContentLinkView(ORMModel):
    id: UUID
    keyword_id: UUID
    target_kind: str
    target_ref: str
    title: str | None
    mapped_intent: str
    similarity: float
    recommendation: str
    evidence_json: dict[str, Any]
    last_observed_at: datetime


class ContentOverlapRequest(StrictModel):
    keyword_ids: list[UUID] = Field(min_length=1, max_length=10_000)


class ContentOverlapResponse(StrictModel):
    links: list[ContentLinkView]
    cannibalization: list[dict[str, Any]]
    gaps: list[dict[str, Any]]
    missing_signals: list[str]


class TrendSummary(StrictModel):
    direction: str
    growth_rate: float | None
    volatility: float | None
    peak_periods: list[str]
    trough_periods: list[str]
    seasonal: bool
    confidence: float


class ProviderStatusView(StrictModel):
    connection: ProviderConnectionView
    calls: int
    cache_hits: int
    errors: int
    last_error_code: str | None
