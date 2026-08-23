"""Agency hierarchy, client portal, white-label, SLA and allocation persistence."""

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
from blogops.domain.b2b.enums import (
    AgencyClientState,
    AgencyState,
    DomainVerificationState,
    PortalGrantState,
    PortalInvitationState,
    ProvisioningState,
    SlaCaseState,
)


class Agency(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "b2b_agencies"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="b2b_agency_workspace_id"),
        UniqueConstraint("workspace_id", name="b2b_agency_workspace_once"),
        CheckConstraint("lock_version > 0", name="b2b_agency_lock_positive"),
        Index("ix_b2b_agency_state", "state", "created_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default=AgencyState.ACTIVE.value)
    consolidated_billing: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    default_client_permissions: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class AgencyClient(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Cross-workspace relationship only; it never grants implicit tenant query access."""

    __tablename__ = "b2b_agency_clients"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="b2b_agency_client_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "agency_id"],
            ["b2b_agencies.workspace_id", "b2b_agencies.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("agency_id", "client_workspace_id", name="b2b_agency_client_once"),
        UniqueConstraint(
            "workspace_id", "id", "client_workspace_id", name="b2b_agency_client_identity"
        ),
        CheckConstraint(
            "workspace_id <> client_workspace_id",
            name="b2b_client_workspace_distinct",
        ),
        CheckConstraint("lock_version > 0", name="b2b_agency_client_lock_positive"),
        Index("ix_b2b_agency_client_state", "workspace_id", "state", "created_at"),
        Index("ix_b2b_client_reverse_lookup", "client_workspace_id", "state"),
    )

    # workspace_id is always the agency workspace and controls RLS ownership.
    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    agency_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    client_workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    client_display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, default=AgencyClientState.PENDING.value
    )
    permissions: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    billing_mode: Mapped[str] = mapped_column(String(24), nullable=False)
    allocation_policy_ref: Mapped[str | None] = mapped_column(String(500))
    relationship_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class PortalInvitation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One-time portal invite; email and token are only stored as hashes."""

    __tablename__ = "b2b_portal_invitations"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="b2b_portal_invite_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "agency_client_id"],
            ["b2b_agency_clients.workspace_id", "b2b_agency_clients.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "agency_client_id", "client_workspace_id"],
            [
                "b2b_agency_clients.workspace_id",
                "b2b_agency_clients.id",
                "b2b_agency_clients.client_workspace_id",
            ],
            name="fk_b2b_portal_invite_client_identity",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("token_prefix", name="b2b_portal_invite_prefix"),
        UniqueConstraint("token_digest", name="b2b_portal_invite_digest"),
        Index("ix_b2b_portal_invite_expiry", "workspace_id", "state", "expires_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    agency_client_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    client_workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    invited_email_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    token_prefix: Mapped[str] = mapped_column(String(24), nullable=False)
    token_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    token_key_version: Mapped[str] = mapped_column(String(80), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, default=PortalInvitationState.PENDING.value
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_by: Mapped[UUID | None] = mapped_column(index=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invited_by: Mapped[UUID] = mapped_column(nullable=False)


class PortalAccessGrant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "b2b_portal_access_grants"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="b2b_portal_grant_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "agency_client_id"],
            ["b2b_agency_clients.workspace_id", "b2b_agency_clients.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "agency_client_id", "client_workspace_id"],
            [
                "b2b_agency_clients.workspace_id",
                "b2b_agency_clients.id",
                "b2b_agency_clients.client_workspace_id",
            ],
            name="fk_b2b_portal_grant_client_identity",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id", "agency_client_id", "user_id", name="b2b_portal_grant_user"
        ),
        CheckConstraint("workspace_id <> client_workspace_id", name="b2b_portal_client_distinct"),
        CheckConstraint("lock_version > 0", name="b2b_portal_grant_lock_positive"),
        Index("ix_b2b_portal_grant_client", "client_workspace_id", "user_id", "state"),
    )

    # Owned by agency RLS. Switch to the client workspace only after validating this row.
    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    agency_client_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    client_workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    user_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    scopes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, default=PortalGrantState.ACTIVE.value
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revocation_reason: Mapped[str | None] = mapped_column(Text)
    granted_by: Mapped[UUID] = mapped_column(nullable=False)
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class WhiteLabelConfigVersion(UUIDPrimaryKeyMixin, Base):
    """Immutable branding and verified-domain configuration."""

    __tablename__ = "b2b_white_label_versions"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="b2b_white_label_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "agency_id"],
            ["b2b_agencies.workspace_id", "b2b_agencies.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("workspace_id", "agency_id", "version", name="b2b_white_label_version"),
        UniqueConstraint("custom_domain", name="b2b_white_label_domain"),
        CheckConstraint("version > 0", name="b2b_white_label_version_positive"),
        Index("ix_b2b_white_label_state", "workspace_id", "domain_state", "created_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    agency_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    custom_domain: Mapped[str | None] = mapped_column(String(253))
    domain_state: Mapped[str] = mapped_column(
        String(16), nullable=False, default=DomainVerificationState.UNVERIFIED.value
    )
    dns_challenge_hash: Mapped[str | None] = mapped_column(String(64))
    dns_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    certificate_ref: Mapped[str | None] = mapped_column(String(512))
    logo_asset_ref: Mapped[str | None] = mapped_column(String(1_000))
    email_sender_domain: Mapped[str | None] = mapped_column(String(253))
    email_sender_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    branding: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ClientProvisioningRequest(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Asynchronous request; QUEUED is not an invented workspace success state."""

    __tablename__ = "b2b_client_provisioning_requests"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="b2b_provisioning_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "agency_id"],
            ["b2b_agencies.workspace_id", "b2b_agencies.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id", "requested_by", "idempotency_key", name="b2b_provisioning_idempotency"
        ),
        CheckConstraint("lock_version > 0", name="lock_positive"),
        Index("ix_b2b_provisioning_state", "workspace_id", "state", "created_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    agency_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    requested_by: Mapped[UUID] = mapped_column(nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    workspace_request: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    requested_permissions: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, default=ProvisioningState.QUEUED.value
    )
    provisioned_workspace_id: Mapped[UUID | None] = mapped_column(index=True)
    provider_operation_ref: Mapped[str | None] = mapped_column(String(500))
    error_code: Mapped[str | None] = mapped_column(String(120))
    error_detail: Mapped[str | None] = mapped_column(Text)
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class AgencyCreditAllocationPolicy(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "b2b_credit_allocation_policies"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="b2b_allocation_policy_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "agency_client_id"],
            ["b2b_agency_clients.workspace_id", "b2b_agency_clients.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id", "agency_client_id", "version", name="b2b_allocation_policy_version"
        ),
        CheckConstraint("version > 0", name="version_positive"),
        CheckConstraint("monthly_credit_limit >= 0", name="limit_nonnegative"),
        Index("ix_b2b_allocation_policy_effective", "workspace_id", "effective_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    agency_client_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    monthly_credit_limit: Mapped[Decimal] = mapped_column(Numeric(19, 6), nullable=False)
    overage_policy: Mapped[str] = mapped_column(String(24), nullable=False)
    policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AgencyCostAllocationRecord(UUIDPrimaryKeyMixin, Base):
    """Append-only reporting allocation, sourced from authoritative usage/credit records."""

    __tablename__ = "b2b_cost_allocation_records"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="b2b_cost_allocation_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "agency_client_id"],
            ["b2b_agency_clients.workspace_id", "b2b_agency_clients.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "agency_client_id", "client_workspace_id"],
            [
                "b2b_agency_clients.workspace_id",
                "b2b_agency_clients.id",
                "b2b_agency_clients.client_workspace_id",
            ],
            name="fk_b2b_cost_allocation_client_identity",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "allocation_policy_id"],
            [
                "b2b_credit_allocation_policies.workspace_id",
                "b2b_credit_allocation_policies.id",
            ],
            name="fk_b2b_cost_allocation_policy_workspace",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["client_workspace_id", "source_usage_record_id"],
            ["billing_usage_records.workspace_id", "billing_usage_records.id"],
            name="fk_b2b_cost_allocation_client_usage",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id",
            "source_usage_record_id",
            name="b2b_cost_allocation_source",
        ),
        CheckConstraint("credit_amount >= 0", name="credit_nonnegative"),
        CheckConstraint("internal_cost >= 0", name="cost_nonnegative"),
        Index("ix_b2b_cost_allocation_client", "workspace_id", "agency_client_id", "occurred_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    agency_client_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    client_workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    source_usage_record_id: Mapped[UUID] = mapped_column(nullable=False)
    allocation_policy_id: Mapped[UUID] = mapped_column(nullable=False)
    metric_key: Mapped[str] = mapped_column(String(160), nullable=False)
    credit_amount: Mapped[Decimal] = mapped_column(Numeric(19, 6), nullable=False)
    internal_cost: Mapped[Decimal] = mapped_column(Numeric(19, 6), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SlaPolicyVersion(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "b2b_sla_policy_versions"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="b2b_sla_policy_workspace_id"),
        UniqueConstraint("workspace_id", "name", "version", name="b2b_sla_policy_version"),
        CheckConstraint("version > 0", name="b2b_sla_policy_version_positive"),
        CheckConstraint("target_minutes > 0", name="b2b_sla_target_positive"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    target_kind: Mapped[str] = mapped_column(String(80), nullable=False)
    target_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    business_calendar: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    escalation_steps: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SlaCase(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "b2b_sla_cases"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="b2b_sla_case_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "policy_version_id"],
            ["b2b_sla_policy_versions.workspace_id", "b2b_sla_policy_versions.id"],
            name="fk_b2b_sla_case_policy_workspace",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "agency_client_id", "client_workspace_id"],
            [
                "b2b_agency_clients.workspace_id",
                "b2b_agency_clients.id",
                "b2b_agency_clients.client_workspace_id",
            ],
            name="fk_b2b_sla_case_client_identity",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("workspace_id", "subject_type", "subject_id", name="b2b_sla_case_subject"),
        Index("ix_b2b_sla_case_due", "workspace_id", "state", "due_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    client_workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    agency_client_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    policy_version_id: Mapped[UUID] = mapped_column(nullable=False)
    subject_type: Mapped[str] = mapped_column(String(80), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(500), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default=SlaCaseState.OPEN.value)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    breached_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    escalation_level: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


def _reject_immutable_b2b_row(_mapper: object, _connection: object, target: object) -> None:
    raise ValueError(f"{type(target).__name__} is append-only")


for _immutable_model in (
    WhiteLabelConfigVersion,
    AgencyCreditAllocationPolicy,
    AgencyCostAllocationRecord,
    SlaPolicyVersion,
):
    event.listen(_immutable_model, "before_update", _reject_immutable_b2b_row)
    event.listen(_immutable_model, "before_delete", _reject_immutable_b2b_row)
