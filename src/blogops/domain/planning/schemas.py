"""Validated DTOs for content strategy, briefs and calendar APIs."""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from blogops.domain.planning.enums import (
    AssignmentStage,
    BriefStatus,
    BudgetCategory,
    BudgetEnforcement,
    CalendarConflictResolution,
    CampaignStatus,
    DecisionKind,
    IdeaStatus,
    IntentSource,
    JourneyStage,
    ProposalStatus,
    RecurrenceFrequency,
    SearchIntent,
    TopicNodeKind,
    TopicNodeStatus,
)
from blogops.domain.planning.rules import MAX_IDEA_CANDIDATES


class DomainSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True, str_strip_whitespace=True)


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        raise ValueError("timezone-aware datetime is required")
    return value


class BudgetLimit(DomainSchema):
    category: BudgetCategory
    amount: Decimal = Field(ge=0, max_digits=19, decimal_places=4)
    currency: str = Field(default="KRW", pattern=r"^[A-Z]{3}$")


class ReferenceSelection(DomainSchema):
    brand_id: UUID | None = None
    persona_id: UUID | None = None
    product_ids: list[UUID] = Field(default_factory=list, max_length=100)
    knowledge_source_ids: list[UUID] = Field(default_factory=list, max_length=200)
    primary_keyword_id: UUID | None = None
    keyword_cluster_id: UUID | None = None
    primary_keyword_text: str | None = Field(default=None, max_length=500)
    secondary_keyword_ids: list[UUID] = Field(default_factory=list, max_length=200)
    secondary_keyword_texts: list[str] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def primary_keyword_is_present(self) -> Self:
        if self.primary_keyword_id is None and not self.primary_keyword_text:
            raise ValueError("primary_keyword_id or primary_keyword_text is required")
        return self


class CampaignCreate(DomainSchema):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=20_000)
    objective: str = Field(min_length=1, max_length=10_000)
    brand_id: UUID | None = None
    channels: list[str] = Field(min_length=1, max_length=30)
    start_date: date
    end_date: date
    timezone: str = Field(default="Asia/Seoul", min_length=1, max_length=64)
    budget_limits: list[BudgetLimit] = Field(default_factory=list, max_length=3)
    budget_enforcement: BudgetEnforcement = BudgetEnforcement.WARN

    @model_validator(mode="after")
    def dates_and_budgets_are_valid(self) -> Self:
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        categories = [item.category for item in self.budget_limits]
        if len(categories) != len(set(categories)):
            raise ValueError("budget category must be unique")
        if len(set(self.channels)) != len(self.channels):
            raise ValueError("channels must be unique")
        return self


class CampaignUpdate(DomainSchema):
    expected_lock_version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=20_000)
    objective: str | None = Field(default=None, min_length=1, max_length=10_000)
    channels: list[str] | None = Field(default=None, min_length=1, max_length=30)
    start_date: date | None = None
    end_date: date | None = None
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    budget_limits: list[BudgetLimit] | None = Field(default=None, max_length=3)
    budget_enforcement: BudgetEnforcement | None = None
    status: CampaignStatus | None = None

    @model_validator(mode="after")
    def includes_change(self) -> Self:
        if not self.model_fields_set.difference({"expected_lock_version"}):
            raise ValueError("at least one campaign field must be supplied")
        return self


class CampaignRead(DomainSchema):
    id: UUID
    workspace_id: UUID
    name: str
    description: str | None
    objective: str
    brand_id: UUID | None
    channels: list[str]
    start_date: date
    end_date: date
    timezone: str
    budget_limits: dict[str, Any]
    budget_enforcement: BudgetEnforcement
    generation_policy_hash: str
    approval_policy_hash: str
    status: CampaignStatus
    created_by: UUID
    lock_version: int
    created_at: datetime
    updated_at: datetime


class SpendRecordCreate(DomainSchema):
    expected_campaign_lock_version: int = Field(ge=1)
    category: BudgetCategory
    amount: Decimal = Field(gt=0, max_digits=19, decimal_places=4)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    source_ref: str = Field(min_length=1, max_length=255)
    details: dict[str, Any] = Field(default_factory=dict)


class SpendDecisionRead(DomainSchema):
    decision: str
    projected: Decimal
    limit: Decimal
    ledger_id: UUID | None = None


