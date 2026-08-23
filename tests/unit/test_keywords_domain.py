from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

import pytest

from blogops.core.errors import AppError
from blogops.domain.keywords.clustering import ClusterCandidate, cluster_keywords
from blogops.domain.keywords.enums import (
    ClusterKind,
    CredentialOwner,
    KeywordIntent,
    ProviderCapability,
    ProviderConnectionState,
    ProviderKind,
    ProviderSourceClass,
    TrendDirection,
)
from blogops.domain.keywords.models import KeywordProviderConnection
from blogops.domain.keywords.normalization import (
    KeywordGuardPolicy,
    aggregate_demographics,
    evaluate_guard,
    exact_duplicate_map,
    normalize_batch,
    parse_csv_rows,
    sanitize_keyword,
)
from blogops.domain.keywords.providers import (
    CredentialMaterial,
    FailClosedSecretResolver,
    GoogleTrendsAlphaAdapter,
    NaverDataLabAdapter,
    ProviderError,
    ProviderQuery,
    ProviderRegistry,
    parse_search_count,
)
from blogops.domain.keywords.scoring import (
    DEFAULT_WEIGHTS,
    analyze_trend,
    classify_intent,
    score_keyword,
    validate_weights,
)


def test_keyword_input_is_normalized_masked_deduplicated_and_guarded() -> None:
    sanitized = sanitize_keyword("  AI\u200b  블로그 test@example.com 010-1234-5678 ")
    assert sanitized.pii_detected is True
    assert sanitized.original_masked == "AI 블로그 [EMAIL] [PHONE]"
    assert sanitized.normalized == "ai 블로그 [email] [phone]"

    rows = normalize_batch(["AI 블로그", "ＡＩ   블로그", "허용 키워드"])
    assert exact_duplicate_map(rows) == {2: 1}

    policy = KeywordGuardPolicy.from_values(["제외"], ["불법"])
    assert evaluate_guard("제외 키워드", policy).excluded is True
    blocked = evaluate_guard("불법 키워드", policy)
    assert blocked.allowed is False
    assert blocked.excluded is False


def test_csv_import_limits_schema_masks_pii_and_keeps_user_metrics() -> None:
    # A comma thousands separator must be quoted by a valid CSV producer.
    csv_text = '검색어,검색량,경쟁도\nAI 블로그,"1,250",0.7\n문의 test@example.com,10,0.3\n'
    rows = parse_csv_rows(
        csv_text,
        {"keyword": "검색어", "search_volume": "검색량", "competition": "경쟁도"},
    )
    assert rows[0].metrics == {"search_volume": 1250.0, "competition": 0.7}
    assert rows[1].keyword.original_masked == "문의 [EMAIL]"
    assert rows[1].keyword.pii_detected is True

    assert len(normalize_batch([f"키워드 {index}" for index in range(1_001)])) == 1_001
    with pytest.raises(AppError) as error:
        normalize_batch([f"키워드 {index}" for index in range(10_001)])
    assert error.value.code == "KEYWORD_BATCH_TOO_LARGE"


def test_search_ads_bounded_count_is_not_stored_as_an_exact_value() -> None:
    exact, bounded = parse_search_count("<10")
    assert exact is None
    assert bounded == {"min_inclusive": 0.0, "max_exclusive": 10.0}
    assert parse_search_count("120") == (120.0, None)


def test_provider_registry_is_official_only_quota_aware_and_fail_closed() -> None:
    now = datetime.now(UTC)
    connection = KeywordProviderConnection(
        workspace_id=uuid5(NAMESPACE_URL, "workspace"),
        provider=ProviderKind.NAVER_DATALAB.value,
        source_class=ProviderSourceClass.OFFICIAL.value,
        name="default",
        credential_owner=CredentialOwner.CUSTOMER.value,
        secret_ref="secret-manager://naver/customer",
        state=ProviderConnectionState.ACTIVE.value,
        capabilities_json=[ProviderCapability.TREND.value],
        config_json={},
        ttl_seconds=3_600,
        daily_quota=1_000,
        quota_remaining=1_000,
        quota_reset_at=now + timedelta(hours=1),
        consecutive_failures=0,
    )
    provider = ProviderRegistry().get(connection, ProviderCapability.TREND)
    assert isinstance(provider, NaverDataLabAdapter)
    assert ProviderCapability.REALTIME not in provider.capabilities

    connection.quota_remaining = 0
    with pytest.raises(ProviderError) as error:
        ProviderRegistry().get(connection, ProviderCapability.TREND)
    assert error.value.code == "PROVIDER_QUOTA_EXHAUSTED"


