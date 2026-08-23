"""Direct rule coverage for ANL-001..040 and REP-001..020 boundaries."""

from collections.abc import Callable
from decimal import Decimal
from types import SimpleNamespace
from typing import cast
from uuid import NAMESPACE_URL, uuid5

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.core.errors import AppError
from blogops.domain.analytics.models import AnalyticsSyncRun
from blogops.domain.analytics.providers import AnalyticsAdapterRegistry
from blogops.domain.analytics.rules import (
    calculate_roi,
    safe_tracking_destination,
    validate_comparable_metrics,
    validate_fact_evidence,
)
from blogops.domain.analytics.service import _finalize_cancelled_run
from blogops.domain.analytics.tasks import (
    _is_retryable_runtime_error as analytics_failure_is_retryable,
)
from blogops.domain.jobs.state import JobState, StepState
from blogops.domain.repurpose import tasks as repurpose_tasks
from blogops.domain.repurpose.enums import RepurposeKind
from blogops.domain.repurpose.models import RepurposeJob, RepurposeJobItem
from blogops.domain.repurpose.rules import (
    REPURPOSE_REQUIREMENTS,
    validate_claim_lineage,
    validate_model_selection,
    validate_platform_policy,
    validate_policy_bundle,
    validate_variant,
)
from blogops.domain.repurpose.schemas import RepurposeJobCreate
from blogops.domain.repurpose.service import (
    _finalize_cancelled_job,
    _reset_items_for_retry,
)
from blogops.domain.repurpose.tasks import (
    _is_retryable_runtime_error as repurpose_failure_is_retryable,
)


def test_repurpose_job_schema_preserves_model_config_api_name() -> None:
    properties = RepurposeJobCreate.model_json_schema(by_alias=True)["properties"]
    assert "model_config" in properties
    assert "generation_config" not in properties


def test_all_fourteen_repurpose_formats_have_requirement_lineage() -> None:
    assert set(REPURPOSE_REQUIREMENTS) == set(RepurposeKind)
    assert set(REPURPOSE_REQUIREMENTS.values()) == {
        f"REP-{number:03d}" for number in range(1, 15)
    }


def test_platform_limits_come_from_versioned_policy_not_constants() -> None:
    policy = {
        "policy_version": "platform-2026-08",
        "source": "official-platform-policy",
        "constraints": {"max_characters": 5},
    }
    validate_platform_policy(RepurposeKind.INSTAGRAM_CAPTION, policy)
    result = validate_variant(
        text="123456",
        document=[],
        platform_policy=policy,
        disclosure_result={"passed": True},
        safety_result={"passed": True},
        pii_result={"passed": True},
    )
    assert result.passed is False
    assert result.violations[0]["limit"] == 5


def test_missing_platform_policy_provenance_fails_closed() -> None:
    with pytest.raises(AppError) as error:
        validate_platform_policy(
            RepurposeKind.LINKEDIN,
            {"constraints": {"max_characters": 100}},
        )
    assert error.value.code == "PLATFORM_POLICY_INCOMPLETE"


def test_model_selection_is_exactly_pinned_by_versioned_policy() -> None:
    model_policy = {
        "policy_version": "models-1",
        "allowed_models": [
            {"provider": "official", "model": "copy", "model_version": "2026-08"}
        ],
    }
    validate_policy_bundle(
        disclosure_policy={"policy_version": "d1"},
        safety_policy={"policy_version": "s1"},
        pii_policy={"policy_version": "p1"},
        approval_policy={"policy_version": "a1"},
        model_policy=model_policy,
    )
    validate_model_selection(
        model_policy,
        provider="official",
        model="copy",
        model_version="2026-08",
    )
    with pytest.raises(AppError) as error:
        validate_model_selection(
            model_policy,
            provider="official",
            model="copy",
            model_version="latest",
        )
    assert error.value.code == "REPURPOSE_MODEL_NOT_ALLOWED"


def test_disclosure_safety_and_pii_must_all_pass() -> None:
    result = validate_variant(
        text="safe copy",
        document=[],
        platform_policy={"constraints": {}},
        disclosure_result={"passed": True},
        safety_result={"passed": False},
        pii_result={"passed": True},
    )
    assert result.passed is False
    assert {item["code"] for item in result.violations} == {"SAFETY_BLOCKED"}