class TopicNodeCreate(DomainSchema):
    campaign_id: UUID | None = None
    parent_id: UUID | None = None
    node_kind: TopicNodeKind
    name: str = Field(min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=20_000)
    keyword_id: UUID | None = None
    keyword_cluster_id: UUID | None = None
    keyword_text: str | None = Field(default=None, max_length=500)
    search_intent: SearchIntent
    intent_source: IntentSource = IntentSource.USER
    journey_stage: JourneyStage
    cta_recommendation: dict[str, Any] = Field(default_factory=dict)
    existing_content_refs: list[dict[str, Any]] = Field(default_factory=list, max_length=200)
    internal_link_recommendations: list[dict[str, Any]] = Field(
        default_factory=list, max_length=200
    )
    content_gap_summary: str | None = Field(default=None, max_length=20_000)
    seasonality: dict[str, Any] = Field(default_factory=dict)
    refresh_interval_days: int | None = Field(default=None, ge=1, le=3650)
    sort_order: int = Field(default=0, ge=0)


class TopicNodeMove(DomainSchema):
    expected_lock_version: int = Field(ge=1)
    parent_id: UUID | None = None
    sort_order: int = Field(ge=0)


class TopicIntentUpdate(DomainSchema):
    expected_lock_version: int = Field(ge=1)
    search_intent: SearchIntent
    journey_stage: JourneyStage
    reason: str | None = Field(default=None, max_length=5_000)


class TopicMergeRequest(DomainSchema):
    source_node_ids: list[UUID] = Field(min_length=1, max_length=100)
    expected_source_versions: dict[UUID, int]
    expected_target_lock_version: int = Field(ge=1)

    @model_validator(mode="after")
    def every_source_has_a_version(self) -> Self:
        if len(self.source_node_ids) != len(set(self.source_node_ids)):
            raise ValueError("source_node_ids must be unique")
        if set(self.source_node_ids) != set(self.expected_source_versions):
            raise ValueError("every source node must have an expected lock version")
        return self


class TopicSplitRequest(DomainSchema):
    expected_source_lock_version: int = Field(ge=1)
    new_node: TopicNodeCreate
    child_node_ids: list[UUID] = Field(default_factory=list, max_length=100)


class TopicNodeRead(DomainSchema):
    id: UUID
    workspace_id: UUID
    campaign_id: UUID | None
    parent_id: UUID | None
    node_kind: TopicNodeKind
    name: str
    description: str | None
    keyword_id: UUID | None
    keyword_cluster_id: UUID | None
    keyword_snapshot: dict[str, Any]
    keyword_snapshot_hash: str
    search_intent: SearchIntent
    intent_source: IntentSource
    journey_stage: JourneyStage
    cta_recommendation: dict[str, Any]
    existing_content_refs: list[dict[str, Any]]
    internal_link_recommendations: list[dict[str, Any]]
    content_gap_summary: str | None
    seasonality: dict[str, Any]
    refresh_interval_days: int | None
    sort_order: int
    status: TopicNodeStatus
    merged_into_id: UUID | None
    lock_version: int


class IdeaCandidate(DomainSchema):
    title: str = Field(min_length=1, max_length=500)
    rationale: str | None = Field(default=None, max_length=10_000)
    primary_keyword_id: UUID | None = None
    keyword_cluster_id: UUID | None = None
    primary_keyword_text: str | None = Field(default=None, max_length=500)
    semantic_group_key: str | None = Field(default=None, max_length=500)
    search_intent: SearchIntent
    journey_stage: JourneyStage
    recommended_cta: dict[str, Any] = Field(default_factory=dict)
    source_signals: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    performance_signals: dict[str, Any] = Field(default_factory=dict)


class IdeaBatchCreate(DomainSchema):
    campaign_id: UUID | None = None
    topic_node_id: UUID | None = None
    brand_id: UUID | None = None
    persona_id: UUID | None = None
    product_ids: list[UUID] = Field(default_factory=list, max_length=100)
    knowledge_source_ids: list[UUID] = Field(default_factory=list, max_length=200)
    candidates: list[IdeaCandidate] = Field(min_length=1, max_length=MAX_IDEA_CANDIDATES)


