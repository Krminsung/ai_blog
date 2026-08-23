"""Strict public contracts for publishing without credential disclosure."""

from __future__ import annotations

from datetime import datetime
import re
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from blogops.domain.jobs.state import JobState, StepState
from blogops.domain.publishing.enums import (
    ConflictAction,
    ConnectionOperation,
    ConnectionState,
    NaverChecklistState,
    PublishOperation,
    PublishedPostState,
    PublishingProvider,
    PublishVisibility,
)
from blogops.domain.publishing.rules import SECRET_KEY_PATTERN


_TRANSPORT_OVERRIDE_PATTERN = re.compile(
    r"(?i)(endpoint|base[_-]?url|host|path|query|mutation|template|header|redirect|proxy)"
)


class DomainSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True, str_strip_whitespace=True)


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError("timezone-aware datetime is required")
    return value


def _reject_secrets(value: Any, path: str = "config") -> Any:
    if isinstance(value, dict):
        for key, item in value.items():
            if SECRET_KEY_PATTERN.search(str(key)):
                raise ValueError(f"secret-like field is not allowed in {path}: {key}")
            _reject_secrets(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_secrets(item, f"{path}.{index}")
    return value


def _reject_transport_overrides(value: Any, path: str = "safe_config") -> Any:
    if isinstance(value, dict):
        for key, item in value.items():
            if _TRANSPORT_OVERRIDE_PATTERN.search(str(key)):
                raise ValueError(
                    f"transport routing fields are not allowed in {path}: {key}"
                )
            _reject_transport_overrides(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_transport_overrides(item, f"{path}.{index}")
    elif isinstance(value, str) and value.strip().lower().startswith(
        ("http://", "https://")
    ):
        raise ValueError(f"transport URLs are not allowed in {path}")
    return value


class PublishingConnectionCreate(DomainSchema):
    provider: PublishingProvider
    name: str = Field(min_length=1, max_length=160)
    site_url: str = Field(min_length=1, max_length=2_048)
    site_timezone: str = Field(min_length=1, max_length=80)
    remote_site_id: str | None = Field(default=None, max_length=500)
    official_contract: str = Field(min_length=1, max_length=120)
    api_version: str = Field(min_length=1, max_length=80)
    api_deprecation_at: datetime | None = None
    credential_secret_ref: str = Field(min_length=1, max_length=512)
    safe_config: dict[str, Any] = Field(default_factory=dict)

    _deprecation_is_aware = field_validator("api_deprecation_at")(_aware)
    _config_has_no_secrets = field_validator("safe_config")(_reject_secrets)

    @field_validator("official_contract", "api_version")
    @classmethod
    def identifiers_are_safe(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value):
            raise ValueError("provider contract and API version must be safe identifiers")
        return value

    @model_validator(mode="after")
    def contract_is_official(self) -> Self:
        expected = {
            PublishingProvider.WORDPRESS: "wordpress-rest-v2",
            PublishingProvider.GHOST: "ghost-admin-api",
            PublishingProvider.BLOGGER: "google-blogger-v3",
        }
        if self.provider is PublishingProvider.NAVER_MANUAL:
            raise ValueError("Naver is manual-package only and cannot have a credential connection")
        if self.provider is PublishingProvider.CUSTOMER_CMS:
            _reject_transport_overrides(self.safe_config)
        if self.provider in expected and self.official_contract != expected[self.provider]:
            raise ValueError(f"official_contract must be {expected[self.provider]}")
        expected_versions = {
            PublishingProvider.WORDPRESS: "v2",
            PublishingProvider.BLOGGER: "v3",
        }
        if (
            self.provider in expected_versions
            and self.api_version != expected_versions[self.provider]
        ):
            raise ValueError(
                f"api_version must be {expected_versions[self.provider]}"
            )
        if self.provider is PublishingProvider.GHOST and not re.fullmatch(
            r"v[0-9]+\.[0-9]+", self.api_version
        ):
            raise ValueError("Ghost api_version must match v{major}.{minor}")
        safe_keys = {
            PublishingProvider.WORDPRESS: {"rest_meta_allowlist"},
            PublishingProvider.GHOST: set(),
            PublishingProvider.BLOGGER: set(),
        }
        if self.provider in safe_keys and set(self.safe_config).difference(
            safe_keys[self.provider]
        ):
            raise ValueError("safe_config contains fields unused by the official adapter")
        if self.provider is PublishingProvider.WORDPRESS:
            allowlist = self.safe_config.get("rest_meta_allowlist", [])
            if (
                not isinstance(allowlist, list)
                or len(allowlist) > 200
                or any(
                    not isinstance(item, str)
                    or not re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", item)
                    or SECRET_KEY_PATTERN.search(item)
                    for item in allowlist
                )
                or len(allowlist) != len(set(allowlist))
            ):
                raise ValueError("rest_meta_allowlist must contain unique safe field names")
        if self.provider is PublishingProvider.BLOGGER and not self.remote_site_id:
            raise ValueError("Blogger connections require a blog id")
        return self


class PublishingConnectionRead(DomainSchema):
    id: UUID
    workspace_id: UUID
    provider: PublishingProvider
    name: str
    site_url: str
    site_timezone: str
    remote_site_id: str | None
    official_contract: str
    api_version: str
    api_deprecation_at: datetime | None
    credential_expires_at: datetime | None
    state: ConnectionState
    capabilities: list[str]
    safe_config_json: dict[str, Any]
    site_settings_snapshot: dict[str, Any]
    site_settings_hash: str | None
    last_diagnosed_at: datetime | None
    last_success_at: datetime | None
    last_error_code: str | None
    disconnected_at: datetime | None
    created_by: UUID
    lock_version: int
    created_at: datetime
    updated_at: datetime


class ConnectionCommandCreate(DomainSchema):
    expected_lock_version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=255)


class ConnectionJobRead(DomainSchema):
    id: UUID
    workspace_id: UUID
    connection_id: UUID
    requested_by: UUID
    operation: ConnectionOperation
    state: JobState
    idempotency_key: str
    request_hash: str
    attempt: int
    max_attempts: int
    checks_json: list[dict[str, Any]]
    safe_result_json: dict[str, Any] | None
    error_code: str | None
    error_detail: str | None
    retry_after_seconds: int | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class PublicationPolicyCreate(DomainSchema):
    expected_previous_version: int = Field(default=0, ge=0)
    daily_quotas: dict[str, int] = Field(min_length=1, max_length=1_000)
    max_schedule_days: int = Field(default=365, ge=1, le=3_650)
    allowed_providers: list[PublishingProvider] = Field(min_length=1, max_length=5)
    require_media_license: bool = True
    allowed_custom_contracts: list[str] = Field(default_factory=list, max_length=100)
    naver_policy_version: str = Field(min_length=1, max_length=80)

    @model_validator(mode="after")
    def values_are_coherent(self) -> Self:
        if any(value < 1 for value in self.daily_quotas.values()):
            raise ValueError("daily quotas must be positive")
        if len(self.allowed_providers) != len(set(self.allowed_providers)):
            raise ValueError("allowed providers must be unique")
        if len(self.allowed_custom_contracts) != len(set(self.allowed_custom_contracts)):
            raise ValueError("custom contracts must be unique")
        return self


class PublicationPolicyRead(DomainSchema):
    id: UUID
    workspace_id: UUID
    version: int
    daily_quotas: dict[str, int]
    max_schedule_days: int
    allowed_providers: list[PublishingProvider]
    require_media_license: bool
    allowed_custom_contracts: list[str]
    naver_policy_version: str
    snapshot_json: dict[str, Any]
    snapshot_hash: str
    created_by: UUID
    created_at: datetime


class PublishOptions(DomainSchema):
    slug: str | None = Field(default=None, max_length=500)
    excerpt: str | None = Field(default=None, max_length=20_000)
    category_ids: list[str] = Field(default_factory=list, max_length=500)
    category_names: list[str] = Field(default_factory=list, max_length=500)
    tags: list[str] = Field(default_factory=list, max_length=500)
    create_missing_taxonomy: bool = False
    remote_author_id: str | None = Field(default=None, max_length=500)
    featured_media_placement: str | None = Field(default=None, max_length=160)
    canonical_url: str | None = Field(default=None, max_length=2_048)
    comment_status: Literal["open", "closed"] | None = None
    newsletter_id: str | None = Field(default=None, max_length=500)
    send_newsletter: bool = False
    member_visibility: Literal["public", "members", "paid"] | None = None
    tracking: dict[str, str] = Field(default_factory=dict)
    allowed_meta: dict[str, Any] = Field(default_factory=dict)
    unsupported_block_policy: Literal["WARN", "REJECT"] = "WARN"

    _meta_has_no_secrets = field_validator("tracking", "allowed_meta")(_reject_secrets)

    @field_validator("category_ids", "category_names", "tags")
    @classmethod
    def taxonomy_values_are_bounded(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("taxonomy values must be unique")
        if any(not item or len(item) > 500 for item in value):
            raise ValueError("taxonomy values must be non-empty and at most 500 characters")
        return value

    @field_validator("tracking")
    @classmethod
    def tracking_is_bounded(cls, value: dict[str, str]) -> dict[str, str]:
        allowed = {
            "utm_source",
            "utm_medium",
            "utm_campaign",
            "utm_term",
            "utm_content",
        }
        if set(value).difference(allowed):
            raise ValueError("only standard UTM tracking keys are supported")
        if len(value) > 5 or any(
            not item or len(key) > 80 or len(item) > 500
            for key, item in value.items()
        ):
            raise ValueError("tracking keys and values must be non-empty and bounded")
        return value

    @model_validator(mode="after")
    def newsletter_is_explicit(self) -> Self:
        if self.send_newsletter != bool(self.newsletter_id):
            raise ValueError(
                "newsletter_id and send_newsletter=true must be supplied together"
            )
        return self


class PublishCreate(DomainSchema):
    content_version_id: UUID
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    approval_request_id: UUID
    connection_id: UUID
    visibility: PublishVisibility
    scheduled_at_utc: datetime | None = None
    scheduled_local: datetime | None = None
    site_timezone: str | None = Field(default=None, max_length=80)
    dst_fold: Literal[0, 1] | None = None
    options: PublishOptions = Field(default_factory=PublishOptions)

    _utc_is_aware = field_validator("scheduled_at_utc")(_aware)

    @model_validator(mode="after")
    def scheduling_fields_match(self) -> Self:
        scheduled = self.visibility is PublishVisibility.SCHEDULED
        values_present = all(
            item is not None
            for item in (self.scheduled_at_utc, self.scheduled_local, self.site_timezone)
        )
        if scheduled != values_present:
            raise ValueError("scheduled visibility requires UTC, local time and site timezone")
        if not scheduled and any(
            item is not None
            for item in (self.scheduled_at_utc, self.scheduled_local, self.site_timezone, self.dst_fold)
        ):
            raise ValueError("schedule fields are only allowed for scheduled publishing")
        return self


class PublishPreviewCreate(DomainSchema):
    content_version_id: UUID
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    approval_request_id: UUID
    connection_id: UUID
    options: PublishOptions = Field(default_factory=PublishOptions)


class PublishPreviewRead(DomainSchema):
    content_id: UUID
    content_version_id: UUID
    content_hash: str
    connection_id: UUID
    provider: PublishingProvider
    title: str
    html: str
    blocks: list[dict[str, Any]]
    render_hash: str
    media_manifest: list[dict[str, Any]]
    unsupported_blocks: list[dict[str, Any]]
    unsupported_options: list[str]
    approximation_notice: str


class PublishedPostUpdate(DomainSchema):
    expected_lock_version: int = Field(ge=1)
    content_version_id: UUID
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    approval_request_id: UUID
    visibility: PublishVisibility
    scheduled_at_utc: datetime | None = None
    scheduled_local: datetime | None = None
    site_timezone: str | None = Field(default=None, max_length=80)
    dst_fold: Literal[0, 1] | None = None
    expected_remote_etag: str | None = Field(default=None, max_length=500)
    expected_remote_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_remote_updated_at: datetime | None = None
    conflict_action: ConflictAction = ConflictAction.ABORT
    options: PublishOptions = Field(default_factory=PublishOptions)

    _dates_are_aware = field_validator(
        "scheduled_at_utc", "expected_remote_updated_at"
    )(_aware)

    @model_validator(mode="after")
    def scheduling_fields_match(self) -> Self:
        scheduled = self.visibility is PublishVisibility.SCHEDULED
        values_present = all(
            item is not None
            for item in (self.scheduled_at_utc, self.scheduled_local, self.site_timezone)
        )
        if scheduled != values_present:
            raise ValueError("scheduled visibility requires UTC, local time and site timezone")
        if not scheduled and any(
            item is not None
            for item in (self.scheduled_at_utc, self.scheduled_local, self.site_timezone, self.dst_fold)
        ):
            raise ValueError("schedule fields are only allowed for scheduled publishing")
        return self


class PublishedPostDelete(DomainSchema):
    expected_lock_version: int = Field(ge=1)
    confirm_remote_id: str = Field(min_length=1, max_length=500)
    expected_remote_etag: str | None = Field(default=None, max_length=500)
    expected_remote_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    force_delete: bool = False


class ReconcileCreate(DomainSchema):
    expected_lock_version: int = Field(ge=1)
    conflict_action: ConflictAction = ConflictAction.ABORT


class RollbackCreate(DomainSchema):
    expected_lock_version: int = Field(ge=1)
    snapshot_id: UUID
    expected_remote_etag: str | None = Field(default=None, max_length=500)
    expected_remote_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class CancelPublishCreate(DomainSchema):
    expected_lock_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=10_000)


class RetryPublishCreate(DomainSchema):
    expected_lock_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=10_000)


