"""Public request/response contracts for generation, content versions and collaboration."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from blogops.domain.generation.enums import (
    CollaborationEventKind,
    ContentChangeKind,
    ContentType,
    FeedbackKind,
    GenerationOperation,
    GenerationQuality,
    TemplateScope,
)


SUPPORTED_BLOCK_TYPES = frozenset(
    {
        "TITLE",
        "HEADING",
        "PARAGRAPH",
        "LIST",
        "TABLE",
        "QUOTE",
        "CODE",
        "FAQ",
        "CTA",
        "IMAGE",
        "EMBED",
    }
)


class ContentBlockInput(BaseModel):
    block_key: UUID = Field(default_factory=uuid4)
    block_type: str = Field(min_length=1, max_length=40)
    payload: dict[str, Any]
    plain_text: str = ""
    locked_facts: list[dict[str, Any]] = Field(default_factory=list)
    source_anchors: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("block_type")
    @classmethod
    def supported_block(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in SUPPORTED_BLOCK_TYPES:
            raise ValueError("unsupported editor block type")
        return normalized


class ContentJobCreate(BaseModel):
    brief_version_id: UUID
    content_type: ContentType
    operation: GenerationOperation = GenerationOperation.CREATE
    quality: GenerationQuality = GenerationQuality.BALANCED
    existing_content_id: UUID | None = None
    template_version_id: UUID
    prompt_version_id: UUID
    model_entry_id: UUID
    pricing_version_id: UUID
    source_version_ids: list[UUID] = Field(default_factory=list)
    keyword_metric_snapshot_ids: list[UUID] = Field(default_factory=list)
    type_input: dict[str, Any]
    variables: dict[str, Any] = Field(default_factory=dict)
    requested_limits: dict[str, Any]
    requested_tools: list[str] = Field(default_factory=list)
    automatic_outline_approval: bool = False

    @model_validator(mode="after")
    def operation_target(self) -> "ContentJobCreate":
        if self.operation is GenerationOperation.CREATE and self.existing_content_id is not None:
            raise ValueError("CREATE cannot target an existing content item")
        if self.operation is not GenerationOperation.CREATE and self.existing_content_id is None:
            raise ValueError("non-CREATE operations require existing_content_id")
        return self


class ContentJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    content_id: UUID
    input_snapshot_id: UUID
    operation: str
    quality: str
    state: str
    estimated_cost: Decimal
    actual_cost: Decimal | None
    currency: str
    estimate_breakdown: dict[str, Any]
    attempt: int
    max_attempts: int | None
    error_code: str | None
    error_detail: str | None
    result: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


class JobCommandRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=2_000)


class GenerationStepRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    step_key: str
    step_kind: str
    section_key: str | None
    ordinal: int
    state: str
    attempt: int
    output_ref: str | None
    output_hash: str | None
    result: dict[str, Any] | None
    error_code: str | None
    created_at: datetime
    updated_at: datetime


class ContentCreate(BaseModel):
    content_type: ContentType
    channel: str = Field(min_length=1, max_length=80)
    language: str = Field(default="ko", min_length=2, max_length=16)
    title: str = Field(min_length=1, max_length=500)
    brand_id: UUID | None = None
    brief_id: UUID | None = None
    document: list[ContentBlockInput] = Field(default_factory=list)
    change_note: str | None = Field(default=None, max_length=2_000)
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)


class ContentUpdate(BaseModel):
    expected_lock_version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=500)
    channel: str | None = Field(default=None, min_length=1, max_length=80)
    folder_path: str | None = Field(default=None, max_length=1_000)
    tags: list[str] | None = None
    metadata_json: dict[str, Any] | None = None
    expires_at: datetime | None = None
    archived: bool | None = None


class ContentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    brief_id: UUID | None
    brand_id: UUID | None
    content_type: str
    channel: str
    language: str
    title: str
    state: str
    current_version_id: UUID | None
    folder_path: str | None
    tags: list[str]
    metadata_json: dict[str, Any]
    expires_at: datetime | None
    archived_at: datetime | None
    deleted_at: datetime | None
    retention_hold: bool
    created_by: UUID
    updated_by: UUID
    lock_version: int
    created_at: datetime
    updated_at: datetime


class ContentVersionCreate(BaseModel):
    expected_current_version_id: UUID | None
    expected_current_hash: str | None = Field(default=None, min_length=64, max_length=64)
    title: str = Field(min_length=1, max_length=500)
    document: list[ContentBlockInput]
    change_kind: ContentChangeKind = ContentChangeKind.MANUAL
    change_note: str | None = Field(default=None, max_length=2_000)
    source_snapshot_hash: str | None = Field(default=None, min_length=64, max_length=64)


class ContentVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    content_id: UUID
    parent_version_id: UUID | None
    restored_from_version_id: UUID | None
    generation_job_id: UUID | None
    generation_snapshot_id: UUID | None
    version_number: int
    title: str
    document: list[dict[str, Any]]
    plain_text: str
    content_hash: str
    source_snapshot_hash: str | None
    change_kind: str
    change_note: str | None
    created_by: UUID
    created_at: datetime


class RestoreVersionRequest(BaseModel):
    expected_current_version_id: UUID
    note: str | None = Field(default=None, max_length=2_000)


class ContentFeedbackCreate(BaseModel):
    content_version_id: UUID
    generation_job_id: UUID | None = None
    kind: FeedbackKind
    details: dict[str, Any] = Field(default_factory=dict)


class CollaborationEventCreate(BaseModel):
    content_version_id: UUID | None = None
    client_operation_id: str = Field(min_length=1, max_length=255)
    event_kind: CollaborationEventKind
    block_key: UUID | None = None
    text_range: dict[str, Any] | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class TemplateCreate(BaseModel):
    scope: TemplateScope = TemplateScope.WORKSPACE
    name: str = Field(min_length=1, max_length=240)
    description: str | None = None
    content_type: ContentType
    industry: str | None = Field(default=None, max_length=120)


class TemplateVersionCreate(BaseModel):
    prompt_version_id: UUID
    input_schema: dict[str, Any]
    prompt_blocks: list[dict[str, Any]] = Field(default_factory=list)
    structure_blocks: list[dict[str, Any]] = Field(default_factory=list)
    quality_rules: list[dict[str, Any]] = Field(default_factory=list)
    channel_config: dict[str, Any] = Field(default_factory=dict)
    policy_snapshot: dict[str, Any]
    publish: bool = False


class TemplateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    scope: str
    owner_id: UUID | None
    name: str
    description: str | None
    content_type: str
    industry: str | None
    current_version_id: UUID | None
    retired_at: datetime | None
    created_at: datetime
    updated_at: datetime


class TemplateVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    template_id: UUID
    prompt_version_id: UUID
    version: int
    status: str
    input_schema: dict[str, Any]
    prompt_blocks: list[dict[str, Any]]
    structure_blocks: list[dict[str, Any]]
    quality_rules: list[dict[str, Any]]
    channel_config: dict[str, Any]
    policy_snapshot: dict[str, Any]
    policy_hash: str
    content_hash: str
    created_by: UUID
    created_at: datetime


class ContentExportQuery(BaseModel):
    format: Literal["md", "html", "txt", "json"] = "md"
