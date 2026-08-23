"""Focused contracts for tenant planning, approval snapshots and calendar rules."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from blogops.domain.planning.enums import (
    BriefEvent,
    BriefStatus,
    BudgetEnforcement,
    CalendarConflictResolution,
)
from blogops.domain.planning.models import (
    BriefVersion,
    CalendarEntry,
    Campaign,
    ContentBrief,
    MonthlyPlanProposal,
    PlanningBoardColumn,
)
from blogops.domain.planning.rules import (
    CalendarSlot,
    InvalidPlanningTransition,
    canonical_json_hash,
    detect_calendar_conflict,
    evaluate_budget,
    idea_duplicate_key,
    resolve_calendar_slot,
    transition_brief_status,
)
from blogops.domain.planning.schemas import ImagePlan, MonthlyPlanProposalCreate, RequiredFact


def test_core_planning_table_names_match_contract() -> None:
    assert Campaign.__tablename__ == "campaigns"
    assert ContentBrief.__tablename__ == "content_briefs"
    assert CalendarEntry.__tablename__ == "content_calendar_items"
    assert BriefVersion.__tablename__ == "content_brief_versions"


def test_policy_and_approved_snapshot_fields_are_persisted() -> None:
    assert {
        "generation_policy_snapshot",
        "generation_policy_hash",
        "approval_policy_snapshot",
        "approval_policy_hash",
    }.issubset(Campaign.__table__.columns.keys())
    assert {
        "reference_snapshot",
        "reference_snapshot_hash",
        "generation_policy_snapshot",
        "generation_policy_hash",
        "approval_policy_snapshot",
        "approval_policy_hash",
        "snapshot_hash",
    }.issubset(BriefVersion.__table__.columns.keys())
    assert {
        "approved_by",
        "approved_at",
        "approved_version",
        "approved_hash",
    }.issubset(MonthlyPlanProposal.__table__.columns.keys())


def test_custom_board_column_is_separate_from_lifecycle_status() -> None:
    assert "status" in ContentBrief.__table__.columns
    assert "board_column_id" in ContentBrief.__table__.columns
    assert "key" in PlanningBoardColumn.__table__.columns
    assert "status" not in PlanningBoardColumn.__table__.columns


def test_brief_transition_requires_valid_state_event_pair() -> None:
    assert (
        transition_brief_status(BriefStatus.DRAFT, BriefEvent.SUBMIT)
        is BriefStatus.WAITING_REVIEW
    )
    assert (
        transition_brief_status(BriefStatus.WAITING_REVIEW, BriefEvent.APPROVE_FINAL)
        is BriefStatus.APPROVED
    )
    with pytest.raises(InvalidPlanningTransition):
        transition_brief_status(BriefStatus.DRAFT, BriefEvent.SCHEDULE)


def test_canonical_hash_is_order_independent() -> None:
    assert canonical_json_hash({"b": [2, 1], "a": {"ko": True}}) == canonical_json_hash(
        {"a": {"ko": True}, "b": [2, 1]}
    )


def test_thousand_keyword_fixture_collapses_180_semantic_duplicates() -> None:
    shared_cluster = uuid4()
    keys = [
        idea_duplicate_key(
            title=f"공통 표현 {index}",
            intent="INFORMATIONAL",
            keyword_cluster_id=shared_cluster,
            semantic_group_key=None,
            primary_keyword=f"공통 키워드 {index}",
        )
        for index in range(180)
    ]
    keys.extend(
        idea_duplicate_key(
            title=f"고유 주제 {index}",
            intent="INFORMATIONAL",
            keyword_cluster_id=None,
            semantic_group_key=f"고유 의미군 {index}",
            primary_keyword=None,
        )
        for index in range(820)
    )
    assert len(keys) == 1_000
    assert len(set(keys)) == 821


def test_calendar_daily_capacity_uses_workspace_local_day() -> None:
    existing = [
        CalendarSlot(
            entry_id=uuid4(),
            scheduled_at=datetime(2026, 1, 1, 0, 15, tzinfo=UTC),
        )
    ]
    conflict = detect_calendar_conflict(
        datetime(2025, 12, 31, 23, 45, tzinfo=UTC),
        existing,
        minimum_spacing=timedelta(0),
        maximum_per_local_day=1,
        timezone="Asia/Seoul",
    )
    assert conflict is not None
    assert conflict.kind == "DAILY_CHANNEL_CAPACITY"


def test_calendar_auto_spread_returns_a_free_workspace_local_day() -> None:
    requested = datetime(2026, 1, 1, 0, 30, tzinfo=UTC)
    existing = [CalendarSlot(entry_id=uuid4(), scheduled_at=requested)]
    resolved, original_conflict = resolve_calendar_slot(
        requested,
        existing,
        resolution=CalendarConflictResolution.AUTO_SPREAD,
        minimum_spacing=timedelta(minutes=60),
        maximum_per_local_day=1,
        timezone="Asia/Seoul",
    )
    assert original_conflict is not None
    assert resolved.astimezone(UTC) > requested
    assert resolved.astimezone(ZoneInfo("Asia/Seoul")).date() > requested.astimezone(
        ZoneInfo("Asia/Seoul")
    ).date()


def test_budget_policy_returns_configured_enforcement_decision() -> None:
    result = evaluate_budget(
        spent=Decimal("90"),
        requested=Decimal("20"),
        limit=Decimal("100"),
        enforcement=BudgetEnforcement.PAUSE,
    )
    assert result.decision.value == "PAUSE"
    assert result.projected == Decimal("110")


def test_required_fact_cannot_be_unlocked() -> None:
    with pytest.raises(ValidationError):
        RequiredFact(
            fact_key="price",
            statement="현재 가격은 10,000원",
            source_reference="product:version:1",
            lock_for_generation=False,
        )


def test_real_photo_requirement_blocks_generated_image_source() -> None:
    with pytest.raises(ValidationError):
        ImagePlan(
            placement="COVER",
            description="실제 상품 포장 사진",
            source="GENERATE",
            requires_real_photo=True,
        )


def test_monthly_proposal_month_must_be_first_day() -> None:
    with pytest.raises(ValidationError):
        MonthlyPlanProposalCreate(
            month=date(2026, 8, 2),
            goal="신규 독자 확보",
            seeds=[{"topic": "콘텐츠 기획"}],
        )
