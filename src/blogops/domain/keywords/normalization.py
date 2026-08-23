"""Deterministic keyword input normalisation and privacy guards."""

import csv
import hashlib
import io
import math
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping, Sequence

from blogops.core.errors import AppError

MAX_KEYWORD_LENGTH = 1_000
# Abuse/memory guard, not a product entitlement. Plan/credit policy may impose a lower
# workspace-specific limit before this parser is called.
TECHNICAL_MAX_BATCH_ROWS = 10_000
MAX_CSV_BYTES = 2 * 1024 * 1024

_ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200f\u2060\ufeff]")
_WHITESPACE_RE = re.compile(r"\s+")
_EMAIL_RE = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?82[- ]?)?0?1[016789][- ]?\d{3,4}[- ]?\d{4}(?!\d)")
_RRN_RE = re.compile(r"(?<!\d)\d{6}[- ]?[1-4]\d{6}(?!\d)")
_CARD_RE = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True, slots=True)
class SanitizedKeyword:
    original_masked: str
    normalized: str
    pii_detected: bool


@dataclass(frozen=True, slots=True)
class ParsedKeywordRow:
    row_no: int
    keyword: SanitizedKeyword
    metrics: dict[str, Any]


@dataclass(frozen=True, slots=True)
class GuardDecision:
    allowed: bool
    excluded: bool
    code: str | None
    reason: str | None


@dataclass(frozen=True, slots=True)
class KeywordGuardPolicy:
    excluded_terms: tuple[str, ...] = ()
    banned_terms: tuple[str, ...] = ()

    @classmethod
    def from_values(
        cls, excluded_terms: Iterable[str], banned_terms: Iterable[str]
    ) -> "KeywordGuardPolicy":
        return cls(
            excluded_terms=tuple(
                sorted({normalize_keyword(item) for item in excluded_terms if item.strip()})
            ),
            banned_terms=tuple(
                sorted({normalize_keyword(item) for item in banned_terms if item.strip()})
            ),
        )


def _mask_pii(value: str) -> tuple[str, bool]:
    masked = value
    detected = False
    for pattern, replacement in (
        (_EMAIL_RE, "[EMAIL]"),
        (_PHONE_RE, "[PHONE]"),
        (_RRN_RE, "[IDENTIFIER]"),
        (_CARD_RE, "[PAYMENT_NUMBER]"),
    ):
        masked, count = pattern.subn(replacement, masked)
        detected = detected or count > 0
    return masked, detected


def normalize_keyword(value: str) -> str:
    """Return a stable NFKC/case-folded key without changing meaningful punctuation."""

    if not isinstance(value, str):
        raise AppError("KEYWORD_INVALID", "키워드는 문자열이어야 합니다.", 422)
    clean = unicodedata.normalize("NFKC", value)
    clean = _ZERO_WIDTH_RE.sub("", clean)
    clean = _CONTROL_RE.sub(" ", clean)
    clean = _WHITESPACE_RE.sub(" ", clean).strip()
    if not clean:
        raise AppError("KEYWORD_EMPTY", "빈 키워드는 분석할 수 없습니다.", 422)
    if len(clean) > MAX_KEYWORD_LENGTH:
        raise AppError(
            "KEYWORD_TOO_LONG",
            "키워드는 1,000자를 초과할 수 없습니다.",
            422,
            fields=[{"path": "keyword", "reason": "max_length_1000"}],
        )
    return clean.casefold()


def sanitize_keyword(value: str) -> SanitizedKeyword:
    cleaned = unicodedata.normalize("NFKC", value)
    cleaned = _ZERO_WIDTH_RE.sub("", cleaned)
    cleaned = _CONTROL_RE.sub(" ", cleaned)
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip()
    if len(cleaned) > MAX_KEYWORD_LENGTH:
        raise AppError(
            "KEYWORD_TOO_LONG",
            "키워드는 1,000자를 초과할 수 없습니다.",
            422,
            fields=[{"path": "keyword", "reason": "max_length_1000"}],
        )
    masked, pii_detected = _mask_pii(cleaned)
    return SanitizedKeyword(
        original_masked=masked,
        normalized=normalize_keyword(masked),
        pii_detected=pii_detected,
    )


def evaluate_guard(normalized: str, policy: KeywordGuardPolicy) -> GuardDecision:
    padded = f" {normalized} "
    for term in policy.banned_terms:
        if term and (term in normalized or f" {term} " in padded):
            return GuardDecision(
                allowed=False,
                excluded=False,
                code="KEYWORD_BANNED_TERM",
                reason=f"금칙어 정책에 의해 차단됨: {term}",
            )
    for term in policy.excluded_terms:
        if term and (term in normalized or f" {term} " in padded):
            return GuardDecision(
                allowed=False,
                excluded=True,
                code="KEYWORD_EXCLUDED_TERM",
                reason=f"제외어 정책에 의해 제외됨: {term}",
            )
    return GuardDecision(allowed=True, excluded=False, code=None, reason=None)


def stable_json_hash(payload: Any) -> str:
    import json

    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _decimal_or_none(value: str | None, *, field: str, row_no: int) -> float | None:
    if value is None or not value.strip():
        return None
    cleaned = value.strip().replace(",", "")
    try:
        number = Decimal(cleaned)
    except InvalidOperation as exc:
        raise AppError(
            "KEYWORD_CSV_VALUE_INVALID",
            f"CSV {row_no}행의 {field} 값이 숫자가 아닙니다.",
            422,
            fields=[{"path": f"rows.{row_no}.{field}", "reason": "invalid_number"}],
        ) from exc
    if not number.is_finite() or number < 0:
        raise AppError(
            "KEYWORD_CSV_VALUE_INVALID",
            f"CSV {row_no}행의 {field} 값은 0 이상의 유한한 수여야 합니다.",
            422,
        )
    return float(number)


