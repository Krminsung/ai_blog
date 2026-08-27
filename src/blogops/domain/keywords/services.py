"""Keyword intelligence application services over an RLS-scoped transaction."""

import csv
import hashlib
import io
import json
import random
import zipfile
from datetime import UTC, datetime, time, timedelta
from typing import Any, Mapping, Protocol, Sequence
from urllib.parse import urlsplit
from uuid import UUID, uuid4
from xml.sax.saxutils import escape as xml_escape
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.core.context import Principal
from blogops.core.errors import AppError
from blogops.core.retries import capped_exponential_delay
from blogops.domain.keywords.clustering import (
    ClusterCandidate,
    TECHNICAL_MAX_CLUSTER_CANDIDATES,
    cannibalization_recommendation,
    cluster_keywords,
)
from blogops.domain.keywords.enums import (
    ClusterDecisionState,
    ClusterKind,
    IntentSource,
    KeywordIntent,
    MetricValueKind,
    ProviderCallState,
    ProviderCapability,
    ProviderConnectionState,
    ProviderKind,
    ProviderSourceClass,
    ResearchInputKind,
    ResearchItemState,
    ResearchJobState,
    TERMINAL_JOB_STATES,
)
from blogops.domain.keywords.models import (
    Keyword,
    KeywordAlertRule,
    KeywordCluster,
    KeywordClusterMember,
    KeywordCollection,
    KeywordCollectionMember,
    KeywordContentLink,
    KeywordIntentRevision,
    KeywordMetricSnapshot,
    KeywordProviderCall,
    KeywordProviderConnection,
    KeywordResearchItem,
    KeywordResearchJob,
    KeywordSavedView,
    KeywordScoreProfile,
    KeywordScoreSnapshot,
)
from blogops.domain.keywords.normalization import (
    KeywordGuardPolicy,
    ParsedKeywordRow,
    TECHNICAL_MAX_BATCH_ROWS,
    evaluate_guard,
    exact_duplicate_map,
    normalize_batch,
    normalize_keyword,
    parse_csv_rows,
    sanitize_keyword,
    stable_json_hash,
)
from blogops.domain.keywords.providers import (
    FailClosedSecretResolver,
    ProviderError,
    ProviderQuery,
    ProviderRegistry,
    SecretResolver,
    raw_response_hash,
    validate_aggregate_demographics,
)
from blogops.domain.keywords.repository import KeywordRepository
from blogops.domain.keywords.schemas import (
    AlertRuleCreate,
    ClusterRequest,
    CollectionCreate,
    ContentLinkCreate,
    KeywordImportRequest,
    ProviderConnectionCreate,
    ResearchRequest,
    SavedViewCreate,
    ScoreProfileCreate,
)
from blogops.domain.keywords.scoring import (
    DEFAULT_WEIGHTS,
    FORMULA_VERSION,
    analyze_trend,
    calculate_brand_alignment,
    classify_intent,
    score_keyword,
    sensitive_topic_tags,
    validate_weights,
)
from blogops.services.audit import append_audit_log
from blogops.services.outbox import add_outbox_event


class RawResponseStore(Protocol):
    async def put_bytes(self, object_key: str, content: bytes, *, content_type: str) -> None: ...


def _next_quota_reset(provider: ProviderKind, now: datetime) -> datetime:
    timezone = ZoneInfo("Asia/Seoul") if provider.name.startswith("NAVER_") else ZoneInfo("UTC")
    local_now = now.astimezone(timezone)
    tomorrow = local_now.date() + timedelta(days=1)
    return datetime.combine(tomorrow, time.min, timezone).astimezone(UTC)


def _official_source_class(provider: ProviderKind) -> ProviderSourceClass:
    if provider == ProviderKind.CONTRACT_DATA:
        return ProviderSourceClass.LICENSED
    if provider == ProviderKind.USER_CSV:
        return ProviderSourceClass.USER_PROVIDED
    return ProviderSourceClass.OFFICIAL


def _default_daily_quota(provider: ProviderKind, requested: int | None) -> int | None:
    if provider in {ProviderKind.NAVER_DATALAB, ProviderKind.NAVER_SHOPPING_INSIGHT}:
        return 1_000
    return requested


def _safe_competitor_hosts(urls: Sequence[str]) -> list[str]:
    hosts: list[str] = []
    for value in urls:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname:
            raise AppError(
                "KEYWORD_COMPETITOR_URL_INVALID",
                "경쟁 사이트는 HTTPS URL이어야 하며 직접 크롤링되지 않습니다.",
                422,
            )
        host = parsed.hostname.casefold().rstrip(".")
        if host not in hosts:
            hosts.append(host)
    return hosts


def _rule_expansions(seed: str, region: str) -> list[tuple[str, str]]:
    """Transparent non-metric expansion; every suggestion carries its rule."""

    candidates = [
        (seed, "SEED"),
        (f"{seed} 방법", "QUESTION_RULE"),
        (f"{seed} 비교", "COMPARISON_RULE"),
        (f"{seed} 추천", "COMMERCIAL_RULE"),
        (f"{seed} 장단점", "LONGTAIL_RULE"),
    ]
    if region and region.upper() not in {"KR", "GLOBAL"}:
        candidates.append((f"{region} {seed}", "LOCAL_RULE"))
    unique: dict[str, tuple[str, str]] = {}
    for text, reason in candidates:
        try:
            sanitized = sanitize_keyword(text)
        except AppError as exc:
            if exc.code == "KEYWORD_TOO_LONG" and reason != "SEED":
                continue
            raise
        unique.setdefault(sanitized.normalized, (sanitized.original_masked, reason))
    return list(unique.values())


async def create_provider_connection(
    session: AsyncSession,
    *,
    principal: Principal,
    data: ProviderConnectionCreate,
) -> KeywordProviderConnection:
    expected = _official_source_class(data.provider)
    if data.source_class != expected:
        raise AppError(
            "KEYWORD_PROVIDER_PROVENANCE_INVALID",
            "공급자 유형과 공식·계약·사용자 제공 계보가 일치하지 않습니다.",
            422,
        )
    daily_quota = _default_daily_quota(data.provider, data.daily_quota)
    now = datetime.now(UTC)
    connection = KeywordProviderConnection(
        workspace_id=principal.workspace_id,
        provider=data.provider.value,
        source_class=data.source_class.value,
        name=data.name,
        credential_owner=data.credential_owner.value,
        secret_ref=data.secret_ref,
        license_ref=data.license_ref,
        license_valid_until=data.license_valid_until,
        state=ProviderConnectionState.ACTIVE.value,
        capabilities_json=sorted(item.value for item in data.capabilities),
        config_json=data.config,
        ttl_seconds=data.ttl_seconds,
        daily_quota=daily_quota,
        quota_remaining=daily_quota,
        quota_reset_at=_next_quota_reset(data.provider, now) if daily_quota else None,
    )
    session.add(connection)
    await session.flush()
    await append_audit_log(
        session,
        workspace_id=principal.workspace_id,
        actor_id=principal.subject_id,
        action="keyword.provider_connection.created",
        target_type="keyword_provider_connection",
        target_id=str(connection.id),
        details={
            "provider": connection.provider,
            "source_class": connection.source_class,
            "credential_owner": connection.credential_owner,
            "has_secret_ref": connection.secret_ref is not None,
            "daily_quota": connection.daily_quota,
        },
    )
    return connection


async def list_provider_connections(
    session: AsyncSession, workspace_id: UUID
) -> list[KeywordProviderConnection]:
    return await KeywordRepository(session, workspace_id).connections()


async def ensure_default_score_profile(
    session: AsyncSession, *, principal: Principal
) -> KeywordScoreProfile:
    repository = KeywordRepository(session, principal.workspace_id)
    existing = await repository.active_score_profile()
    if existing is not None:
        return existing
    content_hash = stable_json_hash({"formula": FORMULA_VERSION, "weights": DEFAULT_WEIGHTS})
    profile = KeywordScoreProfile(
        workspace_id=principal.workspace_id,
        name="default",
        version=1,
        formula_version=FORMULA_VERSION,
        weights_json=DEFAULT_WEIGHTS,
        thresholds_json={},
        content_hash=content_hash,
        is_active=True,
        created_by=principal.subject_id,
    )
    session.add(profile)
    await session.flush()
    return profile


async def create_score_profile(
    session: AsyncSession, *, principal: Principal, data: ScoreProfileCreate
) -> KeywordScoreProfile:
    weights = validate_weights(data.weights)
    repository = KeywordRepository(session, principal.workspace_id)
    version = await repository.next_score_profile_version(data.name)
    content_hash = stable_json_hash(
        {"formula": FORMULA_VERSION, "weights": weights, "thresholds": data.thresholds}
    )
    duplicate = await session.scalar(
        select(KeywordScoreProfile).where(
            KeywordScoreProfile.workspace_id == principal.workspace_id,
            KeywordScoreProfile.name == data.name,
            KeywordScoreProfile.content_hash == content_hash,
        )
    )
    if duplicate:
        return duplicate
    profile = KeywordScoreProfile(
        workspace_id=principal.workspace_id,
        name=data.name,
        version=version,
        formula_version=FORMULA_VERSION,
        weights_json=weights,
        thresholds_json=data.thresholds,
        content_hash=content_hash,
        is_active=True,
        created_by=principal.subject_id,
    )
    session.add(profile)
    await session.flush()
    await append_audit_log(
        session,
        workspace_id=principal.workspace_id,
        actor_id=principal.subject_id,
        action="keyword.score_profile.created",
        target_type="keyword_score_profile",
        target_id=str(profile.id),
        details={"name": profile.name, "version": profile.version},
    )
    return profile


