"""Official/licensed keyword provider adapters with fail-closed resolution."""

import base64
import hashlib
import hmac
import html
import json
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Mapping, Protocol, Sequence
from urllib.parse import quote

import aiohttp

from blogops.domain.keywords.enums import (
    ProviderCapability,
    ProviderConnectionState,
    ProviderKind,
    ProviderSourceClass,
)
from blogops.domain.keywords.models import KeywordProviderConnection
from blogops.domain.keywords.normalization import aggregate_demographics, sanitize_keyword

MAX_PROVIDER_RESPONSE_BYTES = 5 * 1024 * 1024
_HTML_TAG_RE = re.compile(r"<[^>]+>")


class ProviderError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        retry_after_seconds: int | None = None,
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds
        self.http_status = http_status


@dataclass(frozen=True, slots=True)
class CredentialMaterial:
    """Ephemeral secret material returned by a server-side secret resolver."""

    values: Mapping[str, str]
    scopes: frozenset[str] = frozenset()

    def require(self, *keys: str) -> None:
        missing = [key for key in keys if not self.values.get(key)]
        if missing:
            raise ProviderError(
                "PROVIDER_CREDENTIAL_INVALID",
                f"공급자 Credential 필드가 누락되었습니다: {', '.join(missing)}",
            )


class SecretResolver(Protocol):
    async def resolve(self, secret_ref: str) -> CredentialMaterial: ...


class FailClosedSecretResolver:
    async def resolve(self, secret_ref: str) -> CredentialMaterial:
        del secret_ref
        raise ProviderError(
            "PROVIDER_SECRET_RESOLVER_UNAVAILABLE",
            "승인된 Secret Manager가 구성되지 않아 외부 공급자를 호출할 수 없습니다.",
        )


@dataclass(frozen=True, slots=True)
class ProviderQuery:
    keyword: str
    language: str = "ko"
    region: str = "KR"
    start_date: date | None = None
    end_date: date | None = None
    time_unit: str = "month"
    dimensions: Mapping[str, Any] = field(default_factory=dict)
    limit: int = 100


@dataclass(frozen=True, slots=True)
class RelatedKeyword:
    text: str
    reason: str
    metrics: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProviderResult:
    provider: ProviderKind
    source_class: ProviderSourceClass
    source_label: str
    value_kind: str
    measured_at: datetime
    retrieved_at: datetime
    metrics: Mapping[str, Any]
    trend_points: Sequence[Mapping[str, Any]] = ()
    demographics: Mapping[str, Mapping[str, float]] = field(default_factory=dict)
    serp_samples: Sequence[Mapping[str, Any]] = ()
    related_keywords: Sequence[RelatedKeyword] = ()
    limitations: Sequence[str] = ()
    confidence: float = 1.0
    raw_response: bytes | None = None
    adapter_name: str = ""
    adapter_version: str = "1"
    transform_version: str = "1"


class KeywordProvider(Protocol):
    kind: ProviderKind
    source_class: ProviderSourceClass
    capabilities: frozenset[ProviderCapability]

    def validate(self, query: ProviderQuery, credential: CredentialMaterial) -> None: ...

    async def collect(
        self, query: ProviderQuery, credential: CredentialMaterial
    ) -> ProviderResult: ...


def _retry_after(headers: Mapping[str, str]) -> int | None:
    value = headers.get("Retry-After")
    if not value:
        return None
    try:
        return max(1, int(value))
    except ValueError:
        return None


def _numeric(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "")
        if cleaned.startswith("<"):
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def parse_search_count(value: Any) -> tuple[float | None, dict[str, float] | None]:
    """Preserve bounded Search Ads counts without inventing an exact observation."""

    if isinstance(value, str) and value.strip().startswith("<"):
        upper = _numeric(value.strip()[1:])
        return None, {"min_inclusive": 0.0, "max_exclusive": upper} if upper else None
    return _numeric(value), None


