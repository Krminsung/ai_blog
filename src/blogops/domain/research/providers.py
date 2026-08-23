"""Approved search/fetch provider contracts with no implicit network fallback."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Protocol
from uuid import UUID

from blogops.core.errors import AppError


@dataclass(frozen=True, slots=True)
class SearchRequest:
    workspace_id: UUID
    research_run_id: UUID
    query: str
    language: str
    region: str
    allowed_domains: tuple[str, ...]
    denied_domains: tuple[str, ...]
    request_hash: str


@dataclass(frozen=True, slots=True)
class SearchCandidate:
    title: str
    canonical_uri: str
    domain: str
    publisher: str | None
    published_at: datetime | None
    modified_at: datetime | None
    retrieved_at: datetime
    summary: str
    excerpt: str | None
    raw_object_ref: str | None
    metadata: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class SearchResponse:
    provider: str
    provider_version: str
    candidates: tuple[SearchCandidate, ...]
    cache_key: str | None
    cache_hit: bool


class ResearchProvider(Protocol):
    key: str

    async def search(self, request: SearchRequest) -> SearchResponse: ...


class ResearchProviderRegistry:
    def __init__(self, adapters: Mapping[str, ResearchProvider] | None = None) -> None:
        self._adapters = dict(adapters or {})

    def resolve(self, key: str, *, policy_snapshot: Mapping[str, Any]) -> ResearchProvider:
        allowed = tuple(str(item) for item in policy_snapshot.get("allowed_providers", []))
        denied = frozenset(str(item) for item in policy_snapshot.get("denied_providers", []))
        if key not in allowed or key in denied:
            raise AppError(
                code="RESEARCH_PROVIDER_NOT_ALLOWED",
                message="고정된 연구 정책이 이 공급자를 허용하지 않습니다.",
                status_code=422,
                fields=[{"path": "provider", "reason": key}],
            )
        adapter = self._adapters.get(key)
        if adapter is None:
            raise _unavailable(key)
        return adapter


class FailClosedResearchProvider:
    key = "unconfigured"

    async def search(self, request: SearchRequest) -> SearchResponse:
        raise _unavailable(request.query)


def _unavailable(key: str) -> AppError:
    return AppError(
        code="RESEARCH_PROVIDER_UNAVAILABLE",
        message="승인된 연구 공급자가 구성되지 않아 안전하게 중단했습니다.",
        status_code=503,
        fields=[{"path": "adapter", "reason": key}],
    )
