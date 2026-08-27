"""Deterministic health, recovery objective, and GA release-gate rules."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from blogops.core.errors import AppError
from blogops.core.serialization import canonical_json_hash
from blogops.domain.operations.enums import (
    GAGate,
    HealthStatus,
    OperationalIncidentState,
)
from blogops.domain.security.rules import is_sha256_hex


def validate_backup_policy(
    *,
    rpo_minutes: int,
    rto_minutes: int,
    backup_interval_minutes: int,
    pitr_enabled: bool,
    encrypted: bool,
    quarterly_drill_required: bool,
) -> None:
    if rpo_minutes <= 0 or rpo_minutes > 15:
        raise AppError(
            code="BACKUP_RPO_POLICY_INVALID",
            message="핵심 데이터 RPO는 15분 이내여야 합니다.",
            status_code=422,
        )
    if rto_minutes <= 0 or rto_minutes > 120:
        raise AppError(
            code="BACKUP_RTO_POLICY_INVALID",
            message="핵심 서비스 RTO는 2시간 이내여야 합니다.",
            status_code=422,
        )
    if backup_interval_minutes <= 0 or backup_interval_minutes > 1_440:
        raise AppError(
            code="BACKUP_INTERVAL_POLICY_INVALID",
            message="암호화된 일일 백업 주기가 필요합니다.",
            status_code=422,
        )
    if not pitr_enabled or not encrypted or not quarterly_drill_required:
        raise AppError(
            code="BACKUP_PROTECTION_REQUIRED",
            message="PITR, 암호화, 분기 복구 훈련은 필수입니다.",
            status_code=422,
        )


def validate_health_observation(
    *,
    status: HealthStatus,
    checked_at: datetime,
    valid_until: datetime,
    evidence_hash: str,
    latency_ms: int | None = None,
) -> None:
    if checked_at.tzinfo is None or valid_until.tzinfo is None:
        raise AppError(
            code="HEALTH_OBSERVATION_TIME_INVALID",
            message="상태 관측 시각은 시간대 정보를 포함해야 합니다.",
            status_code=503,
        )
    if valid_until <= checked_at:
        raise AppError(
            code="HEALTH_OBSERVATION_WINDOW_INVALID",
            message="상태 관측 만료 시각은 검사 시각 이후여야 합니다.",
            status_code=503,
        )
    if not is_sha256_hex(evidence_hash):
        raise AppError(
            code="HEALTH_EVIDENCE_INVALID",
            message="상태 관측에 검증 가능한 증거 해시가 필요합니다.",
            status_code=503,
        )
    if latency_ms is not None and (type(latency_ms) is not int or latency_ms < 0):
        raise AppError(
            code="HEALTH_LATENCY_INVALID",
            message="상태 확인 지연 시간은 음수일 수 없습니다.",
            status_code=503,
        )
    if status == HealthStatus.UNKNOWN:
        raise AppError(
            code="HEALTH_PROBE_INCONCLUSIVE",
            message="상태 확인 결과를 신뢰할 수 없습니다.",
            status_code=503,
        )


def meets_recovery_objectives(
    *,
    data_loss_minutes: int,
    recovery_minutes: int,
    rpo_minutes: int,
    rto_minutes: int,
) -> bool:
    if any(
        type(value) is not int
        for value in (data_loss_minutes, recovery_minutes, rpo_minutes, rto_minutes)
    ):
        raise AppError(
            code="RECOVERY_METRIC_INVALID",
            message="복구 지표는 분 단위 정수여야 합니다.",
            status_code=422,
        )
    if min(data_loss_minutes, recovery_minutes, rpo_minutes, rto_minutes) < 0:
        raise AppError(
            code="RECOVERY_METRIC_INVALID",
            message="복구 지표는 음수일 수 없습니다.",
            status_code=422,
        )
    return data_loss_minutes <= rpo_minutes and recovery_minutes <= rto_minutes


_INCIDENT_TRANSITIONS: dict[str, frozenset[str]] = {
    OperationalIncidentState.INVESTIGATING.value: frozenset(
        {
            OperationalIncidentState.IDENTIFIED.value,
            OperationalIncidentState.MONITORING.value,
            OperationalIncidentState.RESOLVED.value,
        }
    ),
    OperationalIncidentState.IDENTIFIED.value: frozenset(
        {
            OperationalIncidentState.MONITORING.value,
            OperationalIncidentState.RESOLVED.value,
        }
    ),
    OperationalIncidentState.MONITORING.value: frozenset(
        {
            OperationalIncidentState.IDENTIFIED.value,
            OperationalIncidentState.RESOLVED.value,
        }
    ),
    OperationalIncidentState.RESOLVED.value: frozenset(),
}


def ensure_incident_transition(current: str, target: str) -> None:
    if target not in _INCIDENT_TRANSITIONS.get(current, frozenset()):
        raise AppError(
            code="OPERATIONS_INCIDENT_TRANSITION_INVALID",
            message="운영 장애 상태를 해당 상태로 변경할 수 없습니다.",
            status_code=409,
        )


@dataclass(frozen=True, slots=True)
class GAGateDecision:
    passed: bool
    decisions: tuple[dict[str, Any], ...]


def evaluate_ga_evidence(
    evidence: Sequence[Mapping[str, Any]],
    *,
    now: datetime,
    maximum_evidence_age: timedelta,
) -> GAGateDecision:
    by_gate: dict[str, Mapping[str, Any]] = {}
    for item in evidence:
        gate = str(item.get("gate", ""))
        if gate in by_gate:
            raise AppError(
                code="GA_EVIDENCE_DUPLICATE",
                message="동일한 GA Gate 증거가 중복되었습니다.",
                status_code=503,
            )
        by_gate[gate] = item
    required = {gate.value for gate in GAGate}
    if set(by_gate) != required:
        raise AppError(
            code="GA_EVIDENCE_INCOMPLETE",
            message="정식 출시 판정에 필요한 검증 증거가 완전하지 않습니다.",
            status_code=503,
            fields=[
                {"path": "evidence", "reason": f"missing:{gate}"}
                for gate in sorted(required.difference(by_gate))
            ],
        )
    decisions: list[dict[str, Any]] = []
    for gate in GAGate:
        item = by_gate[gate.value]
        verified_at = item.get("verified_at")
        if (
            not isinstance(verified_at, datetime)
            or verified_at.tzinfo is None
            or type(item.get("passed")) is not bool
            or not is_sha256_hex(item.get("evidence_hash"))
            or not isinstance(item.get("metrics"), Mapping)
        ):
            raise AppError(
                code="GA_EVIDENCE_INVALID",
                message="GA 증거의 형식 또는 검증 시각이 올바르지 않습니다.",
                status_code=503,
            )
        fresh = now - maximum_evidence_age <= verified_at <= now
        passed = item["passed"] and fresh
        metrics = dict(item.get("metrics", {}))
        try:
            if gate == GAGate.SECURITY_FINDINGS:
                passed = passed and int(metrics.get("critical", -1)) == 0
                passed = passed and int(metrics.get("high", -1)) == 0
            elif gate == GAGate.TENANT_ISOLATION:
                passed = passed and int(metrics.get("violations", -1)) == 0
            elif gate == GAGate.BILLING_LEDGER:
                passed = passed and str(metrics.get("delta", "missing")) == "0"
            elif gate == GAGate.PUBLISHING_IDEMPOTENCY:
                passed = passed and int(metrics.get("duplicate_posts", -1)) == 0
            elif gate == GAGate.BACKUP_RESTORE:
                rpo_minutes = int(metrics.get("rpo_minutes", 16))
                rto_minutes = int(metrics.get("rto_minutes", 121))
                passed = passed and metrics.get("restore_verified") is True
                passed = passed and 0 <= rpo_minutes <= 15
                passed = passed and 0 <= rto_minutes <= 120
        except (TypeError, ValueError) as exc:
            raise AppError(
                code="GA_EVIDENCE_METRIC_INVALID",
                message="GA Gate 증거 지표가 올바르지 않습니다.",
                status_code=503,
            ) from exc
        decisions.append(
            {
                "gate": gate.value,
                "passed": passed,
                "verified_at": verified_at.isoformat(),
                "evidence_hash": str(item.get("evidence_hash", "")),
                "reason_codes": list(item.get("reason_codes", [])),
            }
        )
    return GAGateDecision(
        passed=all(item["passed"] for item in decisions), decisions=tuple(decisions)
    )
