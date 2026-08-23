"""Direct C0/C1 security, privacy, recovery, and GA contract coverage."""

from datetime import UTC, datetime, timedelta
from inspect import signature
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from pydantic import ValidationError
from sqlalchemy import event

from blogops.api.router import api_router
from blogops.core.errors import AppError
from blogops.core.permissions import Permission
from blogops.domain.operations.enums import (
    BackupRunState,
    GAAssessmentState,
    GAGate,
    HealthStatus,
    RecoveryExerciseState,
)
from blogops.domain.operations.models import ServiceComponent, _service_component_frozen
from blogops.domain.operations.providers import (
    FailClosedOperationsAdapters,
    GAEvidenceVerifier,
)
from blogops.domain.operations.rules import (
    ensure_incident_transition,
    evaluate_ga_evidence,
    meets_recovery_objectives,
    validate_backup_policy,
    validate_health_observation,
)
from blogops.domain.operations.service import (
    OperationsService,
    _creation_guard_key as operations_creation_guard_key,
    _is_retry_exhausted_failure,
    _stored_attempt_error,
)
from blogops.domain.operations.tasks import (
    _is_retryable_operations_error,
    _retry_delay,
    process_backup_task,
    process_ga_assessment_task,
    process_recovery_task,
)
from blogops.domain.security.enums import (
    CopyrightCaseState,
    DataClass,
    PrivacyActionState,
    PrivacyRequestKind,
    PrivacyRequestState,
)
from blogops.domain.security.providers import FailClosedSecurityAdapters
from blogops.domain.security.rules import (
    append_evidence_hash,
    authorize_export_download,
    ensure_copyright_transition,
    ensure_privacy_transition,
    privacy_completion_state,
    redact_safe_metadata,
    require_secret_reference,
    validate_action_plan,
    validate_retention_rules,
    validate_secure_download_url,
)
from blogops.domain.security.schemas import (
    CopyrightNoticeCreate,
    PrivacyRequestCreate,
)
from blogops.domain.security.service import (
    SecurityService,
    _creation_guard_key as security_creation_guard_key,
)
from blogops.domain.security.tasks import (
    process_copyright_case_task,
    process_privacy_request_task,
    process_retention_sweep_task,
)


def _complete_retention_rules() -> dict[str, dict[str, object]]:
    return {
        item.value: {
            "retention_days": 30,
            "grace_days": 7,
            "disposition": "DELETE",
        }
        for item in DataClass
    }


def test_retention_policy_requires_every_data_class_and_server_bounds() -> None:
    rules = _complete_retention_rules()
    minimum = {item.value: 1 for item in DataClass}
    maximum = {item.value: 365 for item in DataClass}
    validate_retention_rules(rules, minimum_days=minimum, maximum_days=maximum)

    incomplete = dict(rules)
    incomplete.pop(DataClass.BACKUP.value)
    with pytest.raises(AppError) as missing:
        validate_retention_rules(
            incomplete, minimum_days=minimum, maximum_days=maximum
        )
    assert missing.value.code == "RETENTION_POLICY_INCOMPLETE"

    too_short = _complete_retention_rules()
    too_short[DataClass.BILLING.value] = {
        "retention_days": 1,
        "grace_days": 0,
        "disposition": "LEGAL_ARCHIVE",
    }
    minimum[DataClass.BILLING.value] = 10
    with pytest.raises(AppError) as legal_minimum:
        validate_retention_rules(
            too_short, minimum_days=minimum, maximum_days=maximum
        )
    assert legal_minimum.value.code == "RETENTION_LEGAL_MINIMUM_VIOLATION"


def test_sensitive_locators_must_be_opaque_secure_references() -> None:
    assert (
        require_secret_reference(
            "secret-manager://privacy/subject/1", path="subject_locator_ref"
        )
        == "secret-manager://privacy/subject/1"
    )
    with pytest.raises(AppError) as plaintext:
        require_secret_reference("person@example.com", path="subject_locator_ref")
    assert plaintext.value.code == "SECURE_REFERENCE_REQUIRED"


def test_safe_metadata_redacts_nested_secrets_and_direct_identifiers() -> None:
    value = redact_safe_metadata(
        {
            "provider": "approved",
            "nested": {
                "access-token": "secret",
                "email": "person@example.com",
                "latency_ms": 10,
            },
        }
    )
    assert value == {
        "provider": "approved",
        "nested": {
            "access-token": "[REDACTED]",
            "email": "[REDACTED]",
            "latency_ms": 10,
        },
    }