def _research_rows(data: ResearchRequest) -> list[ParsedKeywordRow]:
    if data.input_kind == ResearchInputKind.SEED:
        assert data.seed is not None
        return normalize_batch([item[0] for item in _rule_expansions(data.seed, data.region)])
    if data.input_kind == ResearchInputKind.COMPETITOR:
        assert data.seed is not None
        return normalize_batch([data.seed])
    return normalize_batch(data.keywords)


def _masked_research_snapshot(
    data: ResearchRequest, rows: Sequence[ParsedKeywordRow]
) -> dict[str, Any]:
    return {
        "input_kind": data.input_kind.value,
        "keywords": [row.keyword.original_masked for row in rows],
        "competitor_hosts": _safe_competitor_hosts(data.competitor_urls),
        "language": data.language,
        "region": data.region,
        "start_date": data.start_date.isoformat() if data.start_date else None,
        "end_date": data.end_date.isoformat() if data.end_date else None,
        "time_unit": data.time_unit,
        "dimensions": _mask_nested_strings(data.dimensions),
        "excluded_terms": [sanitize_keyword(item).original_masked for item in data.excluded_terms],
        "banned_terms": [sanitize_keyword(item).original_masked for item in data.banned_terms],
        "brand_terms": [sanitize_keyword(item).original_masked for item in data.brand_terms],
        "require_metrics": data.require_metrics,
        "allow_stale": data.allow_stale,
    }


def _mask_nested_strings(value: Any) -> Any:
    if isinstance(value, str):
        if not value:
            return ""
        return sanitize_keyword(value[:1_000]).original_masked
    if isinstance(value, list):
        return [_mask_nested_strings(item) for item in value[:1_000]]
    if isinstance(value, dict):
        return {str(key)[:120]: _mask_nested_strings(item) for key, item in value.items()}
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:120]


async def _prepare_job(
    session: AsyncSession,
    *,
    principal: Principal,
    input_kind: ResearchInputKind,
    rows: Sequence[ParsedKeywordRow],
    snapshot: dict[str, Any],
    provider_connection_ids: Sequence[UUID],
    capabilities: Sequence[ProviderCapability],
    score_profile_id: UUID | None,
    idempotency_key: str,
    guard_policy: KeywordGuardPolicy,
    expansion_reasons: Mapping[str, str] | None = None,
) -> tuple[KeywordResearchJob, bool]:
    repository = KeywordRepository(session, principal.workspace_id)
    request_hash = stable_json_hash(
        {
            "snapshot": snapshot,
            "connections": sorted(str(item) for item in provider_connection_ids),
            "capabilities": sorted(item.value for item in capabilities),
            "score_profile_id": str(score_profile_id) if score_profile_id else None,
        }
    )
    existing = await repository.idempotent_job(
        input_kind.value, principal.subject_id, idempotency_key
    )
    if existing:
        if existing.input_hash != request_hash:
            raise AppError(
                "IDEMPOTENCY_KEY_REUSED",
                "동일한 Idempotency-Key가 다른 키워드 요청에 사용되었습니다.",
                409,
            )
        enqueue_needed = existing.state in {
            ResearchJobState.QUEUED.value,
            ResearchJobState.RETRYABLE_FAILED.value,
        }
        return existing, enqueue_needed
    profile = (
        await repository.score_profile(score_profile_id)
        if score_profile_id
        else await ensure_default_score_profile(session, principal=principal)
    )
    for connection_id in provider_connection_ids:
        await repository.connection(connection_id)
    job = KeywordResearchJob(
        workspace_id=principal.workspace_id,
        requested_by=principal.subject_id,
        input_kind=input_kind.value,
        state=ResearchJobState.QUEUED.value,
        idempotency_key=idempotency_key,
        input_hash=request_hash,
        input_snapshot_json=snapshot,
        provider_keys_json=[str(item) for item in provider_connection_ids],
        requested_capabilities_json=sorted(item.value for item in capabilities),
        score_profile_id=profile.id,
        total_items=len(rows),
    )
    session.add(job)
    await session.flush()
    duplicates = exact_duplicate_map(rows)
    first_items: dict[int, KeywordResearchItem] = {}
    for row in rows:
        decision = evaluate_guard(row.keyword.normalized, guard_policy)
        state = ResearchItemState.PENDING
        error_code = None
        error_detail = None
        if row.row_no in duplicates:
            state = ResearchItemState.EXCLUDED
            error_code = "KEYWORD_EXACT_DUPLICATE"
            error_detail = f"동일 작업의 {duplicates[row.row_no]}행과 정규화 결과가 같습니다."
        elif not decision.allowed:
            state = ResearchItemState.EXCLUDED if decision.excluded else ResearchItemState.BLOCKED
            error_code = decision.code
            error_detail = decision.reason
        elif row.keyword.pii_detected and provider_connection_ids:
            state = ResearchItemState.BLOCKED
            error_code = "KEYWORD_PII_BLOCKED"
            error_detail = "개인정보가 포함된 검색어는 외부 공급자에 전송하지 않습니다."
        item = KeywordResearchItem(
            workspace_id=principal.workspace_id,
            job_id=job.id,
            row_no=row.row_no,
            original_text_masked=row.keyword.original_masked,
            normalized=row.keyword.normalized,
            state=state.value,
            input_metrics_json=row.metrics,
            expansion_reason=(expansion_reasons or {}).get(row.keyword.normalized),
            error_code=error_code,
            error_detail=error_detail,
        )
        session.add(item)
        await session.flush()
        first_items[row.row_no] = item
        if row.row_no in duplicates:
            item.duplicate_of_item_id = first_items[duplicates[row.row_no]].id
    job.excluded_items = sum(
        1
        for item in first_items.values()
        if item.state in {ResearchItemState.EXCLUDED.value, ResearchItemState.BLOCKED.value}
    )
    await append_audit_log(
        session,
        workspace_id=principal.workspace_id,
        actor_id=principal.subject_id,
        action="keyword.research.queued",
        target_type="keyword_research_job",
        target_id=str(job.id),
        details={
            "input_kind": input_kind.value,
            "row_count": len(rows),
            "provider_count": len(provider_connection_ids),
            "input_hash": request_hash,
        },
    )
    await add_outbox_event(
        session,
        workspace_id=principal.workspace_id,
        aggregate_type="keyword_research_job",
        aggregate_id=str(job.id),
        event_type="keyword.research.queued",
        schema_version="1",
        payload={"job_id": str(job.id), "workspace_id": str(principal.workspace_id)},
    )
    await session.flush()
    return job, True


async def create_research_job(
    session: AsyncSession, *, principal: Principal, data: ResearchRequest
) -> tuple[KeywordResearchJob, bool]:
    rows = _research_rows(data)
    expansion_reasons = {
        normalize_keyword(text): reason
        for text, reason in (_rule_expansions(data.seed, data.region) if data.seed else [])
    }
    snapshot = _masked_research_snapshot(data, rows)
    return await _prepare_job(
        session,
        principal=principal,
        input_kind=data.input_kind,
        rows=rows,
        snapshot=snapshot,
        provider_connection_ids=data.provider_connection_ids,
        capabilities=tuple(data.capabilities),
        score_profile_id=data.score_profile_id,
        idempotency_key=data.idempotency_key,
        guard_policy=KeywordGuardPolicy.from_values(data.excluded_terms, data.banned_terms),
        expansion_reasons=expansion_reasons,
    )


async def create_import_job(
    session: AsyncSession, *, principal: Principal, data: KeywordImportRequest
) -> tuple[KeywordResearchJob, bool]:
    rows = parse_csv_rows(data.csv_content, data.mapping)
    snapshot = {
        "input_kind": ResearchInputKind.CSV.value,
        "keywords": [row.keyword.original_masked for row in rows],
        "language": data.language,
        "region": data.region,
        "brand_terms": [sanitize_keyword(item).original_masked for item in data.brand_terms],
        "require_metrics": False,
        "allow_stale": False,
        "csv_mapping": data.mapping,
        "csv_hash": hashlib.sha256(data.csv_content.encode("utf-8")).hexdigest(),
    }
    return await _prepare_job(
        session,
        principal=principal,
        input_kind=ResearchInputKind.CSV,
        rows=rows,
        snapshot=snapshot,
        provider_connection_ids=(),
        capabilities=(),
        score_profile_id=data.score_profile_id,
        idempotency_key=data.idempotency_key,
        guard_policy=KeywordGuardPolicy.from_values(data.excluded_terms, data.banned_terms),
    )


async def get_research_job(
    session: AsyncSession, workspace_id: UUID, job_id: UUID
) -> KeywordResearchJob:
    return await KeywordRepository(session, workspace_id).job(job_id)


async def list_research_items(
    session: AsyncSession, workspace_id: UUID, job_id: UUID
) -> list[KeywordResearchItem]:
    repository = KeywordRepository(session, workspace_id)
    await repository.job(job_id)
    return await repository.job_items(job_id)


async def request_cancel(
    session: AsyncSession, *, principal: Principal, job_id: UUID
) -> KeywordResearchJob:
    job = await KeywordRepository(session, principal.workspace_id).job(job_id, lock=True)
    current = ResearchJobState(job.state)
    if current in TERMINAL_JOB_STATES:
        return job
    job.state = ResearchJobState.CANCEL_REQUESTED.value
    await append_audit_log(
        session,
        workspace_id=principal.workspace_id,
        actor_id=principal.subject_id,
        action="keyword.research.cancel_requested",
        target_type="keyword_research_job",
        target_id=str(job.id),
    )
    return job


