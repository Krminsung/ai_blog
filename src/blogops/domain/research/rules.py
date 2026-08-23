"""Pure evidence, freshness, copyright and conflict rules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable, Mapping

from blogops.domain.generation.rules import canonical_json_hash
from blogops.domain.research.enums import ClaimKind, ClaimStatus, SourceQualityGrade


@dataclass(frozen=True, slots=True)
class EvidenceAssessment:
    status: ClaimStatus
    reasons: tuple[str, ...]


def assess_claim_evidence(
    kind: ClaimKind,
    grades: Iterable[SourceQualityGrade],
    *,
    user_verified: bool,
    has_conflict: bool,
) -> EvidenceAssessment:
    """Apply the source-grade semantics from RSH-005 without numeric score thresholds."""

    grade_set = frozenset(grades)
    if has_conflict:
        return EvidenceAssessment(ClaimStatus.CONFLICTED, ("CONFLICTING_SOURCES",))
    if user_verified:
        return EvidenceAssessment(ClaimStatus.USER_VERIFIED, ("USER_CONFIRMED_FACT",))
    if grade_set.intersection({SourceQualityGrade.A, SourceQualityGrade.B}):
        return EvidenceAssessment(ClaimStatus.SUPPORTED, ("AUTHORITATIVE_EVIDENCE",))
    if kind in {ClaimKind.EXPERIENCE, ClaimKind.OPINION} and SourceQualityGrade.C in grade_set:
        return EvidenceAssessment(ClaimStatus.SUPPORTED, ("EXPERIENCE_EVIDENCE",))
    if SourceQualityGrade.D in grade_set:
        return EvidenceAssessment(ClaimStatus.UNSUPPORTED, ("GRADE_D_NOT_FACT_EVIDENCE",))
    return EvidenceAssessment(ClaimStatus.UNSUPPORTED, ("CITATION_REQUIRED",))


def enforce_quote_policy(word_count: int, quote_policy: Mapping[str, Any]) -> None:
    """Use the frozen per-source policy; absence of a limit does not invent one."""

    configured = quote_policy.get("max_quote_words")
    if configured is None:
        return
    if word_count > int(configured):
        raise ValueError("quote exceeds the frozen source policy and must be summarized")


def requires_revalidation(
    kind: ClaimKind,
    *,
    retrieved_at: datetime,
    checked_at: datetime,
    freshness_policy: Mapping[str, Any],
) -> bool:
    volatile = {ClaimKind.PRICE, ClaimKind.DATE, ClaimKind.POLICY}
    ttl_by_kind = freshness_policy.get("ttl_seconds_by_claim_kind", {})
    configured = ttl_by_kind.get(kind.value)
    if configured is None:
        return kind in volatile
    return checked_at - retrieved_at > timedelta(seconds=int(configured))


def source_set_hash(artifact_snapshots: Iterable[Mapping[str, Any]]) -> str:
    selected = sorted(
        (dict(item) for item in artifact_snapshots),
        key=lambda item: str(item.get("id") or item.get("artifact_hash")),
    )
    return canonical_json_hash(selected)


def research_export_rows(
    claims: Iterable[Mapping[str, Any]],
    citations_by_claim: Mapping[str, Iterable[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for claim in claims:
        claim_id = str(claim["id"])
        citations = list(citations_by_claim.get(claim_id, ()))
        rows.append(
            {
                "claim_id": claim_id,
                "claim": claim["statement"],
                "kind": claim["kind"],
                "status": claim["status"],
                "claim_hash": claim["claim_hash"],
                "citations": citations,
            }
        )
    return rows