def test_variant_claims_are_limited_to_exact_snapshot_hashes() -> None:
    validate_claim_lineage(
        [{"claim_id": "claim-1", "claim_hash": "a" * 64}],
        {"claim-1": "a" * 64},
    )
    with pytest.raises(AppError) as error:
        validate_claim_lineage(
            [{"claim_id": "claim-1", "claim_hash": "b" * 64}],
            {"claim-1": "a" * 64},
        )
    assert error.value.code == "UNSUPPORTED_REPURPOSE_CLAIM"


@pytest.mark.parametrize(
    "destination",
    [
        "http://example.com/post",
        "https://localhost/post",
        "https://127.0.0.1/post",
        "https://169.254.169.254/latest/meta-data",
        "https://user:password@example.com/post",
    ],
)
def test_tracking_destination_rejects_unsafe_urls(destination: str) -> None:
    with pytest.raises(AppError) as error:
        safe_tracking_destination(destination, {"utm_source": "newsletter"})
    assert error.value.code == "UNSAFE_TRACKING_DESTINATION"


def test_tracking_destination_merges_supplied_parameters() -> None:
    result = safe_tracking_destination(
        "https://example.com/post?existing=1#ignored",
        {"utm_source": "newsletter", "utm_campaign": "launch"},
    )
    assert result == (
        "https://example.com/post?existing=1&utm_source=newsletter&utm_campaign=launch"
    )


def test_fact_requires_exactly_one_evidence_lineage() -> None:
    validate_fact_evidence(provider_call_id="provider-call", evidence_batch_id=None)
    validate_fact_evidence(provider_call_id=None, evidence_batch_id="batch")
    with pytest.raises(AppError):
        validate_fact_evidence(provider_call_id=None, evidence_batch_id=None)
    with pytest.raises(AppError):
        validate_fact_evidence(provider_call_id="call", evidence_batch_id="batch")


def test_metric_comparison_requires_compatible_definition_semantics() -> None:
    first = {"unit": "click", "value_kind": "OBSERVED", "formula": {"field": "clicks"}}
    second = {"unit": "session", "value_kind": "OBSERVED", "formula": {"field": "sessions"}}
    with pytest.raises(AppError) as error:
        validate_comparable_metrics([first, second])
    assert error.value.code == "INCOMPATIBLE_METRIC_DEFINITIONS"


def test_roi_preserves_zero_cost_as_undefined_ratio() -> None:
    result = calculate_roi(revenue=Decimal("10"), cost=Decimal("0"))
    assert result.net_return == Decimal("10")
    assert result.roi_ratio is None


def test_unconfigured_analytics_adapter_registry_fails_closed() -> None:
    with pytest.raises(AppError) as error:
        AnalyticsAdapterRegistry().require("GOOGLE_ANALYTICS")
    assert error.value.code == "ANALYTICS_RUNTIME_UNAVAILABLE"


@pytest.mark.parametrize(
    "classifier",
    [analytics_failure_is_retryable, repurpose_failure_is_retryable],
)
def test_worker_failure_classification_retries_runtime_not_policy_errors(
    classifier: Callable[[Exception], bool],
) -> None:
    assert classifier(AppError("RUNTIME", "unavailable", 503)) is True
    assert classifier(RuntimeError("transport disconnected")) is True
    assert classifier(AppError("POLICY", "invalid policy", 422)) is False


def test_cancellation_finalizers_preserve_completed_repurpose_items() -> None:
    analytics_run = cast(
        AnalyticsSyncRun,
        SimpleNamespace(
            state=JobState.CANCEL_REQUESTED.value,
            error_code="stale",
            error_detail="stale",
            finished_at=None,
        ),
    )
    _finalize_cancelled_run(analytics_run)
    assert analytics_run.state == JobState.CANCELLED.value
    assert analytics_run.finished_at is not None

    succeeded = cast(
        RepurposeJobItem,
        SimpleNamespace(
            state=StepState.SUCCEEDED.value,
            error_code=None,
            error_detail=None,
        ),
    )
    running = cast(
        RepurposeJobItem,
        SimpleNamespace(
            state=StepState.RUNNING.value,
            error_code="stale",
            error_detail="stale",
        ),
    )
    job = cast(
        RepurposeJob,
        SimpleNamespace(
            state=JobState.CANCEL_REQUESTED.value,
            error_code="stale",
            error_detail="stale",
            finished_at=None,
        ),
    )
    _finalize_cancelled_job(job, [succeeded, running])
    assert job.state == JobState.CANCELLED.value
    assert succeeded.state == StepState.SUCCEEDED.value
    assert running.state == StepState.CANCELLED.value