def test_evidence_hash_chain_binds_previous_event() -> None:
    payload = {"kind": "CONTAINMENT", "evidence_hash": "a" * 64}
    root = append_evidence_hash(None, payload)
    chained = append_evidence_hash(root, payload)
    assert len(root) == 64
    assert root != chained


def test_privacy_plan_must_cover_request_kind_and_all_data_classes() -> None:
    validate_action_plan(
        request_kind=PrivacyRequestKind.EXPORT,
        requested_data_classes=[DataClass.ACCOUNT.value],
        actions=[
            {
                "kind": "EXPORT",
                "data_classes": [DataClass.ACCOUNT.value],
                "target_system": "database",
            }
        ],
    )
    with pytest.raises(AppError) as incomplete:
        validate_action_plan(
            request_kind=PrivacyRequestKind.DELETE,
            requested_data_classes=[DataClass.ACCOUNT.value],
            actions=[
                {
                    "kind": "DELETE_DATABASE",
                    "data_classes": [DataClass.ACCOUNT.value],
                    "target_system": "database",
                }
            ],
        )
    assert incomplete.value.code == "PRIVACY_ACTION_PLAN_INCOMPLETE"

    with pytest.raises(AppError) as overbroad:
        validate_action_plan(
            request_kind=PrivacyRequestKind.EXPORT,
            requested_data_classes=[DataClass.ACCOUNT.value],
            actions=[
                {
                    "kind": "EXPORT",
                    "data_classes": [
                        DataClass.ACCOUNT.value,
                        DataClass.BILLING.value,
                    ],
                    "target_system": "database",
                }
            ],
        )
    assert overbroad.value.code == "PRIVACY_ACTION_DATA_CLASS_INVALID"


def test_privacy_state_and_partial_completion_are_fail_closed() -> None:
    ensure_privacy_transition(
        PrivacyRequestState.IDENTITY_PENDING.value,
        PrivacyRequestState.VERIFIED.value,
    )
    with pytest.raises(AppError):
        ensure_privacy_transition(
            PrivacyRequestState.IDENTITY_PENDING.value,
            PrivacyRequestState.COMPLETED.value,
        )
    assert privacy_completion_state(
        [
            PrivacyActionState.SUCCEEDED.value,
            PrivacyActionState.SKIPPED_LEGAL_HOLD.value,
        ]
    ) == PrivacyRequestState.PARTIAL


def test_export_download_requires_completion_freshness_and_remaining_limit() -> None:
    now = datetime.now(UTC)
    authorize_export_download(
        request_state=PrivacyRequestState.COMPLETED.value,
        expires_at=now + timedelta(minutes=5),
        now=now,
        download_count=0,
        maximum_downloads=1,
    )
    with pytest.raises(AppError) as exhausted:
        authorize_export_download(
            request_state=PrivacyRequestState.COMPLETED.value,
            expires_at=now + timedelta(minutes=5),
            now=now,
            download_count=1,
            maximum_downloads=1,
        )
    assert exhausted.value.code == "PRIVACY_EXPORT_DOWNLOAD_LIMIT"
    validate_secure_download_url("https://downloads.example.com/export?signature=opaque")
    with pytest.raises(AppError):
        validate_secure_download_url("http://127.0.0.1/private-export")


def test_copyright_transitions_require_review_before_restore() -> None:
    ensure_copyright_transition(
        CopyrightCaseState.LEGAL_REVIEW.value,
        CopyrightCaseState.RESTORED.value,
    )
    with pytest.raises(AppError):
        ensure_copyright_transition(
            CopyrightCaseState.RECEIVED.value,
            CopyrightCaseState.RESTORED.value,
        )


def test_privacy_and_copyright_schemas_reject_ambiguous_requests() -> None:
    with pytest.raises(ValidationError):
        PrivacyRequestCreate(
            kind=PrivacyRequestKind.CORRECT,
            subject_locator_ref="secret-manager://privacy/subject/1",
            data_classes=[DataClass.ACCOUNT],
            requester_relationship="SELF",
        )
    with pytest.raises(ValidationError):
        CopyrightNoticeCreate(
            claimant_contact_ref="secret-manager://privacy/contact/1",
            work_description="A sufficiently detailed copyrighted work description.",
            target_refs=[{"target_type": "content", "target_id": "content-1"}],
            evidence_object_refs=["evidence://copyright/1"],
            sworn_statement=False,
        )


