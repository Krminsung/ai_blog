"""Auditable platform operations, support access, tickets and notifications."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from blogops.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from blogops.domain.admin.enums import (
    AdminCommandState,
    AdminSessionState,
    NotificationDeliveryState,
    SupportAccessState,
    SupportTicketState,
)


class SupportAccessRequest(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "admin_support_access_requests"
    __table_args__ = (
        UniqueConstraint("target_workspace_id", "id", name="admin_support_access_target_id"),
        UniqueConstraint(
            "target_workspace_id", "requested_by", "idempotency_key",
            name="admin_support_access_idempotency",
        ),
        CheckConstraint("requested_minutes > 0", name="admin_support_minutes_positive"),
        CheckConstraint("lock_version > 0", name="admin_support_access_lock_positive"),
        Index("ix_admin_support_access_state", "state", "expires_at", "created_at"),
    )

    target_workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    requested_by: Mapped[UUID] = mapped_column(nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    ticket_ref: Mapped[str] = mapped_column(String(500), nullable=False)
    requested_scopes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    requested_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    content_access_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    state: Mapped[str] = mapped_column(
        String(24), nullable=False, default=SupportAccessState.PENDING_CUSTOMER.value
    )
    customer_approved_by: Mapped[UUID | None] = mapped_column(index=True)
    customer_approved_scopes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    customer_approved_content: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    customer_decision_reason: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class AdminElevationSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "admin_elevation_sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["target_workspace_id", "access_request_id"],
            [
                "admin_support_access_requests.target_workspace_id",
                "admin_support_access_requests.id",
            ],
            name="fk_admin_elevation_support_workspace",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "target_workspace_id",
            "id",
            name="admin_elevation_target_id",
        ),
        UniqueConstraint(
            "access_request_id",
            "operator_id",
            name="admin_elevation_access_operator",
        ),
        CheckConstraint("lock_version > 0", name="admin_elevation_lock_positive"),
        Index("ix_admin_elevation_active", "operator_id", "state", "expires_at"),
    )

    access_request_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    target_workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    operator_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    scopes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    content_is_masked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, default=AdminSessionState.ACTIVE.value
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoke_reason: Mapped[str | None] = mapped_column(Text)
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class AdminAction(UUIDPrimaryKeyMixin, Base):
    """Append-only record for every material operator read or mutation."""

    __tablename__ = "admin_actions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["target_workspace_id", "elevation_session_id"],
            [
                "admin_elevation_sessions.target_workspace_id",
                "admin_elevation_sessions.id",
            ],
            name="fk_admin_action_elevation_workspace",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("operator_id", "idempotency_key", name="admin_action_idempotency"),
        Index("ix_admin_action_target", "target_workspace_id", "occurred_at", "id"),
        Index("ix_admin_action_operator", "operator_id", "occurred_at", "id"),
    )

    target_workspace_id: Mapped[UUID | None] = mapped_column(index=True)
    operator_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    elevation_session_id: Mapped[UUID | None] = mapped_column(index=True)
    action: Mapped[str] = mapped_column(String(160), nullable=False)
    target_type: Mapped[str] = mapped_column(String(80), nullable=False)
    target_id: Mapped[str] = mapped_column(String(500), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    metadata_masked: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    before_hash: Mapped[str | None] = mapped_column(String(64))
    after_hash: Mapped[str | None] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(128))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AdminCommand(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Approved command envelope; workers/adapters, not this row, establish external success."""

    __tablename__ = "admin_commands"
    __table_args__ = (
        UniqueConstraint("requested_by", "idempotency_key", name="admin_command_idempotency"),
        CheckConstraint("required_approvals > 0", name="admin_command_approvals_positive"),
        CheckConstraint("approval_count >= 0", name="admin_command_approval_count_nonnegative"),
        CheckConstraint(
            "approval_count <= required_approvals",
            name="admin_command_approval_capacity",
        ),
        CheckConstraint("lock_version > 0", name="admin_command_lock_positive"),
        Index("ix_admin_command_queue", "state", "created_at"),
    )

    target_workspace_id: Mapped[UUID | None] = mapped_column(index=True)
    requested_by: Mapped[UUID] = mapped_column(nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(48), nullable=False)
    target_type: Mapped[str] = mapped_column(String(80), nullable=False)
    target_id: Mapped[str] = mapped_column(String(500), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    parameters_masked: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    secure_parameters_ref: Mapped[str | None] = mapped_column(String(1_000))
    required_approvals: Mapped[int] = mapped_column(Integer, nullable=False)
    approval_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    state: Mapped[str] = mapped_column(
        String(24), nullable=False, default=AdminCommandState.PENDING_APPROVAL.value
    )
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_ref: Mapped[str | None] = mapped_column(String(1_000))
    error_code: Mapped[str | None] = mapped_column(String(120))
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class AdminCommandApproval(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "admin_command_approvals"
    __table_args__ = (
        ForeignKeyConstraint(["command_id"], ["admin_commands.id"], ondelete="RESTRICT"),
        UniqueConstraint("command_id", "approver_id", name="admin_command_approver_once"),
        Index("ix_admin_command_approval", "command_id", "decided_at"),
    )

    command_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    approver_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    mfa_verified: Mapped[bool] = mapped_column(Boolean, nullable=False)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class FeatureFlagVersion(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "admin_feature_flag_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["approved_command_id"],
            ["admin_commands.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("flag_key", "version", name="admin_feature_flag_version"),
        CheckConstraint("version > 0", name="admin_feature_flag_version_positive"),
        CheckConstraint(
            "rollout_percent >= 0 AND rollout_percent <= 100",
            name="admin_feature_flag_rollout",
        ),
        Index("ix_admin_feature_flag_effective", "flag_key", "effective_at"),
    )

    flag_key: Mapped[str] = mapped_column(String(160), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    target_workspace_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    target_plan_keys: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    target_regions: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    rollout_percent: Mapped[int] = mapped_column(Integer, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    approved_command_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SupportTicket(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "admin_support_tickets"
    __table_args__ = (
        UniqueConstraint(
            "target_workspace_id",
            "external_ticket_ref",
            name="admin_support_ticket_external",
        ),
        Index("ix_admin_support_ticket_sla", "state", "priority", "due_at"),
    )

    target_workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    external_ticket_ref: Mapped[str] = mapped_column(String(500), nullable=False)
    subject: Mapped[str] = mapped_column(String(300), nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    priority: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(
        String(24), nullable=False, default=SupportTicketState.OPEN.value
    )
    requester_user_id: Mapped[UUID | None] = mapped_column(index=True)
    assigned_operator_id: Mapped[UUID | None] = mapped_column(index=True)
    related_refs: Mapped[list[dict[str, str]]] = mapped_column(JSONB, nullable=False, default=list)
    safe_error_context: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SupportTicketInternalNote(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "admin_support_ticket_notes"
    __table_args__ = (
        ForeignKeyConstraint(["ticket_id"], ["admin_support_tickets.id"], ondelete="RESTRICT"),
        Index("ix_admin_support_note_ticket", "ticket_id", "created_at"),
    )

    ticket_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    author_id: Mapped[UUID] = mapped_column(nullable=False)
    body_object_ref: Mapped[str] = mapped_column(String(1_000), nullable=False)
    body_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class NotificationTemplateVersion(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "notification_template_versions"
    __table_args__ = (
        UniqueConstraint(
            "event_type",
            "channel",
            "locale",
            "version",
            name="notification_template_version",
        ),
        CheckConstraint("version > 0", name="notification_template_version_positive"),
        Index("ix_notification_template_effective", "event_type", "channel", "effective_at"),
    )

    event_type: Mapped[str] = mapped_column(String(160), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    locale: Mapped[str] = mapped_column(String(16), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    subject_template: Mapped[str | None] = mapped_column(Text)
    body_template_ref: Mapped[str] = mapped_column(String(1_000), nullable=False)
    variable_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    template_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    mandatory: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class NotificationPreference(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "notification_preferences"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="notification_preference_workspace_id"),
        UniqueConstraint(
            "workspace_id",
            "user_id",
            "event_type",
            "channel",
            name="notification_preference_user_event",
        ),
        CheckConstraint(
            "digest_hour IS NULL OR digest_hour BETWEEN 0 AND 23",
            name="notification_digest_hour",
        ),
        CheckConstraint("lock_version > 0", name="notification_preference_lock_positive"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    user_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(160), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    frequency: Mapped[str] = mapped_column(String(16), nullable=False)
    digest_hour: Mapped[int | None] = mapped_column(Integer)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    quiet_hours: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class Notification(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="notification_workspace_id"),
        UniqueConstraint(
            "workspace_id",
            "recipient_user_id",
            "deduplication_key",
            name="notification_deduplication",
        ),
        Index(
            "ix_notification_inbox",
            "workspace_id",
            "recipient_user_id",
            "read_at",
            "created_at",
        ),
        Index("ix_notification_snooze", "workspace_id", "snoozed_until"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    recipient_user_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(160), nullable=False)
    priority: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    safe_summary: Mapped[str] = mapped_column(Text, nullable=False)
    action_url: Mapped[str | None] = mapped_column(String(2_048))
    source_type: Mapped[str] = mapped_column(String(80), nullable=False)
    source_id: Mapped[str] = mapped_column(String(500), nullable=False)
    deduplication_key: Mapped[str] = mapped_column(String(255), nullable=False)
    payload_safe: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    mandatory: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    snoozed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NotificationDelivery(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "notification_deliveries"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="notification_delivery_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "notification_id"],
            ["notifications.workspace_id", "notifications.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["template_version_id"],
            ["notification_template_versions.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id",
            "notification_id",
            "channel",
            name="notification_delivery_channel",
        ),
        CheckConstraint("attempt_count >= 0", name="notification_delivery_attempts_nonnegative"),
        CheckConstraint("max_attempts > 0", name="notification_delivery_max_attempts_positive"),
        Index("ix_notification_delivery_queue", "state", "next_attempt_at", "created_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    notification_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    template_version_id: Mapped[UUID] = mapped_column(nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    destination_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, default=NotificationDeliveryState.PENDING.value
    )
    retry_policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_message_ref: Mapped[str | None] = mapped_column(String(500))
    error_code: Mapped[str | None] = mapped_column(String(120))


class WorkInboxItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "notification_work_inbox_items"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="notification_work_item_workspace_id"),
        UniqueConstraint(
            "workspace_id", "assignee_user_id", "subject_type", "subject_id", "task_kind",
            name="notification_work_item_subject",
        ),
        Index(
            "ix_notification_work_item_due",
            "workspace_id",
            "assignee_user_id",
            "state",
            "due_at",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    assignee_user_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    task_kind: Mapped[str] = mapped_column(String(80), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(80), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(500), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="OPEN")
    priority: Mapped[str] = mapped_column(String(16), nullable=False)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    snoozed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    action_url: Mapped[str] = mapped_column(String(2_048), nullable=False)


def _reject_immutable_admin_row(_mapper: object, _connection: object, target: object) -> None:
    raise ValueError(f"{type(target).__name__} is append-only")


for _immutable_model in (
    AdminAction,
    AdminCommandApproval,
    FeatureFlagVersion,
    SupportTicketInternalNote,
    NotificationTemplateVersion,
):
    event.listen(_immutable_model, "before_update", _reject_immutable_admin_row)
    event.listen(_immutable_model, "before_delete", _reject_immutable_admin_row)
