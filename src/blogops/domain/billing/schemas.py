"""Strict billing API contracts."""

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from blogops.domain.billing.enums import BillingCycle, CreditBucketKind, OveragePolicy


def _https_return_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        value != value.strip()
        or parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError(
            "return_url must be an absolute HTTPS URL without credentials or fragment"
        )
    return value


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ORMModel(StrictModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class PaymentIntentCreate(StrictModel):
    operation: Literal["SUBSCRIBE", "CHANGE_PLAN", "PURCHASE_CREDITS"]
    provider: str = Field(min_length=1, max_length=80)
    plan_version_id: UUID | None = None
    billing_cycle: BillingCycle | None = None
    purchase_sku: str | None = Field(default=None, max_length=200)
    idempotency_key: str = Field(min_length=8, max_length=255)
    return_url: str = Field(min_length=8, max_length=2_048)

    @field_validator("return_url")
    @classmethod
    def safe_return_url(cls, value: str) -> str:
        return _https_return_url(value)

    @model_validator(mode="after")
    def require_exact_product(self) -> "PaymentIntentCreate":
        if (self.plan_version_id is None) == (self.purchase_sku is None):
            raise ValueError("exactly one of plan_version_id or purchase_sku is required")
        if self.plan_version_id and self.billing_cycle is None:
            raise ValueError("billing_cycle is required for plan subscriptions")
        if self.operation == "PURCHASE_CREDITS" and self.purchase_sku is None:
            raise ValueError("credit purchases require purchase_sku")
        if self.operation != "PURCHASE_CREDITS" and self.plan_version_id is None:
            raise ValueError("subscription operations require plan_version_id")
        return self


class SubscriptionCheckoutCreate(StrictModel):
    provider: str = Field(min_length=1, max_length=80)
    plan_version_id: UUID
    billing_cycle: BillingCycle
    idempotency_key: str = Field(min_length=8, max_length=255)
    return_url: str = Field(min_length=8, max_length=2_048)

    @field_validator("return_url")
    @classmethod
    def safe_return_url(cls, value: str) -> str:
        return _https_return_url(value)


class CreditPurchaseCreate(StrictModel):
    provider: str = Field(min_length=1, max_length=80)
    purchase_sku: str = Field(min_length=1, max_length=200)
    idempotency_key: str = Field(min_length=8, max_length=255)
    return_url: str = Field(min_length=8, max_length=2_048)

    @field_validator("return_url")
    @classmethod
    def safe_return_url(cls, value: str) -> str:
        return _https_return_url(value)


class PaymentCommandRead(ORMModel):
    id: UUID
    operation: str
    provider: str
    state: str
    checkout_url: str | None
    expires_at: datetime | None
    error_code: str | None
    created_at: datetime


class PaymentProviderEventRead(ORMModel):
    id: UUID
    workspace_id: UUID
    provider: str
    provider_event_id: str
    event_type: str
    occurred_at: datetime
    received_at: datetime


class BillingSubscriptionRead(ORMModel):
    id: UUID
    workspace_id: UUID
    plan_version_id: UUID
    billing_cycle: str
    state: str
    current_period_start: datetime | None
    current_period_end: datetime | None
    trial_ends_at: datetime | None
    cancel_at_period_end: bool
    scheduled_plan_version_id: UUID | None
    scheduled_change_at: datetime | None
    created_at: datetime
    updated_at: datetime


class CreditGrantCreate(StrictModel):
    bucket_kind: CreditBucketKind
    amount: Decimal = Field(gt=0, max_digits=19, decimal_places=6)
    source_type: str = Field(min_length=1, max_length=80)
    source_id: str = Field(min_length=1, max_length=500)
    expires_at: datetime | None = None
    grant_policy_snapshot: dict[str, Any]
    reason_code: str = Field(min_length=3, max_length=120)

    @field_validator("expires_at")
    @classmethod
    def aware_expiry(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("expires_at must be timezone-aware")
        return value


class CreditHoldCreate(StrictModel):
    subject_type: str = Field(min_length=1, max_length=80)
    subject_id: str = Field(min_length=1, max_length=500)
    operation: str = Field(min_length=1, max_length=120)
    price_version_id: UUID
    maximum_amount: Decimal = Field(gt=0, max_digits=19, decimal_places=6)
    idempotency_key: str = Field(min_length=8, max_length=255)
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def aware_expiry(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("expires_at must be timezone-aware")
        return value


class CreditHoldFinalize(StrictModel):
    finalization_event_id: str = Field(min_length=8, max_length=500)
    actual_amount: Decimal = Field(ge=0, max_digits=19, decimal_places=6)
    failure_class: str | None = Field(default=None, min_length=2, max_length=80)
    reason_code: str | None = Field(default=None, min_length=3, max_length=120)

    @model_validator(mode="after")
    def require_failure_reason_pair(self) -> "CreditHoldFinalize":
        if (self.failure_class is None) != (self.reason_code is None):
            raise ValueError("failure_class and reason_code must be provided together")
        return self


class CreditHoldRelease(StrictModel):
    release_event_id: str = Field(min_length=8, max_length=500)
    failure_class: str = Field(min_length=2, max_length=80)
    reason_code: str = Field(min_length=3, max_length=120)


class CreditAccountRead(ORMModel):
    id: UUID
    workspace_id: UUID
    available_balance: Decimal
    held_balance: Decimal
    updated_at: datetime


class CreditHoldRead(ORMModel):
    id: UUID
    subject_type: str
    subject_id: str
    operation: str
    price_version_id: UUID
    maximum_amount: Decimal
    actual_amount: Decimal | None
    released_amount: Decimal | None
    state: str
    finalization_event_id: str | None
    failure_class: str | None
    expires_at: datetime
    finalized_at: datetime | None
    created_at: datetime


class CreditLedgerRead(ORMModel):
    id: UUID
    kind: str
    direction: str
    amount: Decimal
    available_delta: Decimal
    held_delta: Decimal
    available_after: Decimal
    held_after: Decimal
    source_type: str
    source_id: str
    reason_code: str
    posted_at: datetime


class UsageRecordCreate(StrictModel):
    source_event_id: str = Field(min_length=8, max_length=500)
    subject_type: str = Field(min_length=1, max_length=80)
    subject_id: str = Field(min_length=1, max_length=500)
    metric_key: str = Field(min_length=1, max_length=160)
    quantity: Decimal = Field(gt=0, max_digits=19, decimal_places=6)
    price_version_id: UUID
    api_key_id: UUID | None = None
    endpoint: str | None = Field(default=None, max_length=500)
    occurred_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("occurred_at")
    @classmethod
    def aware_occurred(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")
        return value


class UsageRecordRead(ORMModel):
    id: UUID
    source_event_id: str
    metric_key: str
    quantity: Decimal
    unit_name: str
    state: str
    price_version_id: UUID
    credit_amount: Decimal
    internal_cost: Decimal
    cost_currency: str
    api_key_id: UUID | None
    endpoint: str | None
    occurred_at: datetime
    finalized_at: datetime | None


class UsageLimitCheck(StrictModel):
    metric_key: str = Field(min_length=1, max_length=160)
    requested: Decimal = Field(gt=0, max_digits=19, decimal_places=6)
    used: Decimal = Field(ge=0, max_digits=19, decimal_places=6)
    limit: Decimal = Field(ge=0, max_digits=19, decimal_places=6)
    overage_policy: OveragePolicy


class UsageLimitRead(StrictModel):
    allowed: bool
    remaining: Decimal
    overage: Decimal
    policy: OveragePolicy
