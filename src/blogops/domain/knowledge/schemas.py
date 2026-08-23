from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from blogops.domain.knowledge.enums import RightsStatus, SourceQualityGrade, SourceType, UseScope


class FAQItem(BaseModel):
    question: str = Field(min_length=1, max_length=1_000)
    answer: str = Field(min_length=1, max_length=20_000)
    category: str | None = Field(default=None, max_length=120)
    approved: bool = False


class SourceCreate(BaseModel):
    source_type: SourceType
    name: str = Field(min_length=1, max_length=300)
    uri: str | None = Field(default=None, max_length=2_048)
    content: str | None = Field(default=None, max_length=2_000_000)
    faq_items: list[FAQItem] = Field(default_factory=list, max_length=500)
    rights_status: RightsStatus
    use_scope: UseScope = UseScope.INTERNAL_ONLY
    quality_grade: SourceQualityGrade = SourceQualityGrade.D
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_by_type(self) -> "SourceCreate":
        network_types = {
            SourceType.URL,
            SourceType.SITEMAP,
            SourceType.RSS,
            SourceType.API,
            SourceType.YOUTUBE_TRANSCRIPT,
            SourceType.PRODUCT_FEED,
            SourceType.CMS,
        }
        if self.source_type in network_types and not self.uri:
            raise ValueError("uri is required for network sources")
        if self.source_type == SourceType.TEXT and not self.content:
            raise ValueError("content is required for inline sources")
        if self.source_type == SourceType.FAQ and not (self.content or self.faq_items):
            raise ValueError("content or faq_items is required for FAQ sources")
        if self.source_type != SourceType.FAQ and self.faq_items:
            raise ValueError("faq_items is only valid for FAQ sources")
        if self.source_type == SourceType.FILE:
            raise ValueError("use the file upload endpoint for FILE sources")
        if self.rights_status == RightsStatus.PROHIBITED:
            raise ValueError("prohibited sources cannot be stored")
        if self.rights_status == RightsStatus.UNCONFIRMED and self.use_scope != UseScope.INTERNAL_ONLY:
            raise ValueError("unconfirmed sources must remain internal-only")
        return self


class SourceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source_type: str
    name: str
    uri: str | None
    rights_status: str
    use_scope: str
    quality_grade: str
    state: str
    current_version_id: UUID | None
    last_synced_at: datetime | None
    created_at: datetime
    updated_at: datetime


class SourceListResponse(BaseModel):
    items: list[SourceResponse]
    next_cursor: UUID | None = None


class UploadInitiateRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=255)
    size: int = Field(gt=0)
    name: str | None = Field(default=None, max_length=300)
    rights_status: RightsStatus
    use_scope: UseScope = UseScope.INTERNAL_ONLY
    quality_grade: SourceQualityGrade = SourceQualityGrade.D

    @model_validator(mode="after")
    def validate_rights_scope(self) -> "UploadInitiateRequest":
        if self.rights_status == RightsStatus.PROHIBITED:
            raise ValueError("prohibited files cannot be stored")
        if self.rights_status == RightsStatus.UNCONFIRMED and self.use_scope != UseScope.INTERNAL_ONLY:
            raise ValueError("unconfirmed files must remain internal-only")
        return self


class UploadInitiateResponse(BaseModel):
    source_id: UUID
    object_key: str
    upload_url: str
    expires_in: int


class UploadCompleteRequest(BaseModel):
    object_key: str = Field(min_length=1, max_length=2_048)
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class KnowledgeJobResponse(BaseModel):
    job_id: UUID
    state: str
    error_code: str | None = None
    result: dict[str, Any] | None = None


class SourceVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    version: int
    content_hash: str
    retrieved_at: datetime
    metadata_json: dict[str, Any]


class SearchResult(BaseModel):
    chunk_id: UUID
    source_id: UUID
    source_version_id: UUID
    text: str
    locator: dict[str, Any]
    quality_grade: str
    score: float


class SearchResponse(BaseModel):
    items: list[SearchResult]