class PublishJobRead(DomainSchema):
    id: UUID
    workspace_id: UUID
    requested_by: UUID
    operation: PublishOperation
    state: JobState
    content_id: UUID
    content_version_id: UUID
    content_hash: str
    connection_id: UUID
    approval_request_id: UUID
    approval_snapshot_hash: str
    policy_id: UUID
    policy_snapshot_hash: str
    target_published_post_id: UUID | None
    visibility: PublishVisibility
    scheduled_at_utc: datetime | None
    scheduled_local: datetime | None
    site_timezone: str
    dst_fold: int | None
    idempotency_key: str
    request_hash: str
    idempotency_marker: str
    input_snapshot_hash: str
    attempt: int
    max_attempts: int
    cancel_requested_at: datetime | None
    cancelled_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    retry_after_seconds: int | None
    quota_completed_at: datetime | None
    quota_released_at: datetime | None
    error_code: str | None
    error_detail: str | None
    result_json: dict[str, Any] | None
    lock_version: int
    created_at: datetime
    updated_at: datetime


class PublishSagaStepRead(DomainSchema):
    id: UUID
    workspace_id: UUID
    job_id: UUID
    sequence: int
    step_kind: str
    state: StepState
    attempt: int
    request_metadata: dict[str, Any]
    response_metadata: dict[str, Any]
    error_code: str | None
    started_at: datetime | None
    finished_at: datetime | None
    lock_version: int
    created_at: datetime
    updated_at: datetime