class ContentIdeaRead(DomainSchema):
    id: UUID
    workspace_id: UUID
    campaign_id: UUID | None
    topic_node_id: UUID | None
    title: str
    rationale: str | None
    primary_keyword_id: UUID | None
    keyword_cluster_id: UUID | None
    search_intent: SearchIntent
    journey_stage: JourneyStage
    recommended_cta: dict[str, Any]
    source_signals: list[dict[str, Any]]
    performance_signals: dict[str, Any]
    reference_snapshot_hash: str
    duplicate_key: str
    status: IdeaStatus
    lock_version: int
    created_at: datetime


class SuppressedIdea(DomainSchema):
    candidate_index: int
    duplicate_key: str
    reason: Literal["BATCH_DUPLICATE", "EXISTING_IDEA"]
    representative_idea_id: UUID | None = None


class IdeaBatchRead(DomainSchema):
    created: list[ContentIdeaRead]
    suppressed: list[SuppressedIdea]


class RequiredFact(DomainSchema):
    fact_key: str = Field(min_length=1, max_length=160)
    statement: str = Field(min_length=1, max_length=5_000)
    value: Any | None = None
    source_reference: str = Field(min_length=1, max_length=2_048)
    approved_by: UUID | None = None
    approved_at: datetime | None = None
    expires_at: datetime | None = None
    lock_for_generation: Literal[True] = True

    _validate_approved = field_validator("approved_at", "expires_at")(_aware)


class BannedClaim(DomainSchema):
    pattern: str = Field(min_length=1, max_length=2_000)
    reason: str = Field(min_length=1, max_length=2_000)
    severity: Literal["WARN", "BLOCK"] = "BLOCK"
    source: Literal["WORKSPACE", "BRAND", "PRODUCT", "REGULATION", "USER"]


class OutlineSection(DomainSchema):
    heading: str = Field(min_length=1, max_length=500)
    level: int = Field(default=2, ge=1, le=6)
    purpose: str | None = Field(default=None, max_length=2_000)
    required_points: list[str] = Field(default_factory=list, max_length=100)


class CTAPlan(DomainSchema):
    label: str = Field(min_length=1, max_length=500)
    destination_url: str | None = Field(default=None, max_length=2_048)
    conversion_goal: str = Field(min_length=1, max_length=500)
    journey_stage: JourneyStage
    channel: str = Field(min_length=1, max_length=80)
    placement: Literal["INTRO", "BODY", "END", "MULTIPLE"] = "END"


class InternalLinkPlan(DomainSchema):
    target_ref: str = Field(min_length=1, max_length=2_048)
    anchor_suggestion: str = Field(min_length=1, max_length=500)
    relationship: Literal["PILLAR", "CLUSTER", "PRODUCT", "RELATED", "UPDATE"]


class ImagePlan(DomainSchema):
    placement: Literal["COVER", "BODY", "CTA", "SOCIAL"]
    description: str = Field(min_length=1, max_length=2_000)
    source: Literal["GENERATE", "UPLOAD", "OFFICIAL_PRODUCT", "STOCK"]
    requires_real_photo: bool = False
    alt_text: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def real_photos_are_not_generated(self) -> Self:
        if self.requires_real_photo and self.source == "GENERATE":
            raise ValueError("a required real photo cannot use GENERATE source")
        return self


class ApprovalStage(DomainSchema):
    key: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,62}[a-z0-9]$")
    name: str = Field(min_length=1, max_length=100)
    required_approvals: int = Field(default=1, ge=1, le=20)
    approver_user_ids: list[UUID] = Field(default_factory=list, max_length=100)
    require_mfa: bool = False


class AssignmentInput(DomainSchema):
    stage: AssignmentStage
    user_id: UUID
    due_at: datetime | None = None
    sla_seconds: int | None = Field(default=None, gt=0, le=31_536_000)

    _validate_due = field_validator("due_at")(_aware)


