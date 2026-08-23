"""Stable content-planning workflow vocabularies."""

from enum import StrEnum


class CampaignStatus(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    ARCHIVED = "ARCHIVED"


class BudgetCategory(StrEnum):
    AI = "AI"
    DATA = "DATA"
    ADVERTISING = "ADVERTISING"


class BudgetEnforcement(StrEnum):
    WARN = "WARN"
    BLOCK = "BLOCK"
    PAUSE = "PAUSE"


class TopicNodeKind(StrEnum):
    PILLAR = "PILLAR"
    CLUSTER = "CLUSTER"


class TopicNodeStatus(StrEnum):
    ACTIVE = "ACTIVE"
    MERGED = "MERGED"
    ARCHIVED = "ARCHIVED"


class SearchIntent(StrEnum):
    INFORMATIONAL = "INFORMATIONAL"
    COMPARISON = "COMPARISON"
    PURCHASE = "PURCHASE"
    LOCAL = "LOCAL"
    NAVIGATIONAL = "NAVIGATIONAL"
    MIXED = "MIXED"


class JourneyStage(StrEnum):
    AWARENESS = "AWARENESS"
    CONSIDERATION = "CONSIDERATION"
    PURCHASE = "PURCHASE"
    RETENTION = "RETENTION"


class IntentSource(StrEnum):
    RULE = "RULE"
    AI = "AI"
    USER = "USER"


class IdeaStatus(StrEnum):
    SUGGESTED = "SUGGESTED"
    DISMISSED = "DISMISSED"
    PROMOTED = "PROMOTED"


class BriefStatus(StrEnum):
    DRAFT = "DRAFT"
    WAITING_REVIEW = "WAITING_REVIEW"
    REVISION_REQUESTED = "REVISION_REQUESTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SCHEDULED = "SCHEDULED"
    ARCHIVED = "ARCHIVED"


class BriefEvent(StrEnum):
    SUBMIT = "SUBMIT"
    REQUEST_CHANGES = "REQUEST_CHANGES"
    APPROVE_STAGE = "APPROVE_STAGE"
    APPROVE_FINAL = "APPROVE_FINAL"
    REJECT = "REJECT"
    REVISE = "REVISE"
    SCHEDULE = "SCHEDULE"
    UNSCHEDULE = "UNSCHEDULE"
    ARCHIVE = "ARCHIVE"


class DecisionKind(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    REQUEST_CHANGES = "REQUEST_CHANGES"


class AssignmentStage(StrEnum):
    WRITE = "WRITE"
    REVIEW = "REVIEW"
    APPROVE = "APPROVE"
    PUBLISH = "PUBLISH"


class AssignmentStatus(StrEnum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class CommentTarget(StrEnum):
    BRIEF = "BRIEF"
    TOPIC_NODE = "TOPIC_NODE"
    CALENDAR_ENTRY = "CALENDAR_ENTRY"


class CalendarEntryStatus(StrEnum):
    PLANNED = "PLANNED"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"
    PUBLISHED = "PUBLISHED"


class CalendarConflictResolution(StrEnum):
    BLOCK = "BLOCK"
    WARN = "WARN"
    AUTO_SPREAD = "AUTO_SPREAD"


class RecurrenceFrequency(StrEnum):
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"
    SEASONAL = "SEASONAL"


class ProposalStatus(StrEnum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class BoardColumnKind(StrEnum):
    BACKLOG = "BACKLOG"
    ACTIVE = "ACTIVE"
    REVIEW = "REVIEW"
    DONE = "DONE"


class SpendDecision(StrEnum):
    ALLOW = "ALLOW"
    WARN = "WARN"
    BLOCK = "BLOCK"
    PAUSE = "PAUSE"
