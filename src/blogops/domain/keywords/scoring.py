"""Versioned, explainable keyword classification, trend and opportunity scoring."""

import math
import re
import statistics
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from blogops.core.errors import AppError
from blogops.domain.keywords.enums import IntentSource, KeywordIntent, TrendDirection
from blogops.domain.keywords.normalization import normalize_keyword

FORMULA_VERSION = "1"
DEFAULT_WEIGHTS: dict[str, float] = {
    "trend": 0.20,
    "search_demand": 0.25,
    "low_competition": 0.20,
    "business_relevance": 0.20,
    "content_gap": 0.15,
}

_INTENT_TERMS: dict[KeywordIntent, tuple[str, ...]] = {
    KeywordIntent.COMPARISON: ("비교", "차이", "vs", "추천", "순위", "장단점", "best"),
    KeywordIntent.PURCHASE: ("가격", "구매", "할인", "쿠폰", "배송", "판매", "렌탈", "예약"),
    KeywordIntent.LOCAL: ("근처", "맛집", "병원", "카페", "매장", "서울", "부산", "제주", "지역"),
    KeywordIntent.NAVIGATIONAL: ("로그인", "홈페이지", "공식", "고객센터", "사이트"),
    KeywordIntent.INFORMATIONAL: ("무엇", "방법", "하는 법", "왜", "원인", "뜻", "가이드", "how"),
}

_RISK_TERMS: dict[str, tuple[str, ...]] = {
    "MEDICAL": ("치료", "질병", "약", "수술", "효능", "부작용", "다이어트"),
    "FINANCIAL": ("대출", "투자", "주식", "코인", "수익률", "보험"),
    "LEGAL": ("법률", "소송", "변호사", "형사", "민사", "불법"),
    "ADULT": ("성인", "도박", "카지노"),
    "ILLEGAL": ("해킹", "마약", "위조", "불법 다운로드"),
}


@dataclass(frozen=True, slots=True)
class IntentClassification:
    intent: KeywordIntent
    source: IntentSource
    confidence: float
    signals: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class TrendAnalysis:
    direction: TrendDirection
    growth_rate: float | None
    volatility: float | None
    peak_periods: tuple[str, ...]
    trough_periods: tuple[str, ...]
    seasonal: bool
    score: float | None
    confidence: float


@dataclass(frozen=True, slots=True)
class ScoreResult:
    opportunity_score: float | None
    components: Mapping[str, Mapping[str, Any]]
    coverage: float
    confidence: float
    saturation_score: float | None
    difficulty_lower: float | None
    difficulty_upper: float | None
    difficulty_confidence: float
    commerciality_score: float | None
    freshness_score: float | None
    risk_score: float


def validate_weights(weights: Mapping[str, float]) -> dict[str, float]:
    if set(weights) != set(DEFAULT_WEIGHTS):
        raise AppError(
            "KEYWORD_SCORE_WEIGHTS_INVALID",
            "점수 가중치는 trend/search_demand/low_competition/"
            "business_relevance/content_gap을 모두 포함해야 합니다.",
            422,
        )
    parsed = {key: float(value) for key, value in weights.items()}
    if any(not math.isfinite(value) or value < 0 for value in parsed.values()):
        raise AppError(
            "KEYWORD_SCORE_WEIGHTS_INVALID", "점수 가중치는 0 이상의 유한한 수여야 합니다.", 422
        )
    if not math.isclose(sum(parsed.values()), 1.0, rel_tol=0.0, abs_tol=1e-6):
        raise AppError(
            "KEYWORD_SCORE_WEIGHTS_INVALID", "점수 가중치 합은 1이어야 합니다.", 422
        )
    return parsed


def classify_intent(
    keyword: str, serp_samples: Sequence[Mapping[str, Any]] = ()
) -> IntentClassification:
    normalized = normalize_keyword(keyword)
    corpus = [normalized]
    for sample in serp_samples[:20]:
        for field in ("title", "query"):
            value = sample.get(field)
            if isinstance(value, str) and value:
                corpus.append(normalize_keyword(value[:1_000]))
    joined = " ".join(corpus)
    counts = {
        intent: sum(1 for term in terms if term in joined)
        for intent, terms in _INTENT_TERMS.items()
    }
    maximum = max(counts.values(), default=0)
    winners = [intent for intent, count in counts.items() if count == maximum and count > 0]
    if not winners:
        return IntentClassification(
            KeywordIntent.UNKNOWN,
            IntentSource.PROVIDER_SERP if serp_samples else IntentSource.RULE,
            0.2 if serp_samples else 0.0,
            {"matched_terms": {}, "serp_sample_count": len(serp_samples)},
        )
    intent = winners[0] if len(winners) == 1 else KeywordIntent.MIXED
    signal_total = sum(counts.values())
    confidence = min(
        0.95,
        0.45
        + (maximum / max(1, signal_total)) * 0.35
        + min(0.15, len(corpus) / 100),
    )
    return IntentClassification(
        intent,
        IntentSource.PROVIDER_SERP if serp_samples else IntentSource.RULE,
        confidence,
        {
            "matched_terms": {item.value: counts[item] for item in counts if counts[item]},
            "serp_sample_count": len(serp_samples),
        },
    )