def parse_csv_rows(csv_content: str, mapping: Mapping[str, str]) -> list[ParsedKeywordRow]:
    """Parse rows within the implementation safety guard without retaining raw input."""

    if len(csv_content.encode("utf-8")) > MAX_CSV_BYTES:
        raise AppError("KEYWORD_CSV_TOO_LARGE", "CSV는 2MB를 초과할 수 없습니다.", 413)
    keyword_column = mapping.get("keyword")
    if not keyword_column:
        raise AppError(
            "KEYWORD_CSV_MAPPING_REQUIRED",
            "keyword 열 매핑이 필요합니다.",
            422,
            fields=[{"path": "mapping.keyword", "reason": "required"}],
        )
    reader = csv.DictReader(io.StringIO(csv_content.lstrip("\ufeff")))
    if reader.fieldnames is None or keyword_column not in reader.fieldnames:
        raise AppError(
            "KEYWORD_CSV_COLUMN_MISSING",
            "매핑한 keyword 열을 CSV에서 찾을 수 없습니다.",
            422,
        )
    rows: list[ParsedKeywordRow] = []
    for row_no, raw in enumerate(reader, start=1):
        if row_no > TECHNICAL_MAX_BATCH_ROWS:
            raise AppError(
                "KEYWORD_BATCH_TOO_LARGE",
                "단일 요청이 기술 안전 한도 10,000행을 초과했습니다.",
                422,
            )
        keyword_value = raw.get(keyword_column, "")
        if not keyword_value or not keyword_value.strip():
            raise AppError(
                "KEYWORD_CSV_KEYWORD_EMPTY",
                f"CSV {row_no}행의 키워드가 비어 있습니다.",
                422,
                fields=[{"path": f"rows.{row_no}.keyword", "reason": "required"}],
            )
        metrics: dict[str, Any] = {}
        for canonical in ("search_volume", "competition", "cpc", "document_count"):
            source_column = mapping.get(canonical)
            if source_column:
                metrics[canonical] = _decimal_or_none(
                    raw.get(source_column), field=canonical, row_no=row_no
                )
        rows.append(
            ParsedKeywordRow(
                row_no=row_no,
                keyword=sanitize_keyword(keyword_value),
                metrics={key: value for key, value in metrics.items() if value is not None},
            )
        )
    if not rows:
        raise AppError("KEYWORD_CSV_EMPTY", "CSV에 처리할 키워드가 없습니다.", 422)
    return rows


def normalize_batch(values: Sequence[str]) -> list[ParsedKeywordRow]:
    if len(values) > TECHNICAL_MAX_BATCH_ROWS:
        raise AppError(
            "KEYWORD_BATCH_TOO_LARGE",
            "단일 요청이 기술 안전 한도 10,000개를 초과했습니다.",
            422,
        )
    if not values:
        raise AppError("KEYWORD_BATCH_EMPTY", "분석할 키워드가 없습니다.", 422)
    return [
        ParsedKeywordRow(row_no=index, keyword=sanitize_keyword(value), metrics={})
        for index, value in enumerate(values, start=1)
    ]


def exact_duplicate_map(rows: Sequence[ParsedKeywordRow]) -> dict[int, int]:
    """Map duplicate row numbers to the first equivalent row number."""

    first_by_key: dict[str, int] = {}
    duplicates: dict[int, int] = {}
    for row in rows:
        first = first_by_key.setdefault(row.keyword.normalized, row.row_no)
        if first != row.row_no:
            duplicates[row.row_no] = first
    return duplicates


def aggregate_demographics(value: Mapping[str, Any]) -> dict[str, dict[str, float]]:
    """Allow only anonymous aggregate buckets; reject row/user-level provider data."""

    allowed_dimensions = {"device", "gender", "age"}
    unknown = set(value).difference(allowed_dimensions)
    if unknown:
        raise AppError(
            "KEYWORD_DEMOGRAPHICS_NOT_AGGREGATED",
            "인구통계는 기기·성별·연령 집계값만 저장할 수 있습니다.",
            422,
            fields=[
                {"path": "demographics", "reason": f"unsupported:{item}"}
                for item in sorted(unknown)
            ],
        )
    aggregate: dict[str, dict[str, float]] = {}
    for dimension, buckets in value.items():
        if not isinstance(buckets, Mapping):
            raise AppError(
                "KEYWORD_DEMOGRAPHICS_NOT_AGGREGATED",
                "인구통계 bucket은 집계 객체여야 합니다.",
                422,
            )
        parsed: dict[str, float] = {}
        for bucket, raw_number in buckets.items():
            if (
                not isinstance(bucket, str)
                or isinstance(raw_number, bool)
                or not isinstance(raw_number, (int, float))
                or not math.isfinite(float(raw_number))
                or raw_number < 0
            ):
                raise AppError(
                    "KEYWORD_DEMOGRAPHICS_NOT_AGGREGATED",
                    "인구통계 bucket에는 숫자 집계값만 허용됩니다.",
                    422,
                )
            parsed[bucket] = float(raw_number)
        aggregate[dimension] = parsed
    return aggregate
