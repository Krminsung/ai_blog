"""Research plan, source, claim and citation API contracts."""

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from blogops.domain.research.enums import (
    CitationStyle,
    ClaimKind,
    ResearchArtifactKind,
    ResearchDecisionKind,
    SourceQualityGrade,
    SourceSelection,
)


class ResearchRunCreate(BaseModel):
    brief_version_id: UUID
    content_id: UUID | None = None
    generation_job_id: UUID | None = None
    operation: str = Field(default="CONTENT_RESEARCH", min_length=1, max_length=40)
    questions: list[str]
    required_facts: list[dict[str, Any]] = Field(default_factory=list)
    queries: list[str]
    provider_keys: list[str]
    search_policy: dict[str, Any]
    source_policy: dict[str, Any]


class ResearchRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    generation_job_id: UUID | None
    content_id: UUID | None
    brief_version_id: UUID
    requested_by: UUID
    operation: str
    state: str
    plan_snapshot: dict[str, Any]
    plan_hash: str
    search_policy_snapshot: dict[str, Any]
    source_policy_snapshot: dict[str, Any]
    provider_keys: list[str]
    approved_source_set_hash: str | None
    approved_by: UUID | None
    approved_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ResearchArtifactCreate(BaseModel):
    query_id: UUID | None = None
    source_version_id: UUID | None = None
    artifact_kind: ResearchArtifactKind
    selection: SourceSelection = SourceSelection.USER_SELECTED
    selection_reason: str | None = None
    exclusion_reason: str | None = None
    grade: SourceQualityGrade
    title: str = Field(min_length=1, max_length=1_000)
    domain: str | None = Field(default=None, max_length=255)
    canonical_uri: str | None = None
    publisher: str | None = Field(default=None, max_length=500)
    published_at: datetime | None = None
    modified_at: datetime | None = None
    retrieved_at: datetime
    freshness_score: Decimal | None = Field(default=None, ge=0, le=1)
    freshness_policy_snapshot: dict[str, Any] = Field(default_factory=dict)
    rights_status: str = Field(min_length=1, max_length=32)
    use_scope: str = Field(min_length=1, max_length=32)
    quote_policy_snapshot: dict[str, Any] = Field(default_factory=dict)
    summary: str
    excerpt: str | None = None
    raw_object_ref: str | None = Field(default=None, max_length=1_000)
    provider: str | None = Field(default=None, max_length=120)
    provider_version: str | None = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def excluded_reason(self) -> "ResearchArtifactCreate":
        if self.selection is SourceSelection.EXCLUDED and not self.exclusion_reason:
            raise ValueError("excluded sources require an exclusion_reason")
        return self


class ResearchArtifactRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    research_run_id: UUID
    query_id: UUID | None
    source_version_id: UUID | None
    artifact_kind: str
    selection: str
    selection_reason: str | None
    exclusion_reason: str | None
    grade: str
    title: str
    domain: str | None
    canonical_uri: str | None
    publisher: str | None
    published_at: datetime | None
    modified_at: datetime | None
    retrieved_at: datetime
    freshness_score: Decimal | None
    rights_status: str
    use_scope: str
    summary: str
    excerpt: str | None
    excerpt_hash: str | None
    artifact_hash: str
    created_at: datetime


class CitationCreate(BaseModel):
    research_artifact_id: UUID | None = None
    source_version_id: UUID | None = None
    canonical_uri: str | None = None
    locator: dict[str, Any]
    excerpt: str | None = None
    style: CitationStyle = CitationStyle.LINK
    quote_policy_snapshot: dict[str, Any]
    publisher: str | None = None
    published_at: datetime | None = None
    modified_at: datetime | None = None
    retrieved_at: datetime

    @model_validator(mode="after")
    def evidence_target(self) -> "CitationCreate":
        if not any((self.research_artifact_id, self.source_version_id, self.canonical_uri)):
            raise ValueError("citation requires an artifact, source version or canonical URI")
        return self


class ClaimCreate(BaseModel):
    research_run_id: UUID | None = None
    claim_key: str = Field(min_length=1, max_length=160)
    block_key: UUID | None = None
    text_range: dict[str, Any] | None = None
    statement: str = Field(min_length=1)
    kind: ClaimKind
    confidence: Decimal | None = Field(default=None, ge=0, le=1)
    temporal_validity: dict[str, Any] = Field(default_factory=dict)
    user_verified: bool = False
    verification_policy_version: str = Field(min_length=1, max_length=80)
    citations: list[CitationCreate] = Field(default_factory=list)
    has_conflict: bool = False


class CitationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    claim_id: UUID
    research_artifact_id: UUID | None
    source_version_id: UUID | None
    canonical_uri: str | None
    locator: dict[str, Any]
    excerpt: str | None
    excerpt_hash: str
    evidence_hash: str
    style: str
    quote_word_count: int
    quote_policy_snapshot: dict[str, Any]
    retrieved_at: datetime


class ClaimRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    content_version_id: UUID
    research_run_id: UUID | None
    claim_key: str
    block_key: UUID | None
    text_range: dict[str, Any] | None
    statement: str
    kind: str
    status: str
    confidence: Decimal | None
    temporal_validity: dict[str, Any]
    user_verified: bool
    verification_policy_version: str
    claim_hash: str
    created_by: UUID
    created_at: datetime
    citations: list[CitationRead] = Field(default_factory=list)


class ClaimDecisionCreate(BaseModel):
    decision: ResearchDecisionKind
    reason: str = Field(min_length=1)
    replacement_claim_id: UUID | None = None
    evidence_snapshot: dict[str, Any] = Field(default_factory=dict)


class ResearchExportQuery(BaseModel):
    format: Literal["csv", "md", "json"] = "md"
