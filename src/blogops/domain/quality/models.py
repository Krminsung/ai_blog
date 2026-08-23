"""Tenant-owned immutable quality evidence and exact-version approval persistence."""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
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
from blogops.domain.quality.enums import (
    ApprovalRequestStatus,
    AssessmentDecision,
    OverrideStatus,
)


class QualityRuleSet(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "quality_rule_sets"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="quality_rule_set_workspace_id"),
        UniqueConstraint(
            "workspace_id", "layer", "name", "version", name="quality_rule_set_version"
        ),
        UniqueConstraint(
            "workspace_id", "layer", "name", "snapshot_hash", name="quality_rule_set_hash"
        ),
        CheckConstraint("version > 0", name="quality_rule_set_version_positive"),
        Index("ix_quality_rule_sets_lookup", "workspace_id", "layer", "name", "version"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    layer: Mapped[str] = mapped_column(String(24), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    rules_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    analyzer_requirements_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class WorkspaceQualityConfig(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "workspace_quality_configs"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="quality_config_workspace_id"),
        UniqueConstraint("workspace_id", "version", name="quality_config_version"),
        UniqueConstraint("workspace_id", "config_hash", name="quality_config_hash"),
        CheckConstraint("version > 0", name="quality_config_version_positive"),
        CheckConstraint(
            "minimum_total_score >= 0 AND minimum_total_score <= 100",
            name="quality_config_total_range",
        ),
        Index("ix_quality_config_latest", "workspace_id", "version"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    minimum_total_score: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False
    )
    minimum_component_scores: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    required_report_kinds: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    approval_stages: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    threshold_override_allowed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    notes: Mapped[str | None] = mapped_column(Text)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class QualityReport(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "quality_reports"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="quality_report_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "content_id"],
            ["contents.workspace_id", "contents.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "content_version_id", "content_hash"],
            [
                "content_versions.workspace_id",
                "content_versions.id",
                "content_versions.content_hash",
            ],
            name="fk_quality_report_exact_content_version",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id",
            "content_version_id",
            "report_kind",
            "report_hash",
            name="quality_report_version_kind_hash",
        ),
        Index(
            "ix_quality_report_content_version",
            "workspace_id",
            "content_version_id",
            "report_kind",
            "created_at",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    content_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    content_version_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    report_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    analyzer_name: Mapped[str] = mapped_column(String(120), nullable=False)
    analyzer_version: Mapped[str] = mapped_column(String(80), nullable=False)
    model_name: Mapped[str | None] = mapped_column(String(160))
    model_version: Mapped[str | None] = mapped_column(String(120))
    dictionary_name: Mapped[str | None] = mapped_column(String(160))
    dictionary_version: Mapped[str | None] = mapped_column(String(120))
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    analyzer_config_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    analyzer_config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    rule_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    rule_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    policy_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    summary_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    findings_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    hard_blockers_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    report_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MorphologyReport(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "morphology_reports"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "report_id"],
            ["quality_reports.workspace_id", "quality_reports.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("workspace_id", "report_id", name="morphology_report_parent"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    report_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    sentence_count: Mapped[int] = mapped_column(Integer, nullable=False)
    unknown_token_rate: Mapped[float] = mapped_column(Float, nullable=False)
    spacing_issues: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    grammar_issues: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    token_analysis: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    metrics_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )


class NaturalnessReport(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "naturalness_reports"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "report_id"],
            ["quality_reports.workspace_id", "quality_reports.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("workspace_id", "report_id", name="naturalness_report_parent"),
        CheckConstraint(
            "naturalness_score >= 0 AND naturalness_score <= 100",
            name="naturalness_score_range",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    report_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    naturalness_score: Mapped[float] = mapped_column(Float, nullable=False)
    usefulness_score: Mapped[float] = mapped_column(Float, nullable=False)
    readability_score: Mapped[float] = mapped_column(Float, nullable=False)
    brand_fit_score: Mapped[float] = mapped_column(Float, nullable=False)
    fluency_metrics: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    sentence_metrics: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    awkward_expressions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )


class SEOReport(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "seo_reports"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "report_id"],
            ["quality_reports.workspace_id", "quality_reports.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("workspace_id", "report_id", name="seo_report_parent"),
        CheckConstraint(
            "search_intent_score >= 0 AND search_intent_score <= 100",
            name="seo_intent_score_range",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    report_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    search_intent_score: Mapped[float] = mapped_column(Float, nullable=False)
    primary_keyword: Mapped[str | None] = mapped_column(String(1000))
    keyword_metrics: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    title_checks: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    heading_checks: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    meta_checks: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    recommendations: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )


class DuplicationReport(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "duplication_reports"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "report_id"],
            ["quality_reports.workspace_id", "quality_reports.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("workspace_id", "report_id", name="duplication_report_parent"),
        CheckConstraint(
            "originality_score >= 0 AND originality_score <= 100",
            name="duplication_originality_range",
        ),
        CheckConstraint(
            "duplicate_ratio >= 0 AND duplicate_ratio <= 1",
            name="duplication_ratio_range",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    report_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    originality_score: Mapped[float] = mapped_column(Float, nullable=False)
    duplicate_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    algorithm: Mapped[str] = mapped_column(String(120), nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(80), nullable=False)
    corpus_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    near_duplicates: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    cannibalization_findings: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )


class FactCitationReport(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "fact_citation_reports"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "report_id"],
            ["quality_reports.workspace_id", "quality_reports.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("workspace_id", "report_id", name="fact_citation_report_parent"),
        CheckConstraint(
            "accuracy_score >= 0 AND accuracy_score <= 100",
            name="fact_citation_accuracy_range",
        ),
        CheckConstraint(
            "citation_link_rate >= 0 AND citation_link_rate <= 100",
            name="fact_citation_link_rate_range",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    report_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    accuracy_score: Mapped[float] = mapped_column(Float, nullable=False)
    claim_count: Mapped[int] = mapped_column(Integer, nullable=False)
    supported_claim_count: Mapped[int] = mapped_column(Integer, nullable=False)
    citation_count: Mapped[int] = mapped_column(Integer, nullable=False)
    linked_citation_count: Mapped[int] = mapped_column(Integer, nullable=False)
    citation_link_rate: Mapped[float] = mapped_column(Float, nullable=False)
    claim_citation_graph: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    unsupported_claims: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    invalid_citations: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )


class SafetyPolicyReport(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "safety_policy_reports"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "report_id"],
            ["quality_reports.workspace_id", "quality_reports.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("workspace_id", "report_id", name="safety_policy_report_parent"),
        CheckConstraint(
            "compliance_score >= 0 AND compliance_score <= 100",
            name="safety_compliance_score_range",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    report_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    compliance_score: Mapped[float] = mapped_column(Float, nullable=False)
    policy_findings: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    safety_categories: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    required_disclosures: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    banned_claim_matches: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )


class QualityAssessment(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "quality_assessments"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="quality_assessment_workspace_id"),
        UniqueConstraint(
            "workspace_id",
            "id",
            "content_id",
            "content_version_id",
            "content_hash",
            name="quality_assessment_exact_identity",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "content_id"],
            ["contents.workspace_id", "contents.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "content_version_id", "content_hash"],
            [
                "content_versions.workspace_id",
                "content_versions.id",
                "content_versions.content_hash",
            ],
            name="fk_quality_assessment_exact_content_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "quality_config_id"],
            ["workspace_quality_configs.workspace_id", "workspace_quality_configs.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id",
            "content_version_id",
            "assessment_hash",
            name="quality_assessment_version_hash",
        ),
        CheckConstraint(
            "total_score >= 0 AND total_score <= 100",
            name="quality_assessment_total_range",
        ),
        Index(
            "ix_quality_assessment_content_version",
            "workspace_id",
            "content_version_id",
            "created_at",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    content_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    content_version_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    quality_config_id: Mapped[UUID] = mapped_column(nullable=False)
    quality_config_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    quality_config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    report_manifest: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    component_scores: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    component_weights: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    weighted_contributions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    total_score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    formula_version: Mapped[str] = mapped_column(String(40), nullable=False)
    failed_thresholds: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    blocking_policy_event_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    non_overrideable_policy_event_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    decision: Mapped[str] = mapped_column(
        String(24), nullable=False, default=AssessmentDecision.NEEDS_REVISION.value
    )
    assessment_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class QualityAssessmentReport(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "quality_assessment_reports"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "assessment_id"],
            ["quality_assessments.workspace_id", "quality_assessments.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "report_id"],
            ["quality_reports.workspace_id", "quality_reports.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id", "assessment_id", "report_kind", name="quality_assessment_report_kind"
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    assessment_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    report_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    report_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    report_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class PolicyEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "policy_events"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="policy_event_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "content_id"],
            ["contents.workspace_id", "contents.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "content_version_id", "content_hash"],
            [
                "content_versions.workspace_id",
                "content_versions.id",
                "content_versions.content_hash",
            ],
            name="fk_policy_event_exact_content_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "report_id"],
            ["quality_reports.workspace_id", "quality_reports.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "assessment_id"],
            ["quality_assessments.workspace_id", "quality_assessments.id"],
            ondelete="RESTRICT",
        ),
        Index(
            "ix_policy_event_content_version",
            "workspace_id",
            "content_version_id",
            "priority",
            "created_at",
        ),
        UniqueConstraint(
            "workspace_id", "report_id", "event_key", name="policy_event_report_key"
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    content_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    content_version_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    report_id: Mapped[UUID | None] = mapped_column(index=True)
    assessment_id: Mapped[UUID | None] = mapped_column(index=True)
    event_key: Mapped[str] = mapped_column(String(160), nullable=False)
    layer: Mapped[str] = mapped_column(String(24), nullable=False)
    rule_code: Mapped[str] = mapped_column(String(160), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    hard_block: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    override_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    rule_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    event_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PolicyOverride(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "quality_policy_overrides"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "policy_event_id"],
            ["policy_events.workspace_id", "policy_events.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("workspace_id", "policy_event_id", name="quality_policy_override_event"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    policy_event_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    event_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=OverrideStatus.ACTIVE.value
    )
    overridden_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ApprovalRequest(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "content_approval_requests"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="approval_request_workspace_id"),
        UniqueConstraint(
            "workspace_id",
            "id",
            "content_id",
            "content_version_id",
            "content_hash",
            name="approval_request_exact_identity",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "content_id"],
            ["contents.workspace_id", "contents.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "workspace_id",
                "approved_content_version_id",
                "approved_content_hash",
            ],
            [
                "content_versions.workspace_id",
                "content_versions.id",
                "content_versions.content_hash",
            ],
            name="fk_approval_request_approved_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "content_version_id", "content_hash"],
            [
                "content_versions.workspace_id",
                "content_versions.id",
                "content_versions.content_hash",
            ],
            name="fk_approval_request_content_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "assessment_id"],
            ["quality_assessments.workspace_id", "quality_assessments.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "quality_config_id"],
            ["workspace_quality_configs.workspace_id", "workspace_quality_configs.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "supersedes_request_id"],
            ["content_approval_requests.workspace_id", "content_approval_requests.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id",
            "content_version_id",
            "assessment_id",
            name="approval_request_version_assessment",
        ),
        CheckConstraint("current_stage_index >= 0", name="approval_stage_index_nonnegative"),
        CheckConstraint("lock_version > 0", name="approval_request_lock_positive"),
        Index("ix_approval_request_content", "workspace_id", "content_id", "status"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    content_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    content_version_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    assessment_id: Mapped[UUID] = mapped_column(nullable=False)
    assessment_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    quality_config_id: Mapped[UUID] = mapped_column(nullable=False)
    quality_config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    approval_stages_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False
    )
    approval_stages_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ApprovalRequestStatus.PENDING.value
    )
    current_stage_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stage_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    supersedes_request_id: Mapped[UUID | None] = mapped_column(index=True)
    requested_by: Mapped[UUID] = mapped_column(nullable=False)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    approved_by: Mapped[UUID | None]
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_content_version_id: Mapped[UUID | None]
    approved_content_hash: Mapped[str | None] = mapped_column(String(64))
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invalidated_by: Mapped[UUID | None]
    invalidation_reason: Mapped[str | None] = mapped_column(Text)
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class ApprovalDecision(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "content_approval_decisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "approval_request_id"],
            ["content_approval_requests.workspace_id", "content_approval_requests.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "content_version_id", "content_hash"],
            [
                "content_versions.workspace_id",
                "content_versions.id",
                "content_versions.content_hash",
            ],
            name="fk_approval_decision_exact_content_version",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id",
            "approval_request_id",
            "stage_key",
            "decided_by",
            name="approval_decision_actor_stage",
        ),
        Index(
            "ix_approval_decision_timeline",
            "workspace_id",
            "approval_request_id",
            "decided_at",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    approval_request_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    content_version_id: Mapped[UUID] = mapped_column(nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    stage_key: Mapped[str] = mapped_column(String(80), nullable=False)
    stage_index: Mapped[int] = mapped_column(Integer, nullable=False)
    decision: Mapped[str] = mapped_column(String(24), nullable=False)
    from_status: Mapped[str] = mapped_column(String(32), nullable=False)
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    authentication_method: Mapped[str] = mapped_column(String(40), nullable=False)
    decided_by: Mapped[UUID] = mapped_column(nullable=False)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ApprovalStateEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "content_approval_state_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "approval_request_id"],
            ["content_approval_requests.workspace_id", "content_approval_requests.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "content_version_id", "content_hash"],
            [
                "content_versions.workspace_id",
                "content_versions.id",
                "content_versions.content_hash",
            ],
            name="fk_approval_state_event_exact_content_version",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_approval_state_event_timeline",
            "workspace_id",
            "approval_request_id",
            "created_at",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    approval_request_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(32))
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    content_version_id: Mapped[UUID] = mapped_column(nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    details_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    actor_id: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class QualityComment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "quality_comments"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="quality_comment_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "parent_comment_id"],
            ["quality_comments.workspace_id", "quality_comments.id"],
            ondelete="RESTRICT",
        ),
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
        CheckConstraint("lock_version > 0", name="quality_comment_lock_positive"),
        Index(
            "ix_quality_comment_target",
            "workspace_id",
            "target_type",
            "target_id",
            "created_at",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    content_id: Mapped[UUID | None] = mapped_column(index=True)
    content_version_id: Mapped[UUID | None] = mapped_column(index=True)
    parent_comment_id: Mapped[UUID | None] = mapped_column(index=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    author_id: Mapped[UUID] = mapped_column(nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[UUID | None]
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class QualityMention(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "quality_mentions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "comment_id"],
            ["quality_comments.workspace_id", "quality_comments.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "workspace_id", "comment_id", "mentioned_user_id", name="quality_mention_user"
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    comment_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    mentioned_user_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    mentioned_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class QualityActivity(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "quality_activity_events"
    __table_args__ = (
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
        Index(
            "ix_quality_activity_workspace_time",
            "workspace_id",
            "created_at",
        ),
        Index(
            "ix_quality_activity_content_time",
            "workspace_id",
            "content_id",
            "created_at",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    content_id: Mapped[UUID | None] = mapped_column(index=True)
    content_version_id: Mapped[UUID | None] = mapped_column(index=True)
    activity_kind: Mapped[str] = mapped_column(String(48), nullable=False)
    target_type: Mapped[str] = mapped_column(String(40), nullable=False)
    target_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    details_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    actor_id: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


def _reject_immutable_quality_row(
    _mapper: object, _connection: object, target: object
) -> None:
    raise ValueError(f"{type(target).__name__} rows are immutable")


for _immutable_model in (
    QualityRuleSet,
    WorkspaceQualityConfig,
    QualityReport,
    MorphologyReport,
    NaturalnessReport,
    SEOReport,
    DuplicationReport,
    FactCitationReport,
    SafetyPolicyReport,
    QualityAssessment,
    QualityAssessmentReport,
    PolicyEvent,
    PolicyOverride,
    ApprovalDecision,
    ApprovalStateEvent,
    QualityMention,
    QualityActivity,
):
    event.listen(_immutable_model, "before_update", _reject_immutable_quality_row)
    event.listen(_immutable_model, "before_delete", _reject_immutable_quality_row)