@pytest.mark.asyncio
async def test_unconfigured_security_adapters_never_synthesize_success() -> None:
    adapter = FailClosedSecurityAdapters()
    with pytest.raises(AppError) as policy_error:
        await adapter.request_sla_days(
            workspace_id=uuid4(), request_kind="DELETE"
        )
    assert policy_error.value.code == "DATA_RIGHTS_POLICY_UNAVAILABLE"
    with pytest.raises(AppError) as executor_error:
        await adapter.execute_action()
    assert executor_error.value.code == "DATA_RIGHTS_EXECUTOR_UNAVAILABLE"
    with pytest.raises(AppError) as retention_error:
        await adapter.execute_retention_sweep()
    assert retention_error.value.code == "RETENTION_EXECUTOR_UNAVAILABLE"


def test_backup_policy_enforces_documented_rpo_rto_and_protection() -> None:
    validate_backup_policy(
        rpo_minutes=15,
        rto_minutes=120,
        backup_interval_minutes=1_440,
        pitr_enabled=True,
        encrypted=True,
        quarterly_drill_required=True,
    )
    with pytest.raises(AppError) as rpo:
        validate_backup_policy(
            rpo_minutes=16,
            rto_minutes=120,
            backup_interval_minutes=1_440,
            pitr_enabled=True,
            encrypted=True,
            quarterly_drill_required=True,
        )
    assert rpo.value.code == "BACKUP_RPO_POLICY_INVALID"


def test_recovery_objectives_use_measured_loss_and_recovery_time() -> None:
    assert meets_recovery_objectives(
        data_loss_minutes=15,
        recovery_minutes=120,
        rpo_minutes=15,
        rto_minutes=120,
    )
    assert not meets_recovery_objectives(
        data_loss_minutes=16,
        recovery_minutes=90,
        rpo_minutes=15,
        rto_minutes=120,
    )


def test_health_observation_rejects_unknown_or_unverifiable_result() -> None:
    now = datetime.now(UTC)
    validate_health_observation(
        status=HealthStatus.OPERATIONAL,
        checked_at=now,
        valid_until=now + timedelta(minutes=1),
        evidence_hash="a" * 64,
    )
    with pytest.raises(AppError) as unknown:
        validate_health_observation(
            status=HealthStatus.UNKNOWN,
            checked_at=now,
            valid_until=now + timedelta(minutes=1),
            evidence_hash="a" * 64,
        )
    assert unknown.value.code == "HEALTH_PROBE_INCONCLUSIVE"


def _ga_evidence(now: datetime) -> list[dict[str, object]]:
    evidence: list[dict[str, object]] = []
    for gate in GAGate:
        metrics: dict[str, object] = {}
        if gate == GAGate.SECURITY_FINDINGS:
            metrics = {"critical": 0, "high": 0}
        elif gate == GAGate.TENANT_ISOLATION:
            metrics = {"violations": 0}
        elif gate == GAGate.BILLING_LEDGER:
            metrics = {"delta": "0"}
        elif gate == GAGate.PUBLISHING_IDEMPOTENCY:
            metrics = {"duplicate_posts": 0}
        elif gate == GAGate.BACKUP_RESTORE:
            metrics = {"restore_verified": True, "rpo_minutes": 15, "rto_minutes": 120}
        evidence.append(
            {
                "gate": gate.value,
                "passed": True,
                "verified_at": now,
                "evidence_hash": "a" * 64,
                "metrics": metrics,
                "reason_codes": [],
            }
        )
    return evidence


def test_ga_gate_requires_complete_fresh_verified_evidence_and_zero_findings() -> None:
    now = datetime.now(UTC)
    evidence = _ga_evidence(now)
    decision = evaluate_ga_evidence(
        evidence, now=now, maximum_evidence_age=timedelta(days=30)
    )
    assert decision.passed is True

    security = next(
        item for item in evidence if item["gate"] == GAGate.SECURITY_FINDINGS.value
    )
    security["metrics"] = {"critical": 0, "high": 1}
    decision = evaluate_ga_evidence(
        evidence, now=now, maximum_evidence_age=timedelta(days=30)
    )
    assert decision.passed is False


def test_operational_incident_cannot_reopen_after_resolution() -> None:
    ensure_incident_transition("INVESTIGATING", "IDENTIFIED")
    with pytest.raises(AppError):
        ensure_incident_transition("RESOLVED", "INVESTIGATING")


@pytest.mark.asyncio
async def test_unconfigured_operations_adapters_fail_closed() -> None:
    adapter = FailClosedOperationsAdapters()
    with pytest.raises(AppError) as backup:
        await adapter.execute_backup()
    assert backup.value.code == "BACKUP_CONTROLLER_UNAVAILABLE"
    with pytest.raises(AppError) as ga:
        await adapter.verify_release()
    assert ga.value.code == "GA_EVIDENCE_VERIFIER_UNAVAILABLE"