class BriefPayload(DomainSchema):
    template_ref: str | None = Field(default=None, max_length=255)
    title: str = Field(min_length=1, max_length=500)
    objective: str = Field(min_length=1, max_length=20_000)
    references: ReferenceSelection
    search_intent: SearchIntent
    journey_stage: JourneyStage
    questions: list[str] = Field(default_factory=list, max_length=200)
    competitor_gap_summary: str | None = Field(default=None, max_length=20_000)
    required_facts: list[RequiredFact] = Field(default_factory=list, max_length=500)
    banned_claims: list[BannedClaim] = Field(default_factory=list, max_length=500)
    outline: list[OutlineSection] = Field(min_length=1, max_length=200)
    cta_plan: list[CTAPlan] = Field(default_factory=list, max_length=50)
    internal_link_plan: list[InternalLinkPlan] = Field(default_factory=list, max_length=200)
    image_plan: list[ImagePlan] = Field(default_factory=list, max_length=100)
    approval_stages: list[ApprovalStage] = Field(default_factory=list, max_length=20)
    channel: str = Field(min_length=1, max_length=80)
    language: str = Field(default="ko-KR", min_length=2, max_length=16)
    tone: dict[str, Any] = Field(default_factory=dict)
    target_length_min: int = Field(default=1_500, ge=100, le=100_000)
    target_length_max: int = Field(default=3_000, ge=100, le=100_000)
    disclosures: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def brief_is_coherent(self) -> Self:
        if self.target_length_max < self.target_length_min:
            raise ValueError("target_length_max must be at least target_length_min")
        stage_keys = [stage.key for stage in self.approval_stages]
        if len(stage_keys) != len(set(stage_keys)):
            raise ValueError("approval stage keys must be unique")
        fact_keys = [fact.fact_key for fact in self.required_facts]
        if len(fact_keys) != len(set(fact_keys)):
            raise ValueError("required fact keys must be unique")
        return self


class BriefCreate(DomainSchema):
    campaign_id: UUID | None = None
    idea_id: UUID | None = None
    topic_node_id: UUID | None = None
    board_column_id: UUID | None = None
    payload: BriefPayload
    assignments: list[AssignmentInput] = Field(default_factory=list, max_length=100)


class BriefVersionCreate(DomainSchema):
    expected_lock_version: int = Field(ge=1)
    payload: BriefPayload
    assignments: list[AssignmentInput] | None = Field(default=None, max_length=100)


class BriefVersionRead(DomainSchema):
    id: UUID
    workspace_id: UUID
    brief_id: UUID
    version_number: int
    template_ref: str | None
    title: str
    objective: str
    audience_snapshot: dict[str, Any]
    search_intent: SearchIntent
    journey_stage: JourneyStage
    keyword_snapshot: dict[str, Any]
    questions: list[str]
    knowledge_source_snapshot: list[dict[str, Any]]
    competitor_gap_summary: str | None
    required_facts: list[dict[str, Any]]
    banned_claims: list[dict[str, Any]]
    outline: list[dict[str, Any]]
    cta_plan: list[dict[str, Any]]
    internal_link_plan: list[dict[str, Any]]
    image_plan: list[dict[str, Any]]
    approval_stages: list[dict[str, Any]]
    channel: str
    language: str
    tone: dict[str, Any]
    target_length_min: int
    target_length_max: int
    disclosures: list[str]
    reference_snapshot_hash: str
    generation_policy_hash: str
    approval_policy_hash: str
    snapshot_hash: str
    created_by: UUID
    created_at: datetime


class BriefRead(DomainSchema):
    id: UUID
    workspace_id: UUID
    campaign_id: UUID | None
    idea_id: UUID | None
    topic_node_id: UUID | None
    current_version_id: UUID | None
    board_column_id: UUID | None
    status: BriefStatus
    approval_stage_index: int
    next_refresh_at: datetime | None
    created_by: UUID
    lock_version: int
    created_at: datetime
    updated_at: datetime
    current_version: BriefVersionRead | None = None


class BriefTransitionRequest(DomainSchema):
    expected_lock_version: int = Field(ge=1)
    comment: str | None = Field(default=None, max_length=20_000)


class BriefDecisionRequest(BriefTransitionRequest):
    decision: DecisionKind


class AssignmentUpsert(DomainSchema):
    expected_brief_lock_version: int = Field(ge=1)
    assignments: list[AssignmentInput] = Field(max_length=100)


class AssignmentRead(DomainSchema):
    id: UUID
    workspace_id: UUID
    brief_id: UUID
    stage: AssignmentStage
    user_id: UUID
    due_at: datetime | None
    sla_seconds: int | None
    status: str
    lock_version: int


