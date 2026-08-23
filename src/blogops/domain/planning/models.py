"""Tenant-owned content strategy, brief and calendar persistence models.

Cross-domain entities are referenced by UUID plus immutable JSON snapshots rather than foreign
keys. Planning therefore remains reproducible when keyword, brand, product or knowledge roots
change later, and generation consumes one exact approved brief version.
"""

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
from blogops.domain.planning.enums import (
    AssignmentStatus,
    BoardColumnKind,
    BriefStatus,
    BudgetEnforcement,
    CalendarEntryStatus,
    CampaignStatus,
    IdeaStatus,
    IntentSource,
    ProposalStatus,
    TopicNodeStatus,
)


class Campaign(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "campaigns"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="planning_campaign_workspace_id"),
        UniqueConstraint("workspace_id", "name", name="planning_campaign_workspace_name"),
        CheckConstraint("lock_version > 0", name="planning_campaign_lock_positive"),
        CheckConstraint("end_date >= start_date", name="planning_campaign_dates_ordered"),
        Index("ix_planning_campaign_workspace_status", "workspace_id", "status"),
        Index("ix_planning_campaign_workspace_dates", "workspace_id", "start_date", "end_date"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    brand_id: Mapped[UUID | None] = mapped_column(index=True)
    brand_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    brand_snapshot_hash: Mapped[str | None] = mapped_column(String(64))
    channels: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    budget_limits: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    budget_enforcement: Mapped[str] = mapped_column(
        String(16), nullable=False, default=BudgetEnforcement.WARN.value
    )
    generation_policy_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    generation_policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    approval_policy_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    approval_policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=CampaignStatus.DRAFT.value
    )
    created_by: Mapped[UUID] = mapped_column(nullable=False, index=True)
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class CampaignSpendLedger(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "campaign_spend_ledger"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "campaign_id"],
            ["campaigns.workspace_id", "campaigns.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("workspace_id", "campaign_id", "source_ref", name="campaign_spend_source"),
        CheckConstraint("amount >= 0", name="campaign_spend_nonnegative"),
        CheckConstraint("char_length(currency) = 3", name="campaign_spend_currency_length"),
        Index("ix_campaign_spend_workspace_campaign", "workspace_id", "campaign_id", "occurred_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    campaign_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(24), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(19, 4), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    recorded_by: Mapped[UUID | None] = mapped_column(index=True)


class TopicNode(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "topic_clusters"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="planning_topic_node_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "campaign_id"],
            ["campaigns.workspace_id", "campaigns.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "parent_id"],
            ["topic_clusters.workspace_id", "topic_clusters.id"],
            name="fk_planning_topic_parent",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "merged_into_id"],
            ["topic_clusters.workspace_id", "topic_clusters.id"],
            name="fk_planning_topic_merged_into",
            ondelete="RESTRICT",
        ),
        CheckConstraint("sort_order >= 0", name="planning_topic_node_sort_nonnegative"),
        CheckConstraint("lock_version > 0", name="planning_topic_node_lock_positive"),
        Index("ix_planning_topic_tree", "workspace_id", "parent_id", "sort_order"),
        Index("ix_planning_topic_keyword_cluster", "workspace_id", "keyword_cluster_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    campaign_id: Mapped[UUID | None] = mapped_column(index=True)
    parent_id: Mapped[UUID | None] = mapped_column(index=True)
    node_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    keyword_id: Mapped[UUID | None] = mapped_column(index=True)
    keyword_cluster_id: Mapped[UUID | None] = mapped_column(index=True)
    keyword_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    keyword_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    search_intent: Mapped[str] = mapped_column(String(24), nullable=False)
    intent_source: Mapped[str] = mapped_column(
        String(16), nullable=False, default=IntentSource.RULE.value
    )
    journey_stage: Mapped[str] = mapped_column(String(24), nullable=False)
    cta_recommendation: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    existing_content_refs: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    internal_link_recommendations: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    content_gap_summary: Mapped[str | None] = mapped_column(Text)
    seasonality: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    refresh_interval_days: Mapped[int | None] = mapped_column(Integer)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=TopicNodeStatus.ACTIVE.value
    )
    merged_into_id: Mapped[UUID | None]
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class TopicIntentRevision(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "topic_intent_revisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "topic_node_id"],
            ["topic_clusters.workspace_id", "topic_clusters.id"],
            ondelete="CASCADE",
        ),
        Index("ix_topic_intent_revision_node", "workspace_id", "topic_node_id", "created_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    topic_node_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    previous_intent: Mapped[str] = mapped_column(String(24), nullable=False)
    revised_intent: Mapped[str] = mapped_column(String(24), nullable=False)
    previous_journey_stage: Mapped[str] = mapped_column(String(24), nullable=False)
    revised_journey_stage: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    revised_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ContentIdea(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "content_ideas"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="planning_idea_workspace_id"),
        UniqueConstraint("workspace_id", "duplicate_key", name="planning_idea_duplicate_key"),
        ForeignKeyConstraint(
            ["workspace_id", "campaign_id"],
            ["campaigns.workspace_id", "campaigns.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "topic_node_id"],
            ["topic_clusters.workspace_id", "topic_clusters.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("lock_version > 0", name="planning_idea_lock_positive"),
        Index("ix_planning_idea_workspace_status", "workspace_id", "status"),
        Index("ix_planning_idea_campaign", "workspace_id", "campaign_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    campaign_id: Mapped[UUID | None] = mapped_column(index=True)
    topic_node_id: Mapped[UUID | None] = mapped_column(index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text)
    primary_keyword_id: Mapped[UUID | None] = mapped_column(index=True)
    keyword_cluster_id: Mapped[UUID | None] = mapped_column(index=True)
    search_intent: Mapped[str] = mapped_column(String(24), nullable=False)
    journey_stage: Mapped[str] = mapped_column(String(24), nullable=False)
    recommended_cta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    source_signals: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    performance_signals: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    reference_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    reference_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    duplicate_key: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=IdeaStatus.SUGGESTED.value
    )
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class PlanningBoardColumn(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "content_board_columns"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="planning_board_column_workspace_id"),
        UniqueConstraint("workspace_id", "key", name="planning_board_column_key"),
        UniqueConstraint("workspace_id", "position", name="planning_board_column_position"),
        CheckConstraint("position >= 0", name="board_position_nonnegative"),
        CheckConstraint("lock_version > 0", name="planning_board_column_lock_positive"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default=BoardColumnKind.ACTIVE.value
    )
    color: Mapped[str | None] = mapped_column(String(16))
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class ContentBrief(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "content_briefs"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="planning_brief_workspace_id"),
        UniqueConstraint("workspace_id", "idea_id", name="planning_brief_workspace_idea"),
        ForeignKeyConstraint(
            ["workspace_id", "campaign_id"],
            ["campaigns.workspace_id", "campaigns.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "idea_id"],
            ["content_ideas.workspace_id", "content_ideas.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "topic_node_id"],
            ["topic_clusters.workspace_id", "topic_clusters.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "current_version_id"],
            ["content_brief_versions.workspace_id", "content_brief_versions.id"],
            name="fk_planning_brief_current_version",
            ondelete="RESTRICT",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "board_column_id"],
            ["content_board_columns.workspace_id", "content_board_columns.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("lock_version > 0", name="planning_brief_lock_positive"),
        CheckConstraint("approval_stage_index >= 0", name="planning_brief_stage_nonnegative"),
        Index("ix_planning_brief_workspace_status", "workspace_id", "status"),
        Index("ix_planning_brief_campaign", "workspace_id", "campaign_id"),
        Index("ix_planning_brief_refresh", "workspace_id", "next_refresh_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    campaign_id: Mapped[UUID | None] = mapped_column(index=True)
    idea_id: Mapped[UUID | None] = mapped_column(index=True)
    topic_node_id: Mapped[UUID | None] = mapped_column(index=True)
    current_version_id: Mapped[UUID | None] = mapped_column(index=True)
    board_column_id: Mapped[UUID | None] = mapped_column(index=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=BriefStatus.DRAFT.value
    )
    approval_stage_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_refresh_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class BriefVersion(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "content_brief_versions"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="planning_brief_version_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "brief_id"],
            ["content_briefs.workspace_id", "content_briefs.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("workspace_id", "brief_id", "version_number", name="planning_brief_version_no"),
        UniqueConstraint("workspace_id", "brief_id", "snapshot_hash", name="planning_brief_snapshot_hash"),
        CheckConstraint("version_number > 0", name="planning_brief_version_positive"),
        CheckConstraint("target_length_min > 0", name="planning_brief_target_min_positive"),
        CheckConstraint("target_length_max >= target_length_min", name="planning_brief_target_ordered"),
        Index("ix_planning_brief_version_brief", "workspace_id", "brief_id", "version_number"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    brief_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    template_ref: Mapped[str | None] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    audience_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    search_intent: Mapped[str] = mapped_column(String(24), nullable=False)
    journey_stage: Mapped[str] = mapped_column(String(24), nullable=False)
    keyword_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    questions: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    knowledge_source_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    competitor_gap_summary: Mapped[str | None] = mapped_column(Text)
    required_facts: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    banned_claims: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    outline: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    cta_plan: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    internal_link_plan: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    image_plan: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    approval_stages: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    channel: Mapped[str] = mapped_column(String(80), nullable=False)
    language: Mapped[str] = mapped_column(String(16), nullable=False)
    tone: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    target_length_min: Mapped[int] = mapped_column(Integer, nullable=False)
    target_length_max: Mapped[int] = mapped_column(Integer, nullable=False)
    disclosures: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    reference_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    reference_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    generation_policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    generation_policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    approval_policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    approval_policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BriefDecision(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "content_brief_decisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "brief_id"],
            ["content_briefs.workspace_id", "content_briefs.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "brief_version_id"],
            ["content_brief_versions.workspace_id", "content_brief_versions.id"],
            ondelete="RESTRICT",
        ),
        Index("ix_planning_brief_decision_timeline", "workspace_id", "brief_id", "decided_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    brief_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    brief_version_id: Mapped[UUID] = mapped_column(nullable=False)
    stage_key: Mapped[str] = mapped_column(String(80), nullable=False)
    decision: Mapped[str] = mapped_column(String(24), nullable=False)
    from_status: Mapped[str] = mapped_column(String(32), nullable=False)
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[UUID] = mapped_column(nullable=False)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PlanningAssignment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "content_brief_assignments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "brief_id"],
            ["content_briefs.workspace_id", "content_briefs.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("workspace_id", "brief_id", "stage", "user_id", name="planning_assignment_identity"),
        CheckConstraint("sla_seconds IS NULL OR sla_seconds > 0", name="planning_assignment_sla_positive"),
        CheckConstraint("lock_version > 0", name="planning_assignment_lock_positive"),
        Index("ix_planning_assignment_user_due", "workspace_id", "user_id", "due_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    brief_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    stage: Mapped[str] = mapped_column(String(24), nullable=False)
    user_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sla_seconds: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=AssignmentStatus.PENDING.value
    )
    assigned_by: Mapped[UUID] = mapped_column(nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class PlanningComment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "planning_comments"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="planning_comment_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "parent_comment_id"],
            ["planning_comments.workspace_id", "planning_comments.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint("lock_version > 0", name="planning_comment_lock_positive"),
        Index("ix_planning_comment_target", "workspace_id", "target_type", "target_id", "created_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    parent_comment_id: Mapped[UUID | None]
    body: Mapped[str] = mapped_column(Text, nullable=False)
    author_id: Mapped[UUID] = mapped_column(nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[UUID | None]
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class CalendarRecurrence(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "content_calendar_recurrences"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="planning_recurrence_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "campaign_id"],
            ["campaigns.workspace_id", "campaigns.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "brief_id"],
            ["content_briefs.workspace_id", "content_briefs.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("interval > 0", name="recurrence_interval_positive"),
        CheckConstraint("ends_at IS NULL OR ends_at > starts_at", name="recurrence_dates_ordered"),
        CheckConstraint("lock_version > 0", name="recurrence_lock_positive"),
        Index("ix_planning_recurrence_workspace_active", "workspace_id", "active"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    campaign_id: Mapped[UUID | None] = mapped_column(index=True)
    brief_id: Mapped[UUID | None] = mapped_column(index=True)
    frequency: Mapped[str] = mapped_column(String(24), nullable=False)
    interval: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recurrence_config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    exception_dates: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class CalendarEntry(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "content_calendar_items"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="planning_calendar_entry_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "campaign_id"],
            ["campaigns.workspace_id", "campaigns.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "idea_id"],
            ["content_ideas.workspace_id", "content_ideas.id"],
            ondelete="RESTRICT",
        ),
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
            ["workspace_id", "recurrence_id"],
            ["content_calendar_recurrences.workspace_id", "content_calendar_recurrences.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("lock_version > 0", name="planning_calendar_entry_lock_positive"),
        Index("ix_planning_calendar_workspace_time", "workspace_id", "scheduled_at"),
        Index("ix_planning_calendar_channel_time", "workspace_id", "channel", "scheduled_at"),
        Index("ix_planning_calendar_campaign", "workspace_id", "campaign_id", "scheduled_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    campaign_id: Mapped[UUID | None] = mapped_column(index=True)
    idea_id: Mapped[UUID | None] = mapped_column(index=True)
    brief_id: Mapped[UUID | None] = mapped_column(index=True)
    brief_version_id: Mapped[UUID | None] = mapped_column(index=True)
    recurrence_id: Mapped[UUID | None] = mapped_column(index=True)
    title_snapshot: Mapped[str] = mapped_column(String(500), nullable=False)
    brief_snapshot_hash: Mapped[str | None] = mapped_column(String(64))
    channel: Mapped[str] = mapped_column(String(80), nullable=False)
    language: Mapped[str] = mapped_column(String(16), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=CalendarEntryStatus.PLANNED.value
    )
    conflict_warnings: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class MonthlyPlanProposal(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "monthly_plan_proposals"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="monthly_plan_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "campaign_id"],
            ["campaigns.workspace_id", "campaigns.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("workspace_id", "campaign_id", "month", "proposal_version", name="monthly_plan_version"),
        CheckConstraint("proposal_version > 0", name="monthly_plan_version_positive"),
        CheckConstraint("lock_version > 0", name="monthly_plan_lock_positive"),
        Index("ix_monthly_plan_workspace_month", "workspace_id", "month", "status"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    campaign_id: Mapped[UUID | None] = mapped_column(index=True)
    month: Mapped[date] = mapped_column(Date, nullable=False)
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    requested_budget: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    seed_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    proposed_items: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    provider: Mapped[str] = mapped_column(String(120), nullable=False)
    provider_version: Mapped[str] = mapped_column(String(80), nullable=False)
    generation_policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    generation_policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    approval_policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    approval_policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    proposal_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    proposal_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ProposalStatus.PENDING_APPROVAL.value
    )
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    approved_by: Mapped[UUID | None]
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_version: Mapped[int | None]
    approved_hash: Mapped[str | None] = mapped_column(String(64))
    rejected_by: Mapped[UUID | None]
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


def _reject_immutable_planning_row(
    _mapper: object, _connection: object, target: object
) -> None:
    raise ValueError(f"{type(target).__name__} rows are immutable")


for _immutable_model in (
    CampaignSpendLedger,
    TopicIntentRevision,
    BriefVersion,
    BriefDecision,
):
    event.listen(_immutable_model, "before_update", _reject_immutable_planning_row)
    event.listen(_immutable_model, "before_delete", _reject_immutable_planning_row)
