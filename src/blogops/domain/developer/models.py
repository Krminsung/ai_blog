"""API credentials, rate policies, idempotency and outbound webhook persistence."""

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
from blogops.domain.developer.enums import (
    ApiKeyState,
    WebhookDeliveryState,
    WebhookEndpointState,
)


class ApiKey(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """API key metadata. The raw credential is deliberately not representable here."""

    __tablename__ = "developer_api_keys"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="developer_api_key_workspace_id"),
        UniqueConstraint("prefix", name="developer_api_key_prefix"),
        UniqueConstraint("secret_digest", name="developer_api_key_digest"),
        ForeignKeyConstraint(
            ["workspace_id", "rotated_from_id"],
            ["developer_api_keys.workspace_id", "developer_api_keys.id"],
            name="fk_developer_api_key_rotated_from",
            ondelete="RESTRICT",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "rotated_to_id"],
            ["developer_api_keys.workspace_id", "developer_api_keys.id"],
            name="fk_developer_api_key_rotated_to",
            ondelete="RESTRICT",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint("generation > 0", name="developer_api_key_generation_positive"),
        CheckConstraint("lock_version > 0", name="developer_api_key_lock_positive"),
        Index("ix_developer_api_key_state", "workspace_id", "state", "expires_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    prefix: Mapped[str] = mapped_column(String(32), nullable=False)
    secret_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    hash_key_version: Mapped[str] = mapped_column(String(80), nullable=False)
    environment: Mapped[str] = mapped_column(String(16), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    ip_allowlist: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    endpoint_allowlist: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default=ApiKeyState.ACTIVE.value)
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    rotated_from_id: Mapped[UUID | None] = mapped_column(index=True)
    rotated_to_id: Mapped[UUID | None] = mapped_column(index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_ip_hash: Mapped[str | None] = mapped_column(String(64))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revocation_reason: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class ApiRateLimitPolicy(UUIDPrimaryKeyMixin, Base):
    """Immutable policy; workspace, endpoint and optional key rules are all enforced."""

    __tablename__ = "developer_rate_limit_policies"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="developer_rate_policy_workspace_id"),
        UniqueConstraint(
            "workspace_id", "scope_kind", "scope_ref", "endpoint_pattern", "version",
            name="developer_rate_policy_version",
        ),
        CheckConstraint("version > 0", name="version_positive"),
        CheckConstraint("request_limit > 0", name="limit_positive"),
        CheckConstraint("window_seconds > 0", name="window_positive"),
        CheckConstraint("burst >= 0", name="burst_nonnegative"),
        Index("ix_developer_rate_policy_active", "workspace_id", "active_from", "active_until"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    scope_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_ref: Mapped[str] = mapped_column(String(500), nullable=False)
    endpoint_pattern: Mapped[str] = mapped_column(String(500), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    request_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    window_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    burst: Mapped[int] = mapped_column(Integer, nullable=False)
    concurrent_limit: Mapped[int | None] = mapped_column(Integer)
    policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    active_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    active_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ApiIdempotencyRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "developer_idempotency_records"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="developer_idempotency_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "api_key_id"],
            ["developer_api_keys.workspace_id", "developer_api_keys.id"],
            name="fk_developer_idempotency_api_key_workspace",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id", "api_key_id", "operation", "idempotency_key",
            name="developer_idempotency_key",
        ),
        Index("ix_developer_idempotency_expiry", "expires_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    api_key_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    operation: Mapped[str] = mapped_column(String(160), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_object_ref: Mapped[str | None] = mapped_column(String(1_000))
    response_hash: Mapped[str | None] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="PROCESSING")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OAuthApplication(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "developer_oauth_apps"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="developer_oauth_app_workspace_id"),
        UniqueConstraint("client_id", name="developer_oauth_client_id"),
        UniqueConstraint("client_secret_digest", name="developer_oauth_secret_digest"),
        Index("ix_developer_oauth_app_state", "workspace_id", "state"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    client_id: Mapped[str] = mapped_column(String(120), nullable=False)
    client_secret_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    hash_key_version: Mapped[str] = mapped_column(String(80), nullable=False)
    redirect_uris: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    allowed_scopes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WebhookEndpoint(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "developer_webhook_endpoints"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="developer_webhook_endpoint_workspace_id"),
        UniqueConstraint("workspace_id", "normalized_url", name="developer_webhook_endpoint_url"),
        CheckConstraint("failure_count >= 0", name="failures_nonnegative"),
        CheckConstraint(
            "failure_disable_threshold > 0", name="disable_threshold_positive"
        ),
        CheckConstraint("lock_version > 0", name="lock_positive"),
        Index("ix_developer_webhook_endpoint_state", "workspace_id", "state"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    normalized_url: Mapped[str] = mapped_column(String(2_048), nullable=False)
    hostname: Mapped[str] = mapped_column(String(253), nullable=False)
    event_types: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    secret_ref: Mapped[str] = mapped_column(String(512), nullable=False)
    secret_version: Mapped[str] = mapped_column(String(80), nullable=False)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default=WebhookEndpointState.PENDING_VERIFICATION.value
    )
    verification_challenge_digest: Mapped[str | None] = mapped_column(String(64))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_disable_threshold: Mapped[int] = mapped_column(Integer, nullable=False)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disabled_reason: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class WebhookEvent(UUIDPrimaryKeyMixin, Base):
    """Immutable event envelope; sensitive payload lives in a private object."""

    __tablename__ = "developer_webhook_events"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="developer_webhook_event_workspace_id"),
        UniqueConstraint("workspace_id", "source_event_id", name="developer_webhook_source_event"),
        Index("ix_developer_webhook_event_type", "workspace_id", "event_type", "occurred_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    source_event_id: Mapped[str] = mapped_column(String(500), nullable=False)
    event_type: Mapped[str] = mapped_column(String(160), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(80), nullable=False)
    payload_object_ref: Mapped[str] = mapped_column(String(1_000), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_preview: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class WebhookDelivery(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "developer_webhook_deliveries"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="developer_webhook_delivery_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "endpoint_id"],
            ["developer_webhook_endpoints.workspace_id", "developer_webhook_endpoints.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "event_id"],
            ["developer_webhook_events.workspace_id", "developer_webhook_events.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id",
            "endpoint_id",
            "event_id",
            name="developer_webhook_delivery_once",
        ),
        CheckConstraint("attempt_count >= 0", name="attempts_nonnegative"),
        CheckConstraint(
            "cycle_attempt_count >= 0", name="cycle_attempts_nonnegative"
        ),
        CheckConstraint("max_attempts > 0", name="max_attempts_positive"),
        CheckConstraint("manual_replay_count >= 0", name="replays_nonnegative"),
        CheckConstraint(
            "manual_replay_limit >= 0",
            name="replay_limit_nonnegative",
        ),
        CheckConstraint("lock_version > 0", name="lock_positive"),
        Index("ix_developer_webhook_delivery_claim", "state", "next_attempt_at", "created_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    endpoint_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    event_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    state: Mapped[str] = mapped_column(
        String(24), nullable=False, default=WebhookDeliveryState.PENDING.value
    )
    retry_policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cycle_attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    manual_replay_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    manual_replay_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dead_lettered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(120))
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class WebhookDeliveryAttempt(UUIDPrimaryKeyMixin, Base):
    """Append-only delivery evidence with masked response details."""

    __tablename__ = "developer_webhook_attempts"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="developer_webhook_attempt_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "delivery_id"],
            ["developer_webhook_deliveries.workspace_id", "developer_webhook_deliveries.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id",
            "delivery_id",
            "attempt_no",
            name="developer_webhook_attempt_no",
        ),
        CheckConstraint("attempt_no > 0", name="attempt_positive"),
        CheckConstraint("delivery_cycle >= 0", name="cycle_nonnegative"),
        CheckConstraint("cycle_attempt_no > 0", name="cycle_attempt_positive"),
        CheckConstraint("duration_ms >= 0", name="duration_nonnegative"),
        Index("ix_developer_webhook_attempt_delivery", "workspace_id", "delivery_id", "attempt_no"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    delivery_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    delivery_cycle: Mapped[int] = mapped_column(Integer, nullable=False)
    cycle_attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    event_external_id: Mapped[str] = mapped_column(String(500), nullable=False)
    destination_url: Mapped[str] = mapped_column(String(2_048), nullable=False)
    resolved_addresses: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    signature_version: Mapped[str] = mapped_column(String(16), nullable=False)
    secret_version: Mapped[str] = mapped_column(String(80), nullable=False)
    request_timestamp: Mapped[int] = mapped_column(nullable=False)
    outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_headers_masked: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False)
    response_body_hash: Mapped[str | None] = mapped_column(String(64))
    response_body_preview_masked: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(120))
    attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class WebhookReplayReceipt(UUIDPrimaryKeyMixin, Base):
    """Replay cache persistence for signed inbound handshakes/callbacks."""

    __tablename__ = "developer_webhook_replay_receipts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "endpoint_id"],
            ["developer_webhook_endpoints.workspace_id", "developer_webhook_endpoints.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("workspace_id", "replay_key", name="developer_webhook_replay_once"),
        Index("ix_developer_webhook_replay_expiry", "expires_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    endpoint_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    replay_key: Mapped[str] = mapped_column(String(64), nullable=False)
    event_id: Mapped[str] = mapped_column(String(500), nullable=False)
    timestamp: Mapped[int] = mapped_column(nullable=False)
    body_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


def _reject_immutable_developer_row(_mapper: object, _connection: object, target: object) -> None:
    raise ValueError(f"{type(target).__name__} is append-only")


for _immutable_model in (
    ApiRateLimitPolicy,
    WebhookEvent,
    WebhookDeliveryAttempt,
    WebhookReplayReceipt,
):
    event.listen(_immutable_model, "before_update", _reject_immutable_developer_row)
    event.listen(_immutable_model, "before_delete", _reject_immutable_developer_row)