class _OfficialJsonAdapter:
    allowed_urls: frozenset[str]

    async def _json_request(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        params: Mapping[str, str] | None = None,
        body: Mapping[str, Any] | None = None,
    ) -> tuple[dict[str, Any], bytes]:
        if url not in self.allowed_urls:
            raise ProviderError(
                "PROVIDER_ENDPOINT_NOT_ALLOWED",
                "등록된 공식 공급자 endpoint가 아니므로 호출을 차단했습니다.",
            )
        timeout = aiohttp.ClientTimeout(total=15, connect=5)
        try:
            async with aiohttp.ClientSession(timeout=timeout, raise_for_status=False) as client:
                async with client.request(
                    method,
                    url,
                    headers=dict(headers),
                    params=dict(params or {}),
                    json=dict(body) if body is not None else None,
                    allow_redirects=False,
                ) as response:
                    if 300 <= response.status < 400:
                        raise ProviderError(
                            "PROVIDER_REDIRECT_BLOCKED",
                            "공식 endpoint의 redirect 응답을 안전상 차단했습니다.",
                            http_status=response.status,
                        )
                    payload = await response.content.read(MAX_PROVIDER_RESPONSE_BYTES + 1)
                    if len(payload) > MAX_PROVIDER_RESPONSE_BYTES:
                        raise ProviderError(
                            "PROVIDER_RESPONSE_TOO_LARGE",
                            "공급자 응답이 허용 크기를 초과했습니다.",
                            http_status=response.status,
                        )
                    if response.status == 429:
                        raise ProviderError(
                            "PROVIDER_RATE_LIMITED",
                            "공급자 호출 한도에 도달했습니다.",
                            retryable=True,
                            retry_after_seconds=_retry_after(response.headers),
                            http_status=429,
                        )
                    if response.status in {408, 425} or response.status >= 500:
                        raise ProviderError(
                            "PROVIDER_TEMPORARY_FAILURE",
                            "공급자가 일시적으로 요청을 처리하지 못했습니다.",
                            retryable=True,
                            retry_after_seconds=_retry_after(response.headers),
                            http_status=response.status,
                        )
                    if response.status >= 400:
                        raise ProviderError(
                            "PROVIDER_REQUEST_REJECTED",
                            "공급자가 요청 또는 Credential을 거부했습니다.",
                            http_status=response.status,
                        )
        except TimeoutError as exc:
            raise ProviderError(
                "PROVIDER_TIMEOUT", "공급자 응답 시간이 초과되었습니다.", retryable=True
            ) from exc
        except aiohttp.ClientError as exc:
            raise ProviderError(
                "PROVIDER_NETWORK_ERROR", "공급자 연결에 실패했습니다.", retryable=True
            ) from exc
        try:
            decoded = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderError(
                "PROVIDER_SCHEMA_INVALID", "공급자가 유효한 JSON을 반환하지 않았습니다."
            ) from exc
        if not isinstance(decoded, dict):
            raise ProviderError(
                "PROVIDER_SCHEMA_INVALID", "공급자 응답 최상위 값은 객체여야 합니다."
            )
        return decoded, payload


