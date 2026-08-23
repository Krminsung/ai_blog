"""Validated API contracts for quality evidence, policy and approval."""

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from blogops.domain.quality.enums import (
    ApprovalDecisionKind,
    ApprovalRequestStatus,
    AssessmentDecision,
    CollaborationTarget,
    FindingSeverity,
    PolicyAction,
    PolicyLayer,
    ReportKind,
)
from blogops.domain.quality.rules import QUALITY_COMPONENT_WEIGHTS


class DomainSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True, str_strip_whitespace=True)


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        raise ValueError("timezone-aware datetime is required")
    return value


class AnalyzerPin(DomainSchema):
    analyzer_name: str = Field(min_length=1, max_length=120)
    analyzer_version: str = Field(min_length=1, max_length=80)
    model_name: str | None = Field(default=None, max_length=160)
    model_version: str | None = Field(default=None, max_length=120)
    dictionary_name: str | None = Field(default=None, max_length=160)
    dictionary_version: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def versions_are_paired(self) -> Self:
        if (self.model_name is None) != (self.model_version is None):
            raise ValueError("model_name and model_version must be supplied together")
        if (self.dictionary_name is None) != (self.dictionary_version is None):
            raise ValueError(
                "dictionary_name and dictionary_version must be supplied together"
            )
        return self


class QualityFinding(DomainSchema):
    code: str = Field(min_length=1, max_length=160)
    severity: FindingSeverity
    message: str = Field(min_length=1, max_length=10_000)
    location: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    remediation: str | None = Field(default=None, max_length=10_000)


class PolicyFindingInput(DomainSchema):
    event_key: str = Field(min_length=1, max_length=160)
    layer: PolicyLayer
    rule_code: str = Field(min_length=1, max_length=160)
    action: PolicyAction
    severity: FindingSeverity
    hard_block: bool = False
    override_allowed: bool = False
    message: str = Field(min_length=1, max_length=10_000)
    evidence: dict[str, Any] = Field(default_factory=dict)


class BaseReportCreate(DomainSchema):
    content_id: UUID
    content_version_id: UUID
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    analyzer: AnalyzerPin
    analyzer_config: dict[str, Any] = Field(default_factory=dict)
    rule_set_ids: list[UUID] = Field(default_factory=list, max_length=100)
    summary: dict[str, Any] = Field(default_factory=dict)
    findings: list[QualityFinding] = Field(default_factory=list, max_length=10_000)
    hard_blockers: list[PolicyFindingInput] = Field(
        default_factory=list, max_length=1_000
    )


class MorphologyDetail(DomainSchema):
    token_count: int = Field(ge=0)
    sentence_count: int = Field(ge=0)
    unknown_token_rate: float = Field(ge=0, le=1)
    spacing_issues: list[dict[str, Any]] = Field(default_factory=list, max_length=10_000)
    grammar_issues: list[dict[str, Any]] = Field(default_factory=list, max_length=10_000)
    token_analysis: list[dict[str, Any]] = Field(default_factory=list, max_length=100_000)
    metrics: dict[str, Any] = Field(default_factory=dict)


class MorphologyReportCreate(BaseReportCreate):
    detail: MorphologyDetail

    @model_validator(mode="after")
    def dictionary_is_pinned(self) -> Self:
        if self.analyzer.dictionary_name is None:
            raise ValueError("morphology reports require a pinned dictionary")
        return self


class NaturalnessDetail(DomainSchema):
    naturalness_score: float = Field(ge=0, le=100)
    usefulness_score: float = Field(ge=0, le=100)
    readability_score: float = Field(ge=0, le=100)
    brand_fit_score: float = Field(ge=0, le=100)
    fluency_metrics: dict[str, Any] = Field(default_factory=dict)
    sentence_metrics: dict[str, Any] = Field(default_factory=dict)
    awkward_expressions: list[dict[str, Any]] = Field(
        default_factory=list, max_length=10_000
    )


class NaturalnessReportCreate(BaseReportCreate):
    detail: NaturalnessDetail


