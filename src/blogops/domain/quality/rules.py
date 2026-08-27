"""Pure, explainable quality scoring, policy precedence and approval rules."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Mapping
from uuid import UUID

from blogops.core.serialization import canonical_json_hash
from blogops.domain.quality.enums import (
    ApprovalDecisionKind,
    ApprovalRequestStatus,
    AssessmentDecision,
    FindingSeverity,
    PolicyLayer,
)


QUALITY_COMPONENT_WEIGHTS: dict[str, Decimal] = {
    "accuracy": Decimal("0.25"),
    "usefulness": Decimal("0.20"),
    "originality": Decimal("0.15"),
    "search_intent": Decimal("0.15"),
    "readability": Decimal("0.10"),
    "brand_fit": Decimal("0.10"),
    "compliance": Decimal("0.05"),
}

# Product-level north-star metric only. It is deliberately absent from assessment blocking rules.
CITATION_LINK_RATE_PRODUCT_GOAL = Decimal("90")

POLICY_LAYER_PRIORITY: dict[PolicyLayer, int] = {
    PolicyLayer.LAW: 0,
    PolicyLayer.COPYRIGHT: 0,
    PolicyLayer.PLATFORM: 1,
    PolicyLayer.SERVICE: 2,
    PolicyLayer.WORKSPACE: 3,
    PolicyLayer.CONTENT: 4,
}

NON_OVERRIDEABLE_HARD_BLOCK_LAYERS = frozenset(
    {
        PolicyLayer.LAW,
        PolicyLayer.COPYRIGHT,
        PolicyLayer.PLATFORM,
        PolicyLayer.SERVICE,
    }
)


class InvalidQualityInput(ValueError):
    pass


class InvalidApprovalTransition(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class QualityScoreBreakdown:
    total: Decimal
    component_scores: dict[str, Decimal]
    weighted_contributions: dict[str, Decimal]
    formula_version: str = "default-1"


@dataclass(frozen=True, slots=True)
class PolicyFinding:
    event_key: str
    layer: PolicyLayer
    rule_code: str
    severity: FindingSeverity
    hard_block: bool
    override_allowed: bool


@dataclass(frozen=True, slots=True)
class PolicyResolution:
    ordered_findings: tuple[PolicyFinding, ...]
    blocking_event_keys: tuple[str, ...]
    non_overrideable_event_keys: tuple[str, ...]
    overrideable_event_keys: tuple[str, ...]

    @property
    def blocked(self) -> bool:
        return bool(self.blocking_event_keys)


@dataclass(frozen=True, slots=True)
class QualityGateResult:
    decision: AssessmentDecision
    failed_thresholds: dict[str, str]


APPROVAL_TRANSITIONS: dict[
    tuple[ApprovalRequestStatus, ApprovalDecisionKind], ApprovalRequestStatus
] = {
    (ApprovalRequestStatus.PENDING, ApprovalDecisionKind.REQUEST_CHANGES): (
        ApprovalRequestStatus.CHANGES_REQUESTED
    ),
    (ApprovalRequestStatus.PENDING, ApprovalDecisionKind.REJECT): (
        ApprovalRequestStatus.REJECTED
    ),
    (ApprovalRequestStatus.PENDING, ApprovalDecisionKind.APPROVE): (
        ApprovalRequestStatus.PENDING
    ),
}


def calculate_quality_score(
    component_scores: Mapping[str, Decimal | int | float | str],
) -> QualityScoreBreakdown:
    if set(component_scores) != set(QUALITY_COMPONENT_WEIGHTS):
        missing = sorted(set(QUALITY_COMPONENT_WEIGHTS).difference(component_scores))
        extra = sorted(set(component_scores).difference(QUALITY_COMPONENT_WEIGHTS))
        raise InvalidQualityInput(f"component mismatch: missing={missing}, extra={extra}")
    normalized: dict[str, Decimal] = {}
    contributions: dict[str, Decimal] = {}
    for component, weight in QUALITY_COMPONENT_WEIGHTS.items():
        score = Decimal(str(component_scores[component]))
        if score < 0 or score > 100:
            raise InvalidQualityInput(f"{component} must be between 0 and 100")
        normalized[component] = score.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        contributions[component] = (score * weight).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    total = sum(contributions.values(), start=Decimal("0")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return QualityScoreBreakdown(
        total=total,
        component_scores=normalized,
        weighted_contributions=contributions,
    )


def resolve_policy_findings(
    findings: list[PolicyFinding], *, overridden_event_keys: set[str] | None = None
) -> PolicyResolution:
    overridden = overridden_event_keys or set()
    normalized: list[PolicyFinding] = []
    for finding in findings:
        override_allowed = finding.override_allowed
        is_blocking = finding.hard_block or finding.severity is FindingSeverity.BLOCK
        if is_blocking and finding.layer in NON_OVERRIDEABLE_HARD_BLOCK_LAYERS:
            override_allowed = False
        normalized.append(
            PolicyFinding(
                event_key=finding.event_key,
                layer=finding.layer,
                rule_code=finding.rule_code,
                severity=finding.severity,
                hard_block=finding.hard_block,
                override_allowed=override_allowed,
            )
        )
    ordered = tuple(
        sorted(
            normalized,
            key=lambda item: (
                POLICY_LAYER_PRIORITY[item.layer],
                0 if item.hard_block else 1,
                item.rule_code,
                item.event_key,
            ),
        )
    )
    blocking = tuple(
        item.event_key
        for item in ordered
        if (item.hard_block or item.severity is FindingSeverity.BLOCK)
        and (not item.override_allowed or item.event_key not in overridden)
    )
    non_overrideable = tuple(
        item.event_key
        for item in ordered
        if (item.hard_block or item.severity is FindingSeverity.BLOCK)
        and not item.override_allowed
    )
    overrideable = tuple(
        item.event_key
        for item in ordered
        if (item.hard_block or item.severity is FindingSeverity.BLOCK)
        and item.override_allowed
    )
    return PolicyResolution(
        ordered_findings=ordered,
        blocking_event_keys=blocking,
        non_overrideable_event_keys=non_overrideable,
        overrideable_event_keys=overrideable,
    )


def evaluate_quality_gate(
    score: QualityScoreBreakdown,
    *,
    minimum_total_score: Decimal | int | float | str,
    minimum_component_scores: Mapping[str, Decimal | int | float | str],
    policy: PolicyResolution,
) -> QualityGateResult:
    failures: dict[str, str] = {}
    minimum_total = Decimal(str(minimum_total_score))
    if score.total < minimum_total:
        failures["total"] = f"{score.total} < {minimum_total}"
    for component, raw_minimum in minimum_component_scores.items():
        if component not in score.component_scores:
            raise InvalidQualityInput(f"unknown component threshold: {component}")
        minimum = Decimal(str(raw_minimum))
        if minimum < 0 or minimum > 100:
            raise InvalidQualityInput(f"invalid threshold for {component}")
        if score.component_scores[component] < minimum:
            failures[component] = f"{score.component_scores[component]} < {minimum}"
    if policy.blocked:
        decision = AssessmentDecision.BLOCKED
    elif failures:
        decision = AssessmentDecision.NEEDS_REVISION
    else:
        decision = AssessmentDecision.PASS
    return QualityGateResult(decision=decision, failed_thresholds=failures)


def transition_approval(
    current: ApprovalRequestStatus,
    decision: ApprovalDecisionKind,
    *,
    stage_quorum_reached: bool,
    final_stage: bool,
) -> ApprovalRequestStatus:
    try:
        base = APPROVAL_TRANSITIONS[(current, decision)]
    except KeyError as exc:
        raise InvalidApprovalTransition(
            f"{current.value} cannot apply {decision.value}"
        ) from exc
    if decision is ApprovalDecisionKind.APPROVE:
        if stage_quorum_reached and final_stage:
            return ApprovalRequestStatus.APPROVED
        return ApprovalRequestStatus.PENDING
    return base


def approval_quorum_reached(
    prior_approver_ids: set[UUID], actor_id: UUID, required_approvals: int
) -> bool:
    if required_approvals < 1:
        raise InvalidQualityInput("required approvals must be positive")
    return len(prior_approver_ids | {actor_id}) >= required_approvals


def exact_content_version_matches(
    *,
    expected_version_id: UUID,
    expected_content_hash: str,
    actual_version_id: UUID,
    actual_content_hash: str,
) -> bool:
    return (
        expected_version_id == actual_version_id
        and expected_content_hash == actual_content_hash
    )
