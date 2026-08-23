"""Deterministic analytics validation and calculation rules."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import secrets
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping, Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from blogops.core.errors import AppError
from blogops.domain.analytics.enums import MetricValueKind


def canonical_json_hash(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def new_tracking_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return token, hash_tracking_token(token)


def hash_tracking_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def ensure_secret_free_config(config: Mapping[str, Any]) -> None:
    forbidden = {"password", "token", "api_key", "apikey", "secret", "credential"}

    def walk(value: Any, path: str) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                normalized = str(key).lower().replace("-", "_")
                if any(marker in normalized for marker in forbidden):
                    raise AppError(
                        code="ANALYTICS_INLINE_CREDENTIAL_FORBIDDEN",
                        message="분석 자격 증명은 안전 설정이 아닌 secret ref로 저장해야 합니다.",
                        status_code=422,
                        fields=[{"path": f"safe_config.{path}{key}", "reason": "secret ref only"}],
                    )
                walk(child, f"{path}{key}.")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f"{path}{index}.")

    walk(config, "")


def safe_tracking_destination(
    destination_url: str, parameters: Mapping[str, str]
) -> str:
    parsed = urlsplit(destination_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise AppError(
            code="UNSAFE_TRACKING_DESTINATION",
            message="추적 링크 목적지는 자격 증명이 없는 HTTPS URL이어야 합니다.",
            status_code=422,
        )
    host = parsed.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith(".localhost"):
        raise _unsafe_tracking_host()
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise _unsafe_tracking_host()
    if any(not key or not isinstance(value, str) for key, value in parameters.items()):
        raise AppError(
            code="INVALID_TRACKING_PARAMETERS",
            message="추적 파라미터는 비어 있지 않은 문자열 키와 문자열 값이어야 합니다.",
            status_code=422,
        )
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update(parameters)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))


def validate_metric_definition(definition: Mapping[str, Any]) -> None:
    required = {
        "key",
        "version",
        "subject",
        "unit",
        "value_kind",
        "formula",
        "source_provider",
        "source_field",
        "source_contract_version",
        "latency",
        "supported_dimensions",
    }
    missing = sorted(required.difference(definition))
    if missing:
        raise AppError(
            code="METRIC_DEFINITION_INCOMPLETE",
            message="지표 정의에 필수 출처·산식·지연 정보가 없습니다.",
            status_code=422,
            fields=[{"path": item, "reason": "required"} for item in missing],
        )
    try:
        MetricValueKind(str(definition["value_kind"]))
    except ValueError as exc:
        raise AppError(
            code="METRIC_VALUE_KIND_INVALID",
            message="지원하지 않는 지표 값 유형입니다.",
            status_code=422,
        ) from exc


def validate_fact_evidence(
    *, provider_call_id: object | None, evidence_batch_id: object | None
) -> None:
    if (provider_call_id is None) == (evidence_batch_id is None):
        raise AppError(
            code="ANALYTICS_EVIDENCE_REQUIRED",
            message="사실 행은 공급자 호출 또는 수동/CSV 증거 중 정확히 하나를 참조해야 합니다.",
            status_code=422,
        )


def validate_comparable_metrics(definitions: Sequence[Mapping[str, Any]]) -> None:
    if not definitions:
        raise AppError(
            code="METRIC_DEFINITION_REQUIRED",
            message="비교할 지표 정의가 필요합니다.",
            status_code=422,
        )
    signature = _compatibility_signature(definitions[0])
    incompatible = [item for item in definitions[1:] if _compatibility_signature(item) != signature]
    if incompatible:
        raise AppError(
            code="INCOMPATIBLE_METRIC_DEFINITIONS",
            message="단위·값 유형·산식이 다른 지표는 같은 비교에 섞을 수 없습니다.",
            status_code=422,
        )


@dataclass(frozen=True)
class ROIResult:
    net_return: Decimal
    roi_ratio: Decimal | None


def calculate_roi(*, revenue: Decimal, cost: Decimal) -> ROIResult:
    if revenue < 0 or cost < 0:
        raise AppError(
            code="ROI_VALUE_NEGATIVE",
            message="수익과 비용은 음수일 수 없습니다.",
            status_code=422,
        )
    net = revenue - cost
    return ROIResult(net_return=net, roi_ratio=None if cost == 0 else net / cost)


def require_confirmed_or_estimated_status(status: str) -> None:
    if status not in {MetricValueKind.CONFIRMED.value, MetricValueKind.ESTIMATED.value}:
        raise AppError(
            code="ROI_STATUS_INVALID",
            message="ROI 입력은 추정치 또는 확인값으로 명시해야 합니다.",
            status_code=422,
        )


def _compatibility_signature(definition: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        str(definition.get("unit", "")),
        str(definition.get("value_kind", "")),
        canonical_json_hash(definition.get("formula", {})),
    )


def _unsafe_tracking_host() -> AppError:
    return AppError(
        code="UNSAFE_TRACKING_DESTINATION",
        message="로컬 또는 비공개 네트워크는 추적 링크 목적지로 사용할 수 없습니다.",
        status_code=422,
    )
