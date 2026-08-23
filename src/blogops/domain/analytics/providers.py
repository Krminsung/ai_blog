"""Fail-closed official analytics adapter boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Mapping, Protocol, Sequence

from blogops.core.errors import AppError
from blogops.domain.analytics.enums import AnalyticsProvider


@dataclass(frozen=True)
class AnalyticsFetchRequest:
    property_id: str
    date_from: date
    date_to: date
    metric_fields: tuple[str, ...]
    dimensions: tuple[str, ...]
    request_metadata: Mapping[str, Any]


@dataclass(frozen=True)
class AnalyticsFactValue:
    subject: str
    external_fact_id: str
    metric_field: str
    fact_date: date
    value: Decimal
    dimensions: Mapping[str, Any]
    observed_at: datetime


@dataclass(frozen=True)
class AnalyticsFetchResult:
    adapter_name: str
    adapter_version: str
    official_contract: str
    api_version: str
    facts: Sequence[AnalyticsFactValue]
    raw_object_ref: str | None
    raw_response_hash: str
    response_metadata: Mapping[str, Any]
    source_delay: Mapping[str, Any]
    started_at: datetime
    completed_at: datetime


class CredentialResolver(Protocol):
    async def resolve(self, secret_ref: str) -> Mapping[str, str]: ...


class AnalyticsProviderAdapter(Protocol):
    provider: AnalyticsProvider
    official_contract: str

    async def fetch(
        self,
        request: AnalyticsFetchRequest,
        *,
        credentials: Mapping[str, str],
    ) -> AnalyticsFetchResult: ...


class AnalyticsAdapterRegistry:
    def __init__(self, adapters: Sequence[AnalyticsProviderAdapter] = ()) -> None:
        self._adapters: dict[AnalyticsProvider, AnalyticsProviderAdapter] = {}
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: AnalyticsProviderAdapter) -> None:
        try:
            provider = AnalyticsProvider(adapter.provider)
        except ValueError as exc:
            raise AppError(
                code="UNOFFICIAL_ANALYTICS_PROVIDER",
                message="허용된 공식 분석 공급자만 등록할 수 있습니다.",
                status_code=422,
            ) from exc
        if not str(adapter.official_contract).strip():
            raise AppError(
                code="OFFICIAL_CONTRACT_REQUIRED",
                message="공식 API 계약 식별자가 필요합니다.",
                status_code=422,
            )
        self._adapters[provider] = adapter

    def require(self, provider: str) -> AnalyticsProviderAdapter:
        try:
            key = AnalyticsProvider(provider)
        except ValueError as exc:
            raise _runtime_unavailable(provider) from exc
        adapter = self._adapters.get(key)
        if adapter is None:
            raise _runtime_unavailable(provider)
        return adapter


def _runtime_unavailable(provider: str) -> AppError:
    return AppError(
        code="ANALYTICS_RUNTIME_UNAVAILABLE",
        message=f"공식 분석 어댑터가 구성되지 않았습니다: {provider}",
        status_code=503,
    )