def test_stage9_worker_tasks_and_permission_vocabulary_are_registered() -> None:
    assert process_privacy_request_task.name == "security.privacy.process"
    assert process_copyright_case_task.name == "security.copyright.process"
    assert process_retention_sweep_task.name == "security.retention.process"
    assert process_backup_task.name == "operations.backup.process"
    assert process_recovery_task.name == "operations.recovery.process"
    assert process_ga_assessment_task.name == "operations.ga.process"
    assert Permission.PRIVACY_READ.value == "privacy:read"
    assert Permission.PRIVACY_MANAGE.value == "privacy:manage"
    assert Permission.SECURITY_READ.value == "security:read"
    assert Permission.SECURITY_MANAGE.value == "security:manage"


def test_service_component_identity_fields_are_frozen_after_insert() -> None:
    assert event.contains(
        ServiceComponent,
        "before_update",
        _service_component_frozen,
    )


@pytest.mark.asyncio
async def test_stage9_creation_guards_use_stable_scoped_transaction_keys() -> None:
    workspace_id = uuid4()
    actor_id = uuid4()
    identity = (workspace_id, actor_id, "DELETE", "same:key")
    namespace = "privacy-request-idempotency"
    guard_key = security_creation_guard_key(namespace, *identity)

    assert guard_key == security_creation_guard_key(namespace, *identity)
    assert guard_key != security_creation_guard_key(
        namespace, uuid4(), actor_id, "DELETE", "same:key"
    )
    assert guard_key != security_creation_guard_key(
        namespace, workspace_id, actor_id, "DELETE:same", "key"
    )
    assert len(guard_key.rsplit(":", 1)[-1]) == 64

    security_session = SimpleNamespace(execute=AsyncMock())
    await SecurityService(security_session)._lock_creation_guard(
        namespace, *identity
    )
    statement, parameters = security_session.execute.await_args.args
    assert "pg_advisory_xact_lock" in str(statement)
    assert "hashtextextended" in str(statement)
    assert parameters == {"guard_key": guard_key}

    operations_session = SimpleNamespace(execute=AsyncMock())
    await OperationsService(operations_session)._lock_creation_guard(
        "backup-run-idempotency", "platform", "same:key"
    )
    _, operations_parameters = operations_session.execute.await_args.args
    assert operations_parameters == {
        "guard_key": operations_creation_guard_key(
            "backup-run-idempotency", "platform", "same:key"
        )
    }


def test_operations_retries_only_transient_failures_with_bounded_backoff() -> None:
    assert _is_retryable_operations_error(
        AppError("UPSTREAM_TIMEOUT", "timeout", 408)
    )
    assert _is_retryable_operations_error(
        AppError("UPSTREAM_RATE_LIMITED", "rate limited", 429)
    )
    assert _is_retryable_operations_error(
        AppError("UPSTREAM_UNAVAILABLE", "unavailable", 503)
    )
    assert _is_retryable_operations_error(TimeoutError())
    assert _is_retryable_operations_error(ConnectionError())

    assert not _is_retryable_operations_error(
        AppError("BACKUP_RESULT_INVALID", "invalid evidence", 503)
    )
    assert not _is_retryable_operations_error(
        AppError("BACKUP_POLICY_INVALID", "invalid policy", 422)
    )
    assert not _is_retryable_operations_error(RuntimeError("programming error"))
    assert [_retry_delay(value) for value in range(3)] == [5, 10, 20]
    assert _retry_delay(99) == 300

    exhausted = _stored_attempt_error("UPSTREAM_TIMEOUT", retry_exhausted=True)
    assert _is_retry_exhausted_failure(exhausted)
    assert len(exhausted) <= 120
    assert {
        BackupRunState.RETRYING.value,
        RecoveryExerciseState.RETRYING.value,
        GAAssessmentState.RETRYING.value,
    } == {"RETRYING"}
    assert process_backup_task.max_retries == 3
    assert process_recovery_task.max_retries == 3
    assert process_ga_assessment_task.max_retries == 3
    assert "idempotency_key" in signature(
        GAEvidenceVerifier.verify_release
    ).parameters


def test_stage9_public_and_authenticated_routes_have_distinct_boundaries() -> None:
    app = FastAPI()
    app.include_router(api_router)
    paths = set(app.openapi()["paths"])
    assert "/webhooks/data-deletion/{provider}" in paths
    assert "/v1/operations/status" in paths
    assert "/v1/privacy/requests" in paths
    assert "/v1/security/incidents" in paths
    assert "/v1/webhooks/data-deletion/{provider}" not in paths