@pytest.mark.asyncio
async def test_unconfigured_secret_and_google_trends_alpha_fail_closed() -> None:
    with pytest.raises(ProviderError) as secret_error:
        await FailClosedSecretResolver().resolve("secret-manager://missing")
    assert secret_error.value.code == "PROVIDER_SECRET_RESOLVER_UNAVAILABLE"

    with pytest.raises(ProviderError) as trends_error:
        await GoogleTrendsAlphaAdapter().collect(
            ProviderQuery(keyword="AI 블로그"),
            CredentialMaterial(values={"access_token": "not-used"}),
        )
    assert trends_error.value.code == "GOOGLE_TRENDS_ALPHA_NOT_CONFIGURED"


def test_demographics_accepts_only_anonymous_aggregate_buckets() -> None:
    aggregate = aggregate_demographics(
        {"device": {"pc": 30, "mobile": 70}, "age": {"20-29": 45, "30-39": 55}}
    )
    assert aggregate["device"]["mobile"] == 70.0
    with pytest.raises(AppError) as error:
        aggregate_demographics({"user_id": {"person-1": 1}})
    assert error.value.code == "KEYWORD_DEMOGRAPHICS_NOT_AGGREGATED"


def test_intent_trend_and_opportunity_scores_are_explainable_ranges() -> None:
    intent = classify_intent("AI 블로그 도구 비교 추천")
    assert intent.intent == KeywordIntent.COMPARISON
    points = [
        {"period": f"2026-{month:02d}", "value": 20.0 + month * 5}
        for month in range(1, 13)
    ]
    trend = analyze_trend(points)
    assert trend.direction in {TrendDirection.RISING, TrendDirection.SURGING}
    score = score_keyword(
        metrics={
            "search_volume": 2_000,
            "competition": 0.4,
            "document_count": 20_000,
            "cpc": 1_500,
            "content_gap_score": 80,
        },
        trend_points=points,
        brand_alignment=0.9,
        content_gap_score=80,
        evidence_confidence=0.8,
        risk_tags=[],
    )
    assert score.opportunity_score is not None
    assert 0 <= score.opportunity_score <= 100
    assert score.difficulty_lower is not None
    assert score.difficulty_upper is not None
    assert score.difficulty_lower <= score.difficulty_upper
    assert all("available" in component for component in score.components.values())

    with pytest.raises(AppError):
        validate_weights({**DEFAULT_WEIGHTS, "trend": 0.9})


def test_thousand_row_fixture_groups_180_semantically_equivalent_keywords() -> None:
    variants = (
        "AI 블로그 자동화 방법",
        "AI 블로그 자동화 방법!",
        "AI 블로그 자동화 방법?",
        "AI 블로그 자동화 방법 가이드",
        "AI 블로그 자동화 방법 총정리",
    )
    candidates = [
        ClusterCandidate(
            keyword_id=uuid5(NAMESPACE_URL, f"equivalent-{index}"),
            text=variants[index % len(variants)],
            intent=KeywordIntent.INFORMATIONAL,
            opportunity_score=80 - index / 1_000,
        )
        for index in range(180)
    ]
    candidates.extend(
        ClusterCandidate(
            keyword_id=uuid5(NAMESPACE_URL, f"independent-{index}"),
            text=f"독립 주제 {index} 고유 토큰 z{index * 7919}",
            intent=KeywordIntent.UNKNOWN,
        )
        for index in range(820)
    )
    clusters = cluster_keywords(candidates, kind=ClusterKind.KEYWORD)
    equivalent_ids = {item.keyword_id for item in candidates[:180]}
    matching = [
        cluster
        for cluster in clusters
        if equivalent_ids.issubset({member.candidate.keyword_id for member in cluster.members})
    ]
    assert len(matching) == 1
    assert matching[0].decision_required is True
    assert len(matching[0].members) >= 180
    # The gate consumes the proposed cluster; it does not create 180 downstream articles.
    assert sum(max(0, len(cluster.members) - 1) for cluster in clusters) >= 179