class NaverDataLabAdapter(_OfficialJsonAdapter):
    kind = ProviderKind.NAVER_DATALAB
    source_class = ProviderSourceClass.OFFICIAL
    capabilities = frozenset(
        {ProviderCapability.TREND, ProviderCapability.DEMOGRAPHICS}
    )
    endpoint = "https://openapi.naver.com/v1/datalab/search"
    allowed_urls = frozenset({endpoint})

    def validate(self, query: ProviderQuery, credential: CredentialMaterial) -> None:
        credential.require("client_id", "client_secret")
        if query.time_unit not in {"date", "week", "month"}:
            raise ProviderError(
                "PROVIDER_QUERY_INVALID", "DataLab timeUnit은 date/week/month만 허용됩니다."
            )
        groups = query.dimensions.get("keyword_groups")
        if groups is not None and (not isinstance(groups, list) or not 1 <= len(groups) <= 5):
            raise ProviderError(
                "PROVIDER_QUERY_INVALID", "DataLab keywordGroups는 1~5개여야 합니다."
            )

    async def collect(
        self, query: ProviderQuery, credential: CredentialMaterial
    ) -> ProviderResult:
        self.validate(query, credential)
        groups = query.dimensions.get("keyword_groups")
        if groups is None:
            groups = [{"groupName": query.keyword, "keywords": [query.keyword]}]
        if not isinstance(groups, list) or not 1 <= len(groups) <= 5:
            raise ProviderError(
                "PROVIDER_QUERY_INVALID", "DataLab keywordGroups는 1~5개여야 합니다."
            )
        start = query.start_date or date.today().replace(day=1)
        end = query.end_date or date.today()
        body: dict[str, Any] = {
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "timeUnit": query.time_unit,
            "keywordGroups": groups,
        }
        for key in ("device", "gender", "ages"):
            value = query.dimensions.get(key)
            if value:
                body[key] = value
        decoded, raw = await self._json_request(
            method="POST",
            url=self.endpoint,
            headers={
                "X-Naver-Client-Id": credential.values["client_id"],
                "X-Naver-Client-Secret": credential.values["client_secret"],
            },
            body=body,
        )
        results = decoded.get("results")
        if not isinstance(results, list):
            raise ProviderError("PROVIDER_SCHEMA_INVALID", "DataLab results가 누락되었습니다.")
        points: list[dict[str, Any]] = []
        for group in results:
            if not isinstance(group, dict) or not isinstance(group.get("data"), list):
                continue
            group_name = str(group.get("title", ""))[:1_000]
            for point in group["data"]:
                if not isinstance(point, dict):
                    continue
                ratio = _numeric(point.get("ratio"))
                period = point.get("period")
                if ratio is not None and isinstance(period, str):
                    points.append({"period": period, "value": ratio, "group": group_name})
        return ProviderResult(
            provider=self.kind,
            source_class=self.source_class,
            source_label="Naver DataLab Search Trend API",
            value_kind="RELATIVE",
            measured_at=datetime.now(UTC),
            retrieved_at=datetime.now(UTC),
            metrics={"relative_trend": True, "time_unit": query.time_unit},
            trend_points=points,
            limitations=("상대 지수이며 절대 검색량이 아닙니다.", "Credential별 일 1,000회 한도"),
            confidence=1.0 if points else 0.0,
            raw_response=raw,
            adapter_name=type(self).__name__,
        )


class NaverBlogSearchAdapter(_OfficialJsonAdapter):
    kind = ProviderKind.NAVER_BLOG_SEARCH
    source_class = ProviderSourceClass.OFFICIAL
    capabilities = frozenset(
        {ProviderCapability.BLOG_RESULTS, ProviderCapability.RELATED_KEYWORDS}
    )
    endpoint = "https://openapi.naver.com/v1/search/blog"
    allowed_urls = frozenset({endpoint})

    def validate(self, query: ProviderQuery, credential: CredentialMaterial) -> None:
        del query
        credential.require("client_id", "client_secret")

    async def collect(
        self, query: ProviderQuery, credential: CredentialMaterial
    ) -> ProviderResult:
        self.validate(query, credential)
        decoded, raw = await self._json_request(
            method="GET",
            url=self.endpoint,
            headers={
                "X-Naver-Client-Id": credential.values["client_id"],
                "X-Naver-Client-Secret": credential.values["client_secret"],
            },
            params={"query": query.keyword, "display": str(min(query.limit, 100)), "sort": "sim"},
        )
        total = _numeric(decoded.get("total"))
        raw_items = decoded.get("items", [])
        if not isinstance(raw_items, list) or total is None:
            raise ProviderError("PROVIDER_SCHEMA_INVALID", "Blog Search 응답 형식이 다릅니다.")
        samples: list[dict[str, Any]] = []
        for item in raw_items[:10]:
            if not isinstance(item, dict):
                continue
            title = html.unescape(_HTML_TAG_RE.sub("", str(item.get("title", ""))))
            samples.append(
                {
                    "title": sanitize_keyword(title[:1_000]).original_masked if title else "",
                    "link": str(item.get("link", ""))[:2_048],
                    "published_at": str(item.get("postdate", ""))[:16],
                }
            )
        return ProviderResult(
            provider=self.kind,
            source_class=self.source_class,
            source_label="Naver Blog Search Open API",
            value_kind="ABSOLUTE",
            measured_at=datetime.now(UTC),
            retrieved_at=datetime.now(UTC),
            metrics={"document_count": total, "sample_count": len(samples)},
            serp_samples=samples,
            limitations=("검색 API가 반환한 결과 수와 제한된 샘플만 저장합니다.",),
            confidence=1.0,
            raw_response=raw,
            adapter_name=type(self).__name__,
        )


