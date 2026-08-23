"""Separate money, credit and usage ledgers with immutable source events."""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
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
from blogops.domain.billing.enums import (
    CreditHoldState,
    PaymentCommandState,
    PricingVersionState,
    SubscriptionState,
)


class BillingPlanVersion(UUIDPrimaryKeyMixin, Base):
    """Immutable plan, price and entitlement contract used for historical billing."""

    __tablename__ = "billing_plan_versions"
    __table_args__ = (
        UniqueConstraint("catalog_scope", "plan_key", "version", name="billing_plan_version"),
        CheckConstraint(
            "(catalog_scope = 'GLOBAL' AND workspace_id IS NULL) OR "
            "(workspace_id IS NOT NULL AND "
            "catalog_scope = 'contract:' || workspace_id::text)",
            name="billing_plan_scope_workspace",
        ),
        CheckConstraint("version > 0", name="billing_plan_version_positive"),
        CheckConstraint("monthly_price >= 0", name="billing_plan_monthly_nonnegative"),
        CheckConstraint("annual_price >= 0", name="billing_plan_annual_nonnegative"),
        Index("ix_billing_plan_effective", "plan_key", "state", "effective_at"),
    )

    # GLOBAL is shared; contract:<workspace UUID> is an explicit negotiated catalog.
    catalog_scope: Mapped[str] = mapped_column(String(120), nullable=False)
    workspace_id: Mapped[UUID | None] = mapped_column(index=True)
    plan_key: Mapped[str] = mapped_column(String(80), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, default=PricingVersionState.DRAFT.value
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    monthly_price: Mapped[Decimal] = mapped_column(Numeric(19, 6), nullable=False)
    annual_price: Mapped[Decimal] = mapped_column(Numeric(19, 6), nullable=False)
    credit_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    entitlement_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    api_rate_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    usage_thresholds: Mapped[list[int]] = mapped_column(JSONB, nullable=False)
    policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BillingPriceVersion(UUIDPrimaryKeyMixin, Base):
    """Immutable unit price for a billable metric/vendor/model combination."""

    __tablename__ = "billing_price_versions"
    __table_args__ = (
        UniqueConstraint(
            "catalog_scope",
            "metric_key",
            "vendor",
            "vendor_sku",
            "version",
            name="billing_price_metric_version",
        ),
        CheckConstraint(
            "(catalog_scope = 'GLOBAL' AND workspace_id IS NULL) OR "
            "(workspace_id IS NOT NULL AND "
            "catalog_scope = 'contract:' || workspace_id::text)",
            name="billing_price_scope_workspace",
        ),
        CheckConstraint("version > 0", name="billing_price_version_positive"),
        CheckConstraint("unit_size > 0", name="billing_price_unit_size_positive"),
        CheckConstraint("credit_unit_price >= 0", name="billing_price_credit_nonnegative"),
        CheckConstraint("internal_unit_cost >= 0", name="billing_price_cost_nonnegative"),
        Index("ix_billing_price_effective", "metric_key", "state", "effective_at"),
    )

    catalog_scope: Mapped[str] = mapped_column(String(120), nullable=False)
    workspace_id: Mapped[UUID | None] = mapped_column(index=True)
    metric_key: Mapped[str] = mapped_column(String(160), nullable=False)
    vendor: Mapped[str] = mapped_column(String(120), nullable=False)
    vendor_sku: Mapped[str] = mapped_column(String(200), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, default=PricingVersionState.DRAFT.value
    )
    unit_name: Mapped[str] = mapped_column(String(40), nullable=False)
    unit_size: Mapped[Decimal] = mapped_column(Numeric(19, 6), nullable=False)
    credit_unit_price: Mapped[Decimal] = mapped_column(Numeric(19, 6), nullable=False)
    internal_unit_cost: Mapped[Decimal] = mapped_column(Numeric(19, 6), nullable=False)
    cost_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    pricing_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    pricing_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BillingSubscription(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "billing_subscriptions"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="billing_subscription_workspace_id"),
        UniqueConstraint("workspace_id", name="billing_subscription_workspace_once"),
        ForeignKeyConstraint(
            ["plan_version_id"],
            ["billing_plan_versions.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["scheduled_plan_version_id"],
            ["billing_plan_versions.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "entitlement_snapshot_id"],
            [
                "billing_entitlement_snapshots.workspace_id",
                "billing_entitlement_snapshots.id",
            ],
            ondelete="RESTRICT",
        ),
        CheckConstraint("lock_version > 0", name="billing_subscription_lock_positive"),
        Index("ix_billing_subscription_state", "workspace_id", "state"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    plan_version_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    billing_cycle: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default=SubscriptionState.PENDING_PROVIDER.value
    )
    provider: Mapped[str | None] = mapped_column(String(80))
    provider_customer_ref: Mapped[str | None] = mapped_column(String(500))
    provider_subscription_ref: Mapped[str | None] = mapped_column(String(500), unique=True)
    current_period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    trial_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    scheduled_plan_version_id: Mapped[UUID | None] = mapped_column(index=True)
    scheduled_change_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dunning_policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    entitlement_snapshot_id: Mapped[UUID | None] = mapped_column(index=True)
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class EntitlementSnapshot(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "billing_entitlement_snapshots"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="billing_entitlement_workspace_id"),
        ForeignKeyConstraint(
            ["plan_version_id"],
            ["billing_plan_versions.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("workspace_id", "snapshot_hash", name="billing_entitlement_hash"),
        Index("ix_billing_entitlement_active", "workspace_id", "valid_from", "valid_until"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    plan_version_id: Mapped[UUID] = mapped_column(nullable=False)
    addon_refs: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    contract_ref: Mapped[str | None] = mapped_column(String(500))
    entitlements: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PaymentCommand(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Local intent only; a provider adapter/event is required for any success state."""

    __tablename__ = "billing_payment_commands"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="billing_payment_command_workspace_id"),
        UniqueConstraint(
            "workspace_id", "requested_by", "operation", "idempotency_key",
            name="billing_payment_command_idempotency",
        ),
        Index("ix_billing_payment_command_state", "workspace_id", "state", "created_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    requested_by: Mapped[UUID] = mapped_column(nullable=False)
    operation: Mapped[str] = mapped_column(String(48), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    provider_request_ref: Mapped[str | None] = mapped_column(String(500), unique=True)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default=PaymentCommandState.PENDING_PROVIDER.value
    )
    checkout_url: Mapped[str | None] = mapped_column(String(2_048))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(120))


class PaymentProviderEvent(UUIDPrimaryKeyMixin, Base):
    """Immutable, signature-verified payment provider fact."""

    __tablename__ = "billing_provider_events"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="billing_provider_event_workspace_id"),
        UniqueConstraint("provider", "provider_event_id", name="billing_provider_event_once"),
        Index("ix_billing_provider_workspace", "workspace_id", "occurred_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    provider_event_id: Mapped[str] = mapped_column(String(500), nullable=False)
    event_type: Mapped[str] = mapped_column(String(160), nullable=False)
    signature_key_version: Mapped[str] = mapped_column(String(80), nullable=False)
    raw_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_payload_ref: Mapped[str] = mapped_column(String(1_000), nullable=False)
    normalized_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MoneyAccount(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "billing_money_accounts"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="billing_money_account_workspace_id"),
        UniqueConstraint("workspace_id", "currency", name="billing_money_account_currency"),
        CheckConstraint("posted_balance >= 0", name="billing_money_balance_nonnegative"),
        CheckConstraint("lock_version > 0", name="billing_money_lock_positive"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    posted_balance: Mapped[Decimal] = mapped_column(
        Numeric(19, 6), nullable=False, default=Decimal("0")
    )
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class MoneyLedgerEntry(UUIDPrimaryKeyMixin, Base):
    """Append-only money fact. Refunds and corrections are new entries."""

    __tablename__ = "billing_money_ledger"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="billing_money_entry_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "account_id"],
            ["billing_money_accounts.workspace_id", "billing_money_accounts.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "reversal_of_id"],
            ["billing_money_ledger.workspace_id", "billing_money_ledger.id"],
            name="fk_billing_money_reversal_workspace",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "provider_event_id"],
            ["billing_provider_events.workspace_id", "billing_provider_events.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("workspace_id", "idempotency_key", name="billing_money_idempotency"),
        UniqueConstraint("reversal_of_id", name="billing_money_reversal_once"),
        CheckConstraint("amount > 0", name="billing_money_entry_positive"),
        CheckConstraint("balance_after >= 0", name="billing_money_after_nonnegative"),
        Index("ix_billing_money_posted", "workspace_id", "account_id", "posted_at", "id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    account_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(19, 6), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    balance_after: Mapped[Decimal] = mapped_column(Numeric(19, 6), nullable=False)
    transaction_group_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    reversal_of_id: Mapped[UUID | None] = mapped_column(index=True)
    provider_event_id: Mapped[UUID | None] = mapped_column(index=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(120), nullable=False)
    source_type: Mapped[str] = mapped_column(String(80), nullable=False)
    source_id: Mapped[str] = mapped_column(String(500), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    actor_id: Mapped[UUID | None]
    posted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CreditAccount(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "billing_credit_accounts"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="billing_credit_account_workspace_id"),
        UniqueConstraint("workspace_id", name="billing_credit_account_workspace_once"),
        CheckConstraint("available_balance >= 0", name="billing_credit_available_nonnegative"),
        CheckConstraint("held_balance >= 0", name="billing_credit_held_nonnegative"),
        CheckConstraint("lock_version > 0", name="billing_credit_lock_positive"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    available_balance: Mapped[Decimal] = mapped_column(
        Numeric(19, 6), nullable=False, default=Decimal("0")
    )
    held_balance: Mapped[Decimal] = mapped_column(
        Numeric(19, 6), nullable=False, default=Decimal("0")
    )
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class CreditGrant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Mutable remaining balance of one grant; its movements remain in the ledger."""

    __tablename__ = "billing_credit_grants"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="billing_credit_grant_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "account_id"],
            ["billing_credit_accounts.workspace_id", "billing_credit_accounts.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id",
            "source_type",
            "source_id",
            name="billing_credit_grant_source",
        ),
        CheckConstraint("original_amount > 0", name="billing_credit_grant_positive"),
        CheckConstraint("available_amount >= 0", name="billing_credit_grant_available"),
        CheckConstraint("held_amount >= 0", name="billing_credit_grant_held"),
        CheckConstraint(
            "available_amount + held_amount <= original_amount",
            name="billing_credit_grant_capacity",
        ),
        CheckConstraint("lock_version > 0", name="billing_credit_grant_lock_positive"),
        Index("ix_billing_credit_grant_expiry", "workspace_id", "expires_at", "created_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    account_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    bucket_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    original_amount: Mapped[Decimal] = mapped_column(Numeric(19, 6), nullable=False)
    available_amount: Mapped[Decimal] = mapped_column(Numeric(19, 6), nullable=False)
    held_amount: Mapped[Decimal] = mapped_column(
        Numeric(19, 6), nullable=False, default=Decimal("0")
    )
    source_type: Mapped[str] = mapped_column(String(80), nullable=False)
    source_id: Mapped[str] = mapped_column(String(500), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    grant_policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class CreditHold(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "billing_credit_holds"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="billing_credit_hold_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "account_id"],
            ["billing_credit_accounts.workspace_id", "billing_credit_accounts.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["price_version_id"],
            ["billing_price_versions.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id", "subject_type", "subject_id", "idempotency_key",
            name="billing_credit_hold_idempotency",
        ),
        UniqueConstraint(
            "workspace_id",
            "finalization_event_id",
            name="billing_credit_finalize_once",
        ),
        CheckConstraint("maximum_amount > 0", name="billing_credit_hold_positive"),
        CheckConstraint(
            "actual_amount IS NULL OR actual_amount >= 0",
            name="billing_credit_actual_nonnegative",
        ),
        CheckConstraint(
            "actual_amount IS NULL OR actual_amount <= maximum_amount",
            name="billing_credit_actual_max",
        ),
        CheckConstraint("lock_version > 0", name="billing_credit_hold_lock_positive"),
        Index("ix_billing_credit_hold_state", "workspace_id", "state", "expires_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    account_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    subject_type: Mapped[str] = mapped_column(String(80), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(500), nullable=False)
    operation: Mapped[str] = mapped_column(String(120), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    price_version_id: Mapped[UUID] = mapped_column(nullable=False)
    pricing_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    maximum_amount: Mapped[Decimal] = mapped_column(Numeric(19, 6), nullable=False)
    actual_amount: Mapped[Decimal | None] = mapped_column(Numeric(19, 6))
    released_amount: Mapped[Decimal | None] = mapped_column(Numeric(19, 6))
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, default=CreditHoldState.HELD.value
    )
    finalization_event_id: Mapped[str | None] = mapped_column(String(500))
    failure_class: Mapped[str | None] = mapped_column(String(80))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    requested_by: Mapped[UUID] = mapped_column(nullable=False)
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class CreditHoldAllocation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "billing_credit_hold_allocations"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="billing_credit_allocation_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "hold_id"],
            ["billing_credit_holds.workspace_id", "billing_credit_holds.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "grant_id"],
            ["billing_credit_grants.workspace_id", "billing_credit_grants.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id",
            "hold_id",
            "grant_id",
            name="billing_credit_allocation_grant",
        ),
        CheckConstraint("held_amount > 0", name="billing_credit_allocation_positive"),
        CheckConstraint("allocation_order > 0", name="billing_credit_allocation_order_positive"),
        CheckConstraint("consumed_amount >= 0", name="billing_credit_allocation_consumed"),
        CheckConstraint("released_amount >= 0", name="billing_credit_allocation_released"),
        CheckConstraint(
            "consumed_amount + released_amount <= held_amount",
            name="billing_credit_allocation_capacity",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    hold_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    grant_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    allocation_order: Mapped[int] = mapped_column(Integer, nullable=False)
    held_amount: Mapped[Decimal] = mapped_column(Numeric(19, 6), nullable=False)
    consumed_amount: Mapped[Decimal] = mapped_column(
        Numeric(19, 6), nullable=False, default=Decimal("0")
    )
    released_amount: Mapped[Decimal] = mapped_column(
        Numeric(19, 6), nullable=False, default=Decimal("0")
    )


class CreditLedgerEntry(UUIDPrimaryKeyMixin, Base):
    """Append-only credit movement; amount is always positive and direction is explicit."""

    __tablename__ = "billing_credit_ledger"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="billing_credit_entry_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "account_id"],
            ["billing_credit_accounts.workspace_id", "billing_credit_accounts.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "reversal_of_id"],
            ["billing_credit_ledger.workspace_id", "billing_credit_ledger.id"],
            name="fk_billing_credit_reversal_workspace",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "grant_id"],
            ["billing_credit_grants.workspace_id", "billing_credit_grants.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "hold_id"],
            ["billing_credit_holds.workspace_id", "billing_credit_holds.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="billing_credit_entry_idempotency",
        ),
        UniqueConstraint("reversal_of_id", name="billing_credit_reversal_once"),
        CheckConstraint("amount > 0", name="billing_credit_entry_positive"),
        CheckConstraint(
            "available_delta <> 0 OR held_delta <> 0",
            name="billing_credit_entry_has_movement",
        ),
        CheckConstraint("available_after >= 0", name="billing_credit_entry_available"),
        CheckConstraint("held_after >= 0", name="billing_credit_entry_held"),
        Index("ix_billing_credit_posted", "workspace_id", "account_id", "posted_at", "id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    account_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    grant_id: Mapped[UUID | None] = mapped_column(index=True)
    hold_id: Mapped[UUID | None] = mapped_column(index=True)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(19, 6), nullable=False)
    available_delta: Mapped[Decimal] = mapped_column(Numeric(19, 6), nullable=False)
    held_delta: Mapped[Decimal] = mapped_column(Numeric(19, 6), nullable=False)
    available_after: Mapped[Decimal] = mapped_column(Numeric(19, 6), nullable=False)
    held_after: Mapped[Decimal] = mapped_column(Numeric(19, 6), nullable=False)
    transaction_group_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    reversal_of_id: Mapped[UUID | None] = mapped_column(index=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    source_type: Mapped[str] = mapped_column(String(80), nullable=False)
    source_id: Mapped[str] = mapped_column(String(500), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(120), nullable=False)
    pricing_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    actor_id: Mapped[UUID | None]
    posted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class UsageRecord(UUIDPrimaryKeyMixin, Base):
    """Append-only raw meter event, independent from money and credit ledgers."""

    __tablename__ = "billing_usage_records"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="billing_usage_workspace_id"),
        ForeignKeyConstraint(
            ["price_version_id"],
            ["billing_price_versions.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("workspace_id", "source_event_id", name="billing_usage_source_once"),
        CheckConstraint("quantity > 0", name="billing_usage_quantity_positive"),
        CheckConstraint("credit_amount >= 0", name="billing_usage_credit_nonnegative"),
        CheckConstraint("internal_cost >= 0", name="billing_usage_cost_nonnegative"),
        Index("ix_billing_usage_metric", "workspace_id", "metric_key", "occurred_at"),
        Index("ix_billing_usage_api", "workspace_id", "api_key_id", "endpoint", "occurred_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    source_event_id: Mapped[str] = mapped_column(String(500), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(80), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(500), nullable=False)
    metric_key: Mapped[str] = mapped_column(String(160), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(19, 6), nullable=False)
    unit_name: Mapped[str] = mapped_column(String(40), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    price_version_id: Mapped[UUID] = mapped_column(nullable=False)
    pricing_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    credit_amount: Mapped[Decimal] = mapped_column(Numeric(19, 6), nullable=False)
    internal_cost: Mapped[Decimal] = mapped_column(Numeric(19, 6), nullable=False)
    cost_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    api_key_id: Mapped[UUID | None] = mapped_column(index=True)
    endpoint: Mapped[str | None] = mapped_column(String(500))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class UsageAggregate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Rebuildable cache; never the source of billing truth."""

    __tablename__ = "billing_usage_aggregates"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="billing_usage_aggregate_workspace_id"),
        UniqueConstraint(
            "workspace_id", "metric_key", "period_start", "period_end", "dimension_hash",
            name="billing_usage_aggregate_key",
        ),
        CheckConstraint("estimated_quantity >= 0", name="billing_usage_estimated_nonnegative"),
        CheckConstraint("final_quantity >= 0", name="billing_usage_final_nonnegative"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    metric_key: Mapped[str] = mapped_column(String(160), nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    dimensions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    dimension_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    estimated_quantity: Mapped[Decimal] = mapped_column(Numeric(19, 6), nullable=False)
    final_quantity: Mapped[Decimal] = mapped_column(Numeric(19, 6), nullable=False)
    source_watermark: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


def _reject_immutable_billing_row(_mapper: object, _connection: object, target: object) -> None:
    raise ValueError(f"{type(target).__name__} is append-only")


for _immutable_model in (
    BillingPlanVersion,
    BillingPriceVersion,
    EntitlementSnapshot,
    PaymentProviderEvent,
    MoneyLedgerEntry,
    CreditLedgerEntry,
    UsageRecord,
):
    event.listen(_immutable_model, "before_update", _reject_immutable_billing_row)
    event.listen(_immutable_model, "before_delete", _reject_immutable_billing_row)
