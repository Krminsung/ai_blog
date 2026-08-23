"""Bounded previews and row normalization for CSV-family bulk inputs."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from typing import Any, Mapping

from blogops.core.errors import AppError
from blogops.domain.bulk.rules import canonical_hash, validate_mapping
from blogops.domain.knowledge.parsing import mask_pii


@dataclass(frozen=True, slots=True)
class PreviewRow:
    row_no: int
    values: Mapping[str, str]
    mapped_values: Mapping[str, str]
    input_hash: str
    duplicate_of_row_no: int | None
    errors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CsvPreview:
    encoding: str
    delimiter: str
    headers: tuple[str, ...]
    total_rows: int
    preview_rows: tuple[PreviewRow, ...]
    invalid_rows: int
    exact_duplicates: int


def decode_spreadsheet_text(content: bytes) -> tuple[str, str]:
    """Support UTF-8 and common Excel exports without silently replacing bytes."""

    if not content:
        raise AppError("BULK_INPUT_EMPTY", "CSV 파일이 비어 있습니다.", 422)
    encodings = ["utf-8-sig"]
    if content.startswith((b"\xff\xfe", b"\xfe\xff")):
        encodings.append("utf-16")
    encodings.append("cp949")
    for encoding in encodings:
        try:
            return content.decode(encoding), encoding
        except UnicodeError:
            continue
    raise AppError(
        "BULK_ENCODING_UNSUPPORTED",
        "CSV 인코딩을 확인할 수 없습니다. UTF-8 또는 Excel 호환 인코딩을 사용해 주세요.",
        422,
    )


def preview_csv(
    content: bytes,
    *,
    column_mapping: Mapping[str, str],
    required_variables: tuple[str, ...],
    preview_limit: int,
    delimiter: str | None = None,
) -> CsvPreview:
    """Scan all rows for counts while retaining only the requested preview window."""

    if preview_limit < 1:
        raise AppError("BULK_PREVIEW_LIMIT_INVALID", "미리보기 행 수가 올바르지 않습니다.", 422)
    text, encoding = decode_spreadsheet_text(content)
    sample = text[:16_384]
    selected_delimiter = delimiter
    if selected_delimiter is None:
        try:
            selected_delimiter = csv.Sniffer().sniff(sample, delimiters=",\t;").delimiter
        except csv.Error:
            selected_delimiter = ","
    reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=selected_delimiter)
    headers = tuple((item or "").strip() for item in (reader.fieldnames or []))
    if not headers or any(not item for item in headers):
        raise AppError("BULK_HEADER_INVALID", "CSV Header가 비어 있거나 올바르지 않습니다.", 422)
    if len(set(headers)) != len(headers):
        raise AppError("BULK_HEADER_DUPLICATE", "CSV Header 이름이 중복되었습니다.", 422)
    mapping_errors = validate_mapping(
        available_columns=headers,
        column_mapping=column_mapping,
        required_variables=required_variables,
    )
    if mapping_errors:
        raise AppError(
            "BULK_MAPPING_INVALID",
            "열과 템플릿 변수 매핑이 올바르지 않습니다.",
            422,
            fields=[{"path": "mapping", "reason": value} for value in mapping_errors],
        )
    previews: list[PreviewRow] = []
    seen: dict[str, int] = {}
    total = 0
    invalid = 0
    duplicate_count = 0
    for row_no, raw in enumerate(reader, start=2):
        total += 1
        errors: list[str] = []
        if None in raw:
            errors.append("extra_columns")
        masked: dict[str, str] = {}
        for header in headers:
            raw_value = raw.get(header)
            value = "" if raw_value is None else str(raw_value).strip()
            masked_value, _pii_detected = mask_pii(value)
            masked[header] = masked_value
        mapped = {variable: masked[column] for column, variable in column_mapping.items()}
        for variable in required_variables:
            if not mapped.get(variable, "").strip():
                errors.append(f"missing_required:{variable}")
        digest = canonical_hash(mapped)
        duplicate_of = seen.get(digest)
        if duplicate_of is None:
            seen[digest] = row_no
        else:
            duplicate_count += 1
            errors.append("exact_duplicate")
        if any(not item.startswith("exact_duplicate") for item in errors):
            invalid += 1
        if len(previews) < preview_limit:
            previews.append(
                PreviewRow(
                    row_no=row_no,
                    values=masked,
                    mapped_values=mapped,
                    input_hash=digest,
                    duplicate_of_row_no=duplicate_of,
                    errors=tuple(errors),
                )
            )
    if total == 0:
        raise AppError("BULK_INPUT_EMPTY", "CSV에 데이터 행이 없습니다.", 422)
    return CsvPreview(
        encoding=encoding,
        delimiter=selected_delimiter,
        headers=headers,
        total_rows=total,
        preview_rows=tuple(previews),
        invalid_rows=invalid,
        exact_duplicates=duplicate_count,
    )