class NaverShoppingInsightAdapter(_OfficialJsonAdapter):
    kind = ProviderKind.NAVER_SHOPPING_INSIGHT
    source_class = ProviderSourceClass.OFFICIAL
    capabilities = frozenset(
        {
            ProviderCapability.SHOPPING_TREND,
            ProviderCapability.TREND,
            ProviderCapability.DEMOGRAPHICS,
        }
    )
    endpoint = "https://openapi.naver.com/v1/datalab/shopping/category/keywords"
    allowed_urls = frozenset({endpoint})

    def validate(self, query: ProviderQuery, credential: CredentialMaterial) -> None:
        credential.require("client_id", "client_secret")
        category = query.dimensions.get("category")
        if not isinstance(category, str) or not category:
            raise ProviderError(
                "PROVIDER_QUERY_INVALID", "Shopping Insight category가 필요합니다."
            )
        if query.time_unit not in {"date", "week", "month"}:
            raise ProviderError(
                "PROVIDER_QUERY_INVALID", "Shopping Insight timeUnit이 올바르지 않습니다."
            )

    async def collect(
        self, query: ProviderQuery, credential: CredentialMaterial
    ) -> ProviderResult:
        self.validate(query, credential)
        category = query.dimensions.get("category")
        assert isinstance(category, str)
        start = query.start_date or date.today().replace(day=1)
        end = query.end_date or date.today()
        body: dict[str, Any] = {
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "timeUnit": query.time_unit,
            "category": category,
            "keyword": [{"name": query.keyword, "param": [query.keyword]}],
        }
        for key in ("device", "gender", "ages"):
            value = query.dimensions.get(key)
            if value:
                body[key] = value
        decoded, raw = await self._json_request(
            method="POST",
            url=self.endpoint,
            headers={
                "X-Naver-Client-Id": credential.values["client_id"],
                "X-Naver-Client-Secret": credential.values["client_secret"],
            },
            body=body,
        )
        points: list[dict[str, Any]] = []
        for result in decoded.get("results", []):
            if not isinstance(result, dict):
                continue
            for point in result.get("data", []):
                if isinstance(point, dict) and _numeric(point.get("ratio")) is not None:
                    points.append(
                        {"period": str(point.get("period", "")), "value": _numeric(point["ratio"])}
                    )
        return ProviderResult(
            provider=self.kind,
            source_class=self.source_class,
            source_label="Naver Shopping Insight Open API",
            value_kind="RELATIVE",
            measured_at=datetime.now(UTC),
            retrieved_at=datetime.now(UTC),
            metrics={"relative_click_trend": True, "category": category},
            trend_points=points,
            limitations=("상대 클릭 지수이며 절대 구매량이 아닙니다.", "Credential별 일 1,000회 한도"),
            confidence=1.0 if points else 0.0,
            raw_response=raw,
            adapter_name=type(self).__name__,
        )


