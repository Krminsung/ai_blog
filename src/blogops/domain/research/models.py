"""Research artifacts and the append-only claim/citation ledger."""

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
from blogops.domain.jobs.state import JobState
from blogops.domain.research.enums import (
    ClaimStatus,
    CitationStyle,
    SourceQualityGrade,
    SourceSelection,
)


class ResearchRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "research_runs"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="research_run_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "generation_job_id"],
            ["generation_jobs.workspace_id", "generation_jobs.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "content_id"],
            ["contents.workspace_id", "contents.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "brief_version_id"],
            ["content_brief_versions.workspace_id", "content_brief_versions.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id",
            "requested_by",
            "operation",
            "idempotency_key",
            name="research_run_idempotency",
        ),
        Index("ix_research_runs_state", "workspace_id", "state", "created_at"),
        Index("ix_research_runs_content", "workspace_id", "content_id", "created_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    generation_job_id: Mapped[UUID | None] = mapped_column(index=True)
    content_id: Mapped[UUID | None] = mapped_column(index=True)
    brief_version_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    requested_by: Mapped[UUID] = mapped_column(nullable=False, index=True)
    operation: Mapped[str] = mapped_column(String(40), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default=JobState.CREATED.value
    )
    plan_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    plan_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    search_policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    search_policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    source_policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_keys: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    approved_source_set_hash: Mapped[str | None] = mapped_column(String(64))
    approved_by: Mapped[UUID | None]
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(120))
    error_detail: Mapped[str | None] = mapped_column(Text)


class ResearchQuery(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "research_queries"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="research_query_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "research_run_id"],
            ["research_runs.workspace_id", "research_runs.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id", "research_run_id", "request_hash", name="research_query_request"
        ),
        Index("ix_research_queries_run", "workspace_id", "research_run_id", "executed_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    research_run_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(String(120), nullable=False)
    provider_version: Mapped[str] = mapped_column(String(80), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    cache_key: Mapped[str | None] = mapped_column(String(64))
    cache_hit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ResearchArtifact(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "research_artifacts"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="research_artifact_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "research_run_id"],
            ["research_runs.workspace_id", "research_runs.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "query_id"],
            ["research_queries.workspace_id", "research_queries.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "source_version_id"],
            ["source_versions.workspace_id", "source_versions.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id", "research_run_id", "artifact_hash", name="research_artifact_hash"
        ),
        CheckConstraint(
            "freshness_score IS NULL OR (freshness_score >= 0 AND freshness_score <= 1)",
            name="research_artifact_freshness_range",
        ),
        Index("ix_research_artifacts_grade", "workspace_id", "research_run_id", "grade"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    research_run_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    query_id: Mapped[UUID | None] = mapped_column(index=True)
    source_version_id: Mapped[UUID | None] = mapped_column(index=True)
    artifact_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    selection: Mapped[str] = mapped_column(
        String(24), nullable=False, default=SourceSelection.AUTO_SELECTED.value
    )
    selection_reason: Mapped[str | None] = mapped_column(Text)
    exclusion_reason: Mapped[str | None] = mapped_column(Text)
    grade: Mapped[str] = mapped_column(
        String(1), nullable=False, default=SourceQualityGrade.D.value
    )
    title: Mapped[str] = mapped_column(String(1_000), nullable=False)
    domain: Mapped[str | None] = mapped_column(String(255))
    canonical_uri: Mapped[str | None] = mapped_column(Text)
    publisher: Mapped[str | None] = mapped_column(String(500))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    freshness_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 5))
    freshness_policy_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    rights_status: Mapped[str] = mapped_column(String(32), nullable=False)
    use_scope: Mapped[str] = mapped_column(String(32), nullable=False)
    quote_policy_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    excerpt: Mapped[str | None] = mapped_column(Text)
    excerpt_hash: Mapped[str | None] = mapped_column(String(64))
    raw_object_ref: Mapped[str | None] = mapped_column(String(1_000))
    provider: Mapped[str | None] = mapped_column(String(120))
    provider_version: Mapped[str | None] = mapped_column(String(80))
    artifact_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ResearchConflict(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "research_conflicts"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="research_conflict_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "research_run_id"],
            ["research_runs.workspace_id", "research_runs.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id", "research_run_id", "conflict_hash", name="research_conflict_hash"
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    research_run_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    artifact_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    positions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    resolution: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    conflict_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Claim(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "claims"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="claim_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "content_version_id"],
            ["content_versions.workspace_id", "content_versions.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "research_run_id"],
            ["research_runs.workspace_id", "research_runs.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id", "content_version_id", "claim_key", name="claim_version_key"
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="claim_confidence_range",
        ),
        Index("ix_claims_status", "workspace_id", "content_version_id", "status"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    content_version_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    research_run_id: Mapped[UUID | None] = mapped_column(index=True)
    claim_key: Mapped[str] = mapped_column(String(160), nullable=False)
    block_key: Mapped[UUID | None] = mapped_column(index=True)
    text_range: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ClaimStatus.UNSUPPORTED.value
    )
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(6, 5))
    temporal_validity: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    user_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    verification_policy_version: Mapped[str] = mapped_column(String(80), nullable=False)
    claim_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Citation(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "citations"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="citation_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "claim_id"],
            ["claims.workspace_id", "claims.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "research_artifact_id"],
            ["research_artifacts.workspace_id", "research_artifacts.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "source_version_id"],
            ["source_versions.workspace_id", "source_versions.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id", "claim_id", "evidence_hash", name="citation_claim_evidence"
        ),
        CheckConstraint("quote_word_count >= 0", name="citation_quote_words_nonnegative"),
        Index("ix_citations_claim", "workspace_id", "claim_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    claim_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    research_artifact_id: Mapped[UUID | None] = mapped_column(index=True)
    source_version_id: Mapped[UUID | None] = mapped_column(index=True)
    canonical_uri: Mapped[str | None] = mapped_column(Text)
    locator: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    excerpt: Mapped[str | None] = mapped_column(Text)
    excerpt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    style: Mapped[str] = mapped_column(
        String(24), nullable=False, default=CitationStyle.LINK.value
    )
    quote_word_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quote_policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    publisher: Mapped[str | None] = mapped_column(String(500))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ClaimDecision(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "claim_decisions"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="claim_decision_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "claim_id"],
            ["claims.workspace_id", "claims.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "replacement_claim_id"],
            ["claims.workspace_id", "claims.id"],
            ondelete="RESTRICT",
        ),
        Index("ix_claim_decisions_claim", "workspace_id", "claim_id", "created_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    claim_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    replacement_claim_id: Mapped[UUID | None] = mapped_column(index=True)
    decision: Mapped[str] = mapped_column(String(40), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    decided_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ResearchCacheEntry(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "research_cache_entries"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="research_cache_workspace_id"),
        UniqueConstraint(
            "workspace_id",
            "provider",
            "request_hash",
            "policy_hash",
            name="research_cache_identity",
        ),
        Index("ix_research_cache_expiry", "workspace_id", "expires_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(120), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    result_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


def _reject_immutable_research_row(
    _mapper: object, _connection: object, target: object
) -> None:
    raise ValueError(f"{type(target).__name__} rows are immutable")


for _immutable_model in (
    ResearchQuery,
    ResearchArtifact,
    ResearchConflict,
    Claim,
    Citation,
    ClaimDecision,
    ResearchCacheEntry,
):
    event.listen(_immutable_model, "before_update", _reject_immutable_research_row)
    event.listen(_immutable_model, "before_delete", _reject_immutable_research_row)
