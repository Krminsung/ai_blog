"""Stable bulk ingestion and row-processing vocabulary."""

from enum import StrEnum


class BulkInputKind(StrEnum):
    CSV = "CSV"
    XLSX = "XLSX"
    GOOGLE_SHEETS = "GOOGLE_SHEETS"
    API = "API"
    PRODUCT_FEED = "PRODUCT_FEED"
    PASTE = "PASTE"
    SITE_LIST = "SITE_LIST"


class BulkPriority(StrEnum):
    LOW_COST = "LOW_COST"
    NORMAL = "NORMAL"
    URGENT = "URGENT"


class BulkOperation(StrEnum):
    GENERATE_CONTENT = "GENERATE_CONTENT"
    RESEARCH_AND_GENERATE = "RESEARCH_AND_GENERATE"
    UPDATE_CONTENT = "UPDATE_CONTENT"
    QUALITY_ONLY = "QUALITY_ONLY"
    EXPORT = "EXPORT"
    PUBLISH = "PUBLISH"


class BulkRowState(StrEnum):
    PENDING = "PENDING"
    INVALID = "INVALID"
    DUPLICATE = "DUPLICATE"
    READY = "READY"
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    QUALITY_BLOCKED = "QUALITY_BLOCKED"
    WAITING_REVIEW = "WAITING_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SUCCEEDED = "SUCCEEDED"
    RETRYABLE_FAILED = "RETRYABLE_FAILED"
    FINAL_FAILED = "FINAL_FAILED"
    CANCELLED = "CANCELLED"


class DuplicateAction(StrEnum):
    REMOVE = "REMOVE"
    MERGE = "MERGE"
    KEEP = "KEEP"
    REVIEW = "REVIEW"


class ExistingContentAction(StrEnum):
    NEW = "NEW"
    UPDATE = "UPDATE"
    MERGE = "MERGE"
    REVIEW = "REVIEW"


class BulkCommandKind(StrEnum):
    PAUSE = "PAUSE"
    RESUME = "RESUME"
    CANCEL = "CANCEL"
    RETRY_ROWS = "RETRY_ROWS"
    REGENERATE_ROWS = "REGENERATE_ROWS"
    APPROVE_ROWS = "APPROVE_ROWS"
    EXPORT = "EXPORT"
    BUDGET_KILL = "BUDGET_KILL"


class BulkAttemptOutcome(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    RETRYABLE_FAILED = "RETRYABLE_FAILED"
    FINAL_FAILED = "FINAL_FAILED"
    QUALITY_BLOCKED = "QUALITY_BLOCKED"
    CANCELLED = "CANCELLED"


class BulkExportKind(StrEnum):
    ZIP = "ZIP"
    CSV = "CSV"
    MARKDOWN = "MARKDOWN"
    HTML = "HTML"
    PDF_REPORT = "PDF_REPORT"


class BulkScheduleState(StrEnum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    DISABLED = "DISABLED"


RETRYABLE_ROW_STATES = frozenset(
    {BulkRowState.RETRYABLE_FAILED, BulkRowState.QUALITY_BLOCKED}
)


TERMINAL_ROW_STATES = frozenset(
    {
        BulkRowState.APPROVED,
        BulkRowState.REJECTED,
        BulkRowState.SUCCEEDED,
        BulkRowState.FINAL_FAILED,
        BulkRowState.CANCELLED,
    }
)