class NaverSearchAdsAdapter(_OfficialJsonAdapter):
    kind = ProviderKind.NAVER_SEARCH_ADS
    source_class = ProviderSourceClass.OFFICIAL
    capabilities = frozenset(
        {
            ProviderCapability.RELATED_KEYWORDS,
            ProviderCapability.SEARCH_DEMAND,
            ProviderCapability.CPC,
            ProviderCapability.COMPETITION,
        }
    )
    endpoint = "https://api.searchad.naver.com/keywordstool"
    allowed_urls = frozenset({endpoint})

    def validate(self, query: ProviderQuery, credential: CredentialMaterial) -> None:
        del query
        credential.require("api_key", "secret_key", "customer_id")

    async def collect(
        self, query: ProviderQuery, credential: CredentialMaterial
    ) -> ProviderResult:
        self.validate(query, credential)
        timestamp = str(int(time.time() * 1_000))
        path = "/keywordstool"
        signature = base64.b64encode(
            hmac.new(
                credential.values["secret_key"].encode(),
                f"{timestamp}.GET.{path}".encode(),
                hashlib.sha256,
            ).digest()
        ).decode()
        decoded, raw = await self._json_request(
            method="GET",
            url=self.endpoint,
            headers={
                "X-Timestamp": timestamp,
                "X-API-KEY": credential.values["api_key"],
                "X-Customer": credential.values["customer_id"],
                "X-Signature": signature,
            },
            params={"hintKeywords": query.keyword, "showDetail": "1"},
        )
        items = decoded.get("keywordList")
        if not isinstance(items, list):
            raise ProviderError("PROVIDER_SCHEMA_INVALID", "Search Ads keywordList가 없습니다.")
        related: list[RelatedKeyword] = []
        primary_metrics: dict[str, Any] = {}
        estimated = False
        for index, item in enumerate(items[: max(1, min(query.limit, 1_000))]):
            if not isinstance(item, dict) or not item.get("relKeyword"):
                continue
            pc_raw = item.get("monthlyPcQcCnt")
            mobile_raw = item.get("monthlyMobileQcCnt")
            estimated = estimated or (
                isinstance(pc_raw, str) and pc_raw.startswith("<")
            ) or (isinstance(mobile_raw, str) and mobile_raw.startswith("<"))
            pc_value, pc_range = parse_search_count(pc_raw)
            mobile_value, mobile_range = parse_search_count(mobile_raw)
            metrics: dict[str, Any] = {
                "monthly_pc_searches": pc_value,
                "monthly_mobile_searches": mobile_value,
                "competition": item.get("compIdx"),
                "average_pc_clicks": _numeric(item.get("monthlyAvePcClkCnt")),
                "average_mobile_clicks": _numeric(item.get("monthlyAveMobileClkCnt")),
            }
            if pc_range:
                metrics["monthly_pc_searches_range"] = pc_range
            if mobile_range:
                metrics["monthly_mobile_searches_range"] = mobile_range
            related.append(
                RelatedKeyword(
                    text=str(item["relKeyword"])[:1_000],
                    reason="OFFICIAL_RELATED_KEYWORD",
                    metrics=metrics,
                )
            )
            if index == 0:
                primary_metrics = metrics
        limitations = ["검색광고 API 라이선스가 유효한 Credential에서 수집했습니다."]
        if estimated:
            limitations.append("'<10' 형태의 값은 정확값이 아니며 0 이상 10 미만 범위로 보존됩니다.")
        return ProviderResult(
            provider=self.kind,
            source_class=self.source_class,
            source_label="Naver Search Ads API",
            value_kind="ESTIMATED" if estimated else "ABSOLUTE",
            measured_at=datetime.now(UTC),
            retrieved_at=datetime.now(UTC),
            metrics=primary_metrics,
            related_keywords=related,
            limitations=tuple(limitations),
            confidence=0.8 if estimated else 1.0,
            raw_response=raw,
            adapter_name=type(self).__name__,
        )


