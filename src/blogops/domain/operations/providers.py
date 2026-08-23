"""External platform operations ports; defaults never synthesize success."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from blogops.core.errors import AppError


class OperationsPolicy(Protocol):
    async def maximum_health_ttl_seconds(self) -> int: ...

    async def maximum_ga_evidence_age_days(self) -> int: ...


@dataclass(frozen=True, slots=True)
class HealthProbeResult:
    status: str
    checked_at: datetime
    valid_until: datetime
    latency_ms: int | None
    safe_metrics: dict[str, Any]
    evidence_hash: str


class ComponentHealthProbe(Protocol):
    async def probe(
        self, *, component_key: str, endpoint_ref: str
    ) -> HealthProbeResult: ...


@dataclass(frozen=True, slots=True)
class BackupExecutionResult:
    provider_run_ref: str
    snapshot_ref: str
    snapshot_hash: str
    encryption_key_version: str
    started_at: datetime
    completed_at: datetime
    restore_point_at: datetime
    size_bytes: int
    verified: bool
    evidence_object_ref: str
    evidence_hash: str


class BackupController(Protocol):
    async def execute_backup(
        self,
        *,
        run_id: UUID,
        policy_snapshot: dict[str, Any],
        idempotency_key: str,
    ) -> BackupExecutionResult: ...


@dataclass(frozen=True, slots=True)
class RecoveryExecutionResult:
    provider_run_ref: str
    isolated_environment_ref: str
    data_loss_minutes: int
    recovery_minutes: int
    integrity_checks: dict[str, Any]
    started_at: datetime
    completed_at: datetime
    evidence_object_ref: str
    evidence_hash: str


class RecoveryController(Protocol):
    async def execute_recovery(
        self,
        *,
        exercise_id: UUID,
        backup_evidence_ref: str,
        runbook_artifact_ref: str,
        idempotency_key: str,
    ) -> RecoveryExecutionResult: ...


@dataclass(frozen=True, slots=True)
class VerifiedGateEvidence:
    gate: str
    passed: bool
    verified_at: datetime
    verifier: str
    source_artifact_ref: str
    evidence_hash: str
    metrics: dict[str, Any]
    reason_codes: tuple[str, ...]


class GAEvidenceVerifier(Protocol):
    async def verify_release(
        self,
        *,
        assessment_id: UUID,
        release_ref: str,
        artifact_refs: tuple[str, ...],
    ) -> tuple[VerifiedGateEvidence, ...]: ...


@dataclass(frozen=True, slots=True)
class StatusNotificationResult:
    provider_message_ref: str
    delivered_at: datetime
    evidence_hash: str


class StatusNotificationAdapter(Protocol):
    async def publish_update(
        self,
        *,
        incident_id: UUID,
        audience: str,
        template_version: str,
        safe_payload: dict[str, Any],
        idempotency_key: str,
    ) -> StatusNotificationResult: ...


class FailClosedOperationsAdapters:
    async def maximum_health_ttl_seconds(self) -> int:
        raise AppError(
            "OPERATIONS_HEALTH_POLICY_UNAVAILABLE",
            "상태 확인 유효기간 정책이 구성되지 않았습니다.",
            503,
        )

    async def maximum_ga_evidence_age_days(self) -> int:
        raise AppError(
            "OPERATIONS_GA_POLICY_UNAVAILABLE",
            "GA 증거 유효기간 정책이 구성되지 않았습니다.",
            503,
        )

    async def probe(self, **_kwargs: Any) -> HealthProbeResult:
        raise AppError(
            "HEALTH_PROBE_UNAVAILABLE",
            "상태 확인 어댑터가 구성되지 않았습니다.",
            503,
        )

    async def execute_backup(self, **_kwargs: Any) -> BackupExecutionResult:
        raise AppError(
            "BACKUP_CONTROLLER_UNAVAILABLE",
            "백업 실행기가 구성되지 않았습니다.",
            503,
        )

    async def execute_recovery(self, **_kwargs: Any) -> RecoveryExecutionResult:
        raise AppError(
            "RECOVERY_CONTROLLER_UNAVAILABLE",
            "복구 실행기가 구성되지 않았습니다.",
            503,
        )

    async def verify_release(self, **_kwargs: Any) -> tuple[VerifiedGateEvidence, ...]:
        raise AppError(
            "GA_EVIDENCE_VERIFIER_UNAVAILABLE",
            "GA 증거 검증기가 구성되지 않았습니다.",
            503,
        )

    async def publish_update(self, **_kwargs: Any) -> StatusNotificationResult:
        raise AppError(
            "STATUS_NOTIFICATION_UNAVAILABLE",
            "상태 공지 제공자가 구성되지 않았습니다.",
            503,
        )