async def request_retry(
    session: AsyncSession, *, principal: Principal, job_id: UUID
) -> KeywordResearchJob:
    job = await KeywordRepository(session, principal.workspace_id).job(job_id, lock=True)
    if job.state not in {
        ResearchJobState.RETRYABLE_FAILED.value,
        ResearchJobState.PARTIAL.value,
    }:
        raise AppError(
            "KEYWORD_JOB_NOT_RETRYABLE", "현재 상태의 키워드 작업은 재시도할 수 없습니다.", 409
        )
    if job.attempt >= job.max_attempts:
        job.state = ResearchJobState.FINAL_FAILED.value
        raise AppError("KEYWORD_JOB_ATTEMPTS_EXHAUSTED", "최대 재시도 횟수를 초과했습니다.", 409)
    job.state = ResearchJobState.QUEUED.value
    job.next_retry_at = None
    job.retry_after_seconds = None
    for item in await KeywordRepository(session, principal.workspace_id).job_items(job.id):
        if item.state == ResearchItemState.FAILED.value:
            item.state = ResearchItemState.RETRYING.value
    await add_outbox_event(
        session,
        workspace_id=principal.workspace_id,
        aggregate_type="keyword_research_job",
        aggregate_id=str(job.id),
        event_type="keyword.research.retry_queued",
        schema_version="1",
        payload={"job_id": str(job.id)},
    )
    return job


async def _upsert_keyword(
    session: AsyncSession,
    *,
    repository: KeywordRepository,
    principal: Principal,
    item: KeywordResearchItem,
    snapshot: Mapping[str, Any],
) -> Keyword:
    language = str(snapshot.get("language", "ko"))
    region = str(snapshot.get("region", "KR"))
    existing = await repository.keyword_by_normalized(item.normalized, language, region)
    if existing:
        item.keyword_id = existing.id
        return existing
    intent = classify_intent(item.normalized)
    brand_terms = [str(value) for value in snapshot.get("brand_terms", [])]
    alignment = calculate_brand_alignment(item.normalized, brand_terms)
    keyword = Keyword(
        workspace_id=principal.workspace_id,
        display_text=item.original_text_masked,
        normalized=item.normalized,
        language=language,
        region=region,
        intent=intent.intent.value,
        intent_source=intent.source.value,
        intent_confidence=intent.confidence,
        intent_signals_json=dict(intent.signals),
        brand_alignment=alignment or 0.0,
        risk_tags_json=sensitive_topic_tags(item.normalized),
        is_excluded=False,
        created_by=principal.subject_id,
    )
    session.add(keyword)
    await session.flush()
    item.keyword_id = keyword.id
    return keyword


def _metric_dimensions(snapshot: Mapping[str, Any], keyword: Keyword) -> dict[str, Any]:
    return {
        **dict(snapshot.get("dimensions", {})),
        "language": keyword.language,
        "region": keyword.region,
        "start_date": snapshot.get("start_date"),
        "end_date": snapshot.get("end_date"),
        "time_unit": snapshot.get("time_unit", "month"),
    }


async def _store_raw_response(
    store: RawResponseStore | None,
    *,
    workspace_id: UUID,
    provider: str,
    result_hash: str | None,
    content: bytes | None,
) -> str | None:
    if store is None or result_hash is None or content is None:
        return None
    object_key = f"workspaces/{workspace_id}/keyword-provider/{provider}/{result_hash}.json"
    try:
        await store.put_bytes(object_key, content, content_type="application/json")
    except Exception as exc:
        raise ProviderError(
            "PROVIDER_LINEAGE_STORAGE_FAILED",
            "공급자 원본 응답 계보를 안전하게 저장하지 못했습니다.",
            retryable=True,
        ) from exc
    return object_key


def _reset_quota_if_due(connection: KeywordProviderConnection, now: datetime) -> None:
    if (
        connection.daily_quota is not None
        and connection.quota_reset_at is not None
        and connection.quota_reset_at <= now
    ):
        connection.quota_remaining = connection.daily_quota
        connection.quota_reset_at = _next_quota_reset(ProviderKind(connection.provider), now)


def _select_capability(
    connection: KeywordProviderConnection, requested: Sequence[str]
) -> ProviderCapability:
    ordered = list(requested) or [
        ProviderCapability.RELATED_KEYWORDS.value,
        ProviderCapability.SEARCH_DEMAND.value,
        ProviderCapability.TREND.value,
    ]
    available = set(connection.capabilities_json)
    for value in ordered:
        if value in available:
            return ProviderCapability(value)
    raise ProviderError(
        "PROVIDER_CAPABILITY_NOT_ALLOWED",
        "요청 기능을 제공하는 connection capability가 없습니다.",
    )


async def _record_metric(
    session: AsyncSession,
    *,
    job: KeywordResearchJob,
    keyword: Keyword,
    connection: KeywordProviderConnection | None,
    provider: str,
    source_class: str,
    source_label: str,
    value_kind: str,
    measured_at: datetime,
    retrieved_at: datetime,
    ttl_seconds: int,
    dimensions: Mapping[str, Any],
    metrics: Mapping[str, Any],
    trend_points: Sequence[Mapping[str, Any]],
    demographics: Mapping[str, Any],
    serp_samples: Sequence[Mapping[str, Any]],
    confidence: float,
    limitations: Sequence[str],
    request_hash: str,
    adapter_name: str,
    adapter_version: str,
    transform_version: str,
    raw_ref: str | None,
    raw_hash: str | None,
) -> KeywordMetricSnapshot:
    dimensions_json = dict(dimensions)
    metric = KeywordMetricSnapshot(
        workspace_id=job.workspace_id,
        keyword_id=keyword.id,
        job_id=job.id,
        provider_connection_id=connection.id if connection else None,
        provider=provider,
        source_class=source_class,
        source_label=source_label,
        value_kind=value_kind,
        measured_at=measured_at,
        retrieved_at=retrieved_at,
        expires_at=retrieved_at + timedelta(seconds=ttl_seconds),
        period_start=None,
        period_end=None,
        dimensions_json=dimensions_json,
        dimensions_hash=stable_json_hash(dimensions_json),
        metrics_json=dict(metrics),
        trend_points_json=[dict(item) for item in trend_points],
        demographics_json=dict(demographics),
        serp_samples_json=[dict(item) for item in serp_samples[:10]],
        confidence=max(0.0, min(1.0, confidence)),
        limitations_json=list(limitations),
        request_hash=request_hash,
        adapter_name=adapter_name,
        adapter_version=adapter_version,
        transform_version=transform_version,
        raw_object_ref=raw_ref,
        raw_response_hash=raw_hash,
        is_cached=False,
        is_stale=False,
    )
    session.add(metric)
    await session.flush()
    classification = classify_intent(keyword.display_text, metric.serp_samples_json)
    if keyword.intent_source != IntentSource.USER.value:
        keyword.intent = classification.intent.value
        keyword.intent_source = classification.source.value
        keyword.intent_confidence = classification.confidence
        keyword.intent_signals_json = dict(classification.signals)
    return metric


async def _record_score(
    session: AsyncSession,
    *,
    job: KeywordResearchJob,
    keyword: Keyword,
    metric: KeywordMetricSnapshot | None,
    profile: KeywordScoreProfile,
) -> KeywordScoreSnapshot:
    metrics = metric.metrics_json if metric else {}
    trend_points = metric.trend_points_json if metric else []
    result = score_keyword(
        metrics=metrics,
        trend_points=trend_points,
        brand_alignment=keyword.brand_alignment if keyword.brand_alignment > 0 else None,
        content_gap_score=metrics.get("content_gap_score"),
        evidence_confidence=metric.confidence if metric else 0.3,
        risk_tags=keyword.risk_tags_json,
        weights=profile.weights_json,
    )
    score = KeywordScoreSnapshot(
        workspace_id=job.workspace_id,
        keyword_id=keyword.id,
        metric_snapshot_id=metric.id if metric else None,
        profile_id=profile.id,
        opportunity_score=result.opportunity_score,
        components_json=dict(result.components),
        coverage=result.coverage,
        confidence=result.confidence,
        saturation_score=result.saturation_score,
        difficulty_lower=result.difficulty_lower,
        difficulty_upper=result.difficulty_upper,
        difficulty_confidence=result.difficulty_confidence,
        commerciality_score=result.commerciality_score,
        freshness_score=result.freshness_score,
        risk_score=result.risk_score,
        score_version=profile.formula_version,
    )
    session.add(score)
    await session.flush()
    return score