class PublishAttemptRead(DomainSchema):
    id: UUID
    workspace_id: UUID
    job_id: UUID
    step_id: UUID
    attempt_number: int
    provider_request_id: str | None
    method: str
    endpoint_path: str
    request_metadata: dict[str, Any]
    response_status: int | None
    response_metadata: dict[str, Any]
    retry_class: str
    error_code: str | None
    remote_id: str | None
    created_at: datetime


class PublishedPostRead(DomainSchema):
    id: UUID
    workspace_id: UUID
    content_id: UUID
    content_version_id: UUID
    content_hash: str
    approval_request_id: UUID
    connection_id: UUID | None
    created_by_job_id: UUID | None
    naver_package_id: UUID | None
    provider: PublishingProvider
    remote_site_id: str | None
    remote_id: str
    remote_url: str
    state: PublishedPostState
    remote_etag: str | None
    remote_hash: str
    remote_updated_at: datetime | None
    local_snapshot_hash: str
    last_reconciled_at: datetime | None
    conflict_json: dict[str, Any] | None
    deleted_at: datetime | None
    lock_version: int
    created_at: datetime
    updated_at: datetime


class RemoteSnapshotRead(DomainSchema):
    id: UUID
    workspace_id: UUID
    published_post_id: UUID
    captured_by_job_id: UUID
    reason: str
    snapshot_json: dict[str, Any]
    snapshot_hash: str
    remote_etag: str | None
    remote_updated_at: datetime | None
    captured_at: datetime


