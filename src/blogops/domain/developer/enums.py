"""Stable API product and outbound webhook vocabulary."""

from enum import StrEnum


class ApiKeyState(StrEnum):
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"
    ROTATED = "ROTATED"
    EXPIRED = "EXPIRED"


class ApiEnvironment(StrEnum):
    PRODUCTION = "PRODUCTION"
    SANDBOX = "SANDBOX"


class WebhookEndpointState(StrEnum):
    PENDING_VERIFICATION = "PENDING_VERIFICATION"
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class WebhookDeliveryState(StrEnum):
    PENDING = "PENDING"
    RETRY_WAIT = "RETRY_WAIT"
    DELIVERED = "DELIVERED"
    DEAD_LETTERED = "DEAD_LETTERED"
    CANCELLED = "CANCELLED"


class WebhookAttemptOutcome(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    RETRYABLE_FAILURE = "RETRYABLE_FAILURE"
    FINAL_FAILURE = "FINAL_FAILURE"


class RateLimitDecision(StrEnum):
    ALLOWED = "ALLOWED"
    REJECTED = "REJECTED"