async def _collect_provider(
    session: AsyncSession,
    *,
    repository: KeywordRepository,
    principal: Principal,
    job: KeywordResearchJob,
    item: KeywordResearchItem,
    keyword: Keyword,
    connection_id: UUID,
    registry: ProviderRegistry,
    secrets: SecretResolver,
    raw_store: RawResponseStore | None,
) -> tuple[KeywordMetricSnapshot | None, list[tuple[str, str, Mapping[str, Any]]], dict[str, Any]]:
    connection = await repository.connection(connection_id, lock=True)
    now = datetime.now(UTC)
    _reset_quota_if_due(connection, now)
    dimensions = _metric_dimensions(job.input_snapshot_json, keyword)
    dimensions_hash = stable_json_hash(dimensions)
    cached = await repository.cached_metric(
        keyword_id=keyword.id,
        provider_connection_id=connection.id,
        provider=connection.provider,
        dimensions_hash=dimensions_hash,
        allow_stale=bool(job.input_snapshot_json.get("allow_stale", False)),
    )
    if cached:
        stale = cached.expires_at <= now
        call = KeywordProviderCall(
            workspace_id=job.workspace_id,
            connection_id=connection.id,
            job_id=job.id,
            provider=connection.provider,
            capability="CACHE",
            state=ProviderCallState.CACHE_HIT.value,
            request_hash=stable_json_hash(
                {
                    "keyword_hash": hashlib.sha256(keyword.normalized.encode()).hexdigest(),
                    "dimensions": dimensions,
                }
            ),
            request_metadata_json={"keyword_hash_only": True, "dimensions": dimensions},
            cache_hit=True,
            stale_returned=stale,
            quota_remaining_before=connection.quota_remaining,
            quota_remaining_after=connection.quota_remaining,
            quota_reset_at=connection.quota_reset_at,
            completed_at=now,
            latency_ms=0,
        )
        session.add(call)
        return cached, [], {
            "cached": True,
            "stale": stale,
            "retrieved_at": cached.retrieved_at.isoformat(),
            "quota_remaining": connection.quota_remaining,
            "quota_reset_at": connection.quota_reset_at.isoformat()
            if connection.quota_reset_at
            else None,
        }
    request_hash = stable_json_hash(
        {
            "provider": connection.provider,
            "keyword_hash": hashlib.sha256(keyword.normalized.encode()).hexdigest(),
            "dimensions": dimensions,
        }
    )
    call = KeywordProviderCall(
        id=uuid4(),
        workspace_id=job.workspace_id,
        connection_id=connection.id,
        job_id=job.id,
        provider=connection.provider,
        capability="UNRESOLVED",
        state=ProviderCallState.STARTED.value,
        request_hash=request_hash,
        request_metadata_json={"keyword_hash_only": True, "dimensions": dimensions},
        quota_remaining_before=connection.quota_remaining,
        quota_reset_at=connection.quota_reset_at,
    )
    started = datetime.now(UTC)
    quota_reserved = False
    try:
        capability = _select_capability(connection, job.requested_capabilities_json)
        call.capability = capability.value
        provider = registry.get(connection, capability)
        if not connection.secret_ref:
            raise ProviderError(
                "PROVIDER_SECRET_REF_REQUIRED", "외부 공급자에는 Secret Manager 참조가 필요합니다."
            )
        credential = await secrets.resolve(connection.secret_ref)
        provider_query = ProviderQuery(
            keyword=keyword.display_text,
            language=keyword.language,
            region=keyword.region,
            start_date=_date_or_none(job.input_snapshot_json.get("start_date")),
            end_date=_date_or_none(job.input_snapshot_json.get("end_date")),
            time_unit=str(job.input_snapshot_json.get("time_unit", "month")),
            dimensions=dimensions,
            limit=1_000,
        )
        provider.validate(provider_query, credential)
        # Reserve a provider call only after provenance, license, capability and secret
        # resolution succeeded. A local configuration failure must not consume quota.
        if connection.quota_remaining is not None:
            if connection.quota_remaining <= 0:
                raise ProviderError(
                    "PROVIDER_QUOTA_EXHAUSTED",
                    "공급자 Credential 호출 한도를 모두 사용했습니다.",
                    retryable=connection.quota_reset_at is not None,
                    retry_after_seconds=max(
                        1,
                        int((connection.quota_reset_at - now).total_seconds()),
                    )
                    if connection.quota_reset_at and connection.quota_reset_at > now
                    else None,
                )
            connection.quota_remaining -= 1
        quota_reserved = True
        connection.last_requested_at = now
        call.quota_remaining_after = connection.quota_remaining
        result = await provider.collect(provider_query, credential)
        if (
            result.provider.value != connection.provider
            or result.source_class.value != connection.source_class
        ):
            raise ProviderError(
                "PROVIDER_RESPONSE_PROVENANCE_INVALID",
                "공급자 응답 계보가 요청한 connection과 일치하지 않습니다.",
            )
        try:
            MetricValueKind(result.value_kind)
        except ValueError as exc:
            raise ProviderError(
                "PROVIDER_RESPONSE_VALUE_KIND_INVALID",
                "공급자 응답의 값 유형을 검증할 수 없습니다.",
            ) from exc
        demographics = validate_aggregate_demographics(result)
        response_hash = raw_response_hash(result)
        raw_ref = await _store_raw_response(
            raw_store,
            workspace_id=job.workspace_id,
            provider=connection.provider,
            result_hash=response_hash,
            content=result.raw_response,
        )
        metric = await _record_metric(
            session,
            job=job,
            keyword=keyword,
            connection=connection,
            provider=result.provider.value,
            source_class=result.source_class.value,
            source_label=result.source_label,
            value_kind=result.value_kind,
            measured_at=result.measured_at,
            retrieved_at=result.retrieved_at,
            ttl_seconds=connection.ttl_seconds,
            dimensions=dimensions,
            metrics=result.metrics,
            trend_points=result.trend_points,
            demographics=demographics,
            serp_samples=result.serp_samples,
            confidence=result.confidence,
            limitations=result.limitations,
            request_hash=request_hash,
            adapter_name=result.adapter_name,
            adapter_version=result.adapter_version,
            transform_version=result.transform_version,
            raw_ref=raw_ref,
            raw_hash=response_hash,
        )
        call.state = ProviderCallState.SUCCEEDED.value
        call.completed_at = datetime.now(UTC)
        call.latency_ms = int((call.completed_at - started).total_seconds() * 1_000)
        session.add(call)
        connection.consecutive_failures = 0
        connection.last_success_at = call.completed_at
        connection.last_error_code = None
        await append_audit_log(
            session,
            workspace_id=job.workspace_id,
            actor_id=principal.subject_id,
            action="keyword.provider_call.succeeded",
            target_type="keyword_provider_call",
            target_id=str(call.id),
            details={
                "provider": connection.provider,
                "request_hash": request_hash,
                "raw_response_hash": response_hash,
                "quota_remaining": connection.quota_remaining,
            },
        )
        related = [
            (item.text, item.reason, item.metrics) for item in result.related_keywords[:1_000]
        ]
        return metric, related, {
            "cached": False,
            "stale": False,
            "retrieved_at": result.retrieved_at.isoformat(),
            "quota_remaining": connection.quota_remaining,
            "quota_reset_at": connection.quota_reset_at.isoformat()
            if connection.quota_reset_at
            else None,
        }
    except ProviderError as exc:
        completed = datetime.now(UTC)
        call.state = (
            ProviderCallState.RETRYABLE_FAILED.value
            if exc.retryable
            else ProviderCallState.FINAL_FAILED.value
            if quota_reserved
            else ProviderCallState.BLOCKED.value
        )
        call.error_code = exc.code
        call.http_status = exc.http_status
        call.retry_after_seconds = exc.retry_after_seconds
        call.completed_at = completed
        call.latency_ms = int((completed - started).total_seconds() * 1_000)
        session.add(call)
        if quota_reserved:
            connection.consecutive_failures += 1
        connection.last_error_code = exc.code
        if exc.code in {"PROVIDER_CREDENTIAL_INVALID", "PROVIDER_SCOPE_MISSING"}:
            connection.state = ProviderConnectionState.CREDENTIAL_EXPIRED.value
        elif exc.code in {"PROVIDER_LICENSE_EXPIRED", "PROVIDER_LICENSE_REQUIRED"}:
            connection.state = ProviderConnectionState.LICENSE_EXPIRED.value
        elif exc.http_status == 429 and connection.quota_remaining is not None:
            connection.quota_remaining = 0
        elif quota_reserved and connection.consecutive_failures >= 5 and exc.retryable:
            connection.circuit_open_until = completed + timedelta(minutes=5)
        call.quota_remaining_after = connection.quota_remaining
        await append_audit_log(
            session,
            workspace_id=job.workspace_id,
            actor_id=principal.subject_id,
            action=(
                "keyword.provider_call.failed"
                if quota_reserved
                else "keyword.provider_call.blocked"
            ),
            target_type="keyword_provider_call",
            target_id=str(call.id),
            details={
                "provider": connection.provider,
                "request_hash": request_hash,
                "error_code": exc.code,
                "http_status": exc.http_status,
                "retry_after_seconds": exc.retry_after_seconds,
                "retryable": exc.retryable,
                "quota_reserved": quota_reserved,
            },
        )
        raise


def _date_or_none(value: Any):
    from datetime import date

    return date.fromisoformat(value) if isinstance(value, str) and value else None


async def _record_user_metric(
    session: AsyncSession,
    *,
    job: KeywordResearchJob,
    keyword: Keyword,
    metrics: Mapping[str, Any],
) -> KeywordMetricSnapshot:
    now = datetime.now(UTC)
    cleaned_metrics = {key: value for key, value in metrics.items() if not key.startswith("_")}
    embedded_provider = metrics.get("_provider")
    if isinstance(embedded_provider, str):
        provider = embedded_provider
        source_class = str(metrics.get("_source_class", ProviderSourceClass.OFFICIAL.value))
        source_label = "Related-keyword metrics embedded in an approved provider response"
        value_kind = str(metrics.get("_value_kind", MetricValueKind.ESTIMATED.value))
        confidence = float(metrics.get("_confidence", 0.8))
        limitation = "상위 키워드 요청 응답에 포함된 연관 키워드 지표입니다."
    else:
        provider = ProviderKind.USER_CSV.value
        source_class = ProviderSourceClass.USER_PROVIDED.value
        source_label = "User-provided CSV"
        value_kind = MetricValueKind.USER_PROVIDED.value
        confidence = 0.6
        limitation = "사용자가 제공한 값이며 서비스가 독립적으로 검증하지 않았습니다."
    return await _record_metric(
        session,
        job=job,
        keyword=keyword,
        connection=None,
        provider=provider,
        source_class=source_class,
        source_label=source_label,
        value_kind=value_kind,
        measured_at=now,
        retrieved_at=now,
        ttl_seconds=2_592_000,
        dimensions=_metric_dimensions(job.input_snapshot_json, keyword),
        metrics=cleaned_metrics,
        trend_points=(),
        demographics={},
        serp_samples=(),
        confidence=confidence,
        limitations=(limitation,),
        request_hash=job.input_hash,
        adapter_name="UserCsvAdapter" if not embedded_provider else "EmbeddedProviderResult",
        adapter_version="1",
        transform_version="1",
        raw_ref=None,
        raw_hash=None,
    )