class SEODetail(DomainSchema):
    search_intent_score: float = Field(ge=0, le=100)
    primary_keyword: str | None = Field(default=None, max_length=1_000)
    keyword_metrics: dict[str, Any] = Field(default_factory=dict)
    title_checks: list[dict[str, Any]] = Field(default_factory=list, max_length=1_000)
    heading_checks: list[dict[str, Any]] = Field(default_factory=list, max_length=5_000)
    meta_checks: list[dict[str, Any]] = Field(default_factory=list, max_length=1_000)
    recommendations: list[dict[str, Any]] = Field(default_factory=list, max_length=5_000)


class SEOReportCreate(BaseReportCreate):
    detail: SEODetail


class DuplicationDetail(DomainSchema):
    originality_score: float = Field(ge=0, le=100)
    duplicate_ratio: float = Field(ge=0, le=1)
    algorithm: str = Field(min_length=1, max_length=120)
    algorithm_version: str = Field(min_length=1, max_length=80)
    corpus_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    near_duplicates: list[dict[str, Any]] = Field(default_factory=list, max_length=10_000)
    cannibalization_findings: list[dict[str, Any]] = Field(
        default_factory=list, max_length=10_000
    )


class DuplicationReportCreate(BaseReportCreate):
    detail: DuplicationDetail


class FactCitationDetail(DomainSchema):
    accuracy_score: float = Field(ge=0, le=100)
    claim_count: int = Field(ge=0)
    supported_claim_count: int = Field(ge=0)
    citation_count: int = Field(ge=0)
    linked_citation_count: int = Field(ge=0)
    claim_citation_graph: list[dict[str, Any]] = Field(
        default_factory=list, max_length=100_000
    )
    unsupported_claims: list[dict[str, Any]] = Field(
        default_factory=list, max_length=10_000
    )
    invalid_citations: list[dict[str, Any]] = Field(
        default_factory=list, max_length=10_000
    )

    @model_validator(mode="after")
    def counts_are_coherent(self) -> Self:
        if self.supported_claim_count > self.claim_count:
            raise ValueError("supported_claim_count cannot exceed claim_count")
        if self.linked_citation_count > self.citation_count:
            raise ValueError("linked_citation_count cannot exceed citation_count")
        return self


class FactCitationReportCreate(BaseReportCreate):
    detail: FactCitationDetail


class SafetyPolicyDetail(DomainSchema):
    compliance_score: float = Field(ge=0, le=100)
    policy_findings: list[PolicyFindingInput] = Field(
        default_factory=list, max_length=10_000
    )
    safety_categories: dict[str, Any] = Field(default_factory=dict)
    required_disclosures: list[dict[str, Any]] = Field(
        default_factory=list, max_length=1_000
    )
    banned_claim_matches: list[dict[str, Any]] = Field(
        default_factory=list, max_length=10_000
    )


class SafetyPolicyReportCreate(BaseReportCreate):
    detail: SafetyPolicyDetail


class QualityReportRead(DomainSchema):
    id: UUID
    workspace_id: UUID
    content_id: UUID
    content_version_id: UUID
    content_hash: str
    report_kind: ReportKind
    analyzer_name: str
    analyzer_version: str
    model_name: str | None
    model_version: str | None
    dictionary_name: str | None
    dictionary_version: str | None
    input_hash: str
    analyzer_config_snapshot: dict[str, Any]
    analyzer_config_hash: str
    rule_snapshot: list[dict[str, Any]]
    rule_snapshot_hash: str
    policy_snapshot: dict[str, Any]
    policy_snapshot_hash: str
    summary_json: dict[str, Any]
    findings_json: list[dict[str, Any]]
    hard_blockers_json: list[dict[str, Any]]
    report_hash: str
    created_by: UUID
    created_at: datetime
    detail: dict[str, Any]


class RuleDefinition(DomainSchema):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_.-]{1,159}$")
    description: str = Field(min_length=1, max_length=10_000)
    severity: FindingSeverity
    hard_block: bool = False
    override_allowed: bool = False
    conditions: dict[str, Any] = Field(default_factory=dict)
    remediation: str | None = Field(default=None, max_length=10_000)


