"""Strict request and response schemas for media APIs."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from blogops.domain.media.enums import (
    ExifPolicy,
    ImageNeedKind,
    LicenseState,
    LicenseType,
    MediaOperation,
    UsageMode,
)
from blogops.domain.media.rules import find_plaintext_secret_paths, validate_license_fields


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ORMModel(StrictModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class MediaProviderConnectionCreate(StrictModel):
    provider: str = Field(min_length=1, max_length=120)
    name: str = Field(default="default", min_length=1, max_length=120)
    secret_ref: str = Field(min_length=3, max_length=512)
    license_ref: str | None = Field(default=None, max_length=512)
    capabilities: set[MediaOperation] = Field(min_length=1)
    allowed_regions: list[str] = Field(default_factory=list, max_length=100)
    config: dict[str, Any] = Field(default_factory=dict)
    daily_quota: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def no_plaintext_credentials(self) -> "MediaProviderConnectionCreate":
        paths = find_plaintext_secret_paths(self.config)
        if paths:
            raise ValueError("config must not contain plaintext credentials; use secret_ref")
        if {
            MediaOperation.STOCK_SEARCH,
            MediaOperation.STOCK_IMPORT,
        }.intersection(self.capabilities) and not self.license_ref:
            raise ValueError("stock capabilities require an approved license_ref")
        if not isinstance(self.config.get("policy_snapshot"), dict):
            raise ValueError("provider config requires a server-managed policy_snapshot")
        return self


class MediaProviderConnectionRead(ORMModel):
    id: UUID
    provider: str
    name: str
    license_ref: str | None
    capabilities: list[str]
    allowed_regions: list[str]
    state: str
    daily_quota: int | None
    quota_remaining: int | None
    quota_reset_at: datetime | None
    circuit_open_until: datetime | None
    last_success_at: datetime | None
    last_error_code: str | None
    consecutive_failures: int


class MediaUploadInitiate(StrictModel):
    filename: str = Field(min_length=1, max_length=500)
    mime_type: str
    size_bytes: int = Field(gt=0)
    name: str | None = Field(default=None, min_length=1, max_length=500)
    folder_path: str | None = Field(default=None, max_length=1_000)
    tags: list[str] = Field(default_factory=list, max_length=100)
    exif_policy: ExifPolicy = ExifPolicy.REMOVE_PRIVATE
    ai_generated: bool = False
    ai_disclosure_text: str | None = Field(default=None, min_length=3, max_length=500)
    expected_content_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @field_validator("mime_type")
    @classmethod
    def supported_mime(cls, value: str) -> str:
        normalized = value.casefold()
        if normalized not in {"image/jpeg", "image/png", "image/webp"}:
            raise ValueError("only JPG, PNG and WEBP uploads are supported")
        return normalized

    @model_validator(mode="after")
    def declared_ai_origin(self) -> "MediaUploadInitiate":
        if self.ai_generated and not self.ai_disclosure_text:
            raise ValueError("AI-generated uploads require disclosure text")
        if not self.ai_generated and self.ai_disclosure_text:
            raise ValueError("AI disclosure text requires ai_generated=true")
        return self


class MediaUploadGrant(StrictModel):
    asset_id: UUID
    state: str
    upload_url: str
    expires_in: int


class MediaUploadComplete(StrictModel):
    expected_content_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class MediaSensitiveReview(StrictModel):
    approve: bool
    reason: str = Field(min_length=3, max_length=2_000)
    face_consent_confirmed: bool = False
    pii_removal_confirmed: bool = False


class MediaAssetRead(ORMModel):
    id: UUID
    name: str
    media_type: str
    origin: str
    state: str
    declared_mime_type: str
    declared_size_bytes: int
    original_content_hash: str | None
    original_version_id: UUID | None
    current_version_id: UUID | None
    folder_path: str | None
    tags: list[str]
    ai_generated: bool
    ai_disclosure_required: bool
    review_reason: str | None
    metadata_json: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
    lock_version: int


class MediaVersionRead(ORMModel):
    id: UUID
    asset_id: UUID
    parent_version_id: UUID | None
    version_number: int
    version_kind: str
    operation: str
    object_ref: str
    content_hash: str
    mime_type: str
    size_bytes: int
    width: int
    height: int
    prompt_hash: str | None
    model_run_id: UUID | None
    provider: str | None
    provider_version: str | None
    model: str | None
    model_version: str | None
    pii_detected: bool
    face_detected: bool
    trademark_detected: bool
    safety_labels: list[dict[str, Any]]
    ai_generated: bool
    disclosure_text: str | None
    actual_cost: Decimal
    currency: str
    created_at: datetime


class MediaLicenseRevisionCreate(StrictModel):
    state: LicenseState = LicenseState.ACTIVE
    license_type: LicenseType
    source_url: str | None = Field(default=None, max_length=4_000)
    source_asset_ref: str | None = Field(default=None, max_length=1_000)
    author: str | None = Field(default=None, max_length=500)
    downloaded_at: datetime | None = None
    commercial_allowed: bool = False
    editorial_allowed: bool = False
    allowed_channels: list[str] = Field(default_factory=list, max_length=100)
    allowed_regions: list[str] = Field(default_factory=list, max_length=100)
    derivative_allowed: bool = False
    attribution_required: bool = False
    attribution_text: str | None = Field(default=None, max_length=2_000)
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    terms: dict[str, Any] = Field(default_factory=dict)
    evidence_object_ref: str | None = Field(default=None, max_length=1_000)
    model_name: str | None = Field(default=None, max_length=160)
    model_version: str | None = Field(default=None, max_length=120)
    prompt_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_rights(self) -> "MediaLicenseRevisionCreate":
        if find_plaintext_secret_paths(self.terms, "terms"):
            raise ValueError("license terms must not contain plaintext credentials")
        for field_name in ("downloaded_at", "valid_from", "valid_until"):
            value = getattr(self, field_name)
            if value is not None and value.tzinfo is None:
                raise ValueError(f"{field_name} must be timezone-aware")
        if self.valid_from and self.valid_until and self.valid_until <= self.valid_from:
            raise ValueError("valid_until must be later than valid_from")
        if self.attribution_required and not self.attribution_text:
            raise ValueError("attribution_text is required")
        validate_license_fields(
            license_type=self.license_type,
            source_url=self.source_url,
            author=self.author,
            prompt_hash=self.prompt_hash,
            model_name=self.model_name,
            commercial_allowed=self.commercial_allowed,
            editorial_allowed=self.editorial_allowed,
        )
        return self


class MediaLicenseRevisionRead(ORMModel):
    id: UUID
    license_id: UUID
    asset_id: UUID
    revision: int
    state: str
    license_type: str
    source_url: str | None
    source_asset_ref: str | None
    author: str | None
    downloaded_at: datetime | None
    commercial_allowed: bool
    editorial_allowed: bool
    allowed_channels: list[str]
    allowed_regions: list[str]
    derivative_allowed: bool
    attribution_required: bool
    attribution_text: str | None
    valid_from: datetime | None
    valid_until: datetime | None
    terms_json: dict[str, Any]
    evidence_object_ref: str | None
    model_name: str | None
    model_version: str | None
    prompt_hash: str | None
    confirmed_by: UUID
    confirmed_at: datetime
    snapshot_hash: str
    created_at: datetime


class MediaLicenseRead(ORMModel):
    id: UUID
    asset_id: UUID
    current_revision_id: UUID | None
    state: str
    valid_until: datetime | None
    revoked_at: datetime | None
    lock_version: int


class MediaReferenceVersion(StrictModel):
    asset_id: UUID
    version_id: UUID


class MediaOperationCreate(StrictModel):
    operation: MediaOperation
    provider_connection_id: UUID
    source_asset_id: UUID | None = None
    source_version_id: UUID | None = None
    reference_versions: list[MediaReferenceVersion] = Field(
        default_factory=list, max_length=10
    )
    prompt: str | None = Field(default=None, max_length=20_000)
    prohibited_elements: list[str] = Field(default_factory=list, max_length=200)
    region: str | None = Field(default=None, min_length=2, max_length=80)
    parameters: dict[str, Any] = Field(default_factory=dict)
    estimated_cost: Decimal = Field(ge=0)
    maximum_cost: Decimal = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    max_attempts: int = Field(default=3, ge=1, le=10)
    idempotency_key: str = Field(min_length=8, max_length=255)

    @model_validator(mode="after")
    def validate_operation_inputs(self) -> "MediaOperationCreate":
        paths = find_plaintext_secret_paths(self.parameters, "parameters")
        if paths:
            raise ValueError("parameters must not contain plaintext credentials")
        if (self.source_asset_id is None) != (self.source_version_id is None):
            raise ValueError("source_asset_id and source_version_id must be supplied together")
        source_operations = set(MediaOperation) - {
            MediaOperation.TEXT_TO_IMAGE,
            MediaOperation.STOCK_SEARCH,
            MediaOperation.STOCK_IMPORT,
            MediaOperation.THUMBNAIL,
            MediaOperation.CARD_NEWS,
            MediaOperation.INFOGRAPHIC,
        }
        if self.operation in source_operations and (
            self.source_asset_id is None or self.source_version_id is None
        ):
            raise ValueError("this operation requires an exact source asset version")
        if self.operation in {
            MediaOperation.TEXT_TO_IMAGE,
            MediaOperation.REFERENCE_EDIT,
            MediaOperation.BACKGROUND_REPLACE,
            MediaOperation.OBJECT_REMOVE,
            MediaOperation.OUTPAINT,
            MediaOperation.THUMBNAIL,
            MediaOperation.CARD_NEWS,
            MediaOperation.INFOGRAPHIC,
        } and not self.prompt:
            raise ValueError("this operation requires a prompt")
        if self.estimated_cost > self.maximum_cost:
            raise ValueError("estimated_cost exceeds maximum_cost")
        return self


class MediaJobRead(ORMModel):
    id: UUID
    operation: str
    state: str
    provider_connection_id: UUID | None
    source_asset_id: UUID | None
    source_version_id: UUID | None
    result_asset_id: UUID | None
    result_version_id: UUID | None
    estimated_cost: Decimal
    actual_cost: Decimal | None
    currency: str
    budget_kill_switch_triggered: bool
    provider_quota_reserved: bool
    provider_quota_released: bool
    attempt: int
    max_attempts: int
    error_code: str | None
    error_detail: str | None
    result_json: dict[str, Any] | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class MediaJobCommandRequest(StrictModel):
    idempotency_key: str = Field(min_length=8, max_length=255)
    reason: str = Field(min_length=3, max_length=2_000)


class ImagePlanItemCreate(StrictModel):
    section_key: str | None = Field(default=None, max_length=160)
    need_kind: ImageNeedKind
    reason: str = Field(min_length=1, max_length=2_000)
    requires_real_photo: bool = False
    generation_allowed: bool = True
    generation_prompt: str | None = Field(default=None, max_length=20_000)
    prohibited_elements: list[str] = Field(default_factory=list, max_length=200)
    alt_text_plan: str = Field(min_length=1, max_length=2_000)
    caption_plan: str | None = Field(default=None, max_length=2_000)
    aspect_ratio: str | None = Field(default=None, max_length=32)
    placement: dict[str, Any]
    candidate_asset_ids: list[UUID] = Field(default_factory=list, max_length=100)
    duplicate_warning: dict[str, Any] | None = None
    performance_ref: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_real_photo(self) -> "ImagePlanItemCreate":
        for field_name in ("placement", "duplicate_warning", "performance_ref"):
            if find_plaintext_secret_paths(getattr(self, field_name), field_name):
                raise ValueError(f"{field_name} must not contain plaintext credentials")
        if self.requires_real_photo and self.generation_allowed:
            raise ValueError("real-photo evidence positions cannot permit generated substitutes")
        if self.generation_allowed and not self.generation_prompt:
            raise ValueError("generation_prompt is required when generation is allowed")
        return self


class ImagePlanCreate(StrictModel):
    content_id: UUID
    content_version_id: UUID
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    recommended_count: int = Field(ge=0)
    prohibited_elements: list[str] = Field(default_factory=list, max_length=200)
    items: list[ImagePlanItemCreate] = Field(default_factory=list)

    @model_validator(mode="after")
    def count_matches_items(self) -> "ImagePlanCreate":
        if self.recommended_count != len(self.items):
            raise ValueError("recommended_count must equal the number of plan items")
        return self


class ImagePlanRead(ORMModel):
    id: UUID
    content_id: UUID
    content_version_id: UUID
    content_hash: str
    channel: str
    status: str
    recommended_count: int
    count_policy_snapshot: dict[str, Any]
    brand_snapshot: dict[str, Any]
    generation_policy_snapshot: dict[str, Any]
    prohibited_elements: list[str]
    plan_hash: str
    created_by: UUID
    created_at: datetime


class ImagePlanItemRead(ORMModel):
    id: UUID
    plan_id: UUID
    sequence: int
    section_key: str | None
    need_kind: str
    reason: str
    requires_real_photo: bool
    generation_allowed: bool
    generation_prompt: str | None
    prohibited_elements: list[str]
    alt_text_plan: str
    caption_plan: str | None
    aspect_ratio: str | None
    placement_json: dict[str, Any]
    candidate_asset_ids: list[str]
    selection_state: str
    selected_asset_id: UUID | None
    selected_version_id: UUID | None
    duplicate_warning: dict[str, Any] | None
    performance_ref: dict[str, Any] | None
    lock_version: int


class ImagePlanWithItems(StrictModel):
    plan: ImagePlanRead
    items: list[ImagePlanItemRead]


class ImageSelection(StrictModel):
    asset_id: UUID
    version_id: UUID
    region: str | None = Field(default=None, min_length=2, max_length=80)
    usage_mode: UsageMode
    expected_lock_version: int = Field(gt=0)


class MediaUsageCreate(StrictModel):
    content_id: UUID
    content_version_id: UUID
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    asset_id: UUID
    media_version_id: UUID
    license_revision_id: UUID
    placement_key: str = Field(min_length=1, max_length=160)
    channel: str = Field(min_length=1, max_length=80)
    region: str | None = Field(default=None, max_length=80)
    usage_mode: UsageMode
    alt_text: str = Field(min_length=1, max_length=2_000)
    caption: str | None = Field(default=None, max_length=2_000)


class MediaUsageRead(ORMModel):
    id: UUID
    content_id: UUID
    content_version_id: UUID
    content_hash: str
    asset_id: UUID
    media_version_id: UUID
    license_revision_id: UUID
    placement_key: str
    channel: str
    region: str | None
    usage_mode: str
    alt_text: str
    caption: str | None
    attribution_text: str | None
    rights_snapshot_hash: str
    created_at: datetime


class MediaDeleteRequest(StrictModel):
    expected_lock_version: int = Field(gt=0)
    reason: str = Field(min_length=3, max_length=2_000)
    acknowledge_usage_count: int = Field(ge=0)


class MediaRestoreVersion(StrictModel):
    version_id: UUID
    expected_lock_version: int = Field(gt=0)
