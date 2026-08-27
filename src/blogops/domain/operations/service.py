"""Platform health, incident, backup, recovery, and GA readiness workflows."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from math import ceil
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.core.context import Principal
from blogops.core.errors import AppError
from blogops.domain.operations.enums import (
    BackupRunState,
    GAAssessmentState,
    HealthStatus,
    OperationalIncidentEventKind,
    OperationalIncidentState,
    RecoveryExerciseState,
)
from blogops.domain.operations.models import (
    BackupEvidence,
    BackupPolicyVersion,
    BackupRun,
    GAAssessment,
    GAGateEvidence,
    HealthObservation,
    OperationalIncident,
    OperationalIncidentEvent,
    RecoveryEvidence,
    RecoveryExercise,
    RunbookVersion,
    ServiceComponent,
    StatusNotificationEvidence,
)
from blogops.domain.operations.providers import (
    BackupController,
    ComponentHealthProbe,
    GAEvidenceVerifier,
    OperationsPolicy,
    RecoveryController,
    StatusNotificationAdapter,
)
from blogops.domain.operations.rules import (
    canonical_json_hash,
    ensure_incident_transition,
    evaluate_ga_evidence,
    meets_recovery_objectives,
    validate_backup_policy,
    validate_health_observation,
)
from blogops.domain.operations.schemas import (
    BackupPolicyCreate,
    BackupRunCreate,
    GAAssessmentCreate,
    OperationalIncidentCreate,
    OperationalIncidentEventCreate,
    OperationalIncidentNotify,
    RecoveryExerciseCreate,
    RunbookVersionCreate,
    ServiceComponentCreate,
)
from blogops.domain.security.rules import (
    append_evidence_hash,
    is_sha256_hex,
    redact_safe_metadata,
    redact_safe_text,
)
from blogops.services.advisory_locks import (
    acquire_creation_guard,
    creation_guard_key,
)
from blogops.services.audit import append_audit_log
from blogops.services.outbox import add_outbox_event

_SCHEMA_VERSION = "1.0"
_RETRY_EXHAUSTED_PREFIX = "RETRY_EXHAUSTED:"
_creation_guard_key = creation_guard_key


def _stored_attempt_error(code: str, *, retry_exhausted: bool) -> str:
    normalized = (code or "OPERATIONS_EXECUTION_FAILED").strip()
    if retry_exhausted:
        return (
            _RETRY_EXHAUSTED_PREFIX
            + normalized[: 120 - len(_RETRY_EXHAUSTED_PREFIX)]
        )
    return normalized[:120]


def _is_retry_exhausted_failure(code: str | None) -> bool:
    return bool(code and code.startswith(_RETRY_EXHAUSTED_PREFIX))


class OperationsService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _lock_creation_guard(
        self, namespace: str, *identity: object
    ) -> None:
        await acquire_creation_guard(self._session, namespace, *identity)

    async def _record(
        self,
        *,
        principal: Principal | None,
        action: str,
        target_type: str,
        target_id: UUID,
        details: dict[str, Any],
    ) -> None:
        safe_details = redact_safe_metadata(details)
        await append_audit_log(
            self._session,
            workspace_id=None,
            actor_id=None if principal is None else principal.subject_id,
            action=action,
            target_type=target_type,
            target_id=str(target_id),
            details=safe_details,
        )
        await add_outbox_event(
            self._session,
            workspace_id=None,
            aggregate_type=target_type,
            aggregate_id=str(target_id),
            event_type=action,
            schema_version=_SCHEMA_VERSION,
            payload={"aggregate_id": str(target_id), **safe_details},
        )

    async def create_component(
        self, principal: Principal, data: ServiceComponentCreate
    ) -> ServiceComponent:
        value = ServiceComponent(
            component_key=data.component_key,
            display_name=data.display_name,
            kind=data.kind.value,
            endpoint_ref=data.endpoint_ref,
            public=data.public,
            owner_team=data.owner_team,
            service_tier=data.service_tier,
            safe_metadata=redact_safe_metadata(data.safe_metadata),
            created_by=principal.subject_id,
        )
        self._session.add(value)
        await self._session.flush()
        await self._record(
            principal=principal,
            action="operations.component.created",
            target_type="service_component",
            target_id=value.id,
            details={"component_key": value.component_key, "kind": value.kind},
        )
        return value

    async def list_components(self) -> list[ServiceComponent]:
        return list(
            await self._session.scalars(
                select(ServiceComponent).order_by(ServiceComponent.component_key)
            )
        )

    async def probe_component(
        self,
        component_id: UUID,
        *,
        probe: ComponentHealthProbe,
        policy: OperationsPolicy,
    ) -> HealthObservation:
        component = await self._component(component_id)
        if not component.enabled:
            raise AppError("COMPONENT_DISABLED", "비활성 구성 요소는 검사할 수 없습니다.", 409)
        result = await probe.probe(
            component_key=component.component_key, endpoint_ref=component.endpoint_ref
        )
        try:
            status = HealthStatus(result.status)
        except ValueError as exc:
            raise AppError(
                "HEALTH_STATUS_INVALID", "상태 확인 결과가 올바르지 않습니다.", 503
            ) from exc
        validate_health_observation(
            status=status,
            checked_at=result.checked_at,
            valid_until=result.valid_until,
            evidence_hash=result.evidence_hash,
            latency_ms=result.latency_ms,
        )
        now = datetime.now(UTC)
        if (
            result.checked_at > now + timedelta(minutes=5)
            or result.valid_until <= now
            or not isinstance(result.safe_metrics, dict)
        ):
            raise AppError(
                "HEALTH_OBSERVATION_INVALID",
                "상태 관측 시각 또는 안전 지표가 올바르지 않습니다.",
                503,
            )
        maximum_ttl = await policy.maximum_health_ttl_seconds()
        if maximum_ttl <= 0 or result.valid_until - result.checked_at > timedelta(
            seconds=maximum_ttl
        ):
            raise AppError(
                "HEALTH_OBSERVATION_TTL_INVALID",
                "상태 관측 유효기간이 서버 정책을 넘었습니다.",
                503,
            )
        value = HealthObservation(
            component_id=component.id,
            status=status.value,
            checked_at=result.checked_at,
            valid_until=result.valid_until,
            latency_ms=result.latency_ms,
            safe_metrics=redact_safe_metadata(result.safe_metrics),
            evidence_hash=result.evidence_hash,
        )
        self._session.add(value)
        await self._session.flush()
        return value

    async def public_status(self) -> list[dict[str, Any]]:
        rows = await self._session.execute(
            text(
                "SELECT component_key, display_name, status, checked_at, valid_until "
                "FROM app.public_operations_status()"
            )
        )
        return [
            {
                "component": row["component_key"],
                "name": row["display_name"],
                "status": row["status"],
                "checked_at": row["checked_at"],
                "valid_until": row["valid_until"],
            }
            for row in rows.mappings()
        ]

    async def _component(self, component_id: UUID) -> ServiceComponent:
        value = await self._session.scalar(
            select(ServiceComponent).where(ServiceComponent.id == component_id)
        )
        if value is None:
            raise AppError("SERVICE_COMPONENT_NOT_FOUND", "서비스 구성 요소를 찾을 수 없습니다.", 404)
        return value

    async def create_runbook(
        self, principal: Principal, data: RunbookVersionCreate
    ) -> RunbookVersion:
        value = RunbookVersion(
            runbook_key=data.runbook_key,
            version=data.version,
            title=data.title,
            artifact_ref=data.artifact_ref,
            artifact_hash=data.artifact_hash,
            owner_team=data.owner_team,
            escalation_policy_ref=data.escalation_policy_ref,
            exercise_interval_days=data.exercise_interval_days,
            approved_by=principal.subject_id,
            effective_at=data.effective_at,
            retired_at=data.retired_at,
        )
        self._session.add(value)
        await self._session.flush()
        await self._record(
            principal=principal,
            action="operations.runbook.version_created",
            target_type="runbook_version",
            target_id=value.id,
            details={"runbook_key": value.runbook_key, "version": value.version},
        )
        return value

    async def list_runbooks(self) -> list[RunbookVersion]:
        return list(
            await self._session.scalars(
                select(RunbookVersion).order_by(
                    RunbookVersion.runbook_key, RunbookVersion.version.desc()
                )
            )
        )

    async def create_backup_policy(
        self, principal: Principal, data: BackupPolicyCreate
    ) -> BackupPolicyVersion:
        validate_backup_policy(
            rpo_minutes=data.rpo_minutes,
            rto_minutes=data.rto_minutes,
            backup_interval_minutes=data.backup_interval_minutes,
            pitr_enabled=data.pitr_enabled,
            encrypted=data.encrypted,
            quarterly_drill_required=data.quarterly_drill_required,
        )
        payload = data.model_dump(mode="json")
        value = BackupPolicyVersion(
            policy_key=data.policy_key,
            version=data.version,
            data_scope=list(dict.fromkeys(data.data_scope)),
            rpo_minutes=data.rpo_minutes,
            rto_minutes=data.rto_minutes,
            backup_interval_minutes=data.backup_interval_minutes,
            pitr_enabled=data.pitr_enabled,
            encrypted=data.encrypted,
            encryption_key_policy=data.encryption_key_policy,
            retention_cycles=data.retention_cycles,
            quarterly_drill_required=data.quarterly_drill_required,
            region_policy=data.region_policy,
            policy_hash=canonical_json_hash(payload),
            effective_at=data.effective_at,
            retired_at=data.retired_at,
            approved_by=principal.subject_id,
        )
        self._session.add(value)
        await self._session.flush()
        await self._record(
            principal=principal,
            action="operations.backup_policy.version_created",
            target_type="backup_policy_version",
            target_id=value.id,
            details={"policy_key": value.policy_key, "version": value.version},
        )
        return value

    async def list_backup_policies(self) -> list[BackupPolicyVersion]:
        return list(
            await self._session.scalars(
                select(BackupPolicyVersion).order_by(
                    BackupPolicyVersion.policy_key, BackupPolicyVersion.version.desc()
                )
            )
        )

    async def create_backup_run(
        self,
        principal: Principal,
        data: BackupRunCreate,
        *,
        idempotency_key: str,
    ) -> tuple[BackupRun, bool]:
        await self._lock_creation_guard(
            "backup-run-idempotency", "platform", idempotency_key
        )
        existing = await self._session.scalar(
            select(BackupRun).where(BackupRun.idempotency_key == idempotency_key)
        )
        if existing is not None:
            if existing.policy_version_id != data.policy_version_id:
                raise AppError(
                    "IDEMPOTENCY_KEY_REUSED",
                    "같은 Idempotency-Key가 다른 백업 요청에 사용되었습니다.",
                    409,
                )
            return existing, False
        policy = await self._session.scalar(
            select(BackupPolicyVersion).where(BackupPolicyVersion.id == data.policy_version_id)
        )
        now = datetime.now(UTC)
        if (
            policy is None
            or policy.effective_at > now
            or (policy.retired_at is not None and policy.retired_at <= now)
        ):
            raise AppError("BACKUP_POLICY_NOT_ACTIVE", "유효한 백업 정책이 아닙니다.", 409)
        snapshot = self._backup_policy_snapshot(policy)
        value = BackupRun(
            policy_version_id=policy.id,
            policy_snapshot=snapshot,
            policy_snapshot_hash=canonical_json_hash(snapshot),
            idempotency_key=idempotency_key,
            requested_by=principal.subject_id,
        )
        self._session.add(value)
        await self._session.flush()
        await self._record(
            principal=principal,
            action="operations.backup.queued",
            target_type="backup_run",
            target_id=value.id,
            details={"policy_version_id": str(policy.id)},
        )
        return value, True

    async def execute_backup(
        self, run_id: UUID, *, controller: BackupController
    ) -> BackupRun:
        value = await self._backup_run(run_id, lock=True)
        if value.state not in {
            BackupRunState.QUEUED.value,
            BackupRunState.RETRYING.value,
        }:
            return value
        value.state = BackupRunState.RUNNING.value
        value.attempt_count += 1
        value.failure_code = None
        value.completed_at = None
        value.started_at = datetime.now(UTC)
        await self._session.flush()
        result = await controller.execute_backup(
            run_id=value.id,
            policy_snapshot=value.policy_snapshot,
            idempotency_key=value.idempotency_key,
        )
        if (
            result.started_at.tzinfo is None
            or result.completed_at.tzinfo is None
            or result.restore_point_at.tzinfo is None
            or result.completed_at < result.started_at
            or result.started_at < value.requested_at - timedelta(minutes=5)
            or result.completed_at > datetime.now(UTC) + timedelta(minutes=5)
            or result.restore_point_at > result.completed_at
            or type(result.size_bytes) is not int
            or result.size_bytes < 0
            or not result.provider_run_ref
            or len(result.provider_run_ref) > 500
            or not result.snapshot_ref
            or len(result.snapshot_ref) > 1_000
            or not is_sha256_hex(result.snapshot_hash)
            or not result.encryption_key_version
            or len(result.encryption_key_version) > 80
            or type(result.verified) is not bool
            or not result.evidence_object_ref
            or len(result.evidence_object_ref) > 1_000
            or not is_sha256_hex(result.evidence_hash)
        ):
            raise AppError(
                "BACKUP_RESULT_INVALID", "백업 실행 결과의 증거가 올바르지 않습니다.", 503
            )
        evidence = BackupEvidence(
            run_id=value.id,
            provider_run_ref=result.provider_run_ref,
            snapshot_ref=result.snapshot_ref,
            snapshot_hash=result.snapshot_hash,
            encryption_key_version=result.encryption_key_version,
            started_at=result.started_at,
            completed_at=result.completed_at,
            restore_point_at=result.restore_point_at,
            size_bytes=result.size_bytes,
            verified=result.verified,
            evidence_object_ref=result.evidence_object_ref,
            evidence_hash=result.evidence_hash,
        )
        self._session.add(evidence)
        value.provider_run_ref = result.provider_run_ref
        value.started_at = result.started_at
        value.completed_at = result.completed_at
        actual_rpo_minutes = max(
            0,
            ceil((result.completed_at - result.restore_point_at).total_seconds() / 60),
        )
        rpo_met = (
            result.completed_at - result.restore_point_at
            <= timedelta(minutes=int(value.policy_snapshot["rpo_minutes"]))
        )
        if result.verified and result.encryption_key_version and rpo_met:
            value.state = BackupRunState.SUCCEEDED.value
        else:
            value.state = BackupRunState.FAILED.value
            value.failure_code = (
                "BACKUP_RPO_FAILED" if not rpo_met else "BACKUP_VERIFICATION_FAILED"
            )
        await self._session.flush()
        await self._record(
            principal=None,
            action="operations.backup.finished",
            target_type="backup_run",
            target_id=value.id,
            details={
                "state": value.state,
                "verified": result.verified,
                "actual_rpo_minutes": actual_rpo_minutes,
            },
        )
        return value

    async def fail_backup(self, run_id: UUID, *, code: str) -> BackupRun:
        value = await self._backup_run(run_id, lock=True)
        if value.state in {
            BackupRunState.QUEUED.value,
            BackupRunState.RUNNING.value,
            BackupRunState.RETRYING.value,
        }:
            value.state = BackupRunState.FAILED.value
            value.failure_code = code[:120]
            value.completed_at = datetime.now(UTC)
        return value

    async def record_backup_attempt_error(
        self,
        run_id: UUID,
        *,
        code: str,
        retry: bool,
        retry_exhausted: bool,
    ) -> tuple[BackupRun, bool]:
        value = await self._backup_run(run_id, lock=True)
        if value.state not in {
            BackupRunState.QUEUED.value,
            BackupRunState.RUNNING.value,
            BackupRunState.RETRYING.value,
        }:
            return value, False
        if value.state != BackupRunState.RUNNING.value:
            value.attempt_count += 1
        value.failure_code = _stored_attempt_error(
            code, retry_exhausted=retry_exhausted
        )
        value.provider_run_ref = None
        value.started_at = None
        if retry:
            value.state = BackupRunState.RETRYING.value
            value.completed_at = None
        else:
            value.state = BackupRunState.FAILED.value
            value.completed_at = datetime.now(UTC)
        await self._session.flush()
        await self._record(
            principal=None,
            action=(
                "operations.backup.retry_scheduled"
                if retry
                else "operations.backup.failed"
            ),
            target_type="backup_run",
            target_id=value.id,
            details={
                "attempt_count": value.attempt_count,
                "failure_code": value.failure_code,
            },
        )
        return value, retry

    async def retry_backup(
        self, principal: Principal, run_id: UUID
    ) -> tuple[BackupRun, bool]:
        value = await self._backup_run(run_id, lock=True)
        if value.state in {
            BackupRunState.QUEUED.value,
            BackupRunState.RETRYING.value,
        }:
            return value, False
        if (
            value.state != BackupRunState.FAILED.value
            or not _is_retry_exhausted_failure(value.failure_code)
        ):
            raise AppError(
                "OPERATIONS_JOB_NOT_RETRYABLE",
                "일시 장애로 재시도 한도를 소진한 백업 작업만 재시도할 수 있습니다.",
                409,
            )
        evidence_id = await self._session.scalar(
            select(BackupEvidence.id).where(BackupEvidence.run_id == value.id)
        )
        if evidence_id is not None:
            raise AppError(
                "OPERATIONS_JOB_NOT_RETRYABLE",
                "증거가 확정된 백업 작업은 동일 작업에서 재시도할 수 없습니다.",
                409,
            )
        value.state = BackupRunState.RETRYING.value
        value.failure_code = None
        value.provider_run_ref = None
        value.started_at = None
        value.completed_at = None
        await self._session.flush()
        await self._record(
            principal=principal,
            action="operations.backup.retry_requested",
            target_type="backup_run",
            target_id=value.id,
            details={"attempt_count": value.attempt_count},
        )
        return value, True

    async def get_backup_run(self, run_id: UUID) -> BackupRun:
        return await self._backup_run(run_id)

    async def get_backup_evidence(self, run_id: UUID) -> BackupEvidence:
        await self._backup_run(run_id)
        value = await self._session.scalar(
            select(BackupEvidence).where(BackupEvidence.run_id == run_id)
        )
        if value is None:
            raise AppError("BACKUP_EVIDENCE_NOT_FOUND", "백업 증거를 찾을 수 없습니다.", 404)
        return value

    async def _backup_run(self, run_id: UUID, *, lock: bool = False) -> BackupRun:
        query = select(BackupRun).where(BackupRun.id == run_id)
        if lock:
            query = query.with_for_update()
        value = await self._session.scalar(query)
        if value is None:
            raise AppError("BACKUP_RUN_NOT_FOUND", "백업 작업을 찾을 수 없습니다.", 404)
        return value

    @staticmethod
    def _backup_policy_snapshot(policy: BackupPolicyVersion) -> dict[str, Any]:
        return {
            "id": str(policy.id),
            "policy_key": policy.policy_key,
            "version": policy.version,
            "data_scope": policy.data_scope,
            "rpo_minutes": policy.rpo_minutes,
            "rto_minutes": policy.rto_minutes,
            "backup_interval_minutes": policy.backup_interval_minutes,
            "pitr_enabled": policy.pitr_enabled,
            "encrypted": policy.encrypted,
            "encryption_key_policy": policy.encryption_key_policy,
            "retention_cycles": policy.retention_cycles,
            "quarterly_drill_required": policy.quarterly_drill_required,
            "region_policy": policy.region_policy,
            "policy_hash": policy.policy_hash,
            "effective_at": policy.effective_at.isoformat(),
            "retired_at": (
                None if policy.retired_at is None else policy.retired_at.isoformat()
            ),
        }

    async def create_recovery_exercise(
        self,
        principal: Principal,
        data: RecoveryExerciseCreate,
        *,
        idempotency_key: str,
    ) -> tuple[RecoveryExercise, bool]:
        await self._lock_creation_guard(
            "recovery-exercise-idempotency", "platform", idempotency_key
        )
        existing = await self._session.scalar(
            select(RecoveryExercise).where(
                RecoveryExercise.idempotency_key == idempotency_key
            )
        )
        if existing is not None:
            if (
                existing.backup_evidence_id != data.backup_evidence_id
                or existing.runbook_version_id != data.runbook_version_id
            ):
                raise AppError(
                    "IDEMPOTENCY_KEY_REUSED",
                    "같은 Idempotency-Key가 다른 복구 훈련에 사용되었습니다.",
                    409,
                )
            return existing, False
        backup = await self._session.scalar(
            select(BackupEvidence).where(BackupEvidence.id == data.backup_evidence_id)
        )
        runbook = await self._session.scalar(
            select(RunbookVersion).where(RunbookVersion.id == data.runbook_version_id)
        )
        if backup is None or not backup.verified:
            raise AppError(
                "VERIFIED_BACKUP_REQUIRED", "검증된 백업 증거가 필요합니다.", 409
            )
        now = datetime.now(UTC)
        if (
            runbook is None
            or runbook.effective_at > now
            or (runbook.retired_at is not None and runbook.retired_at <= now)
        ):
            raise AppError("RUNBOOK_NOT_ACTIVE", "유효한 복구 Runbook이 아닙니다.", 409)
        backup_run = await self._backup_run(backup.run_id)
        value = RecoveryExercise(
            backup_evidence_id=backup.id,
            runbook_version_id=runbook.id,
            rpo_minutes=int(backup_run.policy_snapshot["rpo_minutes"]),
            rto_minutes=int(backup_run.policy_snapshot["rto_minutes"]),
            idempotency_key=idempotency_key,
            requested_by=principal.subject_id,
        )
        self._session.add(value)
        await self._session.flush()
        await self._record(
            principal=principal,
            action="operations.recovery_exercise.queued",
            target_type="recovery_exercise",
            target_id=value.id,
            details={
                "backup_evidence_id": str(backup.id),
                "runbook_version_id": str(runbook.id),
            },
        )
        return value, True

    async def execute_recovery(
        self, exercise_id: UUID, *, controller: RecoveryController
    ) -> RecoveryExercise:
        value = await self._recovery_exercise(exercise_id, lock=True)
        if value.state not in {
            RecoveryExerciseState.QUEUED.value,
            RecoveryExerciseState.RETRYING.value,
        }:
            return value
        backup = await self._session.scalar(
            select(BackupEvidence).where(BackupEvidence.id == value.backup_evidence_id)
        )
        runbook = await self._session.scalar(
            select(RunbookVersion).where(RunbookVersion.id == value.runbook_version_id)
        )
        if backup is None or runbook is None:
            raise AppError(
                "RECOVERY_INPUT_MISSING", "복구 훈련 입력 증거가 없습니다.", 503
            )
        value.state = RecoveryExerciseState.RUNNING.value
        value.attempt_count += 1
        value.failure_code = None
        value.completed_at = None
        value.started_at = datetime.now(UTC)
        await self._session.flush()
        result = await controller.execute_recovery(
            exercise_id=value.id,
            backup_evidence_ref=backup.evidence_object_ref,
            runbook_artifact_ref=runbook.artifact_ref,
            idempotency_key=value.idempotency_key,
        )
        if (
            result.started_at.tzinfo is None
            or result.completed_at.tzinfo is None
            or result.completed_at < result.started_at
            or result.started_at < value.requested_at - timedelta(minutes=5)
            or result.completed_at > datetime.now(UTC) + timedelta(minutes=5)
            or not result.provider_run_ref
            or len(result.provider_run_ref) > 500
            or not isinstance(result.integrity_checks, dict)
            or type(result.integrity_checks.get("passed")) is not bool
            or not result.evidence_object_ref
            or len(result.evidence_object_ref) > 1_000
            or not is_sha256_hex(result.evidence_hash)
        ):
            raise AppError(
                "RECOVERY_RESULT_INVALID", "복구 훈련 결과의 증거가 올바르지 않습니다.", 503
            )
        if (
            not result.isolated_environment_ref
            or len(result.isolated_environment_ref) > 1_000
            or result.isolated_environment_ref == backup.snapshot_ref
        ):
            raise AppError(
                "RECOVERY_ISOLATION_INVALID",
                "복구 훈련은 격리된 환경에서 실행되어야 합니다.",
                503,
            )
        objectives_met = meets_recovery_objectives(
            data_loss_minutes=result.data_loss_minutes,
            recovery_minutes=result.recovery_minutes,
            rpo_minutes=value.rpo_minutes,
            rto_minutes=value.rto_minutes,
        )
        evidence = RecoveryEvidence(
            exercise_id=value.id,
            provider_run_ref=result.provider_run_ref,
            isolated_environment_ref=result.isolated_environment_ref,
            data_loss_minutes=result.data_loss_minutes,
            recovery_minutes=result.recovery_minutes,
            objectives_met=objectives_met,
            integrity_checks=redact_safe_metadata(result.integrity_checks),
            started_at=result.started_at,
            completed_at=result.completed_at,
            evidence_object_ref=result.evidence_object_ref,
            evidence_hash=result.evidence_hash,
        )
        self._session.add(evidence)
        value.provider_run_ref = result.provider_run_ref
        value.started_at = result.started_at
        value.completed_at = result.completed_at
        if objectives_met and result.integrity_checks["passed"]:
            value.state = RecoveryExerciseState.SUCCEEDED.value
        else:
            value.state = RecoveryExerciseState.FAILED.value
            value.failure_code = "RECOVERY_OBJECTIVE_OR_INTEGRITY_FAILED"
        await self._session.flush()
        await self._record(
            principal=None,
            action="operations.recovery_exercise.finished",
            target_type="recovery_exercise",
            target_id=value.id,
            details={"state": value.state, "objectives_met": objectives_met},
        )
        return value

    async def fail_recovery(self, exercise_id: UUID, *, code: str) -> RecoveryExercise:
        value = await self._recovery_exercise(exercise_id, lock=True)
        if value.state in {
            RecoveryExerciseState.QUEUED.value,
            RecoveryExerciseState.RUNNING.value,
            RecoveryExerciseState.RETRYING.value,
        }:
            value.state = RecoveryExerciseState.FAILED.value
            value.failure_code = code[:120]
            value.completed_at = datetime.now(UTC)
        return value

    async def record_recovery_attempt_error(
        self,
        exercise_id: UUID,
        *,
        code: str,
        retry: bool,
        retry_exhausted: bool,
    ) -> tuple[RecoveryExercise, bool]:
        value = await self._recovery_exercise(exercise_id, lock=True)
        if value.state not in {
            RecoveryExerciseState.QUEUED.value,
            RecoveryExerciseState.RUNNING.value,
            RecoveryExerciseState.RETRYING.value,
        }:
            return value, False
        if value.state != RecoveryExerciseState.RUNNING.value:
            value.attempt_count += 1
        value.failure_code = _stored_attempt_error(
            code, retry_exhausted=retry_exhausted
        )
        value.provider_run_ref = None
        value.started_at = None
        if retry:
            value.state = RecoveryExerciseState.RETRYING.value
            value.completed_at = None
        else:
            value.state = RecoveryExerciseState.FAILED.value
            value.completed_at = datetime.now(UTC)
        await self._session.flush()
        await self._record(
            principal=None,
            action=(
                "operations.recovery_exercise.retry_scheduled"
                if retry
                else "operations.recovery_exercise.failed"
            ),
            target_type="recovery_exercise",
            target_id=value.id,
            details={
                "attempt_count": value.attempt_count,
                "failure_code": value.failure_code,
            },
        )
        return value, retry

    async def retry_recovery(
        self, principal: Principal, exercise_id: UUID
    ) -> tuple[RecoveryExercise, bool]:
        value = await self._recovery_exercise(exercise_id, lock=True)
        if value.state in {
            RecoveryExerciseState.QUEUED.value,
            RecoveryExerciseState.RETRYING.value,
        }:
            return value, False
        if (
            value.state != RecoveryExerciseState.FAILED.value
            or not _is_retry_exhausted_failure(value.failure_code)
        ):
            raise AppError(
                "OPERATIONS_JOB_NOT_RETRYABLE",
                "일시 장애로 재시도 한도를 소진한 복구 작업만 재시도할 수 있습니다.",
                409,
            )
        evidence_id = await self._session.scalar(
            select(RecoveryEvidence.id).where(RecoveryEvidence.exercise_id == value.id)
        )
        if evidence_id is not None:
            raise AppError(
                "OPERATIONS_JOB_NOT_RETRYABLE",
                "증거가 확정된 복구 작업은 동일 작업에서 재시도할 수 없습니다.",
                409,
            )
        value.state = RecoveryExerciseState.RETRYING.value
        value.failure_code = None
        value.provider_run_ref = None
        value.started_at = None
        value.completed_at = None
        await self._session.flush()
        await self._record(
            principal=principal,
            action="operations.recovery_exercise.retry_requested",
            target_type="recovery_exercise",
            target_id=value.id,
            details={"attempt_count": value.attempt_count},
        )
        return value, True

    async def get_recovery_exercise(self, exercise_id: UUID) -> RecoveryExercise:
        return await self._recovery_exercise(exercise_id)

    async def get_recovery_evidence(self, exercise_id: UUID) -> RecoveryEvidence:
        await self._recovery_exercise(exercise_id)
        value = await self._session.scalar(
            select(RecoveryEvidence).where(RecoveryEvidence.exercise_id == exercise_id)
        )
        if value is None:
            raise AppError("RECOVERY_EVIDENCE_NOT_FOUND", "복구 훈련 증거를 찾을 수 없습니다.", 404)
        return value

    async def _recovery_exercise(
        self, exercise_id: UUID, *, lock: bool = False
    ) -> RecoveryExercise:
        query = select(RecoveryExercise).where(RecoveryExercise.id == exercise_id)
        if lock:
            query = query.with_for_update()
        value = await self._session.scalar(query)
        if value is None:
            raise AppError("RECOVERY_EXERCISE_NOT_FOUND", "복구 훈련을 찾을 수 없습니다.", 404)
        return value

    async def create_incident(
        self, principal: Principal, data: OperationalIncidentCreate
    ) -> OperationalIncident:
        if data.started_at > datetime.now(UTC) + timedelta(minutes=5):
            raise AppError(
                "OPERATIONS_INCIDENT_START_INVALID",
                "운영 장애 시작 시각은 미래일 수 없습니다.",
                422,
            )
        component_ids = list(dict.fromkeys(data.component_ids))
        components = list(
            await self._session.scalars(
                select(ServiceComponent).where(ServiceComponent.id.in_(component_ids))
            )
        )
        if len(components) != len(component_ids):
            raise AppError(
                "INCIDENT_COMPONENT_NOT_FOUND", "장애 대상 구성 요소를 찾을 수 없습니다.", 404
            )
        runbook = await self._session.scalar(
            select(RunbookVersion).where(RunbookVersion.id == data.runbook_version_id)
        )
        now = datetime.now(UTC)
        if (
            runbook is None
            or runbook.effective_at > now
            or (runbook.retired_at is not None and runbook.retired_at <= now)
        ):
            raise AppError("RUNBOOK_NOT_FOUND", "장애 Runbook을 찾을 수 없습니다.", 404)
        value = OperationalIncident(
            external_ref=data.external_ref,
            title=data.title,
            safe_summary=redact_safe_text(data.safe_summary),
            severity=data.severity,
            component_ids=[str(item) for item in component_ids],
            affected_workspace_ids=sorted(
                {str(item) for item in data.affected_workspace_ids}
            ),
            started_at=data.started_at,
            runbook_version_id=runbook.id,
            opened_by=principal.subject_id,
        )
        self._session.add(value)
        await self._session.flush()
        await self._append_incident_event(
            value,
            actor_id=principal.subject_id,
            kind=OperationalIncidentEventKind.OPENED,
            state_after=OperationalIncidentState.INVESTIGATING,
            safe_summary=redact_safe_text(data.safe_summary),
            evidence_object_refs=[],
            evidence_hash=canonical_json_hash(data.model_dump(mode="json")),
            occurred_at=data.started_at,
        )
        await self._record(
            principal=principal,
            action="operations.incident.opened",
            target_type="operational_incident",
            target_id=value.id,
            details={"severity": value.severity, "component_ids": value.component_ids},
        )
        return value

    async def append_incident_event(
        self,
        principal: Principal,
        incident_id: UUID,
        data: OperationalIncidentEventCreate,
    ) -> OperationalIncident:
        value = await self._incident(incident_id, lock=True)
        if (
            data.kind == OperationalIncidentEventKind.OPENED
            or (data.kind == OperationalIncidentEventKind.RESOLVED)
            != (data.state_after == OperationalIncidentState.RESOLVED)
        ):
            raise AppError(
                "OPERATIONS_INCIDENT_EVENT_INVALID",
                "운영 장애 이벤트 유형과 후속 상태가 일치하지 않습니다.",
                422,
            )
        if data.state_after.value != value.state:
            ensure_incident_transition(value.state, data.state_after.value)
            value.state = data.state_after.value
        if data.state_after == OperationalIncidentState.IDENTIFIED:
            value.identified_at = data.occurred_at
        if data.state_after == OperationalIncidentState.RESOLVED:
            value.resolved_at = data.occurred_at
        value.safe_summary = redact_safe_text(data.safe_summary)
        await self._append_incident_event(
            value,
            actor_id=principal.subject_id,
            kind=data.kind,
            state_after=data.state_after,
            safe_summary=redact_safe_text(data.safe_summary),
            evidence_object_refs=list(dict.fromkeys(data.evidence_object_refs)),
            evidence_hash=data.evidence_hash,
            occurred_at=data.occurred_at,
        )
        await self._record(
            principal=principal,
            action="operations.incident.event_appended",
            target_type="operational_incident",
            target_id=value.id,
            details={"kind": data.kind.value, "state": value.state},
        )
        return value

    async def notify_incident(
        self,
        principal: Principal,
        incident_id: UUID,
        data: OperationalIncidentNotify,
        *,
        notifier: StatusNotificationAdapter,
    ) -> StatusNotificationEvidence:
        value = await self._incident(incident_id, lock=True)
        safe_payload = redact_safe_metadata(data.safe_payload)
        payload_hash = canonical_json_hash(safe_payload)
        existing = await self._session.scalar(
            select(StatusNotificationEvidence).where(
                StatusNotificationEvidence.incident_id == value.id,
                StatusNotificationEvidence.audience == data.audience,
                StatusNotificationEvidence.template_version == data.template_version,
                StatusNotificationEvidence.payload_hash == payload_hash,
            )
        )
        if existing is not None:
            return existing
        result = await notifier.publish_update(
            incident_id=value.id,
            audience=data.audience,
            template_version=data.template_version,
            safe_payload=safe_payload,
            idempotency_key=(
                f"status:{value.id}:{data.audience}:"
                f"{data.template_version}:{payload_hash[:16]}"
            ),
        )
        if (
            result.delivered_at.tzinfo is None
            or result.delivered_at < value.started_at
            or result.delivered_at > datetime.now(UTC) + timedelta(minutes=5)
            or not result.provider_message_ref
            or len(result.provider_message_ref) > 500
            or not is_sha256_hex(result.evidence_hash)
        ):
            raise AppError(
                "STATUS_NOTIFICATION_RESULT_INVALID",
                "상태 공지 결과 증거가 올바르지 않습니다.",
                503,
            )
        notice = StatusNotificationEvidence(
            incident_id=value.id,
            audience=data.audience,
            template_version=data.template_version,
            payload_hash=payload_hash,
            provider_message_ref=result.provider_message_ref,
            evidence_hash=result.evidence_hash,
            delivered_at=result.delivered_at,
        )
        self._session.add(notice)
        await self._session.flush()
        await self._append_incident_event(
            value,
            actor_id=principal.subject_id,
            kind=OperationalIncidentEventKind.CUSTOMER_NOTICE,
            state_after=OperationalIncidentState(value.state),
            safe_summary=f"status update delivered to {data.audience}",
            evidence_object_refs=[],
            evidence_hash=result.evidence_hash,
            occurred_at=result.delivered_at,
        )
        return notice

    async def get_incident(self, incident_id: UUID) -> OperationalIncident:
        return await self._incident(incident_id)

    async def list_incidents(self) -> list[OperationalIncident]:
        return list(
            await self._session.scalars(
                select(OperationalIncident).order_by(OperationalIncident.started_at.desc())
            )
        )

    async def list_incident_events(
        self, incident_id: UUID
    ) -> list[OperationalIncidentEvent]:
        await self._incident(incident_id)
        return list(
            await self._session.scalars(
                select(OperationalIncidentEvent)
                .where(OperationalIncidentEvent.incident_id == incident_id)
                .order_by(OperationalIncidentEvent.sequence)
            )
        )

    async def list_status_notifications(
        self, incident_id: UUID
    ) -> list[StatusNotificationEvidence]:
        await self._incident(incident_id)
        return list(
            await self._session.scalars(
                select(StatusNotificationEvidence)
                .where(StatusNotificationEvidence.incident_id == incident_id)
                .order_by(StatusNotificationEvidence.delivered_at)
            )
        )

    async def _incident(
        self, incident_id: UUID, *, lock: bool = False
    ) -> OperationalIncident:
        query = select(OperationalIncident).where(OperationalIncident.id == incident_id)
        if lock:
            query = query.with_for_update()
        value = await self._session.scalar(query)
        if value is None:
            raise AppError("OPERATIONS_INCIDENT_NOT_FOUND", "운영 장애를 찾을 수 없습니다.", 404)
        return value

    async def _append_incident_event(
        self,
        incident: OperationalIncident,
        *,
        actor_id: UUID | None,
        kind: OperationalIncidentEventKind,
        state_after: OperationalIncidentState,
        safe_summary: str,
        evidence_object_refs: list[str],
        evidence_hash: str,
        occurred_at: datetime,
    ) -> OperationalIncidentEvent:
        previous = await self._session.scalar(
            select(OperationalIncidentEvent)
            .where(OperationalIncidentEvent.incident_id == incident.id)
            .order_by(OperationalIncidentEvent.sequence.desc())
            .limit(1)
        )
        if (
            occurred_at.tzinfo is None
            or occurred_at < incident.started_at
            or occurred_at > datetime.now(UTC) + timedelta(minutes=5)
            or (previous is not None and occurred_at < previous.occurred_at)
            or not is_sha256_hex(evidence_hash)
        ):
            raise AppError(
                "OPERATIONS_INCIDENT_EVENT_EVIDENCE_INVALID",
                "운영 장애 타임라인 증거가 올바르지 않습니다.",
                422,
            )
        sequence = 1 if previous is None else previous.sequence + 1
        safe_summary = redact_safe_text(safe_summary)
        payload = {
            "incident_id": str(incident.id),
            "sequence": sequence,
            "kind": kind.value,
            "state_after": state_after.value,
            "safe_summary": safe_summary,
            "evidence_hash": evidence_hash,
            "occurred_at": occurred_at,
        }
        value = OperationalIncidentEvent(
            incident_id=incident.id,
            sequence=sequence,
            kind=kind.value,
            state_after=state_after.value,
            actor_id=actor_id,
            safe_summary=safe_summary,
            evidence_object_refs=evidence_object_refs,
            evidence_hash=evidence_hash,
            previous_event_hash=None if previous is None else previous.event_hash,
            event_hash=append_evidence_hash(
                None if previous is None else previous.event_hash, payload
            ),
            occurred_at=occurred_at,
        )
        self._session.add(value)
        await self._session.flush()
        return value

    async def create_ga_assessment(
        self,
        principal: Principal,
        data: GAAssessmentCreate,
        *,
        idempotency_key: str,
    ) -> tuple[GAAssessment, bool]:
        payload = data.model_dump(mode="json")
        request_hash = canonical_json_hash(payload)
        await self._lock_creation_guard(
            "ga-assessment-idempotency", "platform", idempotency_key
        )
        await self._lock_creation_guard(
            "ga-assessment-release", "platform", data.release_ref
        )
        existing = await self._session.scalar(
            select(GAAssessment).where(
                (GAAssessment.idempotency_key == idempotency_key)
                | (GAAssessment.release_ref == data.release_ref)
            )
        )
        if existing is not None:
            if existing.request_hash != request_hash:
                raise AppError(
                    "GA_RELEASE_ASSESSMENT_CONFLICT",
                    "동일 출시 버전 또는 Idempotency-Key의 증거 요청이 다릅니다.",
                    409,
                )
            return existing, False
        value = GAAssessment(
            release_ref=data.release_ref,
            artifact_refs=list(dict.fromkeys(data.artifact_refs)),
            request_hash=request_hash,
            idempotency_key=idempotency_key,
            requested_by=principal.subject_id,
        )
        self._session.add(value)
        await self._session.flush()
        await self._record(
            principal=principal,
            action="operations.ga_assessment.queued",
            target_type="ga_assessment",
            target_id=value.id,
            details={"release_ref": value.release_ref},
        )
        return value, True

    async def execute_ga_assessment(
        self,
        assessment_id: UUID,
        *,
        verifier: GAEvidenceVerifier,
        policy: OperationsPolicy,
    ) -> GAAssessment:
        value = await self._ga_assessment(assessment_id, lock=True)
        if value.state not in {
            GAAssessmentState.QUEUED.value,
            GAAssessmentState.RETRYING.value,
        }:
            return value
        value.state = GAAssessmentState.VERIFYING.value
        value.attempt_count += 1
        value.failure_code = None
        value.verified_at = None
        await self._session.flush()
        evidence = await verifier.verify_release(
            assessment_id=value.id,
            release_ref=value.release_ref,
            artifact_refs=tuple(value.artifact_refs),
            idempotency_key=value.idempotency_key,
        )
        maximum_age_days = await policy.maximum_ga_evidence_age_days()
        if maximum_age_days <= 0 or maximum_age_days > 365:
            raise AppError(
                "GA_EVIDENCE_POLICY_INVALID",
                "GA 증거 유효기간 정책이 올바르지 않습니다.",
                503,
            )
        raw_evidence = [
            {
                "gate": item.gate,
                "passed": item.passed,
                "verified_at": item.verified_at,
                "verifier": item.verifier,
                "source_artifact_ref": item.source_artifact_ref,
                "evidence_hash": item.evidence_hash,
                "metrics": item.metrics,
                "reason_codes": list(item.reason_codes),
            }
            for item in evidence
        ]
        decision = evaluate_ga_evidence(
            raw_evidence,
            now=datetime.now(UTC),
            maximum_evidence_age=timedelta(days=maximum_age_days),
        )
        decisions_by_gate = {
            str(item["gate"]): item for item in decision.decisions
        }
        for item in raw_evidence:
            if (
                not isinstance(item["verifier"], str)
                or not 1 <= len(item["verifier"]) <= 160
                or not isinstance(item["source_artifact_ref"], str)
                or not 1 <= len(item["source_artifact_ref"]) <= 1_000
                or not is_sha256_hex(item["evidence_hash"])
            ):
                raise AppError(
                    "GA_EVIDENCE_HASH_INVALID",
                    "GA Gate 증거 해시가 올바르지 않습니다.",
                    503,
                )
            self._session.add(
                GAGateEvidence(
                    assessment_id=value.id,
                    gate=str(item["gate"]),
                    passed=bool(decisions_by_gate[str(item["gate"])]["passed"]),
                    verified_at=item["verified_at"],
                    verifier=str(item["verifier"]),
                    source_artifact_ref=str(item["source_artifact_ref"]),
                    evidence_hash=str(item["evidence_hash"]),
                    metrics=dict(item["metrics"]),
                    reason_codes=list(item["reason_codes"]),
                )
            )
        value.state = (
            GAAssessmentState.PASSED.value
            if decision.passed
            else GAAssessmentState.BLOCKED.value
        )
        value.verified_at = datetime.now(UTC)
        value.decision_hash = canonical_json_hash(list(decision.decisions))
        await self._session.flush()
        await self._record(
            principal=None,
            action="operations.ga_assessment.finished",
            target_type="ga_assessment",
            target_id=value.id,
            details={
                "release_ref": value.release_ref,
                "state": value.state,
                "decision_hash": value.decision_hash,
            },
        )
        return value

    async def fail_ga_assessment(
        self, assessment_id: UUID, *, code: str
    ) -> GAAssessment:
        value = await self._ga_assessment(assessment_id, lock=True)
        if value.state in {
            GAAssessmentState.QUEUED.value,
            GAAssessmentState.VERIFYING.value,
            GAAssessmentState.RETRYING.value,
        }:
            value.state = GAAssessmentState.FAILED.value
            value.failure_code = code[:120]
            value.verified_at = datetime.now(UTC)
        return value

    async def record_ga_attempt_error(
        self,
        assessment_id: UUID,
        *,
        code: str,
        retry: bool,
        retry_exhausted: bool,
    ) -> tuple[GAAssessment, bool]:
        value = await self._ga_assessment(assessment_id, lock=True)
        if value.state not in {
            GAAssessmentState.QUEUED.value,
            GAAssessmentState.VERIFYING.value,
            GAAssessmentState.RETRYING.value,
        }:
            return value, False
        if value.state != GAAssessmentState.VERIFYING.value:
            value.attempt_count += 1
        value.failure_code = _stored_attempt_error(
            code, retry_exhausted=retry_exhausted
        )
        value.decision_hash = None
        if retry:
            value.state = GAAssessmentState.RETRYING.value
            value.verified_at = None
        else:
            value.state = GAAssessmentState.FAILED.value
            value.verified_at = datetime.now(UTC)
        await self._session.flush()
        await self._record(
            principal=None,
            action=(
                "operations.ga_assessment.retry_scheduled"
                if retry
                else "operations.ga_assessment.failed"
            ),
            target_type="ga_assessment",
            target_id=value.id,
            details={
                "attempt_count": value.attempt_count,
                "failure_code": value.failure_code,
            },
        )
        return value, retry

    async def retry_ga_assessment(
        self, principal: Principal, assessment_id: UUID
    ) -> tuple[GAAssessment, bool]:
        value = await self._ga_assessment(assessment_id, lock=True)
        if value.state in {
            GAAssessmentState.QUEUED.value,
            GAAssessmentState.RETRYING.value,
        }:
            return value, False
        if (
            value.state != GAAssessmentState.FAILED.value
            or not _is_retry_exhausted_failure(value.failure_code)
        ):
            raise AppError(
                "OPERATIONS_JOB_NOT_RETRYABLE",
                "일시 장애로 재시도 한도를 소진한 GA 검증만 재시도할 수 있습니다.",
                409,
            )
        evidence_id = await self._session.scalar(
            select(GAGateEvidence.id).where(GAGateEvidence.assessment_id == value.id)
        )
        if evidence_id is not None:
            raise AppError(
                "OPERATIONS_JOB_NOT_RETRYABLE",
                "증거가 확정된 GA 검증은 동일 작업에서 재시도할 수 없습니다.",
                409,
            )
        value.state = GAAssessmentState.RETRYING.value
        value.failure_code = None
        value.decision_hash = None
        value.verified_at = None
        await self._session.flush()
        await self._record(
            principal=principal,
            action="operations.ga_assessment.retry_requested",
            target_type="ga_assessment",
            target_id=value.id,
            details={"attempt_count": value.attempt_count},
        )
        return value, True

    async def get_ga_assessment(self, assessment_id: UUID) -> GAAssessment:
        return await self._ga_assessment(assessment_id)

    async def list_ga_evidence(self, assessment_id: UUID) -> list[GAGateEvidence]:
        await self._ga_assessment(assessment_id)
        return list(
            await self._session.scalars(
                select(GAGateEvidence)
                .where(GAGateEvidence.assessment_id == assessment_id)
                .order_by(GAGateEvidence.gate)
            )
        )

    async def _ga_assessment(
        self, assessment_id: UUID, *, lock: bool = False
    ) -> GAAssessment:
        query = select(GAAssessment).where(GAAssessment.id == assessment_id)
        if lock:
            query = query.with_for_update()
        value = await self._session.scalar(query)
        if value is None:
            raise AppError("GA_ASSESSMENT_NOT_FOUND", "GA 판정을 찾을 수 없습니다.", 404)
        return value