async def _append_related_items(
    session: AsyncSession,
    *,
    job: KeywordResearchJob,
    existing_items: list[KeywordResearchItem],
    related: Sequence[tuple[str, str, Mapping[str, Any]]],
    provider: str,
    source_class: str,
) -> None:
    normalized_existing = {item.normalized for item in existing_items}
    next_row = max((item.row_no for item in existing_items), default=0) + 1
    for text, reason, metrics in related:
        if len(existing_items) >= TECHNICAL_MAX_BATCH_ROWS:
            break
        sanitized = sanitize_keyword(text)
        if sanitized.pii_detected or sanitized.normalized in normalized_existing:
            continue
        item = KeywordResearchItem(
            workspace_id=job.workspace_id,
            job_id=job.id,
            row_no=next_row,
            original_text_masked=sanitized.original_masked,
            normalized=sanitized.normalized,
            state=ResearchItemState.PENDING.value,
            expansion_reason=reason,
            input_metrics_json={
                **dict(metrics),
                "_provider": provider,
                "_source_class": source_class,
                "_skip_provider_refresh": True,
            },
        )
        session.add(item)
        await session.flush()
        existing_items.append(item)
        normalized_existing.add(item.normalized)
        next_row += 1
    job.total_items = len(existing_items)


async def process_research_job(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    job_id: UUID,
    registry: ProviderRegistry | None = None,
    secrets: SecretResolver | None = None,
    raw_store: RawResponseStore | None = None,
) -> KeywordResearchJob:
    """Process one durable job; callers own the workspace-scoped transaction."""

    repository = KeywordRepository(session, workspace_id)
    job = await repository.job(job_id, lock=True)
    state = ResearchJobState(job.state)
    if state in TERMINAL_JOB_STATES:
        return job
    if state == ResearchJobState.CANCEL_REQUESTED:
        job.state = ResearchJobState.CANCELLED.value
        job.finished_at = datetime.now(UTC)
        for item in await repository.job_items(job.id, lock=True):
            if item.state in {
                ResearchItemState.PENDING.value,
                ResearchItemState.RETRYING.value,
                ResearchItemState.RUNNING.value,
            }:
                item.state = ResearchItemState.CANCELLED.value
        return job
    if state not in {ResearchJobState.QUEUED, ResearchJobState.RETRYABLE_FAILED}:
        # Duplicate queue delivery is expected with at-least-once brokers. The locked row
        # makes non-runnable states an idempotent no-op instead of a task failure.
        return job
    job.state = ResearchJobState.VALIDATING.value
    job.attempt += 1
    job.started_at = job.started_at or datetime.now(UTC)
    principal = Principal(
        subject_id=job.requested_by,
        workspace_id=job.workspace_id,
        session_id=None,
        permissions=frozenset(),
        authentication_method="worker",
    )
    profile = await repository.score_profile(job.score_profile_id) if job.score_profile_id else None
    if profile is None:
        job.state = ResearchJobState.FINAL_FAILED.value
        job.error_code = "KEYWORD_SCORE_PROFILE_MISSING"
        job.error_detail = "점수 프로필 snapshot을 찾을 수 없습니다."
        job.finished_at = datetime.now(UTC)
        return job
    job.state = ResearchJobState.RESEARCHING.value
    provider_registry = registry or ProviderRegistry()
    secret_resolver = secrets or FailClosedSecretResolver()
    items = await repository.job_items(job.id, lock=True)
    provider_ids = [UUID(value) for value in job.provider_keys_json]
    retryable_failures: list[ProviderError] = []
    item_index = 0
    while item_index < len(items):
        item = items[item_index]
        item_index += 1
        if item.state in {
            ResearchItemState.EXCLUDED.value,
            ResearchItemState.BLOCKED.value,
            ResearchItemState.SUCCEEDED.value,
            ResearchItemState.CANCELLED.value,
        }:
            continue
        if job.state == ResearchJobState.CANCEL_REQUESTED.value:
            item.state = ResearchItemState.CANCELLED.value
            continue
        item.state = ResearchItemState.RUNNING.value
        item.started_at = datetime.now(UTC)
        item.attempt += 1
        keyword = await _upsert_keyword(
            session,
            repository=repository,
            principal=principal,
            item=item,
            snapshot=job.input_snapshot_json,
        )
        metrics: list[KeywordMetricSnapshot] = []
        provider_status: dict[str, Any] = dict(item.provider_status_json)
        if item.input_metrics_json:
            metric = await _record_user_metric(
                session, job=job, keyword=keyword, metrics=item.input_metrics_json
            )
            metrics.append(metric)
        skip_provider_refresh = bool(item.input_metrics_json.get("_skip_provider_refresh"))
        if not skip_provider_refresh:
            for connection_id in provider_ids:
                try:
                    metric, related, status = await _collect_provider(
                        session,
                        repository=repository,
                        principal=principal,
                        job=job,
                        item=item,
                        keyword=keyword,
                        connection_id=connection_id,
                        registry=provider_registry,
                        secrets=secret_resolver,
                        raw_store=raw_store,
                    )
                    if metric is not None:
                        metrics.append(metric)
                    connection = await repository.connection(connection_id)
                    provider_status[str(connection_id)] = {
                        "provider": connection.provider,
                        "state": "SUCCEEDED",
                        **status,
                    }
                    await _append_related_items(
                        session,
                        job=job,
                        existing_items=items,
                        related=related,
                        provider=connection.provider,
                        source_class=connection.source_class,
                    )
                except ProviderError as exc:
                    provider_status[str(connection_id)] = {
                        "state": "RETRYABLE_FAILED" if exc.retryable else "FINAL_FAILED",
                        "error_code": exc.code,
                        "retry_after_seconds": exc.retry_after_seconds,
                    }
                    if exc.retryable:
                        retryable_failures.append(exc)
        if not metrics:
            await _record_score(
                session, job=job, keyword=keyword, metric=None, profile=profile
            )
        else:
            for metric in metrics:
                await _record_score(
                    session, job=job, keyword=keyword, metric=metric, profile=profile
                )
        require_metrics = bool(job.input_snapshot_json.get("require_metrics", False))
        provider_failed = bool(provider_ids) and not metrics
        if (require_metrics and not metrics) or provider_failed:
            item.state = ResearchItemState.FAILED.value
            item.error_code = "KEYWORD_PROVIDER_RESULTS_UNAVAILABLE"
            item.error_detail = "요청한 공식·계약 공급자 지표를 확보하지 못했습니다."
        else:
            item.state = ResearchItemState.SUCCEEDED.value
            item.error_code = None
            item.error_detail = None
        item.provider_status_json = provider_status
        item.completed_at = datetime.now(UTC)
    all_items = await repository.job_items(job.id)
    job.processed_items = sum(
        1
        for item in all_items
        if item.state
        in {
            ResearchItemState.SUCCEEDED.value,
            ResearchItemState.FAILED.value,
            ResearchItemState.EXCLUDED.value,
            ResearchItemState.BLOCKED.value,
        }
    )
    job.failed_items = sum(1 for item in all_items if item.state == ResearchItemState.FAILED.value)
    job.excluded_items = sum(
        1
        for item in all_items
        if item.state in {ResearchItemState.EXCLUDED.value, ResearchItemState.BLOCKED.value}
    )
    job.progress_percent = round(job.processed_items / max(1, job.total_items) * 100, 2)
    succeeded = sum(1 for item in all_items if item.state == ResearchItemState.SUCCEEDED.value)
    if job.state == ResearchJobState.CANCEL_REQUESTED.value:
        job.state = ResearchJobState.CANCELLED.value
    elif job.failed_items == 0:
        job.state = ResearchJobState.SUCCEEDED.value
    elif succeeded > 0:
        job.state = ResearchJobState.PARTIAL.value
    elif retryable_failures and job.attempt < job.max_attempts:
        job.state = ResearchJobState.RETRYABLE_FAILED.value
        explicit = [
            item.retry_after_seconds
            for item in retryable_failures
            if item.retry_after_seconds
        ]
        if explicit:
            delay = max(explicit)
        else:
            base_delay = capped_exponential_delay(
                base_seconds=5,
                maximum_seconds=3_600,
                exponent=job.attempt - 1,
            )
            delay = max(1, int(random.SystemRandom().uniform(0.8, 1.2) * base_delay))
        job.retry_after_seconds = delay
        job.next_retry_at = datetime.now(UTC) + timedelta(seconds=delay)
        job.error_code = retryable_failures[-1].code
    else:
        job.state = ResearchJobState.FINAL_FAILED.value
        job.error_code = "KEYWORD_RESEARCH_FAILED"
    keyword_ids = list(
        dict.fromkeys(item.keyword_id for item in all_items if item.keyword_id is not None)
    )
    cluster_payload: dict[str, Any] = {
        "cluster_ids": [],
        "cluster_count": 0,
        "duplicate_count": 0,
        "generation_gate": "OPEN",
    }
    if len(keyword_ids) > TECHNICAL_MAX_CLUSTER_CANDIDATES:
        cluster_payload = {
            "cluster_ids": [],
            "cluster_count": 0,
            "duplicate_count": 0,
            "generation_gate": "DECISION_REQUIRED",
            "cluster_deferred": True,
            "reason": "TECHNICAL_BATCH_PARTITION_REQUIRED",
        }
    elif len(keyword_ids) >= 2 and succeeded:
        cluster_payload = await _persist_keyword_clusters(
            session,
            principal=principal,
            keyword_ids=keyword_ids,
            kind=ClusterKind.KEYWORD,
            similarity_threshold=0.72,
            use_serp_when_licensed=True,
            idempotency_key=f"research:{job.id}",
        )
    job.result_json = {
        "keyword_ids": [str(value) for value in keyword_ids],
        **cluster_payload,
        "provider_status": {
            str(item.id): item.provider_status_json
            for item in all_items
            if item.provider_status_json
        },
    }
    if job.state != ResearchJobState.RETRYABLE_FAILED.value:
        job.finished_at = datetime.now(UTC)
    event_type = (
        "keyword.research.succeeded"
        if job.state == ResearchJobState.SUCCEEDED.value
        else "keyword.research.partial"
        if job.state == ResearchJobState.PARTIAL.value
        else "keyword.research.failed"
    )
    await add_outbox_event(
        session,
        workspace_id=job.workspace_id,
        aggregate_type="keyword_research_job",
        aggregate_id=str(job.id),
        event_type=event_type,
        schema_version="1",
        payload={
            "job_id": str(job.id),
            "state": job.state,
            "processed": job.processed_items,
            "failed": job.failed_items,
            "generation_gate": cluster_payload["generation_gate"],
        },
    )
    await append_audit_log(
        session,
        workspace_id=job.workspace_id,
        actor_id=job.requested_by,
        action=event_type,
        target_type="keyword_research_job",
        target_id=str(job.id),
        details={
            "state": job.state,
            "total": job.total_items,
            "processed": job.processed_items,
            "failed": job.failed_items,
            "input_hash": job.input_hash,
        },
    )
    await session.flush()
    return job