class GoogleSearchConsoleAdapter(_OfficialJsonAdapter):
    kind = ProviderKind.GOOGLE_SEARCH_CONSOLE
    source_class = ProviderSourceClass.OFFICIAL
    capabilities = frozenset(
        {ProviderCapability.SITE_PERFORMANCE, ProviderCapability.SEARCH_DEMAND}
    )
    base_endpoint = "https://www.googleapis.com/webmasters/v3/sites"

    def __init__(self, site_url: str) -> None:
        self.site_url = site_url
        self.endpoint = (
            f"{self.base_endpoint}/{quote(site_url, safe='')}/searchAnalytics/query"
        )
        self.allowed_urls = frozenset({self.endpoint})

    def validate(self, query: ProviderQuery, credential: CredentialMaterial) -> None:
        del query
        credential.require("access_token")
        required_scope = "https://www.googleapis.com/auth/webmasters.readonly"
        if required_scope not in credential.scopes:
            raise ProviderError(
                "PROVIDER_SCOPE_MISSING", "Google Search Console readonly OAuth scope가 필요합니다."
            )

    async def collect(
        self, query: ProviderQuery, credential: CredentialMaterial
    ) -> ProviderResult:
        self.validate(query, credential)
        start = query.start_date or date.today().replace(day=1)
        end = query.end_date or date.today()
        row_limit = min(max(1, query.limit), 25_000)
        body: dict[str, Any] = {
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "dimensions": ["query", "page"],
            "rowLimit": row_limit,
            "dimensionFilterGroups": [
                {
                    "filters": [
                        {
                            "dimension": "query",
                            "operator": "contains",
                            "expression": query.keyword,
                        }
                    ]
                }
            ],
        }
        decoded, raw = await self._json_request(
            method="POST",
            url=self.endpoint,
            headers={"Authorization": f"Bearer {credential.values['access_token']}"},
            body=body,
        )
        rows = decoded.get("rows", [])
        if not isinstance(rows, list):
            raise ProviderError("PROVIDER_SCHEMA_INVALID", "Search Console rows 형식이 다릅니다.")
        samples: list[dict[str, Any]] = []
        totals = {"clicks": 0.0, "impressions": 0.0}
        related: list[RelatedKeyword] = []
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("keys"), list):
                continue
            keys = row["keys"]
            term = sanitize_keyword(str(keys[0])).original_masked if keys else ""
            page = str(keys[1])[:2_048] if len(keys) > 1 else ""
            clicks = _numeric(row.get("clicks")) or 0.0
            impressions = _numeric(row.get("impressions")) or 0.0
            totals["clicks"] += clicks
            totals["impressions"] += impressions
            if term:
                related.append(
                    RelatedKeyword(
                        text=term,
                        reason="OWNED_SITE_QUERY",
                        metrics={"clicks": clicks, "impressions": impressions},
                    )
                )
            if len(samples) < 10:
                samples.append(
                    {
                        "query": term,
                        "page": page,
                        "clicks": clicks,
                        "impressions": impressions,
                        "ctr": _numeric(row.get("ctr")),
                        "position": _numeric(row.get("position")),
                    }
                )
        return ProviderResult(
            provider=self.kind,
            source_class=self.source_class,
            source_label="Google Search Console Search Analytics API",
            value_kind="ABSOLUTE",
            measured_at=datetime.now(UTC),
            retrieved_at=datetime.now(UTC),
            metrics={**totals, "returned_rows": len(rows), "row_limit": row_limit},
            serp_samples=samples,
            related_keywords=related,
            limitations=("사이트 소유자의 데이터만 조회합니다.", "API는 상위 행만 반환할 수 있습니다."),
            confidence=1.0,
            raw_response=raw,
            adapter_name=type(self).__name__,
        )


class GoogleTrendsAlphaAdapter:
    """Explicit fail-closed boundary for the approval-only official alpha API."""

    kind = ProviderKind.GOOGLE_TRENDS_LICENSED
    source_class = ProviderSourceClass.OFFICIAL
    capabilities = frozenset({ProviderCapability.TREND, ProviderCapability.REGION})

    def validate(self, query: ProviderQuery, credential: CredentialMaterial) -> None:
        del query, credential
        raise ProviderError(
            "GOOGLE_TRENDS_ALPHA_NOT_CONFIGURED",
            "Google Trends 공식 API alpha 승인 connection이 구성되지 않았습니다.",
        )

    async def collect(
        self, query: ProviderQuery, credential: CredentialMaterial
    ) -> ProviderResult:
        self.validate(query, credential)
        raise AssertionError("unreachable")


class ContractProviderAdapter:
    """Base contract for compiled, allowlisted licensed-provider integrations.

    It intentionally has no generic URL implementation: accepting an endpoint from an API
    payload would turn this component into an SSRF/provenance bypass. A contract provider must
    subclass this adapter and ship a fixed endpoint plus response transformer.
    """

    kind = ProviderKind.CONTRACT_DATA
    source_class = ProviderSourceClass.LICENSED
    capabilities = frozenset(
        {
            ProviderCapability.RELATED_KEYWORDS,
            ProviderCapability.SEARCH_DEMAND,
            ProviderCapability.COMPETITION,
            ProviderCapability.LICENSED_SERP,
            ProviderCapability.REALTIME,
        }
    )

    def validate(self, query: ProviderQuery, credential: CredentialMaterial) -> None:
        del query, credential
        raise ProviderError(
            "LICENSED_PROVIDER_NOT_IMPLEMENTED",
            "계약별 고정 endpoint adapter가 설치되지 않았습니다.",
        )

    async def collect(
        self, query: ProviderQuery, credential: CredentialMaterial
    ) -> ProviderResult:
        self.validate(query, credential)
        raise AssertionError("unreachable")


