"""Tenant-isolated persistence for durable CMS publishing and manual Naver packages."""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
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
from blogops.domain.jobs.state import JobState, StepState
from blogops.domain.publishing.enums import (
    ConnectionState,
    NaverChecklistState,
    PublishedPostState,
)


class PublishingConnection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "publishing_connections"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="publishing_connection_workspace_id"),
        UniqueConstraint(
            "workspace_id", "provider", "name", name="publishing_connection_name"
        ),
        CheckConstraint("lock_version > 0", name="publishing_connection_lock_positive"),
        Index("ix_publishing_connection_state", "workspace_id", "provider", "state"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    site_url: Mapped[str] = mapped_column(String(2_048), nullable=False)
    site_timezone: Mapped[str] = mapped_column(String(80), nullable=False)
    remote_site_id: Mapped[str | None] = mapped_column(String(500))
    official_contract: Mapped[str] = mapped_column(String(120), nullable=False)
    api_version: Mapped[str] = mapped_column(String(80), nullable=False)
    api_deprecation_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    credential_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    credential_expiry_notified_for: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    credential_secret_ref: Mapped[str] = mapped_column(String(512), nullable=False)
    state: Mapped[str] = mapped_column(
        String(24), nullable=False, default=ConnectionState.PENDING.value
    )
    capabilities: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    safe_config_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    site_settings_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    site_settings_hash: Mapped[str | None] = mapped_column(String(64))
    last_diagnosed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(120))
    disconnected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class PublishingConnectionJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "publishing_connection_jobs"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="publishing_connection_job_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            ["publishing_connections.workspace_id", "publishing_connections.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id",
            "requested_by",
            "operation",
            "idempotency_key",
            name="publishing_connection_job_idempotency",
        ),
        CheckConstraint("attempt >= 0", name="attempt_nonnegative"),
        CheckConstraint("max_attempts > 0", name="max_attempts_positive"),
        Index("ix_publishing_connection_job_state", "workspace_id", "state", "created_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    connection_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    requested_by: Mapped[UUID] = mapped_column(nullable=False)
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default=JobState.QUEUED.value)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expected_lock_version: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    checks_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    safe_result_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(120))
    error_detail: Mapped[str | None] = mapped_column(Text)
    retry_after_seconds: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PublicationPolicy(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "publishing_policies"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="publishing_policy_workspace_id"),
        UniqueConstraint("workspace_id", "version", name="publishing_policy_version"),
        UniqueConstraint("workspace_id", "snapshot_hash", name="publishing_policy_hash"),
        CheckConstraint("version > 0", name="publishing_policy_version_positive"),
        Index("ix_publishing_policy_latest", "workspace_id", "version"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    daily_quotas: Mapped[dict[str, int]] = mapped_column(JSONB, nullable=False)
    max_schedule_days: Mapped[int] = mapped_column(Integer, nullable=False)
    allowed_providers: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    require_media_license: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    allowed_custom_contracts: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    naver_policy_version: Mapped[str] = mapped_column(String(80), nullable=False)
    snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PublishQuotaUsage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "publish_quota_usages"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="publish_quota_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            ["publishing_connections.workspace_id", "publishing_connections.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "policy_id"],
            ["publishing_policies.workspace_id", "publishing_policies.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id",
            "connection_id",
            "provider",
            "channel",
            "local_day",
            name="publish_quota_day",
        ),
        CheckConstraint("reserved_count >= 0", name="publish_quota_reserved_nonnegative"),
        CheckConstraint("completed_count >= 0", name="publish_quota_completed_nonnegative"),
        CheckConstraint("lock_version > 0", name="publish_quota_lock_positive"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    connection_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    policy_id: Mapped[UUID] = mapped_column(nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    channel: Mapped[str] = mapped_column(String(80), nullable=False)
    local_day: Mapped[date] = mapped_column(Date, nullable=False)
    timezone_name: Mapped[str] = mapped_column(String(80), nullable=False)
    quota_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    reserved_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class PublishJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Durable parent job; ``state`` is exclusively a shared JobState value."""

    __tablename__ = "publish_jobs"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="publish_job_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "content_id"],
            ["contents.workspace_id", "contents.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "content_version_id", "content_hash"],
            [
                "content_versions.workspace_id",
                "content_versions.id",
                "content_versions.content_hash",
            ],
            name="fk_publish_job_exact_content_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            ["publishing_connections.workspace_id", "publishing_connections.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "workspace_id",
                "approval_request_id",
                "content_id",
                "content_version_id",
                "content_hash",
            ],
            [
                "content_approval_requests.workspace_id",
                "content_approval_requests.id",
                "content_approval_requests.content_id",
                "content_approval_requests.content_version_id",
                "content_approval_requests.content_hash",
            ],
            name="fk_publish_job_exact_approval",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "policy_id"],
            ["publishing_policies.workspace_id", "publishing_policies.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "target_published_post_id"],
            ["published_posts.workspace_id", "published_posts.id"],
            name="fk_publish_job_target_post",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        UniqueConstraint(
            "workspace_id",
            "requested_by",
            "operation",
            "idempotency_key",
            name="publish_job_idempotency",
        ),
        CheckConstraint("attempt >= 0", name="publish_job_attempt_nonnegative"),
        CheckConstraint("max_attempts > 0", name="publish_job_max_attempts_positive"),
        CheckConstraint("lock_version > 0", name="publish_job_lock_positive"),
        Index("ix_publish_job_state_schedule", "workspace_id", "state", "scheduled_at_utc"),
        Index("ix_publish_job_content", "workspace_id", "content_id", "created_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    requested_by: Mapped[UUID] = mapped_column(nullable=False, index=True)
    operation: Mapped[str] = mapped_column(String(24), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default=JobState.QUEUED.value)
    content_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    content_version_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    connection_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    approval_request_id: Mapped[UUID] = mapped_column(nullable=False)
    approval_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_id: Mapped[UUID] = mapped_column(nullable=False)
    policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    policy_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    target_published_post_id: Mapped[UUID | None] = mapped_column(index=True)
    visibility: Mapped[str] = mapped_column(String(24), nullable=False)
    scheduled_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    scheduled_local: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    site_timezone: Mapped[str] = mapped_column(String(80), nullable=False)
    dst_fold: Mapped[int | None] = mapped_column(Integer)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_marker: Mapped[str] = mapped_column(String(120), nullable=False)
    input_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    input_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retry_after_seconds: Mapped[int | None] = mapped_column(Integer)
    quota_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    quota_released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(120))
    error_detail: Mapped[str | None] = mapped_column(Text)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class PublishSagaStep(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "publish_saga_steps"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="publish_step_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "job_id"],
            ["publish_jobs.workspace_id", "publish_jobs.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("workspace_id", "job_id", "sequence", name="publish_step_sequence"),
        CheckConstraint("sequence > 0", name="publish_step_sequence_positive"),
        CheckConstraint("attempt >= 0", name="publish_step_attempt_nonnegative"),
        CheckConstraint("lock_version > 0", name="publish_step_lock_positive"),
        Index("ix_publish_step_job", "workspace_id", "job_id", "sequence"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    job_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    step_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default=StepState.PENDING.value)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    request_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    response_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(120))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class PublishAttempt(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "publish_attempts"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="publish_attempt_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "job_id"],
            ["publish_jobs.workspace_id", "publish_jobs.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "step_id"],
            ["publish_saga_steps.workspace_id", "publish_saga_steps.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id", "job_id", "step_id", "attempt_number", name="publish_attempt_number"
        ),
        CheckConstraint("attempt_number > 0", name="publish_attempt_positive"),
        Index("ix_publish_attempt_job", "workspace_id", "job_id", "created_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    job_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    step_id: Mapped[UUID] = mapped_column(nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    provider_request_id: Mapped[str | None] = mapped_column(String(500))
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    endpoint_path: Mapped[str] = mapped_column(String(1_000), nullable=False)
    request_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    retry_class: Mapped[str] = mapped_column(String(24), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(120))
    remote_id: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PublishedPost(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "published_posts"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="published_post_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "content_id"],
            ["contents.workspace_id", "contents.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "content_version_id", "content_hash"],
            [
                "content_versions.workspace_id",
                "content_versions.id",
                "content_versions.content_hash",
            ],
            name="fk_published_post_exact_content_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            ["publishing_connections.workspace_id", "publishing_connections.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "workspace_id",
                "approval_request_id",
                "content_id",
                "content_version_id",
                "content_hash",
            ],
            [
                "content_approval_requests.workspace_id",
                "content_approval_requests.id",
                "content_approval_requests.content_id",
                "content_approval_requests.content_version_id",
                "content_approval_requests.content_hash",
            ],
            name="fk_published_post_exact_approval",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "created_by_job_id"],
            ["publish_jobs.workspace_id", "publish_jobs.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "naver_package_id"],
            ["naver_publish_packages.workspace_id", "naver_publish_packages.id"],
            name="fk_published_post_naver_package",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        UniqueConstraint(
            "workspace_id", "connection_id", "remote_id", name="published_post_remote_identity"
        ),
        UniqueConstraint(
            "workspace_id", "created_by_job_id", name="published_post_create_job"
        ),
        CheckConstraint("lock_version > 0", name="published_post_lock_positive"),
        Index("ix_published_post_content", "workspace_id", "content_id", "updated_at"),
        Index("ix_published_post_state", "workspace_id", "provider", "state"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    content_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    content_version_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    approval_request_id: Mapped[UUID] = mapped_column(nullable=False)
    connection_id: Mapped[UUID | None] = mapped_column(index=True)
    created_by_job_id: Mapped[UUID | None] = mapped_column(index=True)
    naver_package_id: Mapped[UUID | None] = mapped_column(index=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    remote_site_id: Mapped[str | None] = mapped_column(String(500))
    remote_id: Mapped[str] = mapped_column(String(500), nullable=False)
    remote_url: Mapped[str] = mapped_column(String(2_048), nullable=False)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default=PublishedPostState.DRAFT.value
    )
    remote_etag: Mapped[str | None] = mapped_column(String(500))
    remote_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    remote_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    local_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    last_reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    conflict_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class RemotePostSnapshot(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "publishing_remote_snapshots"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="remote_snapshot_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "published_post_id"],
            ["published_posts.workspace_id", "published_posts.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "captured_by_job_id"],
            ["publish_jobs.workspace_id", "publish_jobs.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id", "published_post_id", "snapshot_hash", name="remote_snapshot_hash"
        ),
        Index("ix_remote_snapshot_post", "workspace_id", "published_post_id", "captured_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    published_post_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    captured_by_job_id: Mapped[UUID] = mapped_column(nullable=False)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    remote_etag: Mapped[str | None] = mapped_column(String(500))
    remote_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PublishedMediaBinding(UUIDPrimaryKeyMixin, Base):
    """Append-only remote media reuse fence preventing duplicate uploads."""

    __tablename__ = "published_media_bindings"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="published_media_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            ["publishing_connections.workspace_id", "publishing_connections.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "media_version_id"],
            ["media_versions.workspace_id", "media_versions.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "uploaded_by_job_id"],
            ["publish_jobs.workspace_id", "publish_jobs.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id",
            "connection_id",
            "media_version_id",
            "media_content_hash",
            name="published_media_reuse_identity",
        ),
        UniqueConstraint(
            "workspace_id", "connection_id", "remote_media_id", name="published_media_remote"
        ),
        Index("ix_published_media_version", "workspace_id", "media_version_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    connection_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    media_version_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    media_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    remote_media_id: Mapped[str] = mapped_column(String(500), nullable=False)
    remote_url: Mapped[str] = mapped_column(String(2_048), nullable=False)
    uploaded_by_job_id: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class NaverPublishPackage(UUIDPrimaryKeyMixin, Base):
    """Immutable user-handoff package. It never contains account credentials or cookies."""

    __tablename__ = "naver_publish_packages"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="naver_package_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "content_id"],
            ["contents.workspace_id", "contents.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "content_version_id", "content_hash"],
            [
                "content_versions.workspace_id",
                "content_versions.id",
                "content_versions.content_hash",
            ],
            name="fk_naver_package_exact_content_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "workspace_id",
                "approval_request_id",
                "content_id",
                "content_version_id",
                "content_hash",
            ],
            [
                "content_approval_requests.workspace_id",
                "content_approval_requests.id",
                "content_approval_requests.content_id",
                "content_approval_requests.content_version_id",
                "content_approval_requests.content_hash",
            ],
            name="fk_naver_package_exact_approval",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "previous_package_id"],
            ["naver_publish_packages.workspace_id", "naver_publish_packages.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("workspace_id", "package_hash", name="naver_package_hash"),
        Index("ix_naver_package_content", "workspace_id", "content_id", "created_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    content_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    content_version_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    approval_request_id: Mapped[UUID] = mapped_column(nullable=False)
    approval_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    previous_package_id: Mapped[UUID | None] = mapped_column(index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    formatted_blocks: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    copy_manifest: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    image_manifest: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    image_order: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    checklist: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    diff_manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    unsupported_blocks: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(80), nullable=False)
    policy_notice: Mapped[str] = mapped_column(Text, nullable=False)
    policy_notice_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    app_launch_url: Mapped[str | None] = mapped_column(String(2_048))
    package_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class NaverPolicyAcknowledgement(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "naver_policy_acknowledgements"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="naver_ack_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "package_id"],
            ["naver_publish_packages.workspace_id", "naver_publish_packages.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id", "user_id", "policy_version", name="naver_ack_user_policy"
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    package_id: Mapped[UUID] = mapped_column(nullable=False)
    user_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    policy_version: Mapped[str] = mapped_column(String(80), nullable=False)
    notice_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    acknowledged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class NaverChecklistEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "naver_checklist_events"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="naver_checklist_event_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "package_id"],
            ["naver_publish_packages.workspace_id", "naver_publish_packages.id"],
            ondelete="RESTRICT",
        ),
        Index("ix_naver_checklist_package", "workspace_id", "package_id", "created_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    package_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    checklist_key: Mapped[str] = mapped_column(String(120), nullable=False)
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, default=NaverChecklistState.CHECKED.value
    )
    actor_id: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class NaverManualConfirmation(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "naver_manual_confirmations"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="naver_confirmation_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "package_id"],
            ["naver_publish_packages.workspace_id", "naver_publish_packages.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "published_post_id"],
            ["published_posts.workspace_id", "published_posts.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("workspace_id", "package_id", name="naver_confirmation_package"),
        UniqueConstraint("workspace_id", "remote_url", name="naver_confirmation_url"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    package_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    published_post_id: Mapped[UUID] = mapped_column(nullable=False)
    remote_url: Mapped[str] = mapped_column(String(2_048), nullable=False)
    remote_post_id: Mapped[str] = mapped_column(String(160), nullable=False)
    confirmed_by: Mapped[UUID] = mapped_column(nullable=False)
    confirmed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PublishingNotification(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "publishing_notifications"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="publishing_notification_workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "publish_job_id"],
            ["publish_jobs.workspace_id", "publish_jobs.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "naver_package_id"],
            ["naver_publish_packages.workspace_id", "naver_publish_packages.id"],
            ondelete="RESTRICT",
        ),
        Index("ix_publishing_notification_due", "workspace_id", "due_at", "delivered_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    recipient_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    publish_job_id: Mapped[UUID | None] = mapped_column(index=True)
    naver_package_id: Mapped[UUID | None] = mapped_column(index=True)
    notification_type: Mapped[str] = mapped_column(String(80), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


def _reject_immutable_publishing_row(
    _mapper: object, _connection: object, target: object
) -> None:
    raise ValueError(f"{type(target).__name__} rows are immutable")


for _immutable_model in (
    PublicationPolicy,
    PublishAttempt,
    RemotePostSnapshot,
    PublishedMediaBinding,
    NaverPublishPackage,
    NaverPolicyAcknowledgement,
    NaverChecklistEvent,
    NaverManualConfirmation,
):
    event.listen(_immutable_model, "before_update", _reject_immutable_publishing_row)
    event.listen(_immutable_model, "before_delete", _reject_immutable_publishing_row)