def _metric_demand(metrics: Mapping[str, Any]) -> float | None:
    for key in ("search_volume", "monthly_searches", "impressions"):
        value = metrics.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    pc = metrics.get("monthly_pc_searches")
    mobile = metrics.get("monthly_mobile_searches")
    if isinstance(pc, (int, float)) or isinstance(mobile, (int, float)):
        return float(pc or 0) + float(mobile or 0)
    return None


async def _latest_metric_map(
    session: AsyncSession, workspace_id: UUID, keyword_ids: Sequence[UUID]
) -> dict[UUID, KeywordMetricSnapshot]:
    rows = list(
        await session.scalars(
            select(KeywordMetricSnapshot)
            .where(
                KeywordMetricSnapshot.workspace_id == workspace_id,
                KeywordMetricSnapshot.keyword_id.in_(keyword_ids),
            )
            .order_by(
                KeywordMetricSnapshot.keyword_id,
                KeywordMetricSnapshot.measured_at.desc(),
                KeywordMetricSnapshot.id.desc(),
            )
        )
    )
    result: dict[UUID, KeywordMetricSnapshot] = {}
    for row in rows:
        result.setdefault(row.keyword_id, row)
    return result


async def _persist_keyword_clusters(
    session: AsyncSession,
    *,
    principal: Principal,
    keyword_ids: Sequence[UUID],
    kind: ClusterKind,
    similarity_threshold: float,
    use_serp_when_licensed: bool,
    idempotency_key: str,
) -> dict[str, Any]:
    repository = KeywordRepository(session, principal.workspace_id)
    input_hash = stable_json_hash(
        {
            "keyword_ids": sorted(str(value) for value in keyword_ids),
            "kind": kind.value,
            "threshold": similarity_threshold,
            "use_serp": use_serp_when_licensed,
        }
    )
    existing = list(
        await session.scalars(
            select(KeywordCluster).where(
                KeywordCluster.workspace_id == principal.workspace_id,
                KeywordCluster.created_by == principal.subject_id,
                KeywordCluster.signals_json["idempotency_key"].astext == idempotency_key,
            )
        )
    )
    if existing:
        existing_hash = existing[0].signals_json.get("input_hash")
        if existing_hash != input_hash:
            raise AppError(
                "IDEMPOTENCY_KEY_REUSED",
                "동일한 Idempotency-Key가 다른 군집 요청에 사용되었습니다.",
                409,
            )
        member_rows = list(
            await session.scalars(
                select(KeywordClusterMember).where(
                    KeywordClusterMember.workspace_id == principal.workspace_id,
                    KeywordClusterMember.cluster_id.in_([item.id for item in existing]),
                )
            )
        )
        duplicate_count = max(0, len(member_rows) - len(existing))
        decision = any(item.decision_required for item in existing)
        return {
            "cluster_ids": [str(item.id) for item in existing],
            "cluster_count": len(existing),
            "duplicate_count": duplicate_count,
            "generation_gate": "DECISION_REQUIRED" if decision else "OPEN",
        }
    keywords = await repository.keywords_by_ids(keyword_ids)
    metrics = await _latest_metric_map(session, principal.workspace_id, keyword_ids)
    scores = await repository.latest_scores(keyword_ids)
    candidates: list[ClusterCandidate] = []
    for keyword in keywords:
        metric = metrics.get(keyword.id)
        score = scores.get(keyword.id)
        serp_urls: set[str] = set()
        serp_allowed = False
        if metric and metric.source_class in {
            ProviderSourceClass.OFFICIAL.value,
            ProviderSourceClass.LICENSED.value,
        }:
            serp_allowed = True
            for sample in metric.serp_samples_json:
                for key in ("link", "page"):
                    value = sample.get(key)
                    if isinstance(value, str) and value:
                        serp_urls.add(value)
        candidates.append(
            ClusterCandidate(
                keyword_id=keyword.id,
                text=keyword.display_text,
                intent=KeywordIntent(keyword.intent),
                opportunity_score=score.opportunity_score if score else None,
                search_demand=_metric_demand(metric.metrics_json) if metric else None,
                embedding=list(keyword.embedding) if keyword.embedding is not None else None,
                serp_urls=frozenset(serp_urls),
                serp_licensed=serp_allowed,
            )
        )
    calculated = cluster_keywords(
        candidates,
        kind=kind,
        similarity_threshold=similarity_threshold,
        use_serp_when_licensed=use_serp_when_licensed,
    )
    cluster_ids: list[str] = []
    for result in calculated:
        cluster = KeywordCluster(
            workspace_id=principal.workspace_id,
            name=result.name,
            kind=result.kind.value,
            method=result.method.value,
            version=1,
            primary_keyword_id=result.primary.keyword_id,
            intent=result.intent.value,
            confidence=result.confidence,
            decision_state=ClusterDecisionState.PROPOSED.value,
            decision_required=result.decision_required,
            signals_json={
                **dict(result.signals),
                "input_hash": input_hash,
                "idempotency_key": idempotency_key,
            },
            created_by=principal.subject_id,
        )
        session.add(cluster)
        await session.flush()
        cluster_ids.append(str(cluster.id))
        for member in result.members:
            session.add(
                KeywordClusterMember(
                    workspace_id=principal.workspace_id,
                    cluster_id=cluster.id,
                    keyword_id=member.candidate.keyword_id,
                    similarity_score=member.similarity_to_primary,
                    is_primary=member.is_primary,
                    signals_json=dict(member.signals),
                )
            )
    duplicate_count = sum(max(0, len(item.members) - 1) for item in calculated)
    decision_required = any(item.decision_required for item in calculated)
    await append_audit_log(
        session,
        workspace_id=principal.workspace_id,
        actor_id=principal.subject_id,
        action="keyword.cluster.created",
        target_type="keyword_cluster_batch",
        target_id=input_hash,
        details={
            "input_count": len(keyword_ids),
            "cluster_count": len(calculated),
            "duplicate_count": duplicate_count,
            "decision_required": decision_required,
        },
    )
    await add_outbox_event(
        session,
        workspace_id=principal.workspace_id,
        aggregate_type="keyword_cluster_batch",
        aggregate_id=input_hash,
        event_type="keyword.cluster.proposed",
        schema_version="1",
        payload={
            "cluster_ids": cluster_ids,
            "input_count": len(keyword_ids),
            "duplicate_count": duplicate_count,
            "generation_gate": "DECISION_REQUIRED" if decision_required else "OPEN",
        },
    )
    await session.flush()
    return {
        "cluster_ids": cluster_ids,
        "cluster_count": len(calculated),
        "duplicate_count": duplicate_count,
        "generation_gate": "DECISION_REQUIRED" if decision_required else "OPEN",
    }


async def create_keyword_clusters(
    session: AsyncSession, *, principal: Principal, data: ClusterRequest
) -> dict[str, Any]:
    payload = await _persist_keyword_clusters(
        session,
        principal=principal,
        keyword_ids=data.keyword_ids,
        kind=data.kind,
        similarity_threshold=data.similarity_threshold,
        use_serp_when_licensed=data.use_serp_when_licensed,
        idempotency_key=data.idempotency_key,
    )
    payload["input_count"] = len(data.keyword_ids)
    payload["clusters"] = await get_cluster_views(
        session,
        workspace_id=principal.workspace_id,
        cluster_ids=[UUID(value) for value in payload["cluster_ids"]],
    )
    return payload


async def get_cluster_views(
    session: AsyncSession, *, workspace_id: UUID, cluster_ids: Sequence[UUID]
) -> list[dict[str, Any]]:
    if not cluster_ids:
        return []
    clusters = list(
        await session.scalars(
            select(KeywordCluster)
            .where(
                KeywordCluster.workspace_id == workspace_id,
                KeywordCluster.id.in_(cluster_ids),
            )
            .order_by(KeywordCluster.created_at, KeywordCluster.id)
        )
    )
    member_rows = (
        await session.execute(
            select(KeywordClusterMember, Keyword)
            .join(
                Keyword,
                (Keyword.workspace_id == KeywordClusterMember.workspace_id)
                & (Keyword.id == KeywordClusterMember.keyword_id),
            )
            .where(
                KeywordClusterMember.workspace_id == workspace_id,
                KeywordClusterMember.cluster_id.in_(cluster_ids),
            )
            .order_by(
                KeywordClusterMember.cluster_id,
                KeywordClusterMember.is_primary.desc(),
                KeywordClusterMember.similarity_score.desc(),
                KeywordClusterMember.id,
            )
        )
    ).all()
    by_cluster: dict[UUID, list[dict[str, Any]]] = {}
    for member, keyword in member_rows:
        by_cluster.setdefault(member.cluster_id, []).append(
            {
                "keyword_id": member.keyword_id,
                "text": keyword.display_text,
                "similarity_score": member.similarity_score,
                "is_primary": member.is_primary,
                "signals": member.signals_json,
            }
        )
    return [
        {
            "id": cluster.id,
            "name": cluster.name,
            "kind": cluster.kind,
            "method": cluster.method,
            "version": cluster.version,
            "primary_keyword_id": cluster.primary_keyword_id,
            "intent": cluster.intent,
            "confidence": cluster.confidence,
            "decision_state": cluster.decision_state,
            "decision_required": cluster.decision_required,
            "signals": cluster.signals_json,
            "members": by_cluster.get(cluster.id, []),
        }
        for cluster in clusters
    ]


