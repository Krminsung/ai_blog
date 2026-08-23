"""Strict public DTOs for bulk imports, previews, jobs and row actions."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from blogops.domain.bulk.enums import (
    BulkExportKind,
    BulkInputKind,
    BulkOperation,
    BulkPriority,
    BulkScheduleState,
    DuplicateAction,
)
from blogops.domain.media.rules import find_plaintext_secret_paths


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ORMModel(StrictModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class BulkUploadInitiate(StrictModel):
    input_kind: BulkInputKind
    filename: str = Field(min_length=1, max_length=500)
    mime_type: str = Field(min_length=1, max_length=120)
    size_bytes: int = Field(gt=0)

    @model_validator(mode="after")
    def uploaded_file_kind(self) -> "BulkUploadInitiate":
        if self.input_kind not in {BulkInputKind.CSV, BulkInputKind.XLSX}:
            raise ValueError("direct uploads support CSV or XLSX only")
        allowed = {
            BulkInputKind.CSV: {"text/csv", "text/plain", "application/csv"},
            BulkInputKind.XLSX: {
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            },
        }
        if self.mime_type.casefold() not in allowed[self.input_kind]:
            raise ValueError("mime_type does not match input_kind")
        self.mime_type = self.mime_type.casefold()
        return self


class BulkUploadGrant(StrictModel):
    upload_id: UUID
    object_ref: str
    upload_url: str
    expires_in: int


class BulkUploadComplete(StrictModel):
    upload_id: UUID
    input_kind: BulkInputKind
    name: str = Field(min_length=1, max_length=500)
    object_ref: str = Field(min_length=1, max_length=1_000)
    mime_type: str = Field(min_length=1, max_length=120)
    size_bytes: int = Field(gt=0)
    expected_content_hash: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    delimiter: str | None = Field(default=None, min_length=1, max_length=1)
    sheet_name: str | None = Field(default=None, max_length=240)
    header_row: int = Field(default=1, gt=0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def uploaded_file_kind(self) -> "BulkUploadComplete":
        if self.input_kind not in {BulkInputKind.CSV, BulkInputKind.XLSX}:
            raise ValueError("direct uploads support CSV or XLSX only")
        if find_plaintext_secret_paths(self.metadata, "metadata"):
            raise ValueError("metadata must not contain plaintext credentials")
        return self


class BulkInputRegister(StrictModel):
    """Trusted worker DTO created only after byte-level snapshot verification."""

    input_kind: BulkInputKind
    name: str = Field(min_length=1, max_length=500)
    object_ref: str = Field(min_length=1, max_length=1_000)
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(ge=0)
    row_count: int = Field(gt=0)
    encoding: str | None = Field(default=None, max_length=40)
    delimiter: str | None = Field(default=None, min_length=1, max_length=8)
    sheet_name: str | None = Field(default=None, max_length=240)
    sheet_range: str | None = Field(default=None, max_length=240)
    header_row: int = Field(default=1, gt=0)
    headers: list[str] = Field(min_length=1)
    source_locator: str | None = Field(default=None, max_length=4_000)
    source_connection_ref: str | None = Field(default=None, max_length=512)
    source_secret_ref: str | None = Field(default=None, max_length=512)
    malware_scan_status: Literal["CLEAN", "NOT_REQUIRED"]
    malware_scanner: str | None = Field(default=None, max_length=120)
    malware_scanner_version: str | None = Field(default=None, max_length=80)
    malware_scan_result_hash: str | None = Field(
        default=None, pattern=r"^[a-f0-9]{64}$"
    )
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_external_source(self) -> "BulkInputRegister":
        if len(set(self.headers)) != len(self.headers):
            raise ValueError("headers must be distinct")
        if self.input_kind == BulkInputKind.GOOGLE_SHEETS:
            if not self.source_locator or not self.source_secret_ref:
                raise ValueError("Google Sheets snapshots require locator and secret_ref")
        file_kinds = {
            BulkInputKind.CSV,
            BulkInputKind.XLSX,
            BulkInputKind.PRODUCT_FEED,
            BulkInputKind.PASTE,
            BulkInputKind.SITE_LIST,
        }
        if self.input_kind in file_kinds and self.malware_scan_status != "CLEAN":
            raise ValueError("uploaded bulk files require a CLEAN malware scan")
        if self.malware_scan_status == "CLEAN" and (
            not self.malware_scanner
            or not self.malware_scanner_version
            or not self.malware_scan_result_hash
        ):
            raise ValueError("CLEAN scan status requires immutable scanner evidence")
        if self.source_locator and any(
            marker in self.source_locator.casefold()
            for marker in ("access_token=", "api_key=", "key=", "password=")
        ):
            raise ValueError("source_locator must not contain credentials")
        if find_plaintext_secret_paths(self.metadata, "metadata"):
            raise ValueError("metadata must not contain plaintext credentials")
        return self


class BulkInputRead(ORMModel):
    id: UUID
    input_kind: str
    name: str
    object_ref: str
    content_hash: str
    size_bytes: int
    row_count: int
    encoding: str | None
    delimiter: str | None
    sheet_name: str | None
    sheet_range: str | None
    header_row: int
    headers: list[str]
    source_locator: str | None
    source_locator_hash: str | None
    source_connection_ref: str | None
    malware_scan_status: str
    malware_scanner: str | None
    malware_scanner_version: str | None
    malware_scan_result_hash: str | None
    metadata_json: dict[str, Any]
    uploaded_by: UUID
    created_at: datetime


class BulkMappingCreate(StrictModel):
    name: str = Field(min_length=1, max_length=240)
    input_schema: dict[str, Any]
    column_mapping: dict[str, str] = Field(min_length=1)
    variable_schema: dict[str, Any]
    required_variables: list[str] = Field(default_factory=list)
    normalization_rules: list[dict[str, Any]] = Field(default_factory=list, max_length=200)
    duplicate_action: DuplicateAction = DuplicateAction.REVIEW
    semantic_duplicate_enabled: bool = True

    @model_validator(mode="after")
    def validate_mapping_values(self) -> "BulkMappingCreate":
        if len(set(self.column_mapping.values())) != len(self.column_mapping):
            raise ValueError("each template variable may be mapped only once")
        if len(set(self.required_variables)) != len(self.required_variables):
            raise ValueError("required_variables must be distinct")
        if find_plaintext_secret_paths(self.input_schema, "input_schema"):
            raise ValueError("mapping schemas must not contain credentials")
        return self


class BulkMappingRead(ORMModel):
    id: UUID
    name: str
    input_schema: dict[str, Any]
    column_mapping: dict[str, str]
    variable_schema: dict[str, Any]
    required_variables: list[str]
    normalization_rules: list[dict[str, Any]]
    duplicate_policy: dict[str, Any]
    mapping_hash: str
    created_by: UUID
    created_at: datetime


class CsvPreviewRequest(StrictModel):
    content: str | None = Field(default=None, min_length=1, max_length=5_000_000)
    content_base64: str | None = Field(default=None, min_length=4, max_length=7_000_000)
    column_mapping: dict[str, str] = Field(min_length=1)
    required_variables: list[str] = Field(default_factory=list)
    preview_limit: int = Field(default=50, ge=1, le=200)
    delimiter: str | None = Field(default=None, min_length=1, max_length=1)

    @model_validator(mode="after")
    def exactly_one_content_form(self) -> "CsvPreviewRequest":
        if (self.content is None) == (self.content_base64 is None):
            raise ValueError("provide exactly one of content or content_base64")
        return self


class PreviewRowRead(StrictModel):
    row_no: int
    values: dict[str, str]
    mapped_values: dict[str, str]
    input_hash: str
    duplicate_of_row_no: int | None
    errors: list[str]


class CsvPreviewRead(StrictModel):
    encoding: str
    delimiter: str
    headers: list[str]
    total_rows: int
    preview_rows: list[PreviewRowRead]
    invalid_rows: int
    exact_duplicates: int


class BulkJobCreate(StrictModel):
    campaign_id: UUID
    input_file_id: UUID
    mapping_id: UUID
    template_version_id: UUID
    operation: BulkOperation = BulkOperation.GENERATE_CONTENT
    priority: BulkPriority = BulkPriority.NORMAL
    estimated_cost: Decimal = Field(ge=0)
    maximum_cost: Decimal = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    dry_run: bool = False
    sample_size: int | None = Field(default=None, gt=0)
    max_row_attempts: int = Field(default=3, ge=1, le=10)
    requested_concurrency: int = Field(default=1, ge=1)
    requested_daily_throughput: int = Field(default=1, ge=1)
    callback_endpoint_ref: str | None = Field(default=None, max_length=512)
    callback_secret_ref: str | None = Field(default=None, max_length=512)
    idempotency_key: str = Field(min_length=8, max_length=255)

    @model_validator(mode="after")
    def validate_job(self) -> "BulkJobCreate":
        if self.estimated_cost > self.maximum_cost:
            raise ValueError("estimated_cost exceeds maximum_cost")
        if self.dry_run and self.sample_size is None:
            raise ValueError("dry_run requires sample_size")
        if self.callback_endpoint_ref and not self.callback_secret_ref:
            raise ValueError("callback_secret_ref is required for signed callbacks")
        return self


class BulkJobRead(ORMModel):
    id: UUID
    campaign_id: UUID | None
    input_file_id: UUID
    mapping_id: UUID
    template_version_id: UUID
    operation: str
    state: str
    priority: str
    dry_run: bool
    sample_size: int | None
    total_rows: int
    processed_rows: int
    succeeded_rows: int
    review_rows: int
    failed_rows: int
    cancelled_rows: int
    progress_percent: Decimal
    estimated_cost: Decimal
    maximum_cost: Decimal
    authorized_cost: Decimal
    actual_cost: Decimal
    held_cost: Decimal
    currency: str
    budget_kill_switch_triggered: bool
    pause_requested: bool
    max_row_attempts: int
    concurrency_limit: int
    daily_throughput_limit: int
    started_at: datetime | None
    finished_at: datetime | None
    error_code: str | None
    error_detail: str | None
    result_manifest_ref: str | None
    created_at: datetime
    updated_at: datetime
    lock_version: int


class BulkRowInput(StrictModel):
    row_no: int = Field(gt=0)
    values: dict[str, Any]
    idempotency_key: str = Field(min_length=8, max_length=255)
    estimated_cost: Decimal = Field(ge=0)


class BulkRowsIngest(StrictModel):
    """A streaming chunk, not a maximum number of rows in the parent job."""

    rows: list[BulkRowInput] = Field(min_length=1, max_length=1_000)

    @model_validator(mode="after")
    def distinct_rows(self) -> "BulkRowsIngest":
        row_numbers = [row.row_no for row in self.rows]
        keys = [row.idempotency_key for row in self.rows]
        if len(set(row_numbers)) != len(row_numbers):
            raise ValueError("row_no values must be distinct within a chunk")
        if len(set(keys)) != len(keys):
            raise ValueError("row idempotency keys must be distinct within a chunk")
        return self


class BulkRowRead(ORMModel):
    id: UUID
    job_id: UUID
    row_no: int
    input_hash: str
    input_json: dict[str, Any]
    state: str
    validation_errors: list[dict[str, Any]]
    duplicate_of_row_id: UUID | None
    duplicate_action: str | None
    semantic_duplicate_score: Decimal | None
    keyword_cluster_ref: str | None
    existing_content_action: str | None
    existing_content_refs: list[dict[str, Any]]
    generation_job_id: UUID | None
    content_id: UUID | None
    content_version_id: UUID | None
    content_hash: str | None
    quality_assessment_id: UUID | None
    quality_passed: bool | None
    approval_request_id: UUID | None
    approved_content_hash: str | None
    hard_blocked: bool
    risk_findings: list[dict[str, Any]]
    spam_similarity_score: Decimal | None
    value_score: Decimal | None
    estimated_cost: Decimal
    actual_cost: Decimal
    attempt: int
    last_error_code: str | None
    last_error_detail: str | None
    next_retry_at: datetime | None
    approved_by: UUID | None
    approved_at: datetime | None
    lock_version: int


class BulkCommandRequest(StrictModel):
    reason: str = Field(min_length=3, max_length=2_000)
    idempotency_key: str = Field(min_length=8, max_length=255)


class BulkRowsCommand(StrictModel):
    row_ids: list[UUID] = Field(min_length=1, max_length=1_000)
    reason: str = Field(min_length=3, max_length=2_000)
    idempotency_key: str = Field(min_length=8, max_length=255)

    @field_validator("row_ids")
    @classmethod
    def distinct_ids(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("row_ids must be distinct")
        return value


class BulkExportRequest(StrictModel):
    export_kind: BulkExportKind
    include_states: list[str] = Field(default_factory=list, max_length=50)
    idempotency_key: str = Field(min_length=8, max_length=255)


class BulkScheduleCreate(StrictModel):
    input_file_id: UUID
    mapping_id: UUID
    template_version_id: UUID
    timezone: str = Field(min_length=1, max_length=64)
    schedule_expression: str = Field(min_length=1, max_length=240)
    next_run_at: datetime
    config_snapshot: dict[str, Any]

    @model_validator(mode="after")
    def timezone_required(self) -> "BulkScheduleCreate":
        if self.next_run_at.tzinfo is None:
            raise ValueError("next_run_at must be timezone-aware")
        if find_plaintext_secret_paths(self.config_snapshot, "config_snapshot"):
            raise ValueError("schedule snapshot must not contain plaintext credentials")
        return self


class BulkScheduleRead(ORMModel):
    id: UUID
    input_file_id: UUID
    mapping_id: UUID
    template_version_id: UUID
    timezone: str
    schedule_expression: str
    state: BulkScheduleState | str
    last_input_hash: str | None
    next_run_at: datetime
    last_run_at: datetime | None
    config_snapshot_hash: str
    created_by: UUID
    lock_version: int