class CommentCreate(DomainSchema):
    target_type: Literal["BRIEF", "TOPIC_NODE", "CALENDAR_ENTRY"]
    target_id: UUID
    parent_comment_id: UUID | None = None
    body: str = Field(min_length=1, max_length=20_000)


class CommentResolve(DomainSchema):
    expected_lock_version: int = Field(ge=1)
    resolved: bool = True


class CommentRead(DomainSchema):
    id: UUID
    workspace_id: UUID
    target_type: str
    target_id: UUID
    parent_comment_id: UUID | None
    body: str
    author_id: UUID
    resolved_at: datetime | None
    resolved_by: UUID | None
    lock_version: int
    created_at: datetime


class BoardColumnCreate(DomainSchema):
    key: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,62}[a-z0-9]$")
    name: str = Field(min_length=1, max_length=100)
    kind: Literal["BACKLOG", "ACTIVE", "REVIEW", "DONE"] = "ACTIVE"
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    position: int = Field(ge=0)


class BoardColumnRead(DomainSchema):
    id: UUID
    workspace_id: UUID
    key: str
    name: str
    kind: str
    color: str | None
    position: int
    is_system: bool
    lock_version: int


class BriefBoardMove(DomainSchema):
    expected_brief_lock_version: int = Field(ge=1)
    board_column_id: UUID


class CalendarEntryCreate(DomainSchema):
    campaign_id: UUID | None = None
    idea_id: UUID | None = None
    brief_id: UUID | None = None
    title: str | None = Field(default=None, max_length=500)
    channel: str = Field(min_length=1, max_length=80)
    language: str = Field(default="ko-KR", min_length=2, max_length=16)
    timezone: str = Field(default="Asia/Seoul", min_length=1, max_length=64)
    scheduled_at: datetime
    due_at: datetime | None = None
    conflict_resolution: CalendarConflictResolution = CalendarConflictResolution.BLOCK

    _validate_datetimes = field_validator("scheduled_at", "due_at")(_aware)

    @model_validator(mode="after")
    def links_or_title_are_present(self) -> Self:
        if self.brief_id is None and self.idea_id is None and not self.title:
            raise ValueError("brief_id, idea_id or title is required")
        if self.due_at is not None and self.due_at < self.scheduled_at:
            raise ValueError("due_at must be on or after scheduled_at")
        return self


class CalendarEntryMove(DomainSchema):
    expected_lock_version: int = Field(ge=1)
    scheduled_at: datetime
    timezone: str = Field(min_length=1, max_length=64)
    conflict_resolution: CalendarConflictResolution = CalendarConflictResolution.BLOCK

    _validate_scheduled = field_validator("scheduled_at")(_aware)


class CalendarEntryRead(DomainSchema):
    id: UUID
    workspace_id: UUID
    campaign_id: UUID | None
    idea_id: UUID | None
    brief_id: UUID | None
    brief_version_id: UUID | None
    recurrence_id: UUID | None
    title_snapshot: str
    brief_snapshot_hash: str | None
    channel: str
    language: str
    timezone: str
    scheduled_at: datetime
    due_at: datetime | None
    status: str
    conflict_warnings: list[dict[str, Any]]
    lock_version: int


class RecurrenceCreate(DomainSchema):
    campaign_id: UUID | None = None
    brief_id: UUID | None = None
    title: str | None = Field(default=None, max_length=500)
    channel: str = Field(min_length=1, max_length=80)
    language: str = Field(default="ko-KR", min_length=2, max_length=16)
    frequency: RecurrenceFrequency
    interval: int = Field(default=1, ge=1, le=52)
    timezone: str = Field(default="Asia/Seoul", min_length=1, max_length=64)
    starts_at: datetime
    ends_at: datetime | None = None
    recurrence_config: dict[str, Any] = Field(default_factory=dict)
    exception_dates: list[date] = Field(default_factory=list, max_length=366)
    conflict_resolution: CalendarConflictResolution = CalendarConflictResolution.BLOCK

    _validate_datetimes = field_validator("starts_at", "ends_at")(_aware)

    @model_validator(mode="after")
    def recurrence_is_bounded(self) -> Self:
        if self.ends_at is not None and self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at")
        if self.brief_id is None and not self.title:
            raise ValueError("brief_id or title is required")
        return self