class NaverPackageCreate(DomainSchema):
    content_version_id: UUID
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    approval_request_id: UUID
    acknowledged_policy_version: str = Field(min_length=1, max_length=80)
    acknowledge_manual_responsibility: Literal[True]
    previous_package_id: UUID | None = None
    tags: list[str] = Field(default_factory=list, max_length=100)
    reminder_at: datetime | None = None

    _reminder_is_aware = field_validator("reminder_at")(_aware)


class NaverPackageRead(DomainSchema):
    id: UUID
    workspace_id: UUID
    content_id: UUID
    content_version_id: UUID
    content_hash: str
    approval_request_id: UUID
    approval_snapshot_hash: str
    previous_package_id: UUID | None
    title: str
    formatted_blocks: list[dict[str, Any]]
    copy_manifest: list[dict[str, Any]]
    image_manifest: list[dict[str, Any]]
    image_order: list[str]
    tags: list[str]
    checklist: list[dict[str, Any]]
    diff_manifest: dict[str, Any]
    unsupported_blocks: list[dict[str, Any]]
    policy_version: str
    policy_notice: str
    policy_notice_hash: str
    app_launch_url: str | None
    package_hash: str
    requested_by: UUID
    created_at: datetime


class NaverChecklistUpdate(DomainSchema):
    checklist_key: str = Field(min_length=1, max_length=120)
    state: NaverChecklistState


class NaverChecklistEventRead(DomainSchema):
    id: UUID
    workspace_id: UUID
    package_id: UUID
    checklist_key: str
    state: NaverChecklistState
    actor_id: UUID
    created_at: datetime


class NaverManualConfirm(DomainSchema):
    remote_url: str = Field(min_length=1, max_length=2_048)
    remote_post_id: str = Field(min_length=1, max_length=160)


class NaverManualConfirmationRead(DomainSchema):
    id: UUID
    workspace_id: UUID
    package_id: UUID
    published_post_id: UUID
    remote_url: str
    remote_post_id: str
    confirmed_by: UUID
    confirmed_at: datetime


class PublishingNotificationRead(DomainSchema):
    id: UUID
    workspace_id: UUID
    recipient_id: UUID
    publish_job_id: UUID | None
    naver_package_id: UUID | None
    notification_type: str
    payload_json: dict[str, Any]
    due_at: datetime
    delivered_at: datetime | None
    read_at: datetime | None
    created_at: datetime
    updated_at: datetime