async def list_keywords(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    limit: int,
    cursor: UUID | None,
    intent: str | None = None,
    region: str | None = None,
    excluded: bool | None = None,
    query: str | None = None,
) -> list[Keyword]:
    return await KeywordRepository(session, workspace_id).list_keywords(
        limit=limit,
        cursor=cursor,
        intent=intent,
        region=region,
        excluded=excluded,
        query=normalize_keyword(query) if query else None,
    )


async def get_keyword_metrics(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    keyword_id: UUID,
    provider: str | None = None,
    limit: int = 100,
) -> tuple[Keyword, list[KeywordMetricSnapshot], dict[UUID, KeywordProviderConnection]]:
    repository = KeywordRepository(session, workspace_id)
    keyword = await repository.keyword(keyword_id)
    metrics = await repository.metric_history(keyword_id, provider=provider, limit=limit)
    connections: dict[UUID, KeywordProviderConnection] = {}
    for metric in metrics:
        if metric.provider_connection_id and metric.provider_connection_id not in connections:
            connections[metric.provider_connection_id] = await repository.connection(
                metric.provider_connection_id
            )
    return keyword, metrics, connections


async def compare_keywords(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    keyword_ids: Sequence[UUID],
    provider: str | None,
    allow_stale: bool,
) -> dict[str, Any]:
    repository = KeywordRepository(session, workspace_id)
    keywords = await repository.keywords_by_ids(keyword_ids)
    scores = await repository.latest_scores(keyword_ids)
    now = datetime.now(UTC)
    items: list[dict[str, Any]] = []
    periods: set[str] = set()
    value_kinds: set[str] = set()
    for keyword in keywords:
        history = await repository.metric_history(keyword.id, provider=provider, limit=100)
        metric = next(
            (item for item in history if allow_stale or item.expires_at > now),
            None,
        )
        if metric:
            periods.update(
                str(point.get("period"))
                for point in metric.trend_points_json
                if point.get("period")
            )
            value_kinds.add(metric.value_kind)
        score = scores.get(keyword.id)
        items.append(
            {
                "keyword": keyword,
                "metric": metric,
                "score": {
                    "opportunity_score": score.opportunity_score,
                    "difficulty_lower": score.difficulty_lower,
                    "difficulty_upper": score.difficulty_upper,
                    "confidence": score.confidence,
                    "score_version": score.score_version,
                }
                if score
                else None,
            }
        )
    return {
        "items": items,
        "common_axis": {
            "periods": sorted(periods),
            "value_kinds": sorted(value_kinds),
            "mixed_units_warning": len(value_kinds) > 1,
            "source_display_required": True,
        },
    }


async def keyword_trend_summary(
    session: AsyncSession, *, workspace_id: UUID, keyword_id: UUID, provider: str | None
):
    repository = KeywordRepository(session, workspace_id)
    await repository.keyword(keyword_id)
    snapshots = await repository.metric_history(keyword_id, provider=provider, limit=100)
    providers = {snapshot.provider for snapshot in snapshots}
    if provider is None and len(providers) > 1:
        raise AppError(
            "KEYWORD_TREND_PROVIDER_REQUIRED",
            "서로 다른 공급자의 추이 단위는 합칠 수 없으므로 provider를 지정해야 합니다.",
            422,
        )
    points: list[dict[str, Any]] = []
    for snapshot in reversed(snapshots):
        points.extend(snapshot.trend_points_json)
    return analyze_trend(points)


async def update_keyword_intent(
    session: AsyncSession,
    *,
    principal: Principal,
    keyword_id: UUID,
    intent: KeywordIntent,
    reason: str,
) -> Keyword:
    repository = KeywordRepository(session, principal.workspace_id)
    keyword = await repository.keyword(keyword_id)
    revision = KeywordIntentRevision(
        workspace_id=principal.workspace_id,
        keyword_id=keyword.id,
        previous_intent=keyword.intent,
        next_intent=intent.value,
        previous_source=keyword.intent_source,
        next_source=IntentSource.USER.value,
        reason=reason,
        changed_by=principal.subject_id,
    )
    session.add(revision)
    await session.flush()
    keyword.intent = intent.value
    keyword.intent_source = IntentSource.USER.value
    keyword.intent_confidence = 1.0
    keyword.intent_signals_json = {"revision_id": str(revision.id), "reason": reason}
    await append_audit_log(
        session,
        workspace_id=principal.workspace_id,
        actor_id=principal.subject_id,
        action="keyword.intent.updated",
        target_type="keyword",
        target_id=str(keyword.id),
        details={"intent": intent.value, "revision_id": str(revision.id)},
    )
    return keyword


async def save_keyword_view(
    session: AsyncSession, *, principal: Principal, data: SavedViewCreate
) -> KeywordSavedView:
    view = KeywordSavedView(
        workspace_id=principal.workspace_id,
        owner_id=principal.subject_id,
        name=data.name,
        filters_json=data.filters,
        sort_json=data.sort,
    )
    session.add(view)
    await session.flush()
    return view


async def create_collection(
    session: AsyncSession, *, principal: Principal, data: CollectionCreate
) -> KeywordCollection:
    collection = KeywordCollection(
        workspace_id=principal.workspace_id,
        kind=data.kind.value,
        name=data.name,
        campaign_opaque_ref=data.campaign_opaque_ref,
        created_by=principal.subject_id,
    )
    session.add(collection)
    await session.flush()
    return collection


async def add_collection_member(
    session: AsyncSession,
    *,
    principal: Principal,
    collection_id: UUID,
    keyword_id: UUID,
) -> KeywordCollectionMember:
    repository = KeywordRepository(session, principal.workspace_id)
    await repository.keyword(keyword_id)
    collection = await session.scalar(
        select(KeywordCollection).where(
            KeywordCollection.workspace_id == principal.workspace_id,
            KeywordCollection.id == collection_id,
        )
    )
    if collection is None:
        raise AppError("KEYWORD_COLLECTION_NOT_FOUND", "키워드 모음을 찾을 수 없습니다.", 404)
    existing = await session.scalar(
        select(KeywordCollectionMember).where(
            KeywordCollectionMember.workspace_id == principal.workspace_id,
            KeywordCollectionMember.collection_id == collection_id,
            KeywordCollectionMember.keyword_id == keyword_id,
        )
    )
    if existing:
        return existing
    member = KeywordCollectionMember(
        workspace_id=principal.workspace_id,
        collection_id=collection_id,
        keyword_id=keyword_id,
        added_by=principal.subject_id,
    )
    session.add(member)
    await session.flush()
    return member


async def create_alert_rule(
    session: AsyncSession, *, principal: Principal, data: AlertRuleCreate
) -> KeywordAlertRule:
    await KeywordRepository(session, principal.workspace_id).keyword(data.keyword_id)
    now = datetime.now(UTC)
    rule = KeywordAlertRule(
        workspace_id=principal.workspace_id,
        keyword_id=data.keyword_id,
        owner_id=principal.subject_id,
        kinds_json=sorted(item.value for item in data.kinds),
        thresholds_json=data.thresholds,
        channels_json=sorted(data.channels),
        cadence_minutes=data.cadence_minutes,
        next_evaluate_at=now + timedelta(minutes=data.cadence_minutes),
    )
    session.add(rule)
    await session.flush()
    return rule


async def provider_statuses(
    session: AsyncSession, workspace_id: UUID
) -> list[dict[str, Any]]:
    repository = KeywordRepository(session, workspace_id)
    result: list[dict[str, Any]] = []
    for connection in await repository.connections():
        calls, cache_hits, errors, last_error = await repository.provider_call_summary(
            connection.id
        )
        result.append(
            {
                "connection": connection,
                "calls": calls,
                "cache_hits": cache_hits,
                "errors": errors,
                "last_error_code": last_error,
            }
        )
    return result


def _canonical_content_target(target_kind: str, value: str) -> str:
    if target_kind == "URL":
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname:
            raise AppError(
                "KEYWORD_CONTENT_URL_INVALID", "기존 콘텐츠 URL은 HTTPS여야 합니다.", 422
            )
        try:
            parsed_port = parsed.port
        except ValueError as exc:
            raise AppError(
                "KEYWORD_CONTENT_URL_INVALID", "기존 콘텐츠 URL의 포트가 올바르지 않습니다.", 422
            ) from exc
        port = f":{parsed_port}" if parsed_port and parsed_port != 443 else ""
        path = parsed.path or "/"
        return f"https://{parsed.hostname.casefold()}{port}{path}"
    try:
        return str(UUID(value))
    except ValueError as exc:
        raise AppError(
            "KEYWORD_CONTENT_REF_INVALID", "콘텐츠 참조 ID가 올바르지 않습니다.", 422
        ) from exc


