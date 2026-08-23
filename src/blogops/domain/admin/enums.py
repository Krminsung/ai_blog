"""Platform operations, support access and notification vocabulary."""

from enum import StrEnum


class SupportAccessState(StrEnum):
    PENDING_CUSTOMER = "PENDING_CUSTOMER"
    APPROVED = "APPROVED"
    DENIED = "DENIED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


class AdminSessionState(StrEnum):
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


class AdminCommandKind(StrEnum):
    JOB_RETRY = "JOB_RETRY"
    JOB_CANCEL = "JOB_CANCEL"
    DLQ_REQUEUE = "DLQ_REQUEUE"
    CREDIT_ADJUSTMENT = "CREDIT_ADJUSTMENT"
    REFUND_REQUEST = "REFUND_REQUEST"
    ACCOUNT_SUSPEND = "ACCOUNT_SUSPEND"
    CONNECTOR_KILL_SWITCH = "CONNECTOR_KILL_SWITCH"
    GENERATION_KILL_SWITCH = "GENERATION_KILL_SWITCH"
    FEATURE_FLAG_CHANGE = "FEATURE_FLAG_CHANGE"


class AdminCommandState(StrEnum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    READY = "READY"
    DISPATCHED = "DISPATCHED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class AdminApprovalDecision(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


class SupportTicketState(StrEnum):
    OPEN = "OPEN"
    WAITING_CUSTOMER = "WAITING_CUSTOMER"
    WAITING_INTERNAL = "WAITING_INTERNAL"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"


class NotificationChannel(StrEnum):
    IN_APP = "IN_APP"
    EMAIL = "EMAIL"
    SLACK = "SLACK"
    TEAMS = "TEAMS"


class NotificationFrequency(StrEnum):
    IMMEDIATE = "IMMEDIATE"
    DIGEST = "DIGEST"
    DISABLED = "DISABLED"


class NotificationDeliveryState(StrEnum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"
    BOUNCED = "BOUNCED"
    SUPPRESSED = "SUPPRESSED"