class RuleSetCreate(DomainSchema):
    expected_previous_version: int = Field(default=0, ge=0)
    layer: PolicyLayer
    name: str = Field(min_length=1, max_length=120)
    rules: list[RuleDefinition] = Field(min_length=1, max_length=10_000)
    analyzer_requirements: dict[str, Any] = Field(default_factory=dict)
    effective_at: datetime

    _validate_effective = field_validator("effective_at")(_aware)


class RuleSetRead(DomainSchema):
    id: UUID
    workspace_id: UUID
    layer: PolicyLayer
    name: str
    version: int
    rules_json: list[dict[str, Any]]
    analyzer_requirements_json: dict[str, Any]
    snapshot_hash: str
    effective_at: datetime
    created_by: UUID
    created_at: datetime


class ApprovalStageConfig(DomainSchema):
    key: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,62}[a-z0-9]$")
    name: str = Field(min_length=1, max_length=120)
    required_approvals: int = Field(default=1, ge=1, le=50)
    approver_user_ids: list[UUID] = Field(default_factory=list, max_length=500)
    require_mfa: bool = False
    due_seconds: int | None = Field(default=None, ge=60, le=31_536_000)


class QualityConfigCreate(DomainSchema):
    expected_previous_version: int = Field(default=0, ge=0)
    minimum_total_score: Decimal = Field(default=75, ge=0, le=100)
    minimum_component_scores: dict[str, Decimal] = Field(default_factory=dict)
    required_report_kinds: list[ReportKind] = Field(
        default_factory=lambda: list(ReportKind), min_length=1, max_length=6
    )
    approval_stages: list[ApprovalStageConfig] = Field(min_length=1, max_length=50)
    threshold_override_allowed: bool = False
    notes: str | None = Field(default=None, max_length=20_000)

    @model_validator(mode="after")
    def config_is_coherent(self) -> Self:
        unknown = set(self.minimum_component_scores).difference(QUALITY_COMPONENT_WEIGHTS)
        if unknown:
            raise ValueError(f"unknown quality components: {sorted(unknown)}")
        if len(self.required_report_kinds) != len(set(self.required_report_kinds)):
            raise ValueError("required report kinds must be unique")
        keys = [stage.key for stage in self.approval_stages]
        if len(keys) != len(set(keys)):
            raise ValueError("approval stage keys must be unique")
        return self


class QualityConfigRead(DomainSchema):
    id: UUID
    workspace_id: UUID
    version: int
    minimum_total_score: Decimal
    minimum_component_scores: dict[str, Any]
    required_report_kinds: list[ReportKind]
    approval_stages: list[dict[str, Any]]
    threshold_override_allowed: bool
    notes: str | None
    config_hash: str
    created_by: UUID
    created_at: datetime


class QualityAssessmentCreate(DomainSchema):
    content_id: UUID
    content_version_id: UUID
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    quality_config_id: UUID
    report_ids: dict[ReportKind, UUID]


class QualityAssessmentRead(DomainSchema):
    id: UUID
    workspace_id: UUID
    content_id: UUID
    content_version_id: UUID
    content_hash: str
    quality_config_id: UUID
    quality_config_snapshot: dict[str, Any]
    quality_config_hash: str
    report_manifest: list[dict[str, Any]]
    component_scores: dict[str, Any]
    component_weights: dict[str, Any]
    weighted_contributions: dict[str, Any]
    total_score: Decimal
    formula_version: str
    failed_thresholds: dict[str, Any]
    blocking_policy_event_ids: list[str]
    non_overrideable_policy_event_ids: list[str]
    decision: AssessmentDecision
    assessment_hash: str
    created_by: UUID
    created_at: datetime


class PolicyEventRead(DomainSchema):
    id: UUID
    workspace_id: UUID
    content_id: UUID
    content_version_id: UUID
    content_hash: str
    report_id: UUID | None
    assessment_id: UUID | None
    event_key: str
    layer: PolicyLayer
    rule_code: str
    action: PolicyAction
    severity: FindingSeverity
    hard_block: bool
    override_allowed: bool
    priority: int
    message: str
    evidence_json: dict[str, Any]
    rule_snapshot_hash: str
    policy_snapshot_hash: str
    event_hash: str
    created_by: UUID
    created_at: datetime


class PolicyOverrideCreate(DomainSchema):
    expected_event_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str = Field(min_length=10, max_length=20_000)
    evidence: dict[str, Any] = Field(default_factory=dict)


