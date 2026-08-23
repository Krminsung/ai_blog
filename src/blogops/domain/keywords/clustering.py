"""Deterministic keyword/question clustering with optional licensed SERP evidence."""

import math
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Mapping, Sequence
from uuid import UUID

from blogops.domain.keywords.enums import ClusterKind, ClusterMethod, KeywordIntent
from blogops.domain.keywords.normalization import normalize_keyword
from blogops.domain.keywords.scoring import question_keyword

TECHNICAL_MAX_CLUSTER_CANDIDATES = 5_000


@dataclass(frozen=True, slots=True)
class ClusterCandidate:
    keyword_id: UUID
    text: str
    intent: KeywordIntent = KeywordIntent.UNKNOWN
    opportunity_score: float | None = None
    search_demand: float | None = None
    embedding: Sequence[float] | None = None
    serp_urls: frozenset[str] = frozenset()
    serp_licensed: bool = False


@dataclass(frozen=True, slots=True)
class PairSignals:
    similarity: float
    lexical: float
    semantic: float | None
    intent_match: bool
    serp_overlap: float | None


@dataclass(frozen=True, slots=True)
class ClusterMemberResult:
    candidate: ClusterCandidate
    similarity_to_primary: float
    is_primary: bool
    signals: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ClusterResult:
    name: str
    kind: ClusterKind
    method: ClusterMethod
    primary: ClusterCandidate
    intent: KeywordIntent
    confidence: float
    decision_required: bool
    members: tuple[ClusterMemberResult, ...]
    signals: Mapping[str, Any] = field(default_factory=dict)


def _char_ngrams(value: str, size: int = 2) -> set[str]:
    compact = value.replace(" ", "")
    if len(compact) <= size:
        return {compact}
    return {compact[index : index + size] for index in range(len(compact) - size + 1)}


def _jaccard(left: set[str] | frozenset[str], right: set[str] | frozenset[str]) -> float:
    if not left and not right:
        return 1.0
    union = left.union(right)
    return len(left.intersection(right)) / len(union) if union else 0.0


def _cosine(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) != len(right) or not left:
        return None
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return None
    return max(0.0, min(1.0, (dot / (left_norm * right_norm) + 1) / 2))


def pair_similarity(
    left: ClusterCandidate,
    right: ClusterCandidate,
    *,
    use_serp_when_licensed: bool,
) -> PairSignals:
    left_text = normalize_keyword(left.text)
    right_text = normalize_keyword(right.text)
    if left_text == right_text:
        return PairSignals(1.0, 1.0, 1.0, True, 1.0 if left.serp_urls else None)
    token_similarity = _jaccard(set(left_text.split()), set(right_text.split()))
    ngram_similarity = _jaccard(_char_ngrams(left_text), _char_ngrams(right_text))
    sequence_similarity = SequenceMatcher(None, left_text, right_text, autojunk=False).ratio()
    lexical = max(token_similarity, ngram_similarity * 0.95, sequence_similarity * 0.90)
    semantic = (
        _cosine(left.embedding, right.embedding)
        if left.embedding is not None and right.embedding is not None
        else None
    )
    intent_match = (
        left.intent == right.intent
        or left.intent in {KeywordIntent.UNKNOWN, KeywordIntent.MIXED}
        or right.intent in {KeywordIntent.UNKNOWN, KeywordIntent.MIXED}
    )
    serp_overlap = None
    if (
        use_serp_when_licensed
        and left.serp_licensed
        and right.serp_licensed
        and left.serp_urls
        and right.serp_urls
    ):
        serp_overlap = _jaccard(left.serp_urls, right.serp_urls)
    base = max(lexical, semantic or 0.0)
    weighted = base * 0.82 + (0.10 if intent_match else 0.0)
    if serp_overlap is not None:
        weighted = weighted * 0.82 + serp_overlap * 0.18
    return PairSignals(
        similarity=max(0.0, min(1.0, weighted)),
        lexical=lexical,
        semantic=semantic,
        intent_match=intent_match,
        serp_overlap=serp_overlap,
    )


class _DisjointSet:
    def __init__(self, size: int) -> None:
        self.parents = list(range(size))

    def find(self, item: int) -> int:
        while self.parents[item] != item:
            self.parents[item] = self.parents[self.parents[item]]
            item = self.parents[item]
        return item

    def union(self, left: int, right: int) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parents[right_root] = left_root


def _primary(candidates: Sequence[ClusterCandidate]) -> ClusterCandidate:
    return max(
        candidates,
        key=lambda item: (
            item.opportunity_score if item.opportunity_score is not None else -1.0,
            item.search_demand if item.search_demand is not None else -1.0,
            -len(normalize_keyword(item.text)),
            normalize_keyword(item.text),
        ),
    )


def _cluster_intent(candidates: Sequence[ClusterCandidate]) -> KeywordIntent:
    counts: dict[KeywordIntent, int] = {}
    for candidate in candidates:
        if candidate.intent not in {KeywordIntent.UNKNOWN, KeywordIntent.MIXED}:
            counts[candidate.intent] = counts.get(candidate.intent, 0) + 1
    if not counts:
        return KeywordIntent.UNKNOWN
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0].value))
    if len(ordered) > 1 and ordered[0][1] == ordered[1][1]:
        return KeywordIntent.MIXED
    return ordered[0][0]