async def link_keyword_content(
    session: AsyncSession, *, principal: Principal, data: ContentLinkCreate
) -> KeywordContentLink:
    await KeywordRepository(session, principal.workspace_id).keyword(data.keyword_id)
    target = _canonical_content_target(data.target_kind.value, data.target_ref)
    target_hash = hashlib.sha256(target.encode("utf-8")).hexdigest()
    existing = await session.scalar(
        select(KeywordContentLink).where(
            KeywordContentLink.workspace_id == principal.workspace_id,
            KeywordContentLink.keyword_id == data.keyword_id,
            KeywordContentLink.target_kind == data.target_kind.value,
            KeywordContentLink.target_ref == target,
        )
    )
    if existing:
        existing.title = data.title
        existing.mapped_intent = data.mapped_intent.value
        existing.similarity = data.similarity
        existing.recommendation = data.recommendation.value
        existing.evidence_json = data.evidence
        existing.last_observed_at = data.observed_at
        return existing
    link = KeywordContentLink(
        workspace_id=principal.workspace_id,
        keyword_id=data.keyword_id,
        target_kind=data.target_kind.value,
        target_ref=target,
        target_hash=target_hash,
        title=data.title,
        mapped_intent=data.mapped_intent.value,
        similarity=data.similarity,
        recommendation=data.recommendation.value,
        evidence_json=data.evidence,
        last_observed_at=data.observed_at,
    )
    session.add(link)
    await session.flush()
    return link


async def analyze_content_overlap(
    session: AsyncSession, *, workspace_id: UUID, keyword_ids: Sequence[UUID]
) -> dict[str, Any]:
    repository = KeywordRepository(session, workspace_id)
    keywords = await repository.keywords_by_ids(keyword_ids)
    links = list(
        await session.scalars(
            select(KeywordContentLink)
            .where(
                KeywordContentLink.workspace_id == workspace_id,
                KeywordContentLink.keyword_id.in_(keyword_ids),
            )
            .order_by(KeywordContentLink.keyword_id, KeywordContentLink.target_ref)
        )
    )
    keyword_by_id = {item.id: item for item in keywords}
    cannibalization = cannibalization_recommendation(
        [
            {
                "intent": link.mapped_intent,
                "target_ref": link.target_ref,
                "keyword_id": str(link.keyword_id),
            }
            for link in links
        ]
    )
    linked_ids = {link.keyword_id for link in links}
    gaps = [
        {
            "keyword_id": str(keyword.id),
            "keyword": keyword.display_text,
            "intent": keyword.intent,
            "recommendation": "NEW",
            "reason": "NO_EXISTING_CONTENT_MAPPING",
        }
        for keyword in keywords
        if keyword.id not in linked_ids
    ]
    missing_signals: list[str] = []
    if not links:
        missing_signals.append("EXISTING_SITE_CONTENT")
    metrics = await _latest_metric_map(session, workspace_id, keyword_ids)
    if len(metrics) < len(keyword_by_id):
        missing_signals.append("COMPETITOR_OR_DEMAND_METRICS")
    return {
        "links": links,
        "cannibalization": cannibalization,
        "gaps": gaps,
        "missing_signals": sorted(set(missing_signals)),
    }


async def evaluate_due_alerts(
    session: AsyncSession, *, workspace_id: UUID, limit: int = 100
) -> int:
    """Evaluate only saved keyword rules and emit delivery-neutral notification events."""

    now = datetime.now(UTC)
    rules = list(
        await session.scalars(
            select(KeywordAlertRule)
            .where(
                KeywordAlertRule.workspace_id == workspace_id,
                KeywordAlertRule.enabled.is_(True),
                KeywordAlertRule.next_evaluate_at <= now,
            )
            .order_by(KeywordAlertRule.next_evaluate_at, KeywordAlertRule.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )
    emitted = 0
    repository = KeywordRepository(session, workspace_id)
    for rule in rules:
        history = await repository.metric_history(rule.keyword_id, limit=2)
        events: list[str] = []
        if len(history) >= 2:
            current_demand = _metric_demand(history[0].metrics_json)
            previous_demand = _metric_demand(history[1].metrics_json)
            if current_demand is not None and previous_demand not in {None, 0}:
                change = (current_demand - previous_demand) / previous_demand
                surge_threshold = rule.thresholds_json.get("surge_ratio", 0.3)
                decline_threshold = rule.thresholds_json.get("decline_ratio", -0.3)
                if "SURGE" in rule.kinds_json and change >= surge_threshold:
                    events.append("SURGE")
                if "DECLINE" in rule.kinds_json and change <= decline_threshold:
                    events.append("DECLINE")
            current_competition = history[0].metrics_json.get("competition")
            previous_competition = history[1].metrics_json.get("competition")
            if (
                "COMPETITION" in rule.kinds_json
                and isinstance(current_competition, (int, float))
                and isinstance(previous_competition, (int, float))
                and current_competition - previous_competition
                >= rule.thresholds_json.get("competition_delta", 0.2)
            ):
                events.append("COMPETITION")
        trend = analyze_trend(history[0].trend_points_json if history else [])
        if "SEASONAL" in rule.kinds_json and trend.seasonal:
            events.append("SEASONAL")
        if events:
            await add_outbox_event(
                session,
                workspace_id=workspace_id,
                aggregate_type="keyword_alert_rule",
                aggregate_id=str(rule.id),
                event_type="keyword.alert.triggered",
                schema_version="1",
                payload={
                    "rule_id": str(rule.id),
                    "keyword_id": str(rule.keyword_id),
                    "owner_id": str(rule.owner_id),
                    "events": events,
                    "channels": rule.channels_json,
                },
            )
            emitted += 1
        rule.last_evaluated_at = now
        rule.next_evaluate_at = now + timedelta(minutes=rule.cadence_minutes)
    return emitted


def _export_rows(
    keywords: Sequence[Keyword],
    metrics: Mapping[UUID, KeywordMetricSnapshot],
    scores: Mapping[UUID, KeywordScoreSnapshot],
) -> list[list[str]]:
    rows = [
        [
            "keyword_id",
            "keyword",
            "normalized",
            "language",
            "region",
            "intent",
            "provider",
            "measured_at",
            "retrieved_at",
            "value_kind",
            "metrics",
            "opportunity_score",
            "difficulty_lower",
            "difficulty_upper",
            "confidence",
            "limitations",
        ]
    ]
    for keyword in keywords:
        metric = metrics.get(keyword.id)
        score = scores.get(keyword.id)
        rows.append(
            [
                str(keyword.id),
                keyword.display_text,
                keyword.normalized,
                keyword.language,
                keyword.region,
                keyword.intent,
                metric.provider if metric else "",
                metric.measured_at.isoformat() if metric else "",
                metric.retrieved_at.isoformat() if metric else "",
                metric.value_kind if metric else "",
                json.dumps(metric.metrics_json, ensure_ascii=False, sort_keys=True)
                if metric
                else "",
                str(score.opportunity_score)
                if score and score.opportunity_score is not None
                else "",
                str(score.difficulty_lower) if score and score.difficulty_lower is not None else "",
                str(score.difficulty_upper) if score and score.difficulty_upper is not None else "",
                str(score.confidence) if score else "",
                " | ".join(metric.limitations_json) if metric else "",
            ]
        )
    return rows


def _xlsx_bytes(rows: Sequence[Sequence[str]]) -> bytes:
    """Create a small standards-compliant inline-string XLSX without a runtime dependency."""

    output = io.BytesIO()
    sheet_rows: list[str] = []
    for row_index, row in enumerate(rows, start=1):
        cells: list[str] = []
        for column_index, value in enumerate(row, start=1):
            column = ""
            number = column_index
            while number:
                number, remainder = divmod(number - 1, 26)
                column = chr(65 + remainder) + column
            clean = str(value).replace("\x00", "")
            cells.append(
                f'<c r="{column}{row_index}" t="inlineStr"><is><t xml:space="preserve">'
                f"{xml_escape(clean)}</t></is></c>"
            )
        sheet_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(sheet_rows)}</sheetData></worksheet>'
    )
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" '
            'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.'
            'spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.'
            'spreadsheetml.worksheet+xml"/>'
            "</Types>",
        )
        archive.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>",
        )
        archive.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Keywords" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            "</Relationships>",
        )
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return output.getvalue()


def _spreadsheet_safe(value: str) -> str:
    text = str(value)
    return f"'{text}" if text.lstrip().startswith(("=", "+", "-", "@")) else text


async def export_keywords(
    session: AsyncSession,
    *,
    principal: Principal,
    keyword_ids: Sequence[UUID],
    export_format: str,
) -> tuple[bytes, str, str]:
    repository = KeywordRepository(session, principal.workspace_id)
    keywords = await repository.keywords_by_ids(keyword_ids)
    metrics = await _latest_metric_map(session, principal.workspace_id, keyword_ids)
    scores = await repository.latest_scores(keyword_ids)
    rows = _export_rows(keywords, metrics, scores)
    if export_format == "CSV":
        output = io.StringIO()
        writer = csv.writer(output, lineterminator="\r\n")
        writer.writerows([[_spreadsheet_safe(value) for value in row] for row in rows])
        content = ("\ufeff" + output.getvalue()).encode("utf-8")
        media_type = "text/csv; charset=utf-8"
        filename = "keywords.csv"
    elif export_format == "XLSX":
        content = _xlsx_bytes(rows)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        filename = "keywords.xlsx"
    else:
        raise AppError("KEYWORD_EXPORT_FORMAT_INVALID", "지원하지 않는 내보내기 형식입니다.", 422)
    await append_audit_log(
        session,
        workspace_id=principal.workspace_id,
        actor_id=principal.subject_id,
        action="keyword.export.created",
        target_type="keyword_export",
        target_id=str(uuid4()),
        details={"format": export_format, "keyword_count": len(keywords)},
    )
    return content, media_type, filename