class ProviderRegistry:
    def __init__(self, providers: Sequence[KeywordProvider] | None = None) -> None:
        defaults: list[KeywordProvider] = [
            NaverDataLabAdapter(),
            NaverSearchAdsAdapter(),
            NaverBlogSearchAdapter(),
            NaverShoppingInsightAdapter(),
            GoogleTrendsAlphaAdapter(),
            ContractProviderAdapter(),
        ]
        self._providers = {item.kind.value: item for item in (providers or defaults)}

    def get(
        self,
        connection: KeywordProviderConnection,
        capability: ProviderCapability,
    ) -> KeywordProvider:
        now = datetime.now(UTC)
        if connection.state != ProviderConnectionState.ACTIVE.value:
            raise ProviderError(
                "PROVIDER_CONNECTION_INACTIVE", "활성 공급자 connection이 아닙니다."
            )
        if connection.license_valid_until and connection.license_valid_until <= now:
            raise ProviderError("PROVIDER_LICENSE_EXPIRED", "공급자 데이터 라이선스가 만료됐습니다.")
        if connection.circuit_open_until and connection.circuit_open_until > now:
            retry_after = max(1, int((connection.circuit_open_until - now).total_seconds()))
            raise ProviderError(
                "PROVIDER_CIRCUIT_OPEN",
                "공급자 circuit breaker가 열려 있습니다.",
                retryable=True,
                retry_after_seconds=retry_after,
            )
        if connection.quota_remaining is not None and connection.quota_remaining <= 0:
            retry_after = None
            if connection.quota_reset_at and connection.quota_reset_at > now:
                retry_after = max(1, int((connection.quota_reset_at - now).total_seconds()))
            raise ProviderError(
                "PROVIDER_QUOTA_EXHAUSTED",
                "공급자 Credential 호출 한도를 모두 사용했습니다.",
                retryable=retry_after is not None,
                retry_after_seconds=retry_after,
            )
        provider = self._providers.get(connection.provider)
        if connection.provider == ProviderKind.GOOGLE_SEARCH_CONSOLE.value:
            site_url = connection.config_json.get("site_url")
            if not isinstance(site_url, str) or not site_url or len(site_url) > 2_048:
                raise ProviderError(
                    "PROVIDER_CONNECTION_CONFIG_INVALID",
                    "Search Console connection에는 검증된 site_url 속성이 필요합니다.",
                )
            provider = GoogleSearchConsoleAdapter(site_url)
        if provider is None:
            raise ProviderError(
                "PROVIDER_ADAPTER_NOT_INSTALLED", "승인된 공급자 adapter가 설치되지 않았습니다."
            )
        if provider.source_class.value != connection.source_class:
            raise ProviderError(
                "PROVIDER_PROVENANCE_UNVERIFIED",
                "connection과 adapter의 공식·계약 데이터 계보가 일치하지 않습니다.",
            )
        declared = set(connection.capabilities_json)
        if capability.value not in declared or capability not in provider.capabilities:
            raise ProviderError(
                "PROVIDER_CAPABILITY_NOT_ALLOWED",
                "connection 또는 라이선스가 요청 기능을 허용하지 않습니다.",
            )
        expected_source = {
            ProviderSourceClass.OFFICIAL.value,
            ProviderSourceClass.LICENSED.value,
        }
        if connection.source_class not in expected_source:
            raise ProviderError(
                "PROVIDER_PROVENANCE_UNVERIFIED",
                "공식 또는 계약 데이터 계보가 확인되지 않았습니다.",
            )
        if provider.kind in {
            ProviderKind.GOOGLE_TRENDS_LICENSED,
            ProviderKind.CONTRACT_DATA,
        } and not connection.license_ref:
            raise ProviderError(
                "PROVIDER_LICENSE_REQUIRED", "승인 또는 계약 라이선스 참조가 필요합니다."
            )
        return provider


def raw_response_hash(result: ProviderResult) -> str | None:
    return hashlib.sha256(result.raw_response).hexdigest() if result.raw_response else None


def validate_aggregate_demographics(result: ProviderResult) -> dict[str, dict[str, float]]:
    return aggregate_demographics(result.demographics)
