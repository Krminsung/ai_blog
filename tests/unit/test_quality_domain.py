"""Focused behavior contracts for quality scoring, policy precedence and approval."""

from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from blogops.api.v1.quality import router
from blogops.domain.quality.enums import (
    ApprovalDecisionKind,
    ApprovalRequestStatus,
    AssessmentDecision,
    FindingSeverity,
    PolicyLayer,
)
from blogops.domain.quality.models import (
    ApprovalRequest,
    FactCitationReport,
    PolicyEvent,
    QualityAssessment,
    QualityReport,
    WorkspaceQualityConfig,
)
from blogops.domain.quality.rules import (
    CITATION_LINK_RATE_PRODUCT_GOAL,
    InvalidQualityInput,
    PolicyFinding,
    approval_quorum_reached,
    calculate_quality_score,
    evaluate_quality_gate,
    exact_content_version_matches,
    resolve_policy_findings,
    transition_approval,
)
from blogops.domain.quality.schemas import (
    ApprovalDecisionCreate,
    MorphologyReportCreate,
    QualityConfigCreate,
)


def _all_scores(value: int | Decimal) -> dict[str, int | Decimal]:
    return {
        "accuracy": value,
        "usefulness": value,
        "originality": value,
        "search_intent": value,
        "readability": value,
        "brand_fit": value,
        "compliance": value,
    }


def test_default_quality_formula_is_explainable_and_totals_one_hundred() -> None:
    result = calculate_quality_score(
        {
            "accuracy": 100,
            "usefulness": 80,
            "originality": 60,
            "search_intent": 40,
            "readability": 20,
            "brand_fit": 100,
            "compliance": 0,
        }
    )
    assert result.weighted_contributions == {
        "accuracy": Decimal("25.00"),
        "usefulness": Decimal("16.00"),
        "originality": Decimal("9.00"),
        "search_intent": Decimal("6.00"),
        "readability": Decimal("2.00"),
        "brand_fit": Decimal("10.00"),
        "compliance": Decimal("0.00"),
    }
    assert result.total == Decimal("68.00")
    assert result.formula_version == "default-1"


def test_quality_formula_rejects_missing_or_out_of_range_components() -> None:
    with pytest.raises(InvalidQualityInput):
        calculate_quality_score({"accuracy": 100})
    invalid = _all_scores(100)
    invalid["compliance"] = 101
    with pytest.raises(InvalidQualityInput):
        calculate_quality_score(invalid)


def test_non_overrideable_policy_hard_block_wins_even_when_marked_overridden() -> None:
    finding = PolicyFinding(
        event_key="law-1",
        layer=PolicyLayer.LAW,
        rule_code="LAW.PROHIBITED_CLAIM",
        severity=FindingSeverity.BLOCK,
        hard_block=True,
        override_allowed=True,
    )
    resolution = resolve_policy_findings(
        [finding], overridden_event_keys={"law-1"}
    )
    assert resolution.blocked is True
    assert resolution.non_overrideable_event_keys == ("law-1",)


def test_workspace_policy_block_can_be_overridden_when_explicitly_allowed() -> None:
    finding = PolicyFinding(
        event_key="workspace-1",
        layer=PolicyLayer.WORKSPACE,
        rule_code="WORKSPACE.TONE",
        severity=FindingSeverity.BLOCK,
        hard_block=True,
        override_allowed=True,
    )
    resolution = resolve_policy_findings(
        [finding], overridden_event_keys={"workspace-1"}
    )
    assert resolution.blocked is False
    assert resolution.overrideable_event_keys == ("workspace-1",)


def test_policy_precedence_orders_law_before_service_workspace_and_content() -> None:
    findings = [
        PolicyFinding("content", PolicyLayer.CONTENT, "CONTENT.A", FindingSeverity.BLOCK, True, True),
        PolicyFinding("service", PolicyLayer.SERVICE, "SERVICE.A", FindingSeverity.BLOCK, True, False),
        PolicyFinding("workspace", PolicyLayer.WORKSPACE, "WORKSPACE.A", FindingSeverity.BLOCK, True, True),
        PolicyFinding("law", PolicyLayer.LAW, "LAW.A", FindingSeverity.BLOCK, True, False),
    ]
    resolution = resolve_policy_findings(findings)
    assert [item.event_key for item in resolution.ordered_findings] == [
        "law",
        "service",
        "workspace",
        "content",
    ]


