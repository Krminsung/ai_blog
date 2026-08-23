"""Stable billing, credit and metering vocabulary."""

from enum import StrEnum


class BillingCycle(StrEnum):
    MONTHLY = "MONTHLY"
    ANNUAL = "ANNUAL"
    CONTRACT = "CONTRACT"


class SubscriptionState(StrEnum):
    PENDING_PROVIDER = "PENDING_PROVIDER"
    TRIALING = "TRIALING"
    ACTIVE = "ACTIVE"
    PAST_DUE = "PAST_DUE"
    GRACE_PERIOD = "GRACE_PERIOD"
    CANCELLATION_SCHEDULED = "CANCELLATION_SCHEDULED"
    CANCELLED = "CANCELLED"
    SUSPENDED = "SUSPENDED"


class PaymentCommandState(StrEnum):
    PENDING_PROVIDER = "PENDING_PROVIDER"
    PROVIDER_ACCEPTED = "PROVIDER_ACCEPTED"
    PROVIDER_REJECTED = "PROVIDER_REJECTED"
    FAILED = "FAILED"


class LedgerDirection(StrEnum):
    DEBIT = "DEBIT"
    CREDIT = "CREDIT"


class MoneyEntryKind(StrEnum):
    CHARGE = "CHARGE"
    REFUND = "REFUND"
    TAX = "TAX"
    DISCOUNT = "DISCOUNT"
    ADJUSTMENT = "ADJUSTMENT"
    REVERSAL = "REVERSAL"


class CreditBucketKind(StrEnum):
    MONTHLY = "MONTHLY"
    PURCHASED = "PURCHASED"
    PROMOTIONAL = "PROMOTIONAL"
    AGENCY_ALLOCATION = "AGENCY_ALLOCATION"
    ADMIN_ADJUSTMENT = "ADMIN_ADJUSTMENT"


class CreditEntryKind(StrEnum):
    GRANT = "GRANT"
    HOLD = "HOLD"
    CONSUME = "CONSUME"
    RELEASE = "RELEASE"
    EXPIRE = "EXPIRE"
    RECLAIM = "RECLAIM"
    ADJUSTMENT = "ADJUSTMENT"
    REVERSAL = "REVERSAL"


class CreditHoldState(StrEnum):
    HELD = "HELD"
    FINALIZED = "FINALIZED"
    RELEASED = "RELEASED"


class UsageRecordState(StrEnum):
    ESTIMATED = "ESTIMATED"
    FINAL = "FINAL"
    REVERSED = "REVERSED"


class OveragePolicy(StrEnum):
    BLOCK = "BLOCK"
    USE_CREDITS = "USE_CREDITS"
    POSTPAID = "POSTPAID"


class PricingVersionState(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"