def cluster_keywords(
    candidates: Sequence[ClusterCandidate],
    *,
    kind: ClusterKind = ClusterKind.KEYWORD,
    similarity_threshold: float = 0.72,
    use_serp_when_licensed: bool = True,
) -> list[ClusterResult]:
    """Cluster a bounded batch; a 1,000-row/180-equivalent fixture remains one proposal."""

    if not 0 <= similarity_threshold <= 1:
        raise ValueError("similarity_threshold must be between zero and one")
    if len(candidates) > TECHNICAL_MAX_CLUSTER_CANDIDATES:
        raise ValueError("cluster request exceeds the 5,000-row technical safety guard")
    working = [
        candidate
        for candidate in candidates
        if kind != ClusterKind.QUESTION or question_keyword(candidate.text)
    ]
    if not working:
        return []
    disjoint = _DisjointSet(len(working))
    pair_cache: dict[tuple[int, int], PairSignals] = {}
    for left_index, left in enumerate(working):
        for right_index in range(left_index + 1, len(working)):
            signals = pair_similarity(
                left, working[right_index], use_serp_when_licensed=use_serp_when_licensed
            )
            pair_cache[(left_index, right_index)] = signals
            if signals.similarity >= similarity_threshold:
                disjoint.union(left_index, right_index)
    groups: dict[int, list[int]] = {}
    for index in range(len(working)):
        groups.setdefault(disjoint.find(index), []).append(index)
    results: list[ClusterResult] = []
    for indexes in groups.values():
        group = [working[index] for index in indexes]
        primary = _primary(group)
        primary_index = working.index(primary)
        members: list[ClusterMemberResult] = []
        similarities: list[float] = []
        serp_used = False
        embeddings_used = False
        for index in indexes:
            candidate = working[index]
            if candidate.keyword_id == primary.keyword_id:
                signals = PairSignals(1.0, 1.0, 1.0, True, 1.0 if candidate.serp_urls else None)
            else:
                key = (min(primary_index, index), max(primary_index, index))
                signals = pair_cache[key]
            similarities.append(signals.similarity)
            serp_used = serp_used or signals.serp_overlap is not None
            embeddings_used = embeddings_used or signals.semantic is not None
            members.append(
                ClusterMemberResult(
                    candidate=candidate,
                    similarity_to_primary=round(signals.similarity, 6),
                    is_primary=candidate.keyword_id == primary.keyword_id,
                    signals={
                        "lexical": round(signals.lexical, 6),
                        "semantic": round(signals.semantic, 6)
                        if signals.semantic is not None
                        else None,
                        "intent_match": signals.intent_match,
                        "serp_overlap": round(signals.serp_overlap, 6)
                        if signals.serp_overlap is not None
                        else None,
                    },
                )
            )
        method = (
            ClusterMethod.SEMANTIC_SERP_INTENT
            if serp_used
            else ClusterMethod.SEMANTIC_INTENT
            if embeddings_used or len(group) > 1
            else ClusterMethod.EXACT
        )
        confidence = statistics_mean(similarities)
        results.append(
            ClusterResult(
                name=primary.text,
                kind=kind,
                method=method,
                primary=primary,
                intent=_cluster_intent(group),
                confidence=round(confidence, 6),
                decision_required=len(group) > 1,
                members=tuple(
                    sorted(
                        members,
                        key=lambda item: (
                            not item.is_primary,
                            -item.similarity_to_primary,
                            item.candidate.text,
                        ),
                    )
                ),
                signals={
                    "member_count": len(group),
                    "serp_used": serp_used,
                    "serp_omitted_reason": None
                    if serp_used or not use_serp_when_licensed
                    else "NO_LICENSED_SERP_EVIDENCE",
                    "embeddings_used": embeddings_used,
                    "similarity_threshold": similarity_threshold,
                },
            )
        )
    return sorted(results, key=lambda item: (-len(item.members), item.name))


def statistics_mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def cannibalization_recommendation(
    links: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return evidence-backed suggestions without claiming an unavailable ranking signal."""

    by_intent: dict[str, list[Mapping[str, Any]]] = {}
    for link in links:
        intent = str(link.get("intent", KeywordIntent.UNKNOWN.value))
        by_intent.setdefault(intent, []).append(link)
    findings: list[dict[str, Any]] = []
    for intent, items in by_intent.items():
        if intent == KeywordIntent.UNKNOWN.value or len(items) < 2:
            continue
        targets = sorted(
            {str(item.get("target_ref", "")) for item in items if item.get("target_ref")}
        )
        if len(targets) < 2:
            continue
        findings.append(
            {
                "intent": intent,
                "targets": targets,
                "recommendations": ["MERGE", "SPLIT", "INTERNAL_LINK"],
                "decision_required": True,
                "evidence": "multiple_existing_targets_for_same_intent",
            }
        )
    return findings