def test_repurpose_retry_resets_only_incomplete_items() -> None:
    succeeded = cast(
        RepurposeJobItem,
        SimpleNamespace(
            state=StepState.SUCCEEDED.value,
            error_code=None,
            error_detail=None,
        ),
    )
    failed = cast(
        RepurposeJobItem,
        SimpleNamespace(
            state=StepState.FAILED.value,
            error_code="MODEL_TIMEOUT",
            error_detail="timeout",
        ),
    )
    _reset_items_for_retry([succeeded, failed])
    assert succeeded.state == StepState.SUCCEEDED.value
    assert failed.state == StepState.PENDING.value
    assert failed.error_code is None
    assert failed.error_detail is None


@pytest.mark.asyncio
async def test_repurpose_terminal_settlement_uses_persisted_job_cost_and_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    class RecordingBudgetGateway:
        async def finalize(self, **kwargs: object) -> object:
            calls.append(("finalize", kwargs))
            return object()

        async def release(self, **kwargs: object) -> object:
            calls.append(("release", kwargs))
            return object()

    gateway = RecordingBudgetGateway()
    monkeypatch.setattr(
        repurpose_tasks,
        "_budget_gateway_factory",
        lambda _session: gateway,
    )
    workspace_id = uuid5(NAMESPACE_URL, "repurpose-workspace")
    actor_id = uuid5(NAMESPACE_URL, "repurpose-actor")
    job_id = uuid5(NAMESPACE_URL, "repurpose-job")
    job = cast(
        RepurposeJob,
        SimpleNamespace(
            id=job_id,
            workspace_id=workspace_id,
            requested_by=actor_id,
            state=JobState.WAITING_REVIEW.value,
            budget_reservation_ref="credit-hold:repurpose",
            actual_cost=Decimal("3.2500"),
            budget_currency="KRW",
            error_code=None,
        ),
    )
    session = cast(AsyncSession, object())

    await repurpose_tasks._settle_terminal(session, job)
    assert calls == [
        (
            "finalize",
            {
                "workspace_id": workspace_id,
                "actor_id": actor_id,
                "reservation_ref": "credit-hold:repurpose",
                "actual_cost": Decimal("3.2500"),
                "currency": "KRW",
                "terminal_event_id": f"repurpose-job:{job_id}:WAITING_REVIEW",
            },
        )
    ]

    job.state = JobState.FINAL_FAILED.value
    job.error_code = "REPURPOSE_POLICY_FAILED"
    await repurpose_tasks._settle_terminal(session, job)
    assert calls[-1] == (
        "release",
        {
            "workspace_id": workspace_id,
            "actor_id": actor_id,
            "reservation_ref": "credit-hold:repurpose",
            "actual_cost": Decimal("3.2500"),
            "currency": "KRW",
            "terminal_event_id": f"repurpose-job:{job_id}:FINAL_FAILED",
            "failure_class": "FINAL_FAILED",
            "reason_code": "REPURPOSE_POLICY_FAILED",
        },
    )

    class CompletedItemsRepository:
        async def job_items(self, _job_id: object) -> list[SimpleNamespace]:
            return [SimpleNamespace(state=StepState.SUCCEEDED.value)]

    monkeypatch.setattr(
        repurpose_tasks,
        "RepurposeRepository",
        lambda _session, _workspace_id: CompletedItemsRepository(),
    )
    job.state = JobState.CANCELLED.value
    job.error_code = None
    await repurpose_tasks._settle_terminal(session, job)
    assert calls[-1] == calls[0]
