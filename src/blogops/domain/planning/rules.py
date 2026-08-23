"""Pure planning rules shared by services and unit tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import hashlib
import json
import re
import unicodedata
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from blogops.domain.planning.enums import (
    BriefEvent,
    BriefStatus,
    BudgetEnforcement,
    CalendarConflictResolution,
    SpendDecision,
)


MAX_IDEA_CANDIDATES = 1_000


class InvalidPlanningTransition(ValueError):
    pass


BRIEF_TRANSITIONS: dict[tuple[BriefStatus, BriefEvent], BriefStatus] = {
    (BriefStatus.DRAFT, BriefEvent.SUBMIT): BriefStatus.WAITING_REVIEW,
    (BriefStatus.REVISION_REQUESTED, BriefEvent.REVISE): BriefStatus.DRAFT,
    (BriefStatus.REJECTED, BriefEvent.REVISE): BriefStatus.DRAFT,
    (BriefStatus.WAITING_REVIEW, BriefEvent.REQUEST_CHANGES): BriefStatus.REVISION_REQUESTED,
    (BriefStatus.WAITING_REVIEW, BriefEvent.APPROVE_STAGE): BriefStatus.WAITING_REVIEW,
    (BriefStatus.WAITING_REVIEW, BriefEvent.APPROVE_FINAL): BriefStatus.APPROVED,
    (BriefStatus.WAITING_REVIEW, BriefEvent.REJECT): BriefStatus.REJECTED,
    (BriefStatus.APPROVED, BriefEvent.SCHEDULE): BriefStatus.SCHEDULED,
    (BriefStatus.SCHEDULED, BriefEvent.UNSCHEDULE): BriefStatus.APPROVED,
}


def transition_brief_status(current: BriefStatus, event: BriefEvent) -> BriefStatus:
    if event is BriefEvent.ARCHIVE and current is not BriefStatus.ARCHIVED:
        return BriefStatus.ARCHIVED
    try:
        return BRIEF_TRANSITIONS[(current, event)]
    except KeyError as exc:
        raise InvalidPlanningTransition(f"{current.value} cannot apply {event.value}") from exc


def canonical_json_hash(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def normalize_topic(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    return re.sub(r"[^0-9a-z가-힣]+", " ", normalized).strip()


def idea_duplicate_key(
    *,
    title: str,
    intent: str,
    keyword_cluster_id: UUID | None,
    semantic_group_key: str | None,
    primary_keyword: str | None,
) -> str:
    """Collapse keyword batches by their semantic group, not by an invented time window."""

    if keyword_cluster_id is not None:
        identity = f"cluster:{keyword_cluster_id}"
    elif semantic_group_key:
        identity = f"semantic:{normalize_topic(semantic_group_key)}"
    elif primary_keyword:
        identity = f"keyword:{normalize_topic(primary_keyword)}"
    else:
        identity = f"title:{normalize_topic(title)}"
    return canonical_json_hash({"identity": identity, "intent": intent})


@dataclass(frozen=True, slots=True)
class CalendarSlot:
    entry_id: UUID | None
    scheduled_at: datetime


@dataclass(frozen=True, slots=True)
class CalendarConflict:
    kind: str
    conflicting_entry_ids: tuple[UUID, ...]
    day_count: int


def detect_calendar_conflict(
    candidate: datetime,
    existing: list[CalendarSlot],
    *,
    minimum_spacing: timedelta,
    maximum_per_local_day: int,
    timezone: str,
) -> CalendarConflict | None:
    if candidate.tzinfo is None:
        raise ValueError("candidate must be timezone-aware")
    candidate_utc = candidate.astimezone(UTC)
    try:
        local_zone = ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("unknown calendar timezone") from exc
    candidate_local_date = candidate_utc.astimezone(local_zone).date()
    same_day = [
        item
        for item in existing
        if item.scheduled_at.astimezone(local_zone).date() == candidate_local_date
    ]
    too_close = [
        item
        for item in existing
        if abs(item.scheduled_at.astimezone(UTC) - candidate_utc) < minimum_spacing
    ]
    if too_close:
        return CalendarConflict(
            kind="MINIMUM_SPACING",
            conflicting_entry_ids=tuple(
                item.entry_id for item in too_close if item.entry_id is not None
            ),
            day_count=len(same_day),
        )
    if len(same_day) >= maximum_per_local_day:
        return CalendarConflict(
            kind="DAILY_CHANNEL_CAPACITY",
            conflicting_entry_ids=tuple(
                item.entry_id for item in same_day if item.entry_id is not None
            ),
            day_count=len(same_day),
        )
    return None


def resolve_calendar_slot(
    requested: datetime,
    existing: list[CalendarSlot],
    *,
    resolution: CalendarConflictResolution,
    minimum_spacing: timedelta,
    maximum_per_local_day: int,
    timezone: str,
    spread_step: timedelta = timedelta(hours=1),
    max_attempts: int = 24 * 31,
) -> tuple[datetime, CalendarConflict | None]:
    conflict = detect_calendar_conflict(
        requested,
        existing,
        minimum_spacing=minimum_spacing,
        maximum_per_local_day=maximum_per_local_day,
        timezone=timezone,
    )
    if conflict is None or resolution is not CalendarConflictResolution.AUTO_SPREAD:
        return requested, conflict
    candidate = requested
    for _attempt in range(max_attempts):
        candidate += spread_step
        if (
            detect_calendar_conflict(
                candidate,
                existing,
                minimum_spacing=minimum_spacing,
                maximum_per_local_day=maximum_per_local_day,
                timezone=timezone,
            )
            is None
        ):
            return candidate, conflict
    raise ValueError("no calendar slot available in auto-spread horizon")


@dataclass(frozen=True, slots=True)
class BudgetResult:
    decision: SpendDecision
    projected: Decimal
    limit: Decimal


def evaluate_budget(
    *,
    spent: Decimal,
    requested: Decimal,
    limit: Decimal,
    enforcement: BudgetEnforcement,
) -> BudgetResult:
    projected = spent + requested
    if projected <= limit:
        return BudgetResult(SpendDecision.ALLOW, projected, limit)
    decision = {
        BudgetEnforcement.WARN: SpendDecision.WARN,
        BudgetEnforcement.BLOCK: SpendDecision.BLOCK,
        BudgetEnforcement.PAUSE: SpendDecision.PAUSE,
    }[enforcement]
    return BudgetResult(decision, projected, limit)


def _json_default(value: object) -> str:
    if isinstance(value, (datetime, UUID, Decimal)):
        return str(value)
    raise TypeError(f"unsupported canonical JSON value: {type(value).__name__}")
