"""Version-pinned repurposing API contracts."""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from blogops.domain.repurpose.enums import (
    ChannelTemplateStatus,
    RepurposeApprovalDecision,
    RepurposeCommandKind,
    RepurposeExportFormat,
    RepurposeJobOperation,
    RepurposeKind,
)


class ChannelTemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=240)
    description: str | None = None
    kind: RepurposeKind
    channel: str = Field(min_length=1, max_length=80)


class ChannelTemplateVersionCreate(BaseModel):
    status: ChannelTemplateStatus = ChannelTemplateStatus.DRAFT
    prompt_blocks: list[dict[str, Any]] = Field(min_length=1)
    output_schema: dict[str, Any]
    platform_policy: dict[str, Any]
    disclosure_policy: dict[str, Any]
    safety_policy: dict[str, Any]
    pii_policy: dict[str, Any]
    approval_policy: dict[str, Any]
    model_policy: dict[str, Any]


class ChannelTemplateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None
    kind: str
    channel: str
    current_version_id: UUID | None
    retired_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ChannelTemplateVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    template_id: UUID
    version: int
    status: str
    prompt_blocks: list[dict[str, Any]]
    output_schema: dict[str, Any]
    platform_policy: dict[str, Any]
    disclosure_policy: dict[str, Any]
    safety_policy: dict[str, Any]
    pii_policy: dict[str, Any]
    approval_policy: dict[str, Any]
    model_policy: dict[str, Any]
    policy_hash: str
    content_hash: str
    created_at: datetime


class RepurposeItemCreate(BaseModel):
    content_id: UUID
    content_version_id: UUID
    template_version_id: UUID
    variant_count: int = Field(default=1, gt=0)
    instructions: dict[str, Any] = Field(default_factory=dict)


class RepurposeJobCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    operation: RepurposeJobOperation
    items: list[RepurposeItemCreate] = Field(min_length=1)
    model_provider: str = Field(min_length=1, max_length=120)
    model_name: str = Field(min_length=1, max_length=160)
    model_version: str = Field(min_length=1, max_length=120)
    generation_config: dict[str, Any] = Field(alias="model_config")
    estimated_cost: Decimal = Field(ge=0)
    budget_currency: str = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def operation_matches_items(self) -> "RepurposeJobCreate":
        if self.operation is RepurposeJobOperation.SINGLE and len(self.items) != 1:
            raise ValueError("SINGLE jobs require exactly one item")
        return self


class RepurposeJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    operation: str
    state: str
    item_count: int
    variant_count: int
    budget_currency: str
    estimated_cost: Decimal
    reserved_cost: Decimal
    actual_cost: Decimal
    model_provider: str
    model_name: str
    model_version: str
    attempt: int
    error_code: str | None
    error_detail: str | None
    created_at: datetime
    updated_at: datetime


class RepurposeJobItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    job_id: UUID
    snapshot_id: UUID
    position: int
    kind: str
    channel: str
    variant_count: int
    state: str
    error_code: str | None
    error_detail: str | None


class RepurposeVariantRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    job_item_id: UUID
    snapshot_id: UUID
    variant_no: int
    document: list[dict[str, Any]]
    plain_text: str
    character_count: int
    source_content_hash: str
    template_content_hash: str
    result_hash: str
    claim_lineage: list[dict[str, Any]]
    citation_lineage: list[dict[str, Any]]
    validation_result: dict[str, Any]
    disclosure_result: dict[str, Any]
    safety_result: dict[str, Any]
    pii_result: dict[str, Any]
    model_provenance: dict[str, Any]
    created_at: datetime


class RepurposeApprovalCreate(BaseModel):
    decision: RepurposeApprovalDecision
    reason: str = Field(min_length=1)


class RepurposeApprovalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    variant_id: UUID
    variant_hash: str
    decision: str
    reason: str
    policy_snapshot: dict[str, Any]
    decided_by: UUID
    created_at: datetime


class RepurposeExportCreate(BaseModel):
    format: RepurposeExportFormat


class RepurposeExportRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    variant_id: UUID
    approval_id: UUID | None
    variant_hash: str
    format: str
    object_ref: str
    object_hash: str
    media_type: str
    size_bytes: int
    manifest: dict[str, Any]
    created_at: datetime


class RepurposeDeliveryCreate(BaseModel):
    approval_id: UUID
    official_provider: str = Field(min_length=1, max_length=120)
    connection_secret_ref: str = Field(min_length=1, max_length=512)
    destination: dict[str, Any]


class RepurposeDeliveryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    variant_id: UUID
    approval_id: UUID
    variant_hash: str
    official_provider: str
    destination: dict[str, Any]
    state: str
    external_post_id: str | None
    response_metadata: dict[str, Any] | None
    error_code: str | None
    created_at: datetime


class RepurposeJobCommandCreate(BaseModel):
    command: RepurposeCommandKind
    reason: str | None = None