def test_hard_policy_block_takes_precedence_over_perfect_quality_score() -> None:
    score = calculate_quality_score(_all_scores(100))
    policy = resolve_policy_findings(
        [
            PolicyFinding(
                "copyright",
                PolicyLayer.COPYRIGHT,
                "COPYRIGHT.COPY",
                FindingSeverity.BLOCK,
                True,
                False,
            )
        ]
    )
    gate = evaluate_quality_gate(
        score,
        minimum_total_score=75,
        minimum_component_scores={},
        policy=policy,
    )
    assert gate.decision is AssessmentDecision.BLOCKED


def test_citation_link_rate_ninety_is_a_product_goal_not_default_gate() -> None:
    assert CITATION_LINK_RATE_PRODUCT_GOAL == Decimal("90")
    score = calculate_quality_score(_all_scores(80))
    gate = evaluate_quality_gate(
        score,
        minimum_total_score=75,
        minimum_component_scores={},
        policy=resolve_policy_findings([]),
    )
    assert gate.decision is AssessmentDecision.PASS
    assert "citation_link_rate" not in gate.failed_thresholds


def test_approval_requires_exact_version_and_hash() -> None:
    version_id = uuid4()
    assert exact_content_version_matches(
        expected_version_id=version_id,
        expected_content_hash="a" * 64,
        actual_version_id=version_id,
        actual_content_hash="a" * 64,
    )
    assert not exact_content_version_matches(
        expected_version_id=version_id,
        expected_content_hash="a" * 64,
        actual_version_id=uuid4(),
        actual_content_hash="a" * 64,
    )
    assert not exact_content_version_matches(
        expected_version_id=version_id,
        expected_content_hash="a" * 64,
        actual_version_id=version_id,
        actual_content_hash="b" * 64,
    )


def test_approval_quorum_and_final_stage_transition_are_explicit() -> None:
    first, second = uuid4(), uuid4()
    assert not approval_quorum_reached(set(), first, 2)
    assert approval_quorum_reached({first}, second, 2)
    assert (
        transition_approval(
            ApprovalRequestStatus.PENDING,
            ApprovalDecisionKind.APPROVE,
            stage_quorum_reached=True,
            final_stage=True,
        )
        is ApprovalRequestStatus.APPROVED
    )
    assert (
        transition_approval(
            ApprovalRequestStatus.PENDING,
            ApprovalDecisionKind.REQUEST_CHANGES,
            stage_quorum_reached=False,
            final_stage=False,
        )
        is ApprovalRequestStatus.CHANGES_REQUESTED
    )


def test_negative_approval_decision_requires_a_comment() -> None:
    with pytest.raises(ValidationError):
        ApprovalDecisionCreate(
            expected_lock_version=1,
            expected_content_version_id=uuid4(),
            expected_content_hash="a" * 64,
            decision="REJECT",
        )


def test_morphology_report_requires_dictionary_version_pin() -> None:
    with pytest.raises(ValidationError):
        MorphologyReportCreate(
            content_id=uuid4(),
            content_version_id=uuid4(),
            content_hash="a" * 64,
            input_hash="b" * 64,
            analyzer={"analyzer_name": "morph", "analyzer_version": "1"},
            detail={
                "token_count": 1,
                "sentence_count": 1,
                "unknown_token_rate": 0,
            },
        )


def test_workspace_quality_threshold_is_versioned_and_rejects_unknown_component() -> None:
    with pytest.raises(ValidationError):
        QualityConfigCreate(
            minimum_component_scores={"citation_link_rate": 90},
            approval_stages=[
                {
                    "key": "final",
                    "name": "최종 승인",
                    "required_approvals": 1,
                }
            ],
        )
    assert {"version", "config_hash"}.issubset(
        WorkspaceQualityConfig.__table__.columns.keys()
    )


def test_quality_models_pin_tenant_content_version_and_hash() -> None:
    for model in (QualityReport, QualityAssessment, PolicyEvent, ApprovalRequest):
        assert {"workspace_id", "content_version_id", "content_hash"}.issubset(
            model.__table__.columns.keys()
        )
        assert any(
            set(constraint.column_keys)
            == {"workspace_id", "content_version_id", "content_hash"}
            for constraint in model.__table__.foreign_key_constraints
        )
    assert "citation_link_rate" in FactCitationReport.__table__.columns


def test_canonical_quality_and_approval_routes_are_exposed() -> None:
    paths = {getattr(route, "path", "") for route in router.routes}
    assert {
        "/quality/reports/morphology",
        "/quality/reports/naturalness",
        "/quality/reports/seo",
        "/quality/reports/duplication",
        "/quality/reports/fact-citations",
        "/quality/reports/safety-policy",
        "/quality/assessments",
        "/approvals",
    }.issubset(paths)