class PolicyOverrideRead(DomainSchema):
    id: UUID
    workspace_id: UUID
    policy_event_id: UUID
    reason: str
    evidence_json: dict[str, Any]
    event_snapshot_hash: str
    status: str
    overridden_by: UUID
    created_at: datetime


class ApprovalRequestCreate(DomainSchema):
    content_id: UUID
    content_version_id: UUID
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    assessment_id: UUID
    expected_assessment_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    supersedes_request_id: UUID | None = None


class ApprovalDecisionCreate(DomainSchema):
    expected_lock_version: int = Field(ge=1)
    expected_content_version_id: UUID
    expected_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: ApprovalDecisionKind
    comment: str | None = Field(default=None, max_length=20_000)

    @model_validator(mode="after")
    def negative_decisions_have_a_reason(self) -> Self:
        if self.decision is not ApprovalDecisionKind.APPROVE and not self.comment:
            raise ValueError("rejection and change requests require a comment")
        return self


class ApprovalInvalidationCreate(DomainSchema):
    new_content_version_id: UUID
    new_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str = Field(min_length=1, max_length=20_000)


class ApprovalRequestRead(DomainSchema):
    id: UUID
    workspace_id: UUID
    content_id: UUID
    content_version_id: UUID
    content_hash: str
    assessment_id: UUID
    assessment_hash: str
    quality_config_id: UUID
    quality_config_hash: str
    approval_stages_snapshot: list[dict[str, Any]]
    approval_stages_hash: str
    status: ApprovalRequestStatus
    current_stage_index: int
    stage_due_at: datetime | None
    supersedes_request_id: UUID | None
    requested_by: UUID
    requested_at: datetime
    approved_by: UUID | None
    approved_at: datetime | None
    approved_content_version_id: UUID | None
    approved_content_hash: str | None
    invalidated_at: datetime | None
    invalidated_by: UUID | None
    invalidation_reason: str | None
    lock_version: int


class ApprovalDecisionRead(DomainSchema):
    id: UUID
    workspace_id: UUID
    approval_request_id: UUID
    content_version_id: UUID
    content_hash: str
    stage_key: str
    stage_index: int
    decision: ApprovalDecisionKind
    from_status: ApprovalRequestStatus
    to_status: ApprovalRequestStatus
    comment: str | None
    authentication_method: str
    decided_by: UUID
    decided_at: datetime


class ApprovalDecisionResultRead(DomainSchema):
    request: ApprovalRequestRead
    decision: ApprovalDecisionRead | None


class ApprovalProofRead(DomainSchema):
    approval_request_id: UUID
    content_id: UUID
    content_version_id: UUID
    content_hash: str
    assessment_id: UUID
    assessment_hash: str
    approved_by: UUID
    approved_at: datetime
    approval_stages_hash: str
    quality_config_hash: str


class QualityCommentCreate(DomainSchema):
    target_type: CollaborationTarget
    target_id: UUID
    parent_comment_id: UUID | None = None
    body: str = Field(min_length=1, max_length=20_000)
    mentioned_user_ids: list[UUID] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def mentions_are_unique(self) -> Self:
        if len(self.mentioned_user_ids) != len(set(self.mentioned_user_ids)):
            raise ValueError("mentioned users must be unique")
        return self


class QualityCommentResolve(DomainSchema):
    expected_lock_version: int = Field(ge=1)
    resolved: bool = True


class QualityCommentRead(DomainSchema):
    id: UUID
    workspace_id: UUID
    target_type: CollaborationTarget
    target_id: UUID
    content_id: UUID | None
    content_version_id: UUID | None
    parent_comment_id: UUID | None
    body: str
    author_id: UUID
    resolved_at: datetime | None
    resolved_by: UUID | None
    lock_version: int
    created_at: datetime
    mentioned_user_ids: list[UUID] = Field(default_factory=list)


class ActivityRead(DomainSchema):
    id: UUID
    workspace_id: UUID
    content_id: UUID | None
    content_version_id: UUID | None
    activity_kind: str
    target_type: str
    target_id: UUID
    details_json: dict[str, Any]
    actor_id: UUID
    created_at: datetime
