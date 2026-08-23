"""Trusted byte-level ingestion for immutable bulk input snapshots.

Client supplied upload metadata is only a claim.  This module verifies the
workspace-owned quarantine object, scans the exact bytes, derives tabular
metadata server-side, and only then promotes the bytes to immutable storage.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, AsyncIterator, Iterator, Mapping, Protocol
from uuid import UUID

from blogops.core.errors import AppError
from blogops.domain.bulk.enums import BulkInputKind
from blogops.domain.bulk.parsing import decode_spreadsheet_text
from blogops.domain.bulk.rules import canonical_hash
from blogops.domain.bulk.schemas import BulkUploadComplete
from blogops.domain.knowledge.adapters import MalwareScanner
from blogops.domain.knowledge.parsing import mask_pii
from blogops.domain.media.rules import find_plaintext_secret_paths
from blogops.domain.media.storage import PrivateObjectStorage

_CSV_MIME_TYPES = frozenset({"text/csv", "text/plain", "application/csv"})
_XLSX_MIME_TYPES = frozenset(
    {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
)
_SECRET_QUERY_MARKERS = (
    "access_token=",
    "api_key=",
    "client_secret=",
    "password=",
    "refresh_token=",
    "token=",
)
_QUARANTINE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._=-]{0,800}$")
_IMMUTABLE_EXTENSION = re.compile(r"^[a-z0-9]{1,16}$")


@dataclass(frozen=True, slots=True)
class ParsedTabularMetadata:
    """Metadata returned by an approved XLSX parser or connector adapter."""

    headers: tuple[str, ...]
    row_count: int
    header_row: int
    encoding: str | None = None
    delimiter: str | None = None
    sheet_name: str | None = None
    sheet_range: str | None = None
    parser: str | None = None
    parser_version: str | None = None


class XlsxSnapshotParser(Protocol):
    """Approved sandbox/parser boundary for untrusted XLSX bytes."""

    async def inspect(
        self,
        content: bytes,
        *,
        sheet_name: str | None,
        header_row: int,
    ) -> ParsedTabularMetadata: ...


@dataclass(frozen=True, slots=True)
class ExternalSnapshotRequest:
    """Opaque connector request; credentials must remain secret references."""

    input_kind: BulkInputKind
    source_locator: str
    source_connection_ref: str | None
    source_secret_ref: str
    sheet_name: str | None = None
    sheet_range: str | None = None
    header_row: int = 1

    def __post_init__(self) -> None:
        if self.input_kind in {BulkInputKind.CSV, BulkInputKind.XLSX}:
            raise AppError(
                "BULK_EXTERNAL_KIND_INVALID",
                "외부 입력 종류가 올바르지 않습니다.",
                422,
            )
        if self.header_row < 1:
            raise AppError("BULK_HEADER_ROW_INVALID", "Header 행이 올바르지 않습니다.", 422)
        locator = self.source_locator.casefold()
        if any(marker in locator for marker in _SECRET_QUERY_MARKERS):
            raise AppError(
                "BULK_SOURCE_SECRET_EXPOSED",
                "외부 입력 위치에는 평문 자격 증명을 포함할 수 없습니다.",
                422,
            )
        if not _is_secret_reference(self.source_secret_ref):
            raise AppError(
                "BULK_SECRET_REFERENCE_REQUIRED",
                "외부 입력 자격 증명은 Secret 참조로만 전달해야 합니다.",
                422,
            )


@dataclass(frozen=True, slots=True)
class ExternalSnapshotPayload:
    """Exact bytes and metadata fetched by a configured connector adapter."""

    content: bytes
    content_type: str
    metadata: ParsedTabularMetadata


class ExternalSnapshotAdapter(Protocol):
    name: str
    version: str

    async def snapshot(self, request: ExternalSnapshotRequest) -> ExternalSnapshotPayload: ...


class FailClosedExternalSnapshotAdapter:
    name = "unconfigured"
    version = "0"

    async def snapshot(self, request: ExternalSnapshotRequest) -> ExternalSnapshotPayload:
        del request
        raise AppError(
            "BULK_INPUT_ADAPTER_UNAVAILABLE",
            "승인된 외부 입력 어댑터가 구성되지 않아 안전하게 중단했습니다.",
            503,
        )


@dataclass(frozen=True, slots=True)
class VerifiedBulkSnapshot:
    """Trusted service-boundary DTO produced from the exact promoted bytes."""

    upload_id: UUID
    input_kind: BulkInputKind
    name: str
    object_ref: str
    content_hash: str
    size_bytes: int
    row_count: int
    encoding: str | None
    delimiter: str | None
    header_row: int
    headers: tuple[str, ...]
    sheet_name: str | None
    sheet_range: str | None
    malware_scan_status: str
    malware_scanner: str
    malware_scanner_version: str
    malware_scan_result_hash: str
    metadata: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class NormalizedCsvRow:
    """Deterministic, PII-masked row ready for bulk row materialization."""

    row_no: int
    values: Mapping[str, str]
    input_hash: str
    pii_masked: bool


def _is_secret_reference(value: str) -> bool:
    normalized = value.casefold()
    return normalized.startswith(
        ("secret-manager://", "vault://", "aws-secretsmanager://", "gcp-secret://")
    )


def _contains_plaintext_secret_value(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(_contains_plaintext_secret_value(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_plaintext_secret_value(item) for item in value)
    if not isinstance(value, str) or _is_secret_reference(value):
        return False
    normalized = value.casefold().strip()
    return normalized.startswith(("bearer ", "basic ")) or any(
        marker in normalized for marker in _SECRET_QUERY_MARKERS
    )


def _normalized_content_type(value: object) -> str:
    return str(value or "").split(";", 1)[0].strip().casefold()


def _validate_quarantine_ref(
    object_ref: str,
    *,
    workspace_id: UUID,
    upload_id: UUID,
) -> None:
    prefix = f"workspaces/{workspace_id}/bulk/{upload_id}/quarantine/"
    suffix = object_ref.removeprefix(prefix)
    if (
        not object_ref.startswith(prefix)
        or not suffix
        or not _QUARANTINE_NAME.fullmatch(suffix)
    ):
        raise AppError(
            "BULK_QUARANTINE_OBJECT_INVALID",
            "업로드 객체가 요청한 Workspace와 Upload 격리 영역에 속하지 않습니다.",
            422,
            fields=[{"path": "object_ref", "reason": "quarantine ownership mismatch"}],
        )


def _validate_immutable_ref(
    object_ref: str,
    *,
    workspace_id: UUID,
    upload_id: UUID,
    content_hash: str,
) -> None:
    prefix = f"workspaces/{workspace_id}/bulk/{upload_id}/versions/{content_hash}."
    suffix = object_ref.removeprefix(prefix)
    if not object_ref.startswith(prefix) or not _IMMUTABLE_EXTENSION.fullmatch(suffix):
        raise AppError(
            "BULK_SNAPSHOT_OBJECT_INVALID",
            "불변 Snapshot 저장 경로를 확인할 수 없습니다.",
            503,
        )


def _allowed_mime_types(input_kind: BulkInputKind) -> frozenset[str]:
    if input_kind is BulkInputKind.CSV:
        return _CSV_MIME_TYPES
    if input_kind is BulkInputKind.XLSX:
        return _XLSX_MIME_TYPES
    raise AppError(
        "BULK_UPLOAD_KIND_INVALID",
        "직접 업로드는 CSV 또는 XLSX만 지원합니다.",
        422,
    )


def _validate_headers(headers: tuple[str, ...]) -> None:
    if not headers or any(not value for value in headers):
        raise AppError("BULK_HEADER_INVALID", "CSV Header가 비어 있거나 올바르지 않습니다.", 422)
    if len(set(headers)) != len(headers):
        raise AppError("BULK_HEADER_DUPLICATE", "CSV Header 이름이 중복되었습니다.", 422)


def _detect_csv_delimiter(text: str) -> str:
    sample = text[:16_384]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",\t;").delimiter
    except csv.Error:
        return ","


def _csv_reader_at_header(
    content: bytes,
    *,
    encoding: str | None = None,
    delimiter: str | None = None,
    header_row: int,
) -> tuple[Iterator[list[str]], str, str, tuple[str, ...]]:
    if header_row < 1:
        raise AppError("BULK_HEADER_ROW_INVALID", "Header 행이 올바르지 않습니다.", 422)
    if encoding is None:
        text, selected_encoding = decode_spreadsheet_text(content)
    else:
        try:
            text = content.decode(encoding)
        except (LookupError, UnicodeError) as exc:
            raise AppError(
                "BULK_ENCODING_UNSUPPORTED",
                "CSV 인코딩을 확인할 수 없습니다.",
                422,
            ) from exc
        selected_encoding = encoding
    selected_delimiter = delimiter or _detect_csv_delimiter(text)
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=selected_delimiter)
    try:
        for _ in range(header_row - 1):
            next(reader)
        raw_headers = next(reader)
    except StopIteration as exc:
        raise AppError("BULK_HEADER_INVALID", "CSV Header 행을 찾을 수 없습니다.", 422) from exc
    headers = tuple(value.strip() for value in raw_headers)
    _validate_headers(headers)
    return reader, selected_encoding, selected_delimiter, headers


def iter_normalized_csv_rows(
    content: bytes,
    *,
    encoding: str | None = None,
    delimiter: str | None = None,
    header_row: int = 1,
) -> Iterator[NormalizedCsvRow]:
    """Yield data rows numbered from one, with PII masked before hashing."""

    reader, _encoding, _delimiter, headers = _csv_reader_at_header(
        content,
        encoding=encoding,
        delimiter=delimiter,
        header_row=header_row,
    )
    row_no = 0
    for raw_values in reader:
        if not raw_values or not any(value.strip() for value in raw_values):
            continue
        row_no += 1
        if len(raw_values) != len(headers):
            raise AppError(
                "BULK_ROW_SHAPE_INVALID",
                "CSV 행의 열 수가 Header와 일치하지 않습니다.",
                422,
                fields=[{"path": f"rows.{row_no}", "reason": "column count mismatch"}],
            )
        masked_values: dict[str, str] = {}
        pii_detected = False
        for header, raw_value in zip(headers, raw_values, strict=True):
            masked, detected = mask_pii(raw_value.strip())
            masked_values[header] = masked
            pii_detected = pii_detected or detected
        immutable_values = MappingProxyType(masked_values)
        yield NormalizedCsvRow(
            row_no=row_no,
            values=immutable_values,
            input_hash=canonical_hash(masked_values),
            pii_masked=pii_detected,
        )


def derive_csv_metadata(content: bytes, *, header_row: int = 1) -> ParsedTabularMetadata:
    """Derive CSV structure exclusively from the uploaded bytes."""

    _reader, encoding, delimiter, headers = _csv_reader_at_header(
        content,
        header_row=header_row,
    )
    row_count = sum(1 for _ in iter_normalized_csv_rows(
        content,
        encoding=encoding,
        delimiter=delimiter,
        header_row=header_row,
    ))
    if row_count < 1:
        raise AppError("BULK_INPUT_EMPTY", "CSV에 데이터 행이 없습니다.", 422)
    return ParsedTabularMetadata(
        headers=headers,
        row_count=row_count,
        header_row=header_row,
        encoding=encoding,
        delimiter=delimiter,
        parser="python-csv",
        parser_version="1",
    )


def _validate_parsed_metadata(value: ParsedTabularMetadata) -> None:
    _validate_headers(tuple(item.strip() for item in value.headers))
    if value.row_count < 1:
        raise AppError("BULK_INPUT_EMPTY", "표 입력에 데이터 행이 없습니다.", 422)
    if value.header_row < 1:
        raise AppError("BULK_HEADER_ROW_INVALID", "Header 행이 올바르지 않습니다.", 422)


def _scan_status(value: object) -> str:
    raw = getattr(value, "value", value)
    return str(raw).upper()


async def verify_uploaded_bulk_snapshot(
    *,
    workspace_id: UUID,
    upload: BulkUploadComplete,
    storage: PrivateObjectStorage,
    scanner: MalwareScanner,
    scanner_name: str,
    scanner_version: str,
    max_upload_bytes: int,
    xlsx_parser: XlsxSnapshotParser | None = None,
) -> VerifiedBulkSnapshot:
    """Verify and promote one direct upload without trusting client metadata."""

    if max_upload_bytes < 1:
        raise AppError("BULK_UPLOAD_POLICY_INVALID", "업로드 용량 정책이 올바르지 않습니다.", 500)
    if not scanner_name.strip() or not scanner_version.strip():
        raise AppError("BULK_SCANNER_CONFIG_INVALID", "악성코드 검사기 구성이 올바르지 않습니다.", 503)
    if find_plaintext_secret_paths(
        upload.metadata, "metadata"
    ) or _contains_plaintext_secret_value(upload.metadata):
        raise AppError(
            "BULK_METADATA_SECRET_EXPOSED",
            "업로드 Metadata에는 평문 자격 증명을 포함할 수 없습니다.",
            422,
        )

    _validate_quarantine_ref(
        upload.object_ref,
        workspace_id=workspace_id,
        upload_id=upload.upload_id,
    )
    allowed_mime_types = _allowed_mime_types(upload.input_kind)
    details = await storage.head(upload.object_ref)
    try:
        actual_size = int(details.get("ContentLength", -1))
    except (TypeError, ValueError) as exc:
        await storage.delete(upload.object_ref)
        raise AppError(
            "BULK_UPLOAD_METADATA_INVALID",
            "업로드 객체 Metadata를 확인할 수 없습니다.",
            422,
        ) from exc
    actual_type = _normalized_content_type(details.get("ContentType"))
    declared_type = _normalized_content_type(upload.mime_type)
    if (
        actual_size < 1
        or actual_size > max_upload_bytes
        or actual_size != upload.size_bytes
    ):
        await storage.delete(upload.object_ref)
        raise AppError(
            "BULK_UPLOAD_SIZE_MISMATCH",
            "업로드 객체의 실제 용량이 요청 정보와 일치하지 않습니다.",
            422,
        )
    if actual_type != declared_type or actual_type not in allowed_mime_types:
        await storage.delete(upload.object_ref)
        raise AppError(
            "BULK_UPLOAD_TYPE_MISMATCH",
            "업로드 객체의 실제 형식이 요청 정보와 일치하지 않습니다.",
            422,
        )

    content = await storage.get_bytes(upload.object_ref, max_bytes=max_upload_bytes)
    if len(content) != actual_size:
        await storage.delete(upload.object_ref)
        raise AppError(
            "BULK_UPLOAD_SIZE_MISMATCH",
            "업로드 객체의 Metadata와 실제 바이트 수가 일치하지 않습니다.",
            422,
        )
    content_hash = hashlib.sha256(content).hexdigest()
    if upload.expected_content_hash and upload.expected_content_hash != content_hash:
        await storage.delete(upload.object_ref)
        raise AppError(
            "BULK_UPLOAD_HASH_MISMATCH",
            "업로드 객체의 SHA-256이 요청 정보와 일치하지 않습니다.",
            422,
        )

    scan_result = await scanner.scan(content)
    status = _scan_status(scan_result.status)
    if status != "CLEAN":
        status_code = 422 if status == "INFECTED" else 503
        code = "BULK_MALWARE_DETECTED" if status == "INFECTED" else "BULK_SCANNER_UNAVAILABLE"
        if status == "INFECTED":
            await storage.delete(upload.object_ref)
        raise AppError(code, "업로드 파일의 악성코드 안전성을 확인할 수 없습니다.", status_code)
    signature = getattr(scan_result, "signature", None)
    scan_evidence_hash = canonical_hash(
        {
            "content_hash": content_hash,
            "content_type": actual_type,
            "scanner": scanner_name,
            "scanner_version": scanner_version,
            "signature_hash": (
                hashlib.sha256(str(signature).encode("utf-8")).hexdigest()
                if signature
                else None
            ),
            "size_bytes": len(content),
            "status": status,
        }
    )

    if upload.input_kind is BulkInputKind.CSV:
        parsed = derive_csv_metadata(content, header_row=upload.header_row)
    else:
        if xlsx_parser is None:
            raise AppError(
                "BULK_XLSX_PARSER_UNAVAILABLE",
                "승인된 XLSX Parser가 구성되지 않아 안전하게 중단했습니다.",
                503,
            )
        parsed = await xlsx_parser.inspect(
            content,
            sheet_name=upload.sheet_name,
            header_row=upload.header_row,
        )
        _validate_parsed_metadata(parsed)

    immutable_ref = await storage.put_immutable(
        workspace_id=workspace_id,
        namespace="bulk",
        owner_id=upload.upload_id,
        content_hash=content_hash,
        content=content,
        content_type=actual_type,
    )
    _validate_immutable_ref(
        immutable_ref,
        workspace_id=workspace_id,
        upload_id=upload.upload_id,
        content_hash=content_hash,
    )
    await storage.delete(upload.object_ref)

    metadata = MappingProxyType(
        {
            **upload.metadata,
            "parser": parsed.parser,
            "parser_version": parsed.parser_version,
            "verified_content_type": actual_type,
        }
    )
    return VerifiedBulkSnapshot(
        upload_id=upload.upload_id,
        input_kind=upload.input_kind,
        name=upload.name,
        object_ref=immutable_ref,
        content_hash=content_hash,
        size_bytes=len(content),
        row_count=parsed.row_count,
        encoding=parsed.encoding,
        delimiter=parsed.delimiter,
        header_row=parsed.header_row,
        headers=tuple(value.strip() for value in parsed.headers),
        sheet_name=parsed.sheet_name,
        sheet_range=parsed.sheet_range,
        malware_scan_status="CLEAN",
        malware_scanner=scanner_name,
        malware_scanner_version=scanner_version,
        malware_scan_result_hash=scan_evidence_hash,
        metadata=metadata,
    )


async def require_external_snapshot(
    request: ExternalSnapshotRequest,
    *,
    adapter: ExternalSnapshotAdapter | None,
) -> ExternalSnapshotPayload:
    """Invoke only an explicitly configured connector, otherwise fail closed."""

    selected = adapter or FailClosedExternalSnapshotAdapter()
    payload = await selected.snapshot(request)
    if not payload.content:
        raise AppError("BULK_INPUT_EMPTY", "외부 입력 Snapshot이 비어 있습니다.", 422)
    _validate_parsed_metadata(payload.metadata)
    return payload


async def iter_external_rows(
    adapter: AsyncIterator[NormalizedCsvRow],
) -> AsyncIterator[NormalizedCsvRow]:
    """Preserve a typed async row boundary for configured connector workers."""

    async for row in adapter:
        if row.row_no < 1:
            raise AppError("BULK_ROW_NUMBER_INVALID", "입력 행 번호가 올바르지 않습니다.", 422)
        yield row