class RecurrenceRead(DomainSchema):
    id: UUID
    workspace_id: UUID
    campaign_id: UUID | None
    brief_id: UUID | None
    frequency: str
    interval: int
    timezone: str
    starts_at: datetime
    ends_at: datetime | None
    recurrence_config: dict[str, Any]
    exception_dates: list[str]
    active: bool
    lock_version: int


class MonthlyPlanSeed(DomainSchema):
    topic: str = Field(min_length=1, max_length=500)
    primary_keyword_id: UUID | None = None
    keyword_cluster_id: UUID | None = None
    semantic_group_key: str | None = Field(default=None, max_length=500)
    search_intent: SearchIntent = SearchIntent.INFORMATIONAL
    journey_stage: JourneyStage = JourneyStage.AWARENESS
    preferred_channel: str | None = Field(default=None, max_length=80)


class MonthlyPlanProposalCreate(DomainSchema):
    campaign_id: UUID | None = None
    month: date
    goal: str = Field(min_length=1, max_length=20_000)
    requested_budget: list[BudgetLimit] = Field(default_factory=list, max_length=3)
    seeds: list[MonthlyPlanSeed] = Field(min_length=1, max_length=MAX_IDEA_CANDIDATES)
    timezone: str = Field(default="Asia/Seoul", min_length=1, max_length=64)

    @field_validator("month")
    @classmethod
    def month_is_first_day(cls, value: date) -> date:
        if value.day != 1:
            raise ValueError("month must be the first day of a month")
        return value


class MonthlyPlanItem(DomainSchema):
    title: str = Field(min_length=1, max_length=500)
    scheduled_at: datetime
    timezone: str = Field(min_length=1, max_length=64)
    channel: str = Field(min_length=1, max_length=80)
    language: str = Field(default="ko-KR", min_length=2, max_length=16)
    brief_id: UUID | None = None
    primary_keyword_id: UUID | None = None
    keyword_cluster_id: UUID | None = None
    semantic_group_key: str | None = Field(default=None, max_length=500)
    search_intent: SearchIntent
    journey_stage: JourneyStage
    assignee_user_ids: list[UUID] = Field(default_factory=list, max_length=50)

    _validate_scheduled = field_validator("scheduled_at")(_aware)


class MonthlyPlanProposalRevise(DomainSchema):
    expected_lock_version: int = Field(ge=1)
    expected_proposal_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    proposed_items: list[MonthlyPlanItem] = Field(min_length=1, max_length=MAX_IDEA_CANDIDATES)


class MonthlyPlanDecision(DomainSchema):
    expected_lock_version: int = Field(ge=1)
    expected_proposal_version: int = Field(ge=1)
    expected_proposal_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    comment: str | None = Field(default=None, max_length=20_000)
    conflict_resolution: CalendarConflictResolution = CalendarConflictResolution.BLOCK


class MonthlyPlanProposalRead(DomainSchema):
    id: UUID
    workspace_id: UUID
    campaign_id: UUID | None
    month: date
    goal: str
    requested_budget: dict[str, Any]
    proposed_items: list[dict[str, Any]]
    provider: str
    provider_version: str
    generation_policy_hash: str
    approval_policy_hash: str
    proposal_version: int
    proposal_hash: str
    status: ProposalStatus
    created_by: UUID
    approved_by: UUID | None
    approved_at: datetime | None
    approved_version: int | None
    approved_hash: str | None
    lock_version: int


class MonthlyPlanApprovalRead(DomainSchema):
    proposal: MonthlyPlanProposalRead
    calendar_entries: list[CalendarEntryRead]


class GenerationBriefInput(DomainSchema):
    brief_id: UUID
    brief_version_id: UUID
    version_number: int
    snapshot_hash: str
    reference_snapshot_hash: str
    generation_policy_hash: str
    approved_at: datetime
    payload: dict[str, Any]


class CalendarExportQuery(DomainSchema):
    starts_at: datetime
    ends_at: datetime
    timezone: str = Field(default="Asia/Seoul", min_length=1, max_length=64)
    campaign_id: UUID | None = None
    channel: str | None = Field(default=None, max_length=80)

    _validate_datetimes = field_validator("starts_at", "ends_at")(_aware)

    @model_validator(mode="after")
    def range_is_ordered(self) -> Self:
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at")
        return self


class MessageRead(DomainSchema):
    message: str