def sensitive_topic_tags(keyword: str) -> list[str]:
    normalized = normalize_keyword(keyword)
    return sorted(
        category
        for category, terms in _RISK_TERMS.items()
        if any(term in normalized for term in terms)
    )


def calculate_brand_alignment(keyword: str, brand_terms: Sequence[str]) -> float | None:
    if not brand_terms:
        return None
    normalized = normalize_keyword(keyword)
    terms = {normalize_keyword(value) for value in brand_terms if value.strip()}
    if not terms:
        return None
    direct = sum(1 for term in terms if term in normalized)
    keyword_tokens = set(normalized.split())
    brand_tokens = {token for term in terms for token in term.split()}
    overlap = len(keyword_tokens.intersection(brand_tokens)) / max(1, len(keyword_tokens))
    return round(min(1.0, direct / max(1, len(terms)) * 0.6 + overlap * 0.4), 6)


def analyze_trend(points: Sequence[Mapping[str, Any]]) -> TrendAnalysis:
    parsed: list[tuple[str, float]] = []
    for point in points:
        period = point.get("period")
        value = point.get("value")
        if isinstance(period, str) and isinstance(value, (int, float)) and math.isfinite(value):
            parsed.append((period, float(value)))
    if len(parsed) < 3:
        return TrendAnalysis(
            TrendDirection.INSUFFICIENT_DATA, None, None, (), (), False, None, 0.0
        )
    values = [item[1] for item in parsed]
    mean = statistics.fmean(values)
    standard_deviation = statistics.pstdev(values)
    volatility = standard_deviation / max(abs(mean), 1e-9)
    x_mean = (len(values) - 1) / 2
    denominator = sum((index - x_mean) ** 2 for index in range(len(values)))
    slope = (
        sum((index - x_mean) * (value - mean) for index, value in enumerate(values))
        / denominator
        if denominator
        else 0.0
    )
    growth_rate = slope * max(1, len(values) - 1) / max(abs(mean), 1e-9)
    if volatility >= 0.65:
        direction = TrendDirection.VOLATILE
    elif growth_rate >= 0.50:
        direction = TrendDirection.SURGING
    elif growth_rate >= 0.10:
        direction = TrendDirection.RISING
    elif growth_rate <= -0.10:
        direction = TrendDirection.FALLING
    else:
        direction = TrendDirection.STABLE
    ordered = sorted(parsed, key=lambda item: (item[1], item[0]))
    edge_count = min(3, max(1, len(ordered) // 6))
    troughs = tuple(item[0] for item in ordered[:edge_count])
    peaks = tuple(item[0] for item in reversed(ordered[-edge_count:]))
    # Repeated month/day buckets with a material peak-to-mean ratio are a conservative
    # seasonality signal; the label remains false for short histories.
    recurring_buckets: dict[str, list[float]] = {}
    for period, value in parsed:
        bucket = period[5:10] if len(period) >= 10 else period[-2:]
        recurring_buckets.setdefault(bucket, []).append(value)
    repeated_peak = any(
        len(bucket_values) >= 2 and statistics.fmean(bucket_values) >= mean * 1.35
        for bucket_values in recurring_buckets.values()
    )
    seasonal = len(parsed) >= 12 and repeated_peak
    score = max(0.0, min(100.0, 50.0 + growth_rate * 50.0))
    confidence = min(1.0, len(parsed) / 12) * max(0.2, 1.0 - min(0.8, volatility / 2))
    return TrendAnalysis(
        direction,
        round(growth_rate, 6),
        round(volatility, 6),
        peaks,
        troughs,
        seasonal,
        round(score, 4),
        round(confidence, 6),
    )


def _bounded(value: float | None) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return max(0.0, min(100.0, value))


def _metric_number(metrics: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = metrics.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            return float(value)
    return None


def _demand_score(metrics: Mapping[str, Any]) -> float | None:
    demand = _metric_number(metrics, "search_volume", "monthly_searches")
    if demand is None:
        pc = _metric_number(metrics, "monthly_pc_searches")
        mobile = _metric_number(metrics, "monthly_mobile_searches")
        if pc is not None or mobile is not None:
            demand = (pc or 0.0) + (mobile or 0.0)
    if demand is None:
        impressions = _metric_number(metrics, "impressions")
        if impressions is not None:
            demand = impressions
    if demand is None:
        return None
    return _bounded(math.log10(1 + max(0.0, demand)) / 5 * 100)


def _competition_score(metrics: Mapping[str, Any]) -> float | None:
    raw = metrics.get("competition")
    if isinstance(raw, str):
        mapping = {"LOW": 25.0, "MEDIUM": 55.0, "HIGH": 85.0, "낮음": 25.0, "중간": 55.0, "높음": 85.0}
        return mapping.get(raw.upper(), mapping.get(raw))
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        value = float(raw)
        return _bounded(value * 100 if 0 <= value <= 1 else value)
    return None


def _commerciality(metrics: Mapping[str, Any]) -> float | None:
    cpc = _metric_number(metrics, "cpc", "average_cpc")
    product_score = _metric_number(metrics, "product_intent", "conversion_proximity")
    parts: list[float] = []
    if cpc is not None:
        parts.append(min(100.0, math.log10(1 + cpc) / 4 * 100))
    if product_score is not None:
        parts.append(_bounded(product_score) or 0.0)
    return statistics.fmean(parts) if parts else None


def _freshness(metrics: Mapping[str, Any], trend: TrendAnalysis) -> float | None:
    recent = _metric_number(metrics, "recent_document_count", "recent_publications")
    total = _metric_number(metrics, "document_count", "total_documents")
    parts: list[float] = []
    if recent is not None and total is not None and total > 0:
        parts.append(min(100.0, recent / total * 500))
    if (
        trend.direction in {TrendDirection.SURGING, TrendDirection.RISING}
        and trend.score is not None
    ):
        parts.append(trend.score)
    return statistics.fmean(parts) if parts else None


def score_keyword(
    *,
    metrics: Mapping[str, Any],
    trend_points: Sequence[Mapping[str, Any]],
    brand_alignment: float | None,
    content_gap_score: float | None,
    evidence_confidence: float,
    risk_tags: Sequence[str],
    weights: Mapping[str, float] = DEFAULT_WEIGHTS,
) -> ScoreResult:
    parsed_weights = validate_weights(weights)
    trend = analyze_trend(trend_points)
    demand = _demand_score(metrics)
    competition = _competition_score(metrics)
    low_competition = 100.0 - competition if competition is not None else None
    component_values: dict[str, float | None] = {
        "trend": trend.score,
        "search_demand": demand,
        "low_competition": low_competition,
        "business_relevance": brand_alignment * 100 if brand_alignment is not None else None,
        "content_gap": _bounded(content_gap_score),
    }
    available_weight = sum(
        parsed_weights[name] for name, value in component_values.items() if value is not None
    )
    components = {
        name: {
            "value": round(value, 4) if value is not None else None,
            "weight": parsed_weights[name],
            "available": value is not None,
        }
        for name, value in component_values.items()
    }
    opportunity = None
    if available_weight > 0:
        opportunity = sum(
            (value or 0.0) * parsed_weights[name]
            for name, value in component_values.items()
            if value is not None
        ) / available_weight
    coverage = available_weight
    confidence = max(0.0, min(1.0, evidence_confidence)) * coverage
    document_count = _metric_number(metrics, "document_count", "total_documents")
    demand_raw = _metric_number(metrics, "search_volume", "monthly_searches")
    saturation = None
    if document_count is not None and demand_raw is not None:
        saturation = _bounded(math.log10(1 + document_count / max(1.0, demand_raw)) * 33.333)
    difficulty_base_parts = [value for value in (competition, saturation) if value is not None]
    difficulty_base = statistics.fmean(difficulty_base_parts) if difficulty_base_parts else None
    difficulty_confidence = confidence * (len(difficulty_base_parts) / 2)
    width = 30.0 * (1.0 - difficulty_confidence)
    difficulty_lower = _bounded(difficulty_base - width) if difficulty_base is not None else None
    difficulty_upper = _bounded(difficulty_base + width) if difficulty_base is not None else None
    commerciality = _commerciality(metrics)
    freshness = _freshness(metrics, trend)
    risk_score = min(100.0, len(set(risk_tags)) * 25.0)
    return ScoreResult(
        opportunity_score=round(opportunity, 4) if opportunity is not None else None,
        components=components,
        coverage=round(coverage, 6),
        confidence=round(confidence, 6),
        saturation_score=round(saturation, 4) if saturation is not None else None,
        difficulty_lower=round(difficulty_lower, 4) if difficulty_lower is not None else None,
        difficulty_upper=round(difficulty_upper, 4) if difficulty_upper is not None else None,
        difficulty_confidence=round(difficulty_confidence, 6),
        commerciality_score=round(commerciality, 4) if commerciality is not None else None,
        freshness_score=round(freshness, 4) if freshness is not None else None,
        risk_score=risk_score,
    )


def question_keyword(keyword: str) -> bool:
    normalized = normalize_keyword(keyword)
    return bool(
        re.search(r"(?:무엇|뭐|어떻게|왜|언제|어디|누가|얼마|방법|하는 법|인가요|까요)[?？]?", normalized)
        or normalized.endswith(("?", "？"))
    )
