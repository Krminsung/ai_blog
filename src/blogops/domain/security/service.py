"""Security, privacy, legal-hold, copyright, and incident workflows."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.core.context import Principal, request_id_context
from blogops.core.errors import AppError
from blogops.db.session import apply_workspace_scope
from blogops.domain.security.enums import (
    ComplianceDecision,
    CopyrightCaseState,
    CopyrightEventKind,
    DataClass,
    LegalHoldEventKind,
    LegalHoldState,
    PrivacyActionKind,
    PrivacyActionState,
    PrivacyRequestKind,
    PrivacyRequestState,
    RetentionSweepState,
    SecurityIncidentEventKind,
    SecurityIncidentState,
    VerificationOutcome,
)
from blogops.domain.security.models import (
    BackupErasureEvidence,
    BreachNotification,
    ComplianceAssessment,
    CopyrightCase,
    CopyrightCaseEvent,
    CopyrightCounterNotice,
    DeletionCertificate,
    LegalHold,
    LegalHoldEvent,
    PrivacyAccessEvent,
    PrivacyAction,
    PrivacyActionAttempt,
    PrivacyConsentEvidence,
    PrivacyExportArtifact,
    PrivacyRequest,
    PrivacyVerificationEvent,
    ProviderDeletionEvent,
    RetentionDispositionEvidence,
    RetentionPolicyVersion,
    RetentionSweep,
    SecurityIncident,
    SecurityIncidentEvent,
    SubprocessorVersion,
)
from blogops.domain.security.providers import (
    ComplianceEvidenceVerifier,
    CopyrightEnforcementAdapter,
    DataRightsExecutor,
    DataRightsPlanner,
    DataRightsPolicy,
    DownloadGrant,
    IncidentNotificationAdapter,
    RetentionExecutor,
    SecurityIncidentPolicy,
    SubjectIdentityVerifier,
    VerifiedDeletionWebhook,
)
from blogops.domain.security.rules import (
    append_evidence_hash,
    authorize_export_download,
    canonical_json_hash,
    ensure_copyright_transition,
    ensure_privacy_transition,
    ensure_security_incident_transition,
    is_sha256_hex,
    privacy_completion_state,
    redact_safe_metadata,
    redact_safe_text,
    require_secret_reference,
    validate_action_plan,
    validate_retention_rules,
    validate_secure_download_url,
)
from blogops.domain.security.schemas import (
    BackupErasureEvidenceCreate,
    ComplianceAssessmentCreate,
    CopyrightCounterNoticeCreate,
    CopyrightDecision,
    CopyrightNoticeCreate,
    LegalHoldCreate,
    LegalHoldRelease,
    PrivacyConsentCreate,
    PrivacyRequestCreate,
    RetentionPolicyCreate,
    SecurityIncidentCreate,
    SecurityIncidentEventCreate,
    SecurityIncidentNotify,
    SubprocessorVersionCreate,
)
from blogops.services.advisory_locks import (
    acquire_creation_guard,
    acquire_transaction_advisory_lock,
    creation_guard_key,
)
from blogops.services.audit import append_audit_log
from blogops.services.outbox import add_outbox_event

_SCHEMA_VERSION = "1.0"
_creation_guard_key = creation_guard_key


def _same_request(existing_hash: str, request_hash: str) -> None:
    if existing_hash != request_hash:
        raise AppError(
            code="IDEMPOTENCY_KEY_REUSED",
            message="같은 Idempotency-Key가 다른 요청에 사용되었습니다.",
            status_code=409,
        )


class SecurityService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _scope(self, workspace_id: UUID) -> None:
        await apply_workspace_scope(self._session, workspace_id)

    async def _lock_creation_guard(
        self, namespace: str, *identity: object
    ) -> None:
        await acquire_creation_guard(self._session, namespace, *identity)

    async def _lock_legal_hold_guard(self, workspace_id: UUID) -> None:
        await acquire_transaction_advisory_lock(
            self._session,
            f"security-legal-hold:{workspace_id}",
        )

    async def _lock_consent_guard(
        self, workspace_id: UUID, subject_id: UUID, purpose: str
    ) -> None:
        await acquire_transaction_advisory_lock(
            self._session,
            f"security-consent:{workspace_id}:{subject_id}:{purpose}",
        )

    async def _record(
        self,
        *,
        principal: Principal,
        action: str,
        target_type: str,
        target_id: UUID,
        details: dict[str, Any],
    ) -> None:
        safe_details = redact_safe_metadata(details)
        await append_audit_log(
            self._session,
            workspace_id=principal.workspace_id,
            actor_id=principal.subject_id,
            action=action,
            target_type=target_type,
            target_id=str(target_id),
            details=safe_details,
        )
        await add_outbox_event(
            self._session,
            workspace_id=principal.workspace_id,
            aggregate_type=target_type,
            aggregate_id=str(target_id),
            event_type=action,
            schema_version=_SCHEMA_VERSION,
            payload={
                "workspace_id": str(principal.workspace_id),
                "actor_id": str(principal.subject_id),
                "aggregate_id": str(target_id),
                **safe_details,
            },
        )

    async def create_retention_policy(
        self,
        principal: Principal,
        data: RetentionPolicyCreate,
        *,
        policy: DataRightsPolicy,
    ) -> RetentionPolicyVersion:
        await self._scope(principal.workspace_id)
        minimum, maximum = await policy.retention_bounds(principal.workspace_id)
        validate_retention_rules(data.rules, minimum_days=minimum, maximum_days=maximum)
        payload = data.model_dump(mode="json")
        value = RetentionPolicyVersion(
            workspace_id=principal.workspace_id,
            version=data.version,
            rules=data.rules,
            data_region=data.data_region,
            cross_border_policy=data.cross_border_policy,
            backup_erasure_policy=data.backup_erasure_policy,
            legal_basis_snapshot=data.legal_basis_snapshot,
            policy_hash=canonical_json_hash(payload),
            effective_at=data.effective_at,
            retired_at=data.retired_at,
            created_by=principal.subject_id,
        )
        self._session.add(value)
        await self._session.flush()
        await self._record(
            principal=principal,
            action="security.retention_policy.created",
            target_type="retention_policy_version",
            target_id=value.id,
            details={"version": value.version, "policy_hash": value.policy_hash},
        )
        return value

    async def list_retention_policies(
        self, principal: Principal
    ) -> list[RetentionPolicyVersion]:
        await self._scope(principal.workspace_id)
        return list(
            await self._session.scalars(
                select(RetentionPolicyVersion)
                .where(RetentionPolicyVersion.workspace_id == principal.workspace_id)
                .order_by(RetentionPolicyVersion.version.desc())
            )
        )

    async def create_retention_sweep(
        self,
        principal: Principal,
        *,
        idempotency_key: str,
    ) -> tuple[RetentionSweep, bool]:
        await self._scope(principal.workspace_id)
        await self._lock_creation_guard(
            "retention-sweep-idempotency",
            principal.workspace_id,
            idempotency_key,
        )
        await self._lock_legal_hold_guard(principal.workspace_id)
        existing = await self._session.scalar(
            select(RetentionSweep).where(
                RetentionSweep.workspace_id == principal.workspace_id,
                RetentionSweep.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            return existing, False
        policy = await self._current_retention_policy(principal.workspace_id)
        policy_snapshot = {
            "id": str(policy.id),
            "version": policy.version,
            "rules": policy.rules,
            "data_region": policy.data_region,
            "cross_border_policy": policy.cross_border_policy,
            "backup_erasure_policy": policy.backup_erasure_policy,
            "legal_basis_snapshot": policy.legal_basis_snapshot,
            "policy_hash": policy.policy_hash,
            "effective_at": policy.effective_at.isoformat(),
            "retired_at": (
                None if policy.retired_at is None else policy.retired_at.isoformat()
            ),
        }
        hold_snapshot = await self._active_legal_hold_snapshot(
            principal.workspace_id
        )
        value = RetentionSweep(
            workspace_id=principal.workspace_id,
            policy_version_id=policy.id,
            policy_snapshot=policy_snapshot,
            policy_snapshot_hash=canonical_json_hash(policy_snapshot),
            legal_hold_snapshot=hold_snapshot,
            legal_hold_snapshot_hash=canonical_json_hash(hold_snapshot),
            idempotency_key=idempotency_key,
            requested_by=principal.subject_id,
        )
        self._session.add(value)
        await self._session.flush()
        await self._record(
            principal=principal,
            action="security.retention_sweep.queued",
            target_type="retention_sweep",
            target_id=value.id,
            details={
                "policy_version": policy.version,
                "policy_snapshot_hash": value.policy_snapshot_hash,
                "legal_hold_snapshot_hash": value.legal_hold_snapshot_hash,
                "active_legal_hold_count": len(hold_snapshot),
            },
        )
        return value, True

    async def execute_retention_sweep(
        self,
        *,
        workspace_id: UUID,
        sweep_id: UUID,
        executor: RetentionExecutor,
    ) -> RetentionSweep:
        await self._scope(workspace_id)
        await self._lock_legal_hold_guard(workspace_id)
        value = await self._retention_sweep(workspace_id, sweep_id, lock=True)
        if value.state != RetentionSweepState.QUEUED.value:
            return value
        current_policy = await self._current_retention_policy(workspace_id)
        if current_policy.id != value.policy_version_id:
            raise AppError(
                "RETENTION_POLICY_CHANGED",
                "보존 정책이 변경되어 만료 작업을 새 정책으로 다시 생성해야 합니다.",
                409,
            )
        current_hold_snapshot = await self._active_legal_hold_snapshot(
            workspace_id, lock=True
        )
        if canonical_json_hash(current_hold_snapshot) != value.legal_hold_snapshot_hash:
            raise AppError(
                "RETENTION_LEGAL_HOLD_CHANGED",
                "Legal Hold 범위가 변경되어 보존 만료 작업을 안전하게 실행할 수 없습니다.",
                409,
            )
        value.state = RetentionSweepState.RUNNING.value
        value.started_at = datetime.now(UTC)
        await self._session.flush()
        results = await executor.execute_retention_sweep(
            workspace_id=workspace_id,
            sweep_id=value.id,
            policy_snapshot=value.policy_snapshot,
            legal_hold_snapshot=tuple(value.legal_hold_snapshot),
            idempotency_key=value.idempotency_key,
        )
        expected_classes = {item.value for item in DataClass}
        covered_classes: set[str] = set()
        targets: set[tuple[str, str]] = set()
        for result in results:
            target = (result.data_class, result.target_system)
            rule = value.policy_snapshot.get("rules", {}).get(result.data_class)
            if (
                result.data_class not in expected_classes
                or not isinstance(rule, dict)
                or result.disposition != rule.get("disposition")
                or not result.target_system
                or len(result.target_system) > 120
                or target in targets
                or type(result.affected_records) is not int
                or result.affected_records < 0
                or type(result.held_records) is not int
                or result.held_records < 0
                or type(result.passed) is not bool
                or not result.evidence_object_ref
                or len(result.evidence_object_ref) > 1_000
                or not is_sha256_hex(result.evidence_hash)
                or result.cutoff_at.tzinfo is None
                or result.completed_at.tzinfo is None
                or result.cutoff_at > result.completed_at
                or result.completed_at > datetime.now(UTC) + timedelta(minutes=5)
            ):
                raise AppError(
                    "RETENTION_EVIDENCE_INVALID",
                    "보존 만료 실행 증거가 서버 정책과 일치하지 않습니다.",
                    503,
                )
            covered_classes.add(result.data_class)
            targets.add(target)
            self._session.add(
                RetentionDispositionEvidence(
                    workspace_id=workspace_id,
                    sweep_id=value.id,
                    data_class=result.data_class,
                    target_system=result.target_system,
                    cutoff_at=result.cutoff_at,
                    disposition=result.disposition,
                    affected_records=result.affected_records,
                    held_records=result.held_records,
                    passed=result.passed,
                    evidence_object_ref=result.evidence_object_ref,
                    evidence_hash=result.evidence_hash,
                    completed_at=result.completed_at,
                )
            )
        if covered_classes != expected_classes:
            raise AppError(
                "RETENTION_EVIDENCE_INCOMPLETE",
                "보존 만료 실행 증거가 모든 데이터 유형을 포함하지 않습니다.",
                503,
            )
        value.state = (
            RetentionSweepState.SUCCEEDED.value
            if all(result.passed for result in results)
            else RetentionSweepState.PARTIAL.value
        )
        value.completed_at = datetime.now(UTC)
        await self._session.flush()
        await self._record(
            principal=worker_principal(workspace_id),
            action="security.retention_sweep.completed",
            target_type="retention_sweep",
            target_id=value.id,
            details={
                "state": value.state,
                "evidence_count": len(results),
                "policy_snapshot_hash": value.policy_snapshot_hash,
                "legal_hold_snapshot_hash": value.legal_hold_snapshot_hash,
            },
        )
        return value

    async def fail_retention_sweep(
        self, *, workspace_id: UUID, sweep_id: UUID, code: str
    ) -> RetentionSweep:
        value = await self._retention_sweep(workspace_id, sweep_id, lock=True)
        if value.state in {
            RetentionSweepState.QUEUED.value,
            RetentionSweepState.RUNNING.value,
        }:
            value.state = RetentionSweepState.FAILED.value
            value.failure_code = code[:120]
            value.completed_at = datetime.now(UTC)
            await self._session.flush()
            await self._record(
                principal=worker_principal(workspace_id),
                action="security.retention_sweep.failed",
                target_type="retention_sweep",
                target_id=value.id,
                details={"failure_code": value.failure_code},
            )
        return value

    async def get_retention_sweep(
        self, principal: Principal, sweep_id: UUID
    ) -> RetentionSweep:
        return await self._retention_sweep(principal.workspace_id, sweep_id)

    async def list_retention_sweeps(
        self, principal: Principal
    ) -> list[RetentionSweep]:
        await self._scope(principal.workspace_id)
        return list(
            await self._session.scalars(
                select(RetentionSweep)
                .where(RetentionSweep.workspace_id == principal.workspace_id)
                .order_by(RetentionSweep.created_at.desc())
            )
        )

    async def list_retention_disposition_evidence(
        self, principal: Principal, sweep_id: UUID
    ) -> list[RetentionDispositionEvidence]:
        await self.get_retention_sweep(principal, sweep_id)
        return list(
            await self._session.scalars(
                select(RetentionDispositionEvidence)
                .where(
                    RetentionDispositionEvidence.workspace_id
                    == principal.workspace_id,
                    RetentionDispositionEvidence.sweep_id == sweep_id,
                )
                .order_by(
                    RetentionDispositionEvidence.data_class,
                    RetentionDispositionEvidence.target_system,
                )
            )
        )

    async def _active_legal_hold_snapshot(
        self, workspace_id: UUID, *, lock: bool = False
    ) -> list[dict[str, Any]]:
        now = datetime.now(UTC)
        query = (
            select(LegalHold)
            .where(
                LegalHold.workspace_id == workspace_id,
                LegalHold.state == LegalHoldState.ACTIVE.value,
                (LegalHold.expires_at.is_(None)) | (LegalHold.expires_at > now),
            )
            .order_by(LegalHold.activated_at, LegalHold.id)
        )
        if lock:
            query = query.with_for_update()
        holds = list(await self._session.scalars(query))
        return [
            {
                "id": str(hold.id),
                "state": hold.state,
                "scope_snapshot": hold.scope_snapshot,
                "scope_hash": hold.scope_hash,
                "activated_at": hold.activated_at.isoformat(),
                "expires_at": (
                    None if hold.expires_at is None else hold.expires_at.isoformat()
                ),
            }
            for hold in holds
        ]

    async def _retention_sweep(
        self, workspace_id: UUID, sweep_id: UUID, *, lock: bool = False
    ) -> RetentionSweep:
        await self._scope(workspace_id)
        query = select(RetentionSweep).where(
            RetentionSweep.workspace_id == workspace_id,
            RetentionSweep.id == sweep_id,
        )
        if lock:
            query = query.with_for_update()
        value = await self._session.scalar(query)
        if value is None:
            raise AppError(
                "RETENTION_SWEEP_NOT_FOUND", "보존 만료 작업을 찾을 수 없습니다.", 404
            )
        return value

    async def create_legal_hold(
        self, principal: Principal, data: LegalHoldCreate
    ) -> LegalHold:
        await self._scope(principal.workspace_id)
        await self._lock_legal_hold_guard(principal.workspace_id)
        scope = data.scope_snapshot
        subject_hashes = scope.get("subject_locator_hashes", [])
        data_classes = scope.get("data_classes", [])
        target_refs = scope.get("target_refs", [])
        if (
            not isinstance(subject_hashes, list)
            or not all(is_sha256_hex(item) for item in subject_hashes)
            or not isinstance(data_classes, list)
            or not all(
                isinstance(item, str) and item in {value.value for value in DataClass}
                for item in data_classes
            )
            or not isinstance(target_refs, list)
            or not all(isinstance(item, (str, dict)) and bool(item) for item in target_refs)
            or not (
                scope.get("all_workspace") is True
                or subject_hashes
                or data_classes
                or target_refs
            )
        ):
            raise AppError(
                "LEGAL_HOLD_SCOPE_REQUIRED",
                "Legal Hold에는 해시 또는 데이터 유형으로 검증된 보존 범위가 필요합니다.",
                422,
            )
        if not all(
            isinstance(item, str) and 1 <= len(item) <= 1_000
            for item in data.evidence_object_refs
        ):
            raise AppError(
                "LEGAL_HOLD_EVIDENCE_INVALID",
                "Legal Hold 증거 참조가 올바르지 않습니다.",
                422,
            )
        now = datetime.now(UTC)
        if data.expires_at is not None and data.expires_at <= now:
            raise AppError(
                "LEGAL_HOLD_EXPIRY_INVALID", "Legal Hold 만료 시각은 미래여야 합니다.", 422
            )
        value = LegalHold(
            workspace_id=principal.workspace_id,
            external_matter_ref=data.external_matter_ref,
            title=data.title,
            reason=data.reason,
            scope_snapshot=data.scope_snapshot,
            scope_hash=canonical_json_hash(data.scope_snapshot),
            evidence_object_refs=list(dict.fromkeys(data.evidence_object_refs)),
            activated_by=principal.subject_id,
            activated_at=now,
            expires_at=data.expires_at,
        )
        self._session.add(value)
        await self._session.flush()
        await self._append_legal_hold_event(
            value,
            actor_id=principal.subject_id,
            kind=LegalHoldEventKind.ACTIVATED,
            reason=data.reason,
            evidence_hash=canonical_json_hash(data.evidence_object_refs),
        )
        await self._record(
            principal=principal,
            action="security.legal_hold.activated",
            target_type="legal_hold",
            target_id=value.id,
            details={
                "scope_hash": value.scope_hash,
                "external_matter_ref": value.external_matter_ref,
            },
        )
        return value

    async def release_legal_hold(
        self,
        principal: Principal,
        hold_id: UUID,
        data: LegalHoldRelease,
    ) -> LegalHold:
        await self._scope(principal.workspace_id)
        await self._lock_legal_hold_guard(principal.workspace_id)
        hold = await self._legal_hold(principal.workspace_id, hold_id, lock=True)
        if hold.state != LegalHoldState.ACTIVE.value:
            raise AppError("LEGAL_HOLD_NOT_ACTIVE", "활성 Legal Hold가 아닙니다.", 409)
        if hold.activated_by == principal.subject_id:
            raise AppError(
                "LEGAL_HOLD_SEPARATION_OF_DUTIES",
                "Legal Hold를 설정한 사용자는 같은 Hold를 해제할 수 없습니다.",
                409,
            )
        hold.state = LegalHoldState.RELEASED.value
        hold.released_by = principal.subject_id
        hold.released_at = datetime.now(UTC)
        hold.release_reason = data.reason
        await self._append_legal_hold_event(
            hold,
            actor_id=principal.subject_id,
            kind=LegalHoldEventKind.RELEASED,
            reason=data.reason,
            evidence_hash=data.evidence_hash,
        )
        await self._record(
            principal=principal,
            action="security.legal_hold.released",
            target_type="legal_hold",
            target_id=hold.id,
            details={"scope_hash": hold.scope_hash},
        )
        return hold

    async def list_legal_holds(self, principal: Principal) -> list[LegalHold]:
        await self._scope(principal.workspace_id)
        return list(
            await self._session.scalars(
                select(LegalHold)
                .where(LegalHold.workspace_id == principal.workspace_id)
                .order_by(LegalHold.activated_at.desc())
            )
        )

    async def list_legal_hold_events(
        self, principal: Principal, hold_id: UUID
    ) -> list[LegalHoldEvent]:
        await self._legal_hold(principal.workspace_id, hold_id)
        return list(
            await self._session.scalars(
                select(LegalHoldEvent)
                .where(
                    LegalHoldEvent.workspace_id == principal.workspace_id,
                    LegalHoldEvent.hold_id == hold_id,
                )
                .order_by(LegalHoldEvent.sequence)
            )
        )

    async def _legal_hold(
        self, workspace_id: UUID, hold_id: UUID, *, lock: bool = False
    ) -> LegalHold:
        await self._scope(workspace_id)
        query = select(LegalHold).where(
            LegalHold.workspace_id == workspace_id, LegalHold.id == hold_id
        )
        if lock:
            query = query.with_for_update()
        value = await self._session.scalar(query)
        if value is None:
            raise AppError("LEGAL_HOLD_NOT_FOUND", "Legal Hold를 찾을 수 없습니다.", 404)
        return value

    async def _append_legal_hold_event(
        self,
        hold: LegalHold,
        *,
        actor_id: UUID,
        kind: LegalHoldEventKind,
        reason: str,
        evidence_hash: str,
    ) -> LegalHoldEvent:
        previous = await self._session.scalar(
            select(LegalHoldEvent)
            .where(
                LegalHoldEvent.workspace_id == hold.workspace_id,
                LegalHoldEvent.hold_id == hold.id,
            )
            .order_by(LegalHoldEvent.sequence.desc())
            .limit(1)
        )
        sequence = 1 if previous is None else previous.sequence + 1
        payload = {
            "hold_id": str(hold.id),
            "sequence": sequence,
            "kind": kind.value,
            "actor_id": str(actor_id),
            "reason": reason,
            "evidence_hash": evidence_hash,
        }
        value = LegalHoldEvent(
            workspace_id=hold.workspace_id,
            hold_id=hold.id,
            sequence=sequence,
            kind=kind.value,
            actor_id=actor_id,
            reason=reason,
            evidence_hash=evidence_hash,
            previous_event_hash=None if previous is None else previous.event_hash,
            event_hash=append_evidence_hash(
                None if previous is None else previous.event_hash, payload
            ),
        )
        self._session.add(value)
        await self._session.flush()
        return value

    async def create_privacy_request(
        self,
        principal: Principal,
        data: PrivacyRequestCreate,
        *,
        idempotency_key: str,
        policy: DataRightsPolicy,
        source: str = "USER",
        external_request_ref: str | None = None,
    ) -> tuple[PrivacyRequest, bool]:
        await self._scope(principal.workspace_id)
        require_secret_reference(data.subject_locator_ref, path="subject_locator_ref")
        if data.requested_correction_ref is not None:
            require_secret_reference(
                data.requested_correction_ref, path="requested_correction_ref"
            )
        request_payload = {
            "request": data.model_dump(mode="json"),
            "source": source,
            "external_request_ref": external_request_ref,
        }
        request_hash = canonical_json_hash(request_payload)
        await self._lock_creation_guard(
            "privacy-request-idempotency",
            principal.workspace_id,
            principal.subject_id,
            data.kind.value,
            idempotency_key,
        )
        if external_request_ref is not None:
            await self._lock_creation_guard(
                "privacy-request-external",
                principal.workspace_id,
                source,
                external_request_ref,
            )
        existing = await self._session.scalar(
            select(PrivacyRequest).where(
                PrivacyRequest.workspace_id == principal.workspace_id,
                PrivacyRequest.requested_by == principal.subject_id,
                PrivacyRequest.kind == data.kind.value,
                PrivacyRequest.idempotency_key == idempotency_key,
            )
        )
        if existing is None and external_request_ref is not None:
            existing = await self._session.scalar(
                select(PrivacyRequest).where(
                    PrivacyRequest.workspace_id == principal.workspace_id,
                    PrivacyRequest.source == source,
                    PrivacyRequest.external_request_ref == external_request_ref,
                )
            )
        if existing is not None:
            _same_request(existing.request_hash, request_hash)
            return existing, False
        retention = await self._current_retention_policy(principal.workspace_id)
        sla_days = await policy.request_sla_days(
            workspace_id=principal.workspace_id, request_kind=data.kind.value
        )
        if sla_days <= 0 or sla_days > 365:
            raise AppError(
                "DATA_RIGHTS_SLA_INVALID", "데이터 권리 요청 SLA 정책이 올바르지 않습니다.", 503
            )
        data_classes = sorted({item.value for item in data.data_classes})
        retention_snapshot = {
            "id": str(retention.id),
            "version": retention.version,
            "rules": retention.rules,
            "data_region": retention.data_region,
            "cross_border_policy": retention.cross_border_policy,
            "backup_erasure_policy": retention.backup_erasure_policy,
            "policy_hash": retention.policy_hash,
        }
        value = PrivacyRequest(
            workspace_id=principal.workspace_id,
            requested_by=principal.subject_id,
            kind=data.kind.value,
            source=source,
            external_request_ref=external_request_ref,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            subject_locator_ref=data.subject_locator_ref,
            subject_locator_hash=canonical_json_hash(data.subject_locator_ref),
            data_classes=data_classes,
            requested_correction_ref=data.requested_correction_ref,
            requester_relationship=data.requester_relationship,
            retention_policy_version_id=retention.id,
            retention_policy_snapshot=retention_snapshot,
            due_at=datetime.now(UTC) + timedelta(days=sla_days),
        )
        self._session.add(value)
        await self._session.flush()
        await self._record(
            principal=principal,
            action="security.privacy_request.received",
            target_type="privacy_request",
            target_id=value.id,
            details={"kind": value.kind, "data_classes": data_classes, "due_at": value.due_at},
        )
        return value, True

    async def accept_provider_deletion(
        self,
        *,
        provider: str,
        body: bytes,
        verified: VerifiedDeletionWebhook,
        policy: DataRightsPolicy,
        planner: DataRightsPlanner,
    ) -> tuple[PrivacyRequest, bool]:
        await self._scope(verified.workspace_id)
        require_secret_reference(
            verified.subject_locator_ref, path="verified.subject_locator_ref"
        )
        if (
            not verified.provider_event_id
            or len(verified.provider_event_id) > 400
            or not verified.signature_key_version
            or len(verified.signature_key_version) > 80
            or not verified.assurance_level
            or len(verified.assurance_level) > 80
            or verified.occurred_at.tzinfo is None
            or verified.occurred_at > datetime.now(UTC) + timedelta(minutes=5)
            or not is_sha256_hex(verified.evidence_hash)
        ):
            raise AppError(
                "DATA_DELETION_WEBHOOK_EVIDENCE_INVALID",
                "플랫폼 삭제 요청 검증 증거가 올바르지 않습니다.",
                503,
            )
        raw_payload_hash = hashlib.sha256(body).hexdigest()
        await self._lock_creation_guard(
            "provider-deletion-event",
            "global",
            provider,
            verified.provider_event_id,
        )
        existing_event = await self._session.scalar(
            select(ProviderDeletionEvent).where(
                ProviderDeletionEvent.provider == provider,
                ProviderDeletionEvent.provider_event_id == verified.provider_event_id,
            )
        )
        if existing_event is not None:
            if existing_event.raw_payload_hash != raw_payload_hash:
                raise AppError(
                    "DATA_DELETION_WEBHOOK_REPLAY_MISMATCH",
                    "같은 플랫폼 이벤트 ID의 Payload가 다릅니다.",
                    409,
                )
            return (
                await self._privacy_request(
                    verified.workspace_id, existing_event.privacy_request_id
                ),
                False,
            )
        if not verified.data_classes:
            raise AppError(
                "DATA_DELETION_WEBHOOK_SCOPE_INVALID",
                "플랫폼 삭제 요청의 데이터 범위가 비어 있습니다.",
                503,
            )
        try:
            data_classes = [DataClass(value) for value in verified.data_classes]
        except ValueError as exc:
            raise AppError(
                "DATA_DELETION_WEBHOOK_SCOPE_INVALID",
                "플랫폼 삭제 요청의 데이터 범위가 올바르지 않습니다.",
                503,
            ) from exc
        principal = worker_principal(verified.workspace_id)
        request, created = await self.create_privacy_request(
            principal,
            PrivacyRequestCreate(
                kind=PrivacyRequestKind.DELETE,
                subject_locator_ref=verified.subject_locator_ref,
                data_classes=data_classes,
                requester_relationship="VERIFIED_PROVIDER",
            ),
            idempotency_key=(
                f"provider:{provider}:"
                f"{canonical_json_hash(verified.provider_event_id)[:32]}"
            ),
            policy=policy,
            source="PROVIDER_WEBHOOK",
            external_request_ref=f"{provider}:{verified.provider_event_id}",
        )
        ensure_privacy_transition(request.state, PrivacyRequestState.VERIFIED.value)
        request.state = PrivacyRequestState.VERIFIED.value
        request.verified_at = verified.occurred_at
        self._session.add(
            PrivacyVerificationEvent(
                workspace_id=verified.workspace_id,
                request_id=request.id,
                outcome=VerificationOutcome.PASSED.value,
                provider_reference=f"{provider}:{verified.provider_event_id}",
                assurance_level=verified.assurance_level,
                evidence_hash=verified.evidence_hash,
                verified_at=verified.occurred_at,
            )
        )
        self._session.add(
            ProviderDeletionEvent(
                workspace_id=verified.workspace_id,
                privacy_request_id=request.id,
                provider=provider,
                provider_event_id=verified.provider_event_id,
                raw_payload_hash=raw_payload_hash,
                signature_key_version=verified.signature_key_version,
                subject_locator_hash=canonical_json_hash(verified.subject_locator_ref),
                data_classes=sorted({item.value for item in data_classes}),
                assurance_level=verified.assurance_level,
                evidence_hash=verified.evidence_hash,
                occurred_at=verified.occurred_at,
            )
        )
        await self._session.flush()
        request = await self.plan_privacy_request(
            principal, request.id, planner=planner
        )
        return request, created

    async def verify_privacy_request(
        self,
        principal: Principal,
        request_id: UUID,
        *,
        verification_token: str,
        verifier: SubjectIdentityVerifier,
    ) -> PrivacyRequest:
        value = await self._privacy_request(principal.workspace_id, request_id, lock=True)
        if (
            value.requested_by != principal.subject_id
            and "privacy:manage" not in principal.permissions
        ):
            raise AppError("PRIVACY_REQUEST_ACCESS_DENIED", "요청을 확인할 권한이 없습니다.", 403)
        if value.state != PrivacyRequestState.IDENTITY_PENDING.value:
            raise AppError(
                "PRIVACY_VERIFICATION_NOT_PENDING", "신원 확인 대기 중인 요청이 아닙니다.", 409
            )
        result = await verifier.verify_subject(
            workspace_id=principal.workspace_id,
            request_id=value.id,
            subject_locator_ref=value.subject_locator_ref,
            verification_token=verification_token,
        )
        if (
            result.verified_at.tzinfo is None
            or result.verified_at < value.created_at
            or result.verified_at > datetime.now(UTC) + timedelta(minutes=5)
            or not result.provider_reference
            or len(result.provider_reference) > 500
            or not result.assurance_level
            or len(result.assurance_level) > 80
            or not is_sha256_hex(result.evidence_hash)
            or (result.passed and result.failure_code is not None)
            or (
                not result.passed
                and (not result.failure_code or len(result.failure_code) > 120)
            )
        ):
            raise AppError(
                "PRIVACY_VERIFICATION_EVIDENCE_INVALID",
                "신원 확인 제공자의 증거가 올바르지 않습니다.",
                503,
            )
        event = PrivacyVerificationEvent(
            workspace_id=value.workspace_id,
            request_id=value.id,
            outcome=(
                VerificationOutcome.PASSED.value
                if result.passed
                else VerificationOutcome.FAILED.value
            ),
            provider_reference=result.provider_reference,
            assurance_level=result.assurance_level,
            evidence_hash=result.evidence_hash,
            failure_code=result.failure_code,
            verified_at=result.verified_at,
        )
        self._session.add(event)
        if result.passed:
            ensure_privacy_transition(value.state, PrivacyRequestState.VERIFIED.value)
            value.state = PrivacyRequestState.VERIFIED.value
            value.verified_at = result.verified_at
        await self._session.flush()
        await self._record(
            principal=principal,
            action="security.privacy_request.identity_checked",
            target_type="privacy_request",
            target_id=value.id,
            details={"outcome": event.outcome, "assurance_level": event.assurance_level},
        )
        return value

    async def plan_privacy_request(
        self,
        principal: Principal,
        request_id: UUID,
        *,
        planner: DataRightsPlanner,
    ) -> PrivacyRequest:
        value = await self._privacy_request(principal.workspace_id, request_id, lock=True)
        if value.state not in {
            PrivacyRequestState.VERIFIED.value,
            PrivacyRequestState.ON_HOLD.value,
            PrivacyRequestState.FAILED.value,
            PrivacyRequestState.PARTIAL.value,
        }:
            raise AppError("PRIVACY_REQUEST_NOT_PLANNABLE", "실행 계획을 만들 수 없는 상태입니다.", 409)
        if value.kind == PrivacyRequestKind.DELETE.value:
            holds = await self._matching_legal_holds(value)
            if holds:
                if value.state != PrivacyRequestState.ON_HOLD.value:
                    ensure_privacy_transition(value.state, PrivacyRequestState.ON_HOLD.value)
                    value.state = PrivacyRequestState.ON_HOLD.value
                await self._record(
                    principal=principal,
                    action="security.privacy_request.held",
                    target_type="privacy_request",
                    target_id=value.id,
                    details={"legal_hold_ids": [str(item.id) for item in holds]},
                )
                return value
        existing_actions = list(
            await self._session.scalars(
                select(PrivacyAction).where(
                    PrivacyAction.workspace_id == value.workspace_id,
                    PrivacyAction.request_id == value.id,
                )
            )
        )
        if existing_actions:
            if value.state in {
                PrivacyRequestState.FAILED.value,
                PrivacyRequestState.PARTIAL.value,
                PrivacyRequestState.ON_HOLD.value,
            }:
                ensure_privacy_transition(value.state, PrivacyRequestState.QUEUED.value)
                value.state = PrivacyRequestState.QUEUED.value
                for action in existing_actions:
                    if action.state in {
                        PrivacyActionState.FAILED.value,
                        PrivacyActionState.SKIPPED_LEGAL_HOLD.value,
                    }:
                        action.state = PrivacyActionState.PENDING.value
                        action.last_error_code = None
                return value
            return value
        planned = await planner.plan_request(
            workspace_id=value.workspace_id,
            request_id=value.id,
            request_kind=value.kind,
            subject_locator_ref=value.subject_locator_ref,
            data_classes=tuple(value.data_classes),
            retention_policy_snapshot=value.retention_policy_snapshot,
        )
        payloads = [
            {
                "kind": item.kind,
                "data_classes": list(item.data_classes),
                "target_system": item.target_system,
                "target_locator_ref": item.target_locator_ref,
                "sequence": item.sequence,
                "plan_metadata": redact_safe_metadata(item.plan_metadata),
            }
            for item in planned
        ]
        validate_action_plan(
            request_kind=PrivacyRequestKind(value.kind),
            requested_data_classes=value.data_classes,
            actions=payloads,
        )
        sequences = [item.sequence for item in planned]
        if sorted(sequences) != list(range(1, len(sequences) + 1)):
            raise AppError(
                "PRIVACY_ACTION_SEQUENCE_INVALID",
                "데이터 권리 실행 계획 순서가 연속적이지 않습니다.",
                503,
            )
        for item, payload in zip(planned, payloads, strict=True):
            require_secret_reference(item.target_locator_ref, path="target_locator_ref")
            self._session.add(
                PrivacyAction(
                    workspace_id=value.workspace_id,
                    request_id=value.id,
                    sequence=item.sequence,
                    kind=item.kind,
                    data_classes=list(item.data_classes),
                    target_system=item.target_system,
                    target_locator_ref=item.target_locator_ref,
                    plan_metadata=payload["plan_metadata"],
                    plan_hash=canonical_json_hash(payload),
                    idempotency_key=(
                        f"privacy:{value.id}:{item.sequence}:"
                        f"{canonical_json_hash(payload)[:16]}"
                    ),
                )
            )
        ensure_privacy_transition(value.state, PrivacyRequestState.QUEUED.value)
        value.state = PrivacyRequestState.QUEUED.value
        await self._session.flush()
        await self._record(
            principal=principal,
            action="security.privacy_request.queued",
            target_type="privacy_request",
            target_id=value.id,
            details={"action_count": len(planned)},
        )
        return value

    async def _current_retention_policy(self, workspace_id: UUID) -> RetentionPolicyVersion:
        now = datetime.now(UTC)
        value = await self._session.scalar(
            select(RetentionPolicyVersion)
            .where(
                RetentionPolicyVersion.workspace_id == workspace_id,
                RetentionPolicyVersion.effective_at <= now,
                (RetentionPolicyVersion.retired_at.is_(None))
                | (RetentionPolicyVersion.retired_at > now),
            )
            .order_by(RetentionPolicyVersion.version.desc())
            .limit(1)
        )
        if value is None:
            raise AppError(
                "RETENTION_POLICY_UNAVAILABLE", "유효한 데이터 보존 정책이 없습니다.", 503
            )
        return value

    async def _privacy_request(
        self, workspace_id: UUID, request_id: UUID, *, lock: bool = False
    ) -> PrivacyRequest:
        await self._scope(workspace_id)
        query = select(PrivacyRequest).where(
            PrivacyRequest.workspace_id == workspace_id,
            PrivacyRequest.id == request_id,
        )
        if lock:
            query = query.with_for_update()
        value = await self._session.scalar(query)
        if value is None:
            raise AppError(
                "PRIVACY_REQUEST_NOT_FOUND", "데이터 권리 요청을 찾을 수 없습니다.", 404
            )
        return value

    async def _matching_legal_holds(self, request: PrivacyRequest) -> list[LegalHold]:
        now = datetime.now(UTC)
        candidates = list(
            await self._session.scalars(
                select(LegalHold).where(
                    LegalHold.workspace_id == request.workspace_id,
                    LegalHold.state == LegalHoldState.ACTIVE.value,
                    (LegalHold.expires_at.is_(None)) | (LegalHold.expires_at > now),
                )
            )
        )
        matched: list[LegalHold] = []
        requested_classes = set(request.data_classes)
        for hold in candidates:
            scope = hold.scope_snapshot
            subject_hashes = {str(item) for item in scope.get("subject_locator_hashes", [])}
            classes = {str(item) for item in scope.get("data_classes", [])}
            if scope.get("all_workspace") is True:
                matched.append(hold)
            elif request.subject_locator_hash in subject_hashes:
                matched.append(hold)
            elif classes.intersection(requested_classes):
                matched.append(hold)
            elif scope.get("target_refs"):
                # Target references are resolved only by the external deletion planner.
                # Conservatively hold the request instead of risking irreversible deletion.
                matched.append(hold)
        return matched

    async def process_privacy_request(
        self,
        *,
        workspace_id: UUID,
        request_id: UUID,
        executor: DataRightsExecutor,
        policy: DataRightsPolicy,
    ) -> PrivacyRequest:
        await self._scope(workspace_id)
        await self._lock_legal_hold_guard(workspace_id)
        value = await self._privacy_request(workspace_id, request_id, lock=True)
        if value.state != PrivacyRequestState.QUEUED.value:
            return value
        if value.kind == PrivacyRequestKind.DELETE.value:
            holds = await self._matching_legal_holds(value)
            if holds:
                ensure_privacy_transition(value.state, PrivacyRequestState.ON_HOLD.value)
                value.state = PrivacyRequestState.ON_HOLD.value
                await self._session.flush()
                return value
        ensure_privacy_transition(value.state, PrivacyRequestState.PROCESSING.value)
        value.state = PrivacyRequestState.PROCESSING.value
        value.processing_started_at = datetime.now(UTC)
        actions = list(
            await self._session.scalars(
                select(PrivacyAction)
                .where(
                    PrivacyAction.workspace_id == workspace_id,
                    PrivacyAction.request_id == value.id,
                )
                .order_by(PrivacyAction.sequence)
                .with_for_update()
            )
        )
        for action in actions:
            if action.state == PrivacyActionState.SUCCEEDED.value:
                continue
            if value.kind == PrivacyRequestKind.DELETE.value:
                holds = await self._matching_legal_holds(value)
                if holds:
                    action.state = PrivacyActionState.SKIPPED_LEGAL_HOLD.value
                    action.completed_at = datetime.now(UTC)
                    self._session.add(
                        PrivacyActionAttempt(
                            workspace_id=workspace_id,
                            action_id=action.id,
                            attempt_no=action.attempt_count + 1,
                            outcome=PrivacyActionState.SKIPPED_LEGAL_HOLD.value,
                            error_code="LEGAL_HOLD_ACTIVE",
                        )
                    )
                    action.attempt_count += 1
                    continue
            action.state = PrivacyActionState.RUNNING.value
            action.started_at = datetime.now(UTC)
            action.attempt_count += 1
            await self._session.flush()
            try:
                if action.kind == PrivacyActionKind.EXPORT.value:
                    await self._execute_export_action(value, action, executor, policy)
                else:
                    await self._execute_privacy_action(value, action, executor)
            except AppError as exc:
                self._fail_privacy_action(action, code=exc.code, error_class="AppError")
            except Exception as exc:
                self._fail_privacy_action(
                    action,
                    code="PRIVACY_ACTION_EXECUTION_FAILED",
                    error_class=type(exc).__name__,
                )
        final_state = privacy_completion_state(action.state for action in actions)
        ensure_privacy_transition(value.state, final_state.value)
        value.state = final_state.value
        if final_state == PrivacyRequestState.COMPLETED:
            value.completed_at = datetime.now(UTC)
            if value.kind == PrivacyRequestKind.DELETE.value:
                await self._issue_deletion_certificate(value, actions)
        elif final_state == PrivacyRequestState.FAILED:
            value.failure_code = "PRIVACY_ACTIONS_FAILED"
        await self._session.flush()
        await add_outbox_event(
            self._session,
            workspace_id=workspace_id,
            aggregate_type="privacy_request",
            aggregate_id=str(value.id),
            event_type="security.privacy_request.execution_finished",
            schema_version=_SCHEMA_VERSION,
            payload={
                "workspace_id": str(workspace_id),
                "privacy_request_id": str(value.id),
                "state": value.state,
            },
        )
        return value

    async def _execute_export_action(
        self,
        request: PrivacyRequest,
        action: PrivacyAction,
        executor: DataRightsExecutor,
        policy: DataRightsPolicy,
    ) -> None:
        existing = await self._session.scalar(
            select(PrivacyExportArtifact).where(
                PrivacyExportArtifact.workspace_id == request.workspace_id,
                PrivacyExportArtifact.request_id == request.id,
            )
        )
        if existing is None:
            result = await executor.build_export(
                workspace_id=request.workspace_id,
                request_id=request.id,
                subject_locator_ref=request.subject_locator_ref,
                data_classes=tuple(request.data_classes),
                idempotency_key=action.idempotency_key,
            )
            if (
                result.expires_at.tzinfo is None
                or result.expires_at <= datetime.now(UTC)
                or result.size_bytes < 0
                or not result.object_ref
                or len(result.object_ref) > 1_000
                or not isinstance(result.manifest, dict)
                or not is_sha256_hex(result.content_hash)
            ):
                raise AppError(
                    "PRIVACY_EXPORT_RESULT_INVALID",
                    "내보내기 실행 결과가 보안 계약을 충족하지 않습니다.",
                    503,
                )
            maximum_downloads = await policy.maximum_export_downloads(request.workspace_id)
            if maximum_downloads <= 0:
                raise AppError(
                    "PRIVACY_EXPORT_POLICY_INVALID",
                    "내보내기 다운로드 정책이 올바르지 않습니다.",
                    503,
                )
            existing = PrivacyExportArtifact(
                workspace_id=request.workspace_id,
                request_id=request.id,
                object_ref=result.object_ref,
                content_hash=result.content_hash,
                size_bytes=result.size_bytes,
                manifest=result.manifest,
                manifest_hash=canonical_json_hash(result.manifest),
                watermark_policy_snapshot={
                    "required": True,
                    "subject": str(request.requested_by),
                    "request_id": str(request.id),
                },
                maximum_downloads=maximum_downloads,
                expires_at=result.expires_at,
            )
            self._session.add(existing)
            await self._session.flush()
        action.state = PrivacyActionState.SUCCEEDED.value
        action.provider_operation_ref = f"export-artifact:{existing.id}"
        action.affected_records = 1
        action.result_manifest_hash = existing.manifest_hash
        action.evidence_object_ref = existing.object_ref
        action.completed_at = datetime.now(UTC)
        self._session.add(
            PrivacyActionAttempt(
                workspace_id=request.workspace_id,
                action_id=action.id,
                attempt_no=action.attempt_count,
                outcome=PrivacyActionState.SUCCEEDED.value,
                provider_operation_ref=action.provider_operation_ref,
                result_manifest_hash=action.result_manifest_hash,
                evidence_object_ref=action.evidence_object_ref,
            )
        )

    async def _execute_privacy_action(
        self,
        request: PrivacyRequest,
        action: PrivacyAction,
        executor: DataRightsExecutor,
    ) -> None:
        result = await executor.execute_action(
            workspace_id=request.workspace_id,
            request_id=request.id,
            action_id=action.id,
            kind=action.kind,
            target_system=action.target_system,
            target_locator_ref=action.target_locator_ref,
            data_classes=tuple(action.data_classes),
            idempotency_key=action.idempotency_key,
        )
        expected_hash = canonical_json_hash(result.result_manifest)
        if (
            result.completed_at.tzinfo is None
            or action.started_at is None
            or result.completed_at < action.started_at
            or result.affected_records < 0
            or not result.provider_operation_ref
            or len(result.provider_operation_ref) > 500
            or not isinstance(result.result_manifest, dict)
            or result.result_manifest_hash != expected_hash
            or not result.evidence_object_ref
            or len(result.evidence_object_ref) > 1_000
            or (
                result.backup_erasure_due_at is not None
                and (
                    result.backup_erasure_due_at.tzinfo is None
                    or result.backup_erasure_due_at < result.completed_at
                )
            )
            or (
                action.kind == PrivacyActionKind.SCHEDULE_BACKUP_ERASURE.value
                and result.backup_erasure_due_at is None
            )
        ):
            raise AppError(
                "PRIVACY_ACTION_RESULT_INVALID",
                "데이터 권리 실행 결과의 증거가 올바르지 않습니다.",
                503,
            )
        action.state = PrivacyActionState.SUCCEEDED.value
        action.provider_operation_ref = result.provider_operation_ref
        action.affected_records = result.affected_records
        action.result_manifest_hash = result.result_manifest_hash
        action.evidence_object_ref = result.evidence_object_ref
        action.backup_erasure_due_at = result.backup_erasure_due_at
        action.completed_at = result.completed_at
        self._session.add(
            PrivacyActionAttempt(
                workspace_id=request.workspace_id,
                action_id=action.id,
                attempt_no=action.attempt_count,
                outcome=PrivacyActionState.SUCCEEDED.value,
                provider_operation_ref=result.provider_operation_ref,
                result_manifest_hash=result.result_manifest_hash,
                evidence_object_ref=result.evidence_object_ref,
            )
        )

    def _fail_privacy_action(
        self, action: PrivacyAction, *, code: str, error_class: str
    ) -> None:
        code = code[:120]
        error_class = error_class[:120]
        action.state = PrivacyActionState.FAILED.value
        action.last_error_code = code
        action.completed_at = datetime.now(UTC)
        self._session.add(
            PrivacyActionAttempt(
                workspace_id=action.workspace_id,
                action_id=action.id,
                attempt_no=action.attempt_count,
                outcome=PrivacyActionState.FAILED.value,
                error_code=code,
                error_class=error_class,
            )
        )

    async def _issue_deletion_certificate(
        self, request: PrivacyRequest, actions: list[PrivacyAction]
    ) -> DeletionCertificate:
        existing = await self._session.scalar(
            select(DeletionCertificate).where(
                DeletionCertificate.workspace_id == request.workspace_id,
                DeletionCertificate.request_id == request.id,
            )
        )
        if existing is not None:
            return existing
        system_results = [
            {
                "action_id": str(action.id),
                "kind": action.kind,
                "target_system": action.target_system,
                "state": action.state,
                "affected_records": action.affected_records,
                "result_manifest_hash": action.result_manifest_hash,
            }
            for action in actions
        ]
        backup_dates = [
            action.backup_erasure_due_at
            for action in actions
            if action.backup_erasure_due_at is not None
        ]
        manifest = {
            "privacy_request_id": str(request.id),
            "data_classes": request.data_classes,
            "system_results": system_results,
            "backup_erasure_due_at": (
                max(backup_dates).isoformat() if backup_dates else None
            ),
        }
        value = DeletionCertificate(
            workspace_id=request.workspace_id,
            request_id=request.id,
            completed_data_classes=request.data_classes,
            held_data_classes=[],
            system_results=system_results,
            manifest_hash=canonical_json_hash(manifest),
            backup_erasure_due_at=max(backup_dates) if backup_dates else None,
            certificate_code=f"del-{uuid4().hex}",
        )
        self._session.add(value)
        await self._session.flush()
        return value

    async def fail_privacy_runtime(
        self, *, workspace_id: UUID, request_id: UUID, code: str
    ) -> PrivacyRequest:
        value = await self._privacy_request(workspace_id, request_id, lock=True)
        if value.state == PrivacyRequestState.QUEUED.value:
            ensure_privacy_transition(value.state, PrivacyRequestState.PROCESSING.value)
            value.state = PrivacyRequestState.PROCESSING.value
        if value.state == PrivacyRequestState.PROCESSING.value:
            ensure_privacy_transition(value.state, PrivacyRequestState.FAILED.value)
            value.state = PrivacyRequestState.FAILED.value
            value.failure_code = code[:120]
        return value

    async def get_privacy_request(
        self, principal: Principal, request_id: UUID
    ) -> PrivacyRequest:
        value = await self._privacy_request(principal.workspace_id, request_id)
        if (
            value.requested_by != principal.subject_id
            and "privacy:read" not in principal.permissions
        ):
            raise AppError("PRIVACY_REQUEST_ACCESS_DENIED", "요청을 조회할 권한이 없습니다.", 403)
        return value

    async def list_privacy_requests(self, principal: Principal) -> list[PrivacyRequest]:
        await self._scope(principal.workspace_id)
        query = select(PrivacyRequest).where(
            PrivacyRequest.workspace_id == principal.workspace_id
        )
        if "privacy:read" not in principal.permissions:
            query = query.where(PrivacyRequest.requested_by == principal.subject_id)
        return list(
            await self._session.scalars(query.order_by(PrivacyRequest.created_at.desc()))
        )

    async def list_privacy_actions(
        self, principal: Principal, request_id: UUID
    ) -> list[PrivacyAction]:
        await self.get_privacy_request(principal, request_id)
        return list(
            await self._session.scalars(
                select(PrivacyAction)
                .where(
                    PrivacyAction.workspace_id == principal.workspace_id,
                    PrivacyAction.request_id == request_id,
                )
                .order_by(PrivacyAction.sequence)
            )
        )

    async def list_privacy_access_events(
        self, principal: Principal, *, limit: int
    ) -> list[PrivacyAccessEvent]:
        await self._scope(principal.workspace_id)
        return list(
            await self._session.scalars(
                select(PrivacyAccessEvent)
                .where(PrivacyAccessEvent.workspace_id == principal.workspace_id)
                .order_by(PrivacyAccessEvent.occurred_at.desc())
                .limit(limit)
            )
        )

    async def cancel_privacy_request(
        self, principal: Principal, request_id: UUID, *, reason: str
    ) -> PrivacyRequest:
        value = await self._privacy_request(principal.workspace_id, request_id, lock=True)
        if (
            value.requested_by != principal.subject_id
            and "privacy:manage" not in principal.permissions
        ):
            raise AppError("PRIVACY_REQUEST_ACCESS_DENIED", "요청을 취소할 권한이 없습니다.", 403)
        ensure_privacy_transition(value.state, PrivacyRequestState.CANCELLED.value)
        value.state = PrivacyRequestState.CANCELLED.value
        await self._record(
            principal=principal,
            action="security.privacy_request.cancelled",
            target_type="privacy_request",
            target_id=value.id,
            details={"reason": reason},
        )
        return value

    async def reject_privacy_request(
        self,
        principal: Principal,
        request_id: UUID,
        *,
        rejection_code: str,
        reason: str,
    ) -> PrivacyRequest:
        value = await self._privacy_request(principal.workspace_id, request_id, lock=True)
        ensure_privacy_transition(value.state, PrivacyRequestState.REJECTED.value)
        value.state = PrivacyRequestState.REJECTED.value
        value.rejection_code = rejection_code
        await self._record(
            principal=principal,
            action="security.privacy_request.rejected",
            target_type="privacy_request",
            target_id=value.id,
            details={"rejection_code": rejection_code, "reason": reason},
        )
        return value

    async def get_export_artifact(
        self, principal: Principal, request_id: UUID, *, lock: bool = False
    ) -> PrivacyExportArtifact:
        request = await self.get_privacy_request(principal, request_id)
        query = select(PrivacyExportArtifact).where(
            PrivacyExportArtifact.workspace_id == principal.workspace_id,
            PrivacyExportArtifact.request_id == request_id,
        )
        if lock:
            query = query.with_for_update()
        value = await self._session.scalar(query)
        if value is None:
            raise AppError("PRIVACY_EXPORT_NOT_FOUND", "내보내기 산출물을 찾을 수 없습니다.", 404)
        self._session.add(
            PrivacyAccessEvent(
                workspace_id=principal.workspace_id,
                actor_id=principal.subject_id,
                action="READ_METADATA",
                subject_type="privacy_export_artifact",
                subject_id=str(value.id),
                data_classes=request.data_classes,
                purpose="DATA_SUBJECT_EXPORT",
                bulk=False,
                watermark_reference=f"privacy-request:{request.id}",
                request_id=request_id_context.get(),
            )
        )
        await self._session.flush()
        return value

    async def issue_export_download(
        self,
        principal: Principal,
        request_id: UUID,
        *,
        executor: DataRightsExecutor,
    ) -> DownloadGrant:
        request = await self.get_privacy_request(principal, request_id)
        artifact = await self.get_export_artifact(principal, request_id, lock=True)
        download_count = int(
            await self._session.scalar(
                select(func.count(PrivacyAccessEvent.id)).where(
                    PrivacyAccessEvent.workspace_id == principal.workspace_id,
                    PrivacyAccessEvent.subject_type == "privacy_export_artifact",
                    PrivacyAccessEvent.subject_id == str(artifact.id),
                    PrivacyAccessEvent.action == "DOWNLOAD",
                )
            )
            or 0
        )
        authorize_export_download(
            request_state=request.state,
            expires_at=artifact.expires_at,
            now=datetime.now(UTC),
            download_count=download_count,
            maximum_downloads=artifact.maximum_downloads,
        )
        grant = await executor.issue_download(
            workspace_id=principal.workspace_id,
            artifact_id=artifact.id,
            object_ref=artifact.object_ref,
            idempotency_key=(
                f"privacy-download:{artifact.id}:{download_count + 1}"
            ),
        )
        validate_secure_download_url(grant.download_url)
        now = datetime.now(UTC)
        if (
            grant.expires_at.tzinfo is None
            or grant.expires_at <= now
            or grant.expires_at > artifact.expires_at
            or not grant.delivery_reference
            or len(grant.delivery_reference) > 500
        ):
            raise AppError(
                "PRIVACY_DOWNLOAD_GRANT_INVALID",
                "다운로드 제공자의 만료 정책이 요청 정책을 넘었습니다.",
                503,
            )
        self._session.add(
            PrivacyAccessEvent(
                workspace_id=principal.workspace_id,
                actor_id=principal.subject_id,
                action="DOWNLOAD",
                subject_type="privacy_export_artifact",
                subject_id=str(artifact.id),
                data_classes=request.data_classes,
                purpose="DATA_SUBJECT_EXPORT",
                bulk=True,
                watermark_reference=f"privacy-request:{request.id}",
                delivery_reference=grant.delivery_reference,
                request_id=request_id_context.get(),
            )
        )
        await self._session.flush()
        return grant

    async def get_deletion_certificate(
        self, principal: Principal, request_id: UUID
    ) -> DeletionCertificate:
        request = await self.get_privacy_request(principal, request_id)
        value = await self._session.scalar(
            select(DeletionCertificate).where(
                DeletionCertificate.workspace_id == principal.workspace_id,
                DeletionCertificate.request_id == request_id,
            )
        )
        if value is None:
            raise AppError(
                "DELETION_CERTIFICATE_NOT_FOUND", "삭제 완료 증명을 찾을 수 없습니다.", 404
            )
        self._session.add(
            PrivacyAccessEvent(
                workspace_id=principal.workspace_id,
                actor_id=principal.subject_id,
                action="READ",
                subject_type="deletion_certificate",
                subject_id=str(value.id),
                data_classes=request.data_classes,
                purpose="DELETION_EVIDENCE_REVIEW",
                bulk=False,
                request_id=request_id_context.get(),
            )
        )
        await self._session.flush()
        return value

    async def record_backup_erasure(
        self,
        principal: Principal,
        request_id: UUID,
        data: BackupErasureEvidenceCreate,
        *,
        executor: DataRightsExecutor,
    ) -> BackupErasureEvidence:
        certificate = await self.get_deletion_certificate(principal, request_id)
        existing = await self._session.scalar(
            select(BackupErasureEvidence).where(
                BackupErasureEvidence.workspace_id == principal.workspace_id,
                BackupErasureEvidence.certificate_id == certificate.id,
            )
        )
        if existing is not None:
            if existing.submitted_evidence_hash != data.evidence_hash:
                raise AppError(
                    "BACKUP_ERASURE_EVIDENCE_CONFLICT",
                    "이미 검증된 백업 삭제 증거와 제출 증거가 다릅니다.",
                    409,
                )
            return existing
        result = await executor.verify_backup_erasure(
            workspace_id=principal.workspace_id,
            certificate_id=certificate.id,
            evidence_object_ref=data.evidence_object_ref,
            evidence_hash=data.evidence_hash,
        )
        if (
            result.completed_at.tzinfo is None
            or result.completed_at < certificate.issued_at
            or result.completed_at > datetime.now(UTC) + timedelta(minutes=5)
            or not is_sha256_hex(result.evidence_hash)
            or not result.provider_reference
            or len(result.provider_reference) > 500
            or not result.verifier
            or len(result.verifier) > 160
        ):
            raise AppError(
                "BACKUP_ERASURE_EVIDENCE_INVALID",
                "백업 삭제 검증 결과가 올바르지 않습니다.",
                503,
            )
        value = BackupErasureEvidence(
            workspace_id=principal.workspace_id,
            certificate_id=certificate.id,
            provider_reference=result.provider_reference,
            verifier=result.verifier,
            evidence_object_ref=data.evidence_object_ref,
            submitted_evidence_hash=data.evidence_hash,
            verified_evidence_hash=result.evidence_hash,
            completed_at=result.completed_at,
        )
        self._session.add(value)
        await self._session.flush()
        await self._record(
            principal=principal,
            action="security.privacy_request.backup_erasure_verified",
            target_type="deletion_certificate",
            target_id=certificate.id,
            details={"request_id": str(request_id), "completed_at": result.completed_at},
        )
        return value

    async def get_backup_erasure_evidence(
        self, principal: Principal, request_id: UUID
    ) -> BackupErasureEvidence:
        certificate = await self.get_deletion_certificate(principal, request_id)
        value = await self._session.scalar(
            select(BackupErasureEvidence).where(
                BackupErasureEvidence.workspace_id == principal.workspace_id,
                BackupErasureEvidence.certificate_id == certificate.id,
            )
        )
        if value is None:
            raise AppError(
                "BACKUP_ERASURE_EVIDENCE_NOT_FOUND",
                "백업 삭제 완료 증거를 찾을 수 없습니다.",
                404,
            )
        return value

    async def append_consent(
        self,
        principal: Principal,
        data: PrivacyConsentCreate,
        *,
        idempotency_key: str,
    ) -> PrivacyConsentEvidence:
        await self._scope(principal.workspace_id)
        if (
            data.subject_id != principal.subject_id
            and "privacy:manage" not in principal.permissions
        ):
            raise AppError("PRIVACY_CONSENT_ACCESS_DENIED", "동의를 기록할 권한이 없습니다.", 403)
        await self._lock_consent_guard(
            principal.workspace_id, data.subject_id, data.purpose.value
        )
        existing = await self._session.scalar(
            select(PrivacyConsentEvidence).where(
                PrivacyConsentEvidence.workspace_id == principal.workspace_id,
                PrivacyConsentEvidence.subject_id == data.subject_id,
                PrivacyConsentEvidence.purpose == data.purpose.value,
                PrivacyConsentEvidence.policy_version == data.policy_version,
                PrivacyConsentEvidence.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            if existing.evidence_hash != data.evidence_hash:
                raise AppError(
                    "IDEMPOTENCY_KEY_REUSED",
                    "같은 Idempotency-Key가 다른 동의 증거에 사용되었습니다.",
                    409,
                )
            return existing
        superseded: PrivacyConsentEvidence | None = None
        if data.supersedes_id is not None:
            superseded = await self._session.scalar(
                select(PrivacyConsentEvidence).where(
                    PrivacyConsentEvidence.workspace_id == principal.workspace_id,
                    PrivacyConsentEvidence.id == data.supersedes_id,
                ).with_for_update()
            )
            if (
                superseded is None
                or superseded.subject_id != data.subject_id
                or superseded.purpose != data.purpose.value
            ):
                raise AppError(
                    "PRIVACY_CONSENT_SUPERSEDES_MISMATCH",
                    "철회·갱신 대상 동의가 주체와 목적에 일치하지 않습니다.",
                    422,
                )
            successor = await self._session.scalar(
                select(PrivacyConsentEvidence).where(
                    PrivacyConsentEvidence.workspace_id == principal.workspace_id,
                    PrivacyConsentEvidence.supersedes_id == superseded.id,
                )
            )
            if successor is not None:
                raise AppError(
                    "PRIVACY_CONSENT_ALREADY_SUPERSEDED",
                    "이미 후속 동의 증거가 기록된 항목입니다.",
                    409,
                )
            if data.occurred_at <= superseded.occurred_at:
                raise AppError(
                    "PRIVACY_CONSENT_TIME_INVALID",
                    "후속 동의 증거 시각은 이전 증거보다 늦어야 합니다.",
                    422,
                )
        else:
            prior = await self._session.scalar(
                select(PrivacyConsentEvidence)
                .where(
                    PrivacyConsentEvidence.workspace_id == principal.workspace_id,
                    PrivacyConsentEvidence.subject_id == data.subject_id,
                    PrivacyConsentEvidence.purpose == data.purpose.value,
                )
                .limit(1)
            )
            if prior is not None:
                raise AppError(
                    "PRIVACY_CONSENT_SUPERSEDES_REQUIRED",
                    "기존 동의가 있으면 현재 증거를 명시적으로 대체해야 합니다.",
                    422,
                )
        if data.decision.value == "WITHDRAWN" and superseded is None:
            raise AppError(
                "PRIVACY_CONSENT_WITHDRAWAL_TARGET_REQUIRED",
                "동의 철회에는 이전 동의 증거가 필요합니다.",
                422,
            )
        if (
            data.decision.value == "WITHDRAWN"
            and superseded is not None
            and superseded.decision != "GRANTED"
        ):
            raise AppError(
                "PRIVACY_CONSENT_WITHDRAWAL_INVALID",
                "활성 동의 증거만 철회할 수 있습니다.",
                422,
            )
        value = PrivacyConsentEvidence(
            workspace_id=principal.workspace_id,
            subject_id=data.subject_id,
            purpose=data.purpose.value,
            decision=data.decision.value,
            policy_version=data.policy_version,
            policy_hash=data.policy_hash,
            scope_snapshot=data.scope_snapshot,
            transfer_countries=sorted(set(data.transfer_countries)),
            evidence_hash=data.evidence_hash,
            idempotency_key=idempotency_key,
            supersedes_id=data.supersedes_id,
            occurred_at=data.occurred_at,
        )
        self._session.add(value)
        await self._session.flush()
        await self._record(
            principal=principal,
            action="security.privacy_consent.recorded",
            target_type="privacy_consent_evidence",
            target_id=value.id,
            details={
                "subject_id": str(value.subject_id),
                "purpose": value.purpose,
                "decision": value.decision,
                "policy_version": value.policy_version,
            },
        )
        return value

    async def list_consents(
        self, principal: Principal, *, subject_id: UUID | None = None
    ) -> list[PrivacyConsentEvidence]:
        await self._scope(principal.workspace_id)
        target = subject_id or principal.subject_id
        if target != principal.subject_id and "privacy:read" not in principal.permissions:
            raise AppError("PRIVACY_CONSENT_ACCESS_DENIED", "동의를 조회할 권한이 없습니다.", 403)
        return list(
            await self._session.scalars(
                select(PrivacyConsentEvidence)
                .where(
                    PrivacyConsentEvidence.workspace_id == principal.workspace_id,
                    PrivacyConsentEvidence.subject_id == target,
                )
                .order_by(PrivacyConsentEvidence.occurred_at.desc())
            )
        )

    async def create_subprocessor_version(
        self, principal: Principal, data: SubprocessorVersionCreate
    ) -> SubprocessorVersion:
        await self._scope(principal.workspace_id)
        value = SubprocessorVersion(
            workspace_id=principal.workspace_id,
            vendor_key=data.vendor_key,
            vendor_name=data.vendor_name,
            version=data.version,
            purposes=list(dict.fromkeys(data.purposes)),
            data_classes=sorted({item.value for item in data.data_classes}),
            processing_countries=sorted(set(data.processing_countries)),
            transfer_mechanism=data.transfer_mechanism,
            retention_summary=data.retention_summary,
            security_measures=list(dict.fromkeys(data.security_measures)),
            contract_artifact_ref=data.contract_artifact_ref,
            contract_hash=data.contract_hash,
            notice_required=data.notice_required,
            notice_at=data.notice_at,
            effective_at=data.effective_at,
            retired_at=data.retired_at,
            created_by=principal.subject_id,
        )
        self._session.add(value)
        await self._session.flush()
        await self._record(
            principal=principal,
            action="security.subprocessor.version_created",
            target_type="subprocessor_version",
            target_id=value.id,
            details={
                "vendor_key": value.vendor_key,
                "version": value.version,
                "notice_required": value.notice_required,
            },
        )
        return value

    async def list_subprocessors(self, principal: Principal) -> list[SubprocessorVersion]:
        await self._scope(principal.workspace_id)
        return list(
            await self._session.scalars(
                select(SubprocessorVersion)
                .where(SubprocessorVersion.workspace_id == principal.workspace_id)
                .order_by(SubprocessorVersion.vendor_key, SubprocessorVersion.version.desc())
            )
        )

    async def create_copyright_notice(
        self,
        principal: Principal,
        data: CopyrightNoticeCreate,
        *,
        idempotency_key: str,
    ) -> tuple[CopyrightCase, bool]:
        await self._scope(principal.workspace_id)
        require_secret_reference(data.claimant_contact_ref, path="claimant_contact_ref")
        payload = data.model_dump(mode="json")
        request_hash = canonical_json_hash(payload)
        await self._lock_creation_guard(
            "copyright-case-idempotency",
            principal.workspace_id,
            principal.subject_id,
            idempotency_key,
        )
        existing = await self._session.scalar(
            select(CopyrightCase).where(
                CopyrightCase.workspace_id == principal.workspace_id,
                CopyrightCase.reported_by == principal.subject_id,
                CopyrightCase.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            _same_request(existing.request_hash, request_hash)
            return existing, False
        value = CopyrightCase(
            workspace_id=principal.workspace_id,
            reported_by=principal.subject_id,
            idempotency_key=idempotency_key,
            claimant_contact_ref=data.claimant_contact_ref,
            claimant_contact_hash=canonical_json_hash(data.claimant_contact_ref),
            work_description=redact_safe_text(data.work_description),
            target_refs=[item.model_dump(mode="json") for item in data.target_refs],
            evidence_object_refs=list(dict.fromkeys(data.evidence_object_refs)),
            sworn_statement=data.sworn_statement,
            request_hash=request_hash,
        )
        self._session.add(value)
        await self._session.flush()
        await self._append_copyright_event(
            value,
            actor_id=principal.subject_id,
            kind=CopyrightEventKind.NOTICE_RECEIVED,
            reason="copyright notice received",
            metadata={"target_count": len(value.target_refs)},
            evidence_object_ref=value.evidence_object_refs[0],
            evidence_hash=canonical_json_hash(value.evidence_object_refs),
        )
        await self._record(
            principal=principal,
            action="security.copyright.notice_received",
            target_type="copyright_case",
            target_id=value.id,
            details={"target_count": len(value.target_refs)},
        )
        return value, True

    async def process_copyright_case(
        self,
        *,
        workspace_id: UUID,
        case_id: UUID,
        adapter: CopyrightEnforcementAdapter,
    ) -> CopyrightCase:
        value = await self._copyright_case(workspace_id, case_id, lock=True)
        if value.state not in {
            CopyrightCaseState.RECEIVED.value,
            CopyrightCaseState.FAILED.value,
        }:
            return value
        ensure_copyright_transition(value.state, CopyrightCaseState.VALIDATING.value)
        value.state = CopyrightCaseState.VALIDATING.value
        try:
            triage = await adapter.triage_notice(
                workspace_id=workspace_id,
                case_id=value.id,
                target_refs=tuple(value.target_refs),
                evidence_object_refs=tuple(value.evidence_object_refs),
            )
            if (
                triage.response_due_at.tzinfo is None
                or triage.response_due_at <= datetime.now(UTC)
                or not triage.decision_code
                or len(triage.decision_code) > 120
                or not triage.policy_version
                or len(triage.policy_version) > 80
                or (
                    triage.temporary_action is not None
                    and not 1 <= len(triage.temporary_action) <= 80
                )
                or not is_sha256_hex(triage.evidence_hash)
            ):
                raise AppError(
                    "COPYRIGHT_TRIAGE_RESULT_INVALID",
                    "저작권 검토 결과 증거가 올바르지 않습니다.",
                    503,
                )
            value.response_due_at = triage.response_due_at
            value.policy_version = triage.policy_version
            if not triage.accepted:
                ensure_copyright_transition(value.state, CopyrightCaseState.REJECTED.value)
                value.state = CopyrightCaseState.REJECTED.value
                await self._append_copyright_event(
                    value,
                    actor_id=None,
                    kind=CopyrightEventKind.REJECTED,
                    reason=triage.decision_code,
                    metadata={"policy_version": triage.policy_version},
                    evidence_object_ref=None,
                    evidence_hash=triage.evidence_hash,
                )
                return value
            await self._append_copyright_event(
                value,
                actor_id=None,
                kind=CopyrightEventKind.VALIDATED,
                reason=triage.decision_code,
                metadata={"policy_version": triage.policy_version},
                evidence_object_ref=None,
                evidence_hash=triage.evidence_hash,
            )
            if triage.temporary_action is None:
                ensure_copyright_transition(value.state, CopyrightCaseState.LEGAL_REVIEW.value)
                value.state = CopyrightCaseState.LEGAL_REVIEW.value
                return value
            ensure_copyright_transition(value.state, CopyrightCaseState.TEMPORARY_ACTION.value)
            value.state = CopyrightCaseState.TEMPORARY_ACTION.value
            value.temporary_action = triage.temporary_action
            action_result = await adapter.apply_action(
                workspace_id=workspace_id,
                case_id=value.id,
                action=triage.temporary_action,
                target_refs=tuple(value.target_refs),
                idempotency_key=f"copyright:{value.id}:temporary:{triage.evidence_hash[:16]}",
            )
            if (
                action_result.completed_at.tzinfo is None
                or action_result.completed_at < value.created_at
                or action_result.completed_at > datetime.now(UTC) + timedelta(minutes=5)
                or action_result.action != triage.temporary_action
                or not action_result.provider_reference
                or len(action_result.provider_reference) > 500
                or not action_result.evidence_object_ref
                or len(action_result.evidence_object_ref) > 1_000
                or not is_sha256_hex(action_result.evidence_hash)
            ):
                raise AppError(
                    "COPYRIGHT_ACTION_RESULT_INVALID",
                    "저작권 조치 결과 증거가 올바르지 않습니다.",
                    503,
                )
            await self._append_copyright_event(
                value,
                actor_id=None,
                kind=CopyrightEventKind.TEMPORARY_ACTION_APPLIED,
                reason=action_result.action,
                metadata={"provider_reference": action_result.provider_reference},
                evidence_object_ref=action_result.evidence_object_ref,
                evidence_hash=action_result.evidence_hash,
            )
            ensure_copyright_transition(
                value.state, CopyrightCaseState.WAITING_COUNTER_NOTICE.value
            )
            value.state = CopyrightCaseState.WAITING_COUNTER_NOTICE.value
        except AppError as exc:
            ensure_copyright_transition(value.state, CopyrightCaseState.FAILED.value)
            value.state = CopyrightCaseState.FAILED.value
            failure_code = exc.code[:120]
            value.failure_code = failure_code
            await self._append_copyright_event(
                value,
                actor_id=None,
                kind=CopyrightEventKind.FAILED,
                reason=failure_code,
                metadata={},
                evidence_object_ref=None,
                evidence_hash=canonical_json_hash(
                    {"case_id": str(value.id), "code": failure_code}
                ),
            )
        except Exception as exc:
            ensure_copyright_transition(value.state, CopyrightCaseState.FAILED.value)
            value.state = CopyrightCaseState.FAILED.value
            value.failure_code = "COPYRIGHT_EXECUTION_FAILED"
            await self._append_copyright_event(
                value,
                actor_id=None,
                kind=CopyrightEventKind.FAILED,
                reason="COPYRIGHT_EXECUTION_FAILED",
                metadata={"error_class": type(exc).__name__},
                evidence_object_ref=None,
                evidence_hash=canonical_json_hash(
                    {"case_id": str(value.id), "error_class": type(exc).__name__}
                ),
            )
        return value

    async def submit_counter_notice(
        self,
        principal: Principal,
        case_id: UUID,
        data: CopyrightCounterNoticeCreate,
        *,
        adapter: CopyrightEnforcementAdapter,
    ) -> CopyrightCase:
        value = await self._copyright_case(principal.workspace_id, case_id, lock=True)
        require_secret_reference(data.respondent_contact_ref, path="respondent_contact_ref")
        if value.state != CopyrightCaseState.WAITING_COUNTER_NOTICE.value:
            raise AppError(
                "COPYRIGHT_COUNTER_NOTICE_NOT_ALLOWED",
                "현재 상태에서는 이의 제기를 제출할 수 없습니다.",
                409,
            )
        verification = await adapter.verify_counter_notice(
            workspace_id=value.workspace_id,
            case_id=value.id,
            respondent_contact_ref=data.respondent_contact_ref,
            statement_object_ref=data.statement_object_ref,
            statement_hash=data.statement_hash,
        )
        if (
            not verification.passed
            or verification.verified_at.tzinfo is None
            or verification.verified_at < value.created_at
            or verification.verified_at > datetime.now(UTC) + timedelta(minutes=5)
            or not is_sha256_hex(verification.evidence_hash)
            or not verification.provider_reference
            or len(verification.provider_reference) > 500
            or not verification.assurance_level
            or len(verification.assurance_level) > 80
        ):
            raise AppError(
                "COPYRIGHT_COUNTER_NOTICE_VERIFICATION_FAILED",
                "저작권 이의 제기 신원·진술 검증에 실패했습니다.",
                422,
            )
        ensure_copyright_transition(value.state, CopyrightCaseState.LEGAL_REVIEW.value)
        value.state = CopyrightCaseState.LEGAL_REVIEW.value
        value.counter_notice_received_at = datetime.now(UTC)
        self._session.add(
            CopyrightCounterNotice(
                workspace_id=value.workspace_id,
                case_id=value.id,
                submitted_by=principal.subject_id,
                respondent_contact_ref=data.respondent_contact_ref,
                respondent_contact_hash=canonical_json_hash(data.respondent_contact_ref),
                statement_object_ref=data.statement_object_ref,
                statement_hash=data.statement_hash,
                sworn_statement=data.sworn_statement,
                verification_reference=verification.provider_reference,
                verification_assurance=verification.assurance_level,
                verification_evidence_hash=verification.evidence_hash,
                verified_at=verification.verified_at,
            )
        )
        await self._append_copyright_event(
            value,
            actor_id=principal.subject_id,
            kind=CopyrightEventKind.COUNTER_NOTICE_RECEIVED,
            reason="counter notice received",
            metadata={
                "statement_hash": data.statement_hash,
                "verification_assurance": verification.assurance_level,
                "verification_evidence_hash": verification.evidence_hash,
            },
            evidence_object_ref=data.statement_object_ref,
            evidence_hash=data.statement_hash,
        )
        await self._record(
            principal=principal,
            action="security.copyright.counter_notice_received",
            target_type="copyright_case",
            target_id=value.id,
            details={"statement_hash": data.statement_hash},
        )
        return value

    async def decide_copyright_case(
        self,
        principal: Principal,
        case_id: UUID,
        data: CopyrightDecision,
        *,
        adapter: CopyrightEnforcementAdapter,
    ) -> CopyrightCase:
        value = await self._copyright_case(principal.workspace_id, case_id, lock=True)
        target_by_action = {
            "RESTORE": CopyrightCaseState.RESTORED,
            "REMOVE": CopyrightCaseState.REMOVED,
            "REJECT": CopyrightCaseState.REJECTED,
            "CLOSE": CopyrightCaseState.CLOSED,
        }
        target = target_by_action[data.action]
        ensure_copyright_transition(value.state, target.value)
        provider_metadata: dict[str, Any] = {}
        event_evidence_ref = data.evidence_object_ref
        event_evidence_hash = data.evidence_hash
        if data.action in {"RESTORE", "REMOVE"}:
            result = await adapter.apply_action(
                workspace_id=value.workspace_id,
                case_id=value.id,
                action=data.action,
                target_refs=tuple(value.target_refs),
                idempotency_key=f"copyright:{value.id}:decision:{data.evidence_hash[:16]}",
            )
            if (
                result.completed_at.tzinfo is None
                or result.completed_at < value.created_at
                or result.completed_at > datetime.now(UTC) + timedelta(minutes=5)
                or result.action != data.action
                or not result.provider_reference
                or len(result.provider_reference) > 500
                or not result.evidence_object_ref
                or len(result.evidence_object_ref) > 1_000
                or not is_sha256_hex(result.evidence_hash)
            ):
                raise AppError(
                    "COPYRIGHT_ACTION_RESULT_INVALID",
                    "저작권 조치 결과 증거가 올바르지 않습니다.",
                    503,
                )
            provider_metadata = {"provider_reference": result.provider_reference}
            provider_metadata["decision_evidence_hash"] = data.evidence_hash
            event_evidence_ref = result.evidence_object_ref
            event_evidence_hash = result.evidence_hash
        value.state = target.value
        if target in {
            CopyrightCaseState.RESTORED,
            CopyrightCaseState.REMOVED,
            CopyrightCaseState.REJECTED,
            CopyrightCaseState.CLOSED,
        }:
            value.resolved_at = datetime.now(UTC)
        event_kind = {
            "RESTORE": CopyrightEventKind.CONTENT_RESTORED,
            "REMOVE": CopyrightEventKind.CONTENT_REMOVED,
            "REJECT": CopyrightEventKind.REJECTED,
            "CLOSE": CopyrightEventKind.CLOSED,
        }[data.action]
        await self._append_copyright_event(
            value,
            actor_id=principal.subject_id,
            kind=event_kind,
            reason=data.reason,
            metadata=provider_metadata,
            evidence_object_ref=event_evidence_ref,
            evidence_hash=event_evidence_hash,
        )
        await self._record(
            principal=principal,
            action="security.copyright.decided",
            target_type="copyright_case",
            target_id=value.id,
            details={"decision": data.action, "reason": data.reason},
        )
        return value

    async def get_copyright_case(
        self, principal: Principal, case_id: UUID
    ) -> CopyrightCase:
        value = await self._copyright_case(principal.workspace_id, case_id)
        if (
            value.reported_by != principal.subject_id
            and "security:read" not in principal.permissions
        ):
            raise AppError("COPYRIGHT_CASE_ACCESS_DENIED", "신고를 조회할 권한이 없습니다.", 403)
        return value

    async def list_copyright_events(
        self, principal: Principal, case_id: UUID
    ) -> list[CopyrightCaseEvent]:
        await self.get_copyright_case(principal, case_id)
        return list(
            await self._session.scalars(
                select(CopyrightCaseEvent)
                .where(
                    CopyrightCaseEvent.workspace_id == principal.workspace_id,
                    CopyrightCaseEvent.case_id == case_id,
                )
                .order_by(CopyrightCaseEvent.sequence)
            )
        )

    async def _copyright_case(
        self, workspace_id: UUID, case_id: UUID, *, lock: bool = False
    ) -> CopyrightCase:
        await self._scope(workspace_id)
        query = select(CopyrightCase).where(
            CopyrightCase.workspace_id == workspace_id, CopyrightCase.id == case_id
        )
        if lock:
            query = query.with_for_update()
        value = await self._session.scalar(query)
        if value is None:
            raise AppError("COPYRIGHT_CASE_NOT_FOUND", "저작권 신고를 찾을 수 없습니다.", 404)
        return value

    async def _append_copyright_event(
        self,
        case: CopyrightCase,
        *,
        actor_id: UUID | None,
        kind: CopyrightEventKind,
        reason: str,
        metadata: dict[str, Any],
        evidence_object_ref: str | None,
        evidence_hash: str,
    ) -> CopyrightCaseEvent:
        if (
            not is_sha256_hex(evidence_hash)
            or (
                evidence_object_ref is not None
                and not 1 <= len(evidence_object_ref) <= 1_000
            )
        ):
            raise AppError(
                "COPYRIGHT_EVENT_EVIDENCE_INVALID",
                "저작권 처리 이력 증거가 올바르지 않습니다.",
                503,
            )
        previous = await self._session.scalar(
            select(CopyrightCaseEvent)
            .where(
                CopyrightCaseEvent.workspace_id == case.workspace_id,
                CopyrightCaseEvent.case_id == case.id,
            )
            .order_by(CopyrightCaseEvent.sequence.desc())
            .limit(1)
        )
        sequence = 1 if previous is None else previous.sequence + 1
        safe_metadata = redact_safe_metadata(metadata)
        payload = {
            "case_id": str(case.id),
            "sequence": sequence,
            "kind": kind.value,
            "actor_id": None if actor_id is None else str(actor_id),
            "reason": reason,
            "metadata": safe_metadata,
            "evidence_hash": evidence_hash,
        }
        event_value = CopyrightCaseEvent(
            workspace_id=case.workspace_id,
            case_id=case.id,
            sequence=sequence,
            kind=kind.value,
            actor_id=actor_id,
            reason=reason,
            metadata_safe=safe_metadata,
            evidence_object_ref=evidence_object_ref,
            evidence_hash=evidence_hash,
            previous_event_hash=None if previous is None else previous.event_hash,
            event_hash=append_evidence_hash(
                None if previous is None else previous.event_hash, payload
            ),
        )
        self._session.add(event_value)
        await self._session.flush()
        return event_value

    async def create_security_incident(
        self,
        principal: Principal,
        data: SecurityIncidentCreate,
        *,
        policy: SecurityIncidentPolicy,
    ) -> SecurityIncident:
        await self._scope(principal.workspace_id)
        now = datetime.now(UTC)
        if data.detected_at > now + timedelta(minutes=5):
            raise AppError(
                "SECURITY_INCIDENT_DETECTION_TIME_INVALID",
                "보안 사건 탐지 시각은 미래일 수 없습니다.",
                422,
            )
        affected_data_classes = sorted(
            {item.value for item in data.affected_data_classes}
        )
        deadlines = await policy.deadlines(
            workspace_id=principal.workspace_id,
            severity=data.severity.value,
            incident_type=data.incident_type,
            detected_at=data.detected_at,
            affected_data_classes=tuple(affected_data_classes),
        )
        if (
            deadlines.containment_due_at.tzinfo is None
            or deadlines.containment_due_at <= data.detected_at
            or (
                deadlines.notification_due_at is not None
                and (
                    deadlines.notification_due_at.tzinfo is None
                    or deadlines.notification_due_at <= data.detected_at
                )
            )
            or not deadlines.policy_version
        ):
            raise AppError(
                "SECURITY_INCIDENT_DEADLINE_INVALID",
                "보안 사건 대응·통지 기한 정책 결과가 올바르지 않습니다.",
                503,
            )
        value = SecurityIncident(
            workspace_id=principal.workspace_id,
            external_ref=data.external_ref,
            title=data.title,
            incident_type=data.incident_type,
            severity=data.severity.value,
            detected_at=data.detected_at,
            detection_source=data.detection_source,
            runbook_version=data.runbook_version,
            incident_policy_version=deadlines.policy_version,
            impact_snapshot=redact_safe_metadata(data.impact_snapshot),
            affected_data_classes=affected_data_classes,
            affected_subject_count=data.affected_subject_count,
            containment_due_at=deadlines.containment_due_at,
            notification_due_at=deadlines.notification_due_at,
            opened_by=principal.subject_id,
        )
        self._session.add(value)
        await self._session.flush()
        initial_hash = canonical_json_hash(
            {
                "incident_id": str(value.id),
                "detected_at": value.detected_at,
                "impact_snapshot": value.impact_snapshot,
            }
        )
        await self._append_incident_event(
            value,
            actor_id=principal.subject_id,
            kind=SecurityIncidentEventKind.DETECTED,
            state_after=SecurityIncidentState.DETECTED,
            safe_summary="security incident detected",
            evidence_object_refs=[],
            evidence_hash=initial_hash,
            occurred_at=data.detected_at,
            validate_transition=False,
        )
        await self._record(
            principal=principal,
            action="security.incident.opened",
            target_type="security_incident",
            target_id=value.id,
            details={"severity": value.severity, "incident_type": value.incident_type},
        )
        return value

    async def append_security_incident_event(
        self,
        principal: Principal,
        incident_id: UUID,
        data: SecurityIncidentEventCreate,
    ) -> SecurityIncident:
        value = await self._security_incident(principal.workspace_id, incident_id, lock=True)
        if (
            data.kind == SecurityIncidentEventKind.DETECTED
            or (data.kind == SecurityIncidentEventKind.RESOLVED)
            != (data.state_after == SecurityIncidentState.RESOLVED)
        ):
            raise AppError(
                "SECURITY_INCIDENT_EVENT_INVALID",
                "보안 사건 이벤트 유형과 후속 상태가 일치하지 않습니다.",
                422,
            )
        if data.state_after.value != value.state:
            ensure_security_incident_transition(value.state, data.state_after.value)
            value.state = data.state_after.value
        if data.state_after == SecurityIncidentState.CONTAINED:
            value.contained_at = data.occurred_at
        if data.state_after == SecurityIncidentState.RESOLVED:
            value.resolved_at = data.occurred_at
        await self._append_incident_event(
            value,
            actor_id=principal.subject_id,
            kind=data.kind,
            state_after=data.state_after,
            safe_summary=data.safe_summary,
            evidence_object_refs=list(dict.fromkeys(data.evidence_object_refs)),
            evidence_hash=data.evidence_hash,
            occurred_at=data.occurred_at,
            validate_transition=False,
        )
        await self._record(
            principal=principal,
            action="security.incident.event_appended",
            target_type="security_incident",
            target_id=value.id,
            details={"kind": data.kind.value, "state": value.state},
        )
        return value

    async def notify_security_incident(
        self,
        principal: Principal,
        incident_id: UUID,
        data: SecurityIncidentNotify,
        *,
        notifier: IncidentNotificationAdapter,
    ) -> BreachNotification:
        value = await self._security_incident(principal.workspace_id, incident_id, lock=True)
        require_secret_reference(data.destination_ref, path="destination_ref")
        safe_payload = redact_safe_metadata(data.safe_payload)
        destination_hash = canonical_json_hash(data.destination_ref)
        payload_hash = canonical_json_hash(safe_payload)
        existing = await self._session.scalar(
            select(BreachNotification).where(
                BreachNotification.workspace_id == value.workspace_id,
                BreachNotification.incident_id == value.id,
                BreachNotification.audience == data.audience.value,
                BreachNotification.destination_hash == destination_hash,
                BreachNotification.template_version == data.template_version,
                BreachNotification.payload_hash == payload_hash,
            )
        )
        if existing is not None:
            return existing
        result = await notifier.notify(
            workspace_id=value.workspace_id,
            incident_id=value.id,
            audience=data.audience.value,
            destination_ref=data.destination_ref,
            template_version=data.template_version,
            safe_payload=safe_payload,
            idempotency_key=(
                f"incident:{value.id}:{data.audience.value}:"
                f"{destination_hash[:16]}:{data.template_version}:"
                f"{payload_hash[:16]}"
            ),
        )
        if (
            result.delivered_at.tzinfo is None
            or result.delivered_at < value.detected_at
            or result.delivered_at > datetime.now(UTC) + timedelta(minutes=5)
            or not result.provider_message_ref
            or len(result.provider_message_ref) > 500
            or not is_sha256_hex(result.evidence_hash)
        ):
            raise AppError(
                "BREACH_NOTIFICATION_RESULT_INVALID",
                "침해 통지 결과 증거가 올바르지 않습니다.",
                503,
            )
        notification = BreachNotification(
            workspace_id=value.workspace_id,
            incident_id=value.id,
            audience=data.audience.value,
            destination_hash=destination_hash,
            template_version=data.template_version,
            payload_hash=payload_hash,
            provider_message_ref=result.provider_message_ref,
            evidence_hash=result.evidence_hash,
            delivered_at=result.delivered_at,
        )
        self._session.add(notification)
        await self._session.flush()
        await self._append_incident_event(
            value,
            actor_id=principal.subject_id,
            kind=SecurityIncidentEventKind.NOTIFICATION_SENT,
            state_after=SecurityIncidentState(value.state),
            safe_summary=f"notification delivered to {data.audience.value}",
            evidence_object_refs=[],
            evidence_hash=result.evidence_hash,
            occurred_at=result.delivered_at,
            validate_transition=False,
        )
        await self._record(
            principal=principal,
            action="security.incident.notification_sent",
            target_type="security_incident",
            target_id=value.id,
            details={"audience": data.audience.value, "template_version": data.template_version},
        )
        return notification

    async def get_security_incident(
        self, principal: Principal, incident_id: UUID
    ) -> SecurityIncident:
        return await self._security_incident(principal.workspace_id, incident_id)

    async def list_security_incidents(
        self, principal: Principal
    ) -> list[SecurityIncident]:
        await self._scope(principal.workspace_id)
        return list(
            await self._session.scalars(
                select(SecurityIncident)
                .where(SecurityIncident.workspace_id == principal.workspace_id)
                .order_by(SecurityIncident.detected_at.desc())
            )
        )

    async def list_security_incident_events(
        self, principal: Principal, incident_id: UUID
    ) -> list[SecurityIncidentEvent]:
        await self.get_security_incident(principal, incident_id)
        return list(
            await self._session.scalars(
                select(SecurityIncidentEvent)
                .where(
                    SecurityIncidentEvent.workspace_id == principal.workspace_id,
                    SecurityIncidentEvent.incident_id == incident_id,
                )
                .order_by(SecurityIncidentEvent.sequence)
            )
        )

    async def list_breach_notifications(
        self, principal: Principal, incident_id: UUID
    ) -> list[BreachNotification]:
        await self.get_security_incident(principal, incident_id)
        return list(
            await self._session.scalars(
                select(BreachNotification)
                .where(
                    BreachNotification.workspace_id == principal.workspace_id,
                    BreachNotification.incident_id == incident_id,
                )
                .order_by(BreachNotification.delivered_at)
            )
        )

    async def _security_incident(
        self, workspace_id: UUID, incident_id: UUID, *, lock: bool = False
    ) -> SecurityIncident:
        await self._scope(workspace_id)
        query = select(SecurityIncident).where(
            SecurityIncident.workspace_id == workspace_id,
            SecurityIncident.id == incident_id,
        )
        if lock:
            query = query.with_for_update()
        value = await self._session.scalar(query)
        if value is None:
            raise AppError("SECURITY_INCIDENT_NOT_FOUND", "보안 사건을 찾을 수 없습니다.", 404)
        return value

    async def _append_incident_event(
        self,
        incident: SecurityIncident,
        *,
        actor_id: UUID | None,
        kind: SecurityIncidentEventKind,
        state_after: SecurityIncidentState,
        safe_summary: str,
        evidence_object_refs: list[str],
        evidence_hash: str,
        occurred_at: datetime,
        validate_transition: bool,
    ) -> SecurityIncidentEvent:
        if validate_transition and state_after.value != incident.state:
            ensure_security_incident_transition(incident.state, state_after.value)
        previous = await self._session.scalar(
            select(SecurityIncidentEvent)
            .where(
                SecurityIncidentEvent.workspace_id == incident.workspace_id,
                SecurityIncidentEvent.incident_id == incident.id,
            )
            .order_by(SecurityIncidentEvent.sequence.desc())
            .limit(1)
        )
        if (
            occurred_at.tzinfo is None
            or occurred_at < incident.detected_at
            or occurred_at > datetime.now(UTC) + timedelta(minutes=5)
            or (previous is not None and occurred_at < previous.occurred_at)
            or not is_sha256_hex(evidence_hash)
        ):
            raise AppError(
                "SECURITY_INCIDENT_EVENT_EVIDENCE_INVALID",
                "보안 사건 타임라인 증거가 올바르지 않습니다.",
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
        event_value = SecurityIncidentEvent(
            workspace_id=incident.workspace_id,
            incident_id=incident.id,
            sequence=sequence,
            kind=kind.value,
            actor_id=actor_id,
            state_after=state_after.value,
            safe_summary=safe_summary,
            evidence_object_refs=evidence_object_refs,
            evidence_hash=evidence_hash,
            previous_event_hash=None if previous is None else previous.event_hash,
            event_hash=append_evidence_hash(
                None if previous is None else previous.event_hash, payload
            ),
            occurred_at=occurred_at,
        )
        self._session.add(event_value)
        await self._session.flush()
        return event_value

    async def create_compliance_assessment(
        self,
        principal: Principal,
        data: ComplianceAssessmentCreate,
        *,
        verifier: ComplianceEvidenceVerifier,
    ) -> ComplianceAssessment:
        await self._scope(principal.workspace_id)
        existing = await self._session.scalar(
            select(ComplianceAssessment).where(
                ComplianceAssessment.workspace_id == principal.workspace_id,
                ComplianceAssessment.kind == data.kind.value,
                ComplianceAssessment.artifact_hash == data.artifact_hash,
                ComplianceAssessment.control_version == data.control_version,
            )
        )
        if existing is not None:
            return existing
        result = await verifier.verify_assessment(
            workspace_id=principal.workspace_id,
            kind=data.kind.value,
            artifact_ref=data.artifact_ref,
            artifact_hash=data.artifact_hash,
            control_version=data.control_version,
        )
        try:
            ComplianceDecision(result.decision)
        except ValueError as exc:
            raise AppError(
                "COMPLIANCE_DECISION_INVALID",
                "규정 준수 검증기의 판정이 올바르지 않습니다.",
                503,
            ) from exc
        if (
            result.verified_at.tzinfo is None
            or result.verified_at > datetime.now(UTC) + timedelta(minutes=5)
            or (
                result.expires_at is not None
                and (
                    result.expires_at.tzinfo is None
                    or result.expires_at <= result.verified_at
                )
            )
            or not is_sha256_hex(result.evidence_hash)
            or not result.verifier
            or len(result.verifier) > 160
        ):
            raise AppError(
                "COMPLIANCE_EVIDENCE_INVALID",
                "규정 준수 검증 증거가 올바르지 않습니다.",
                503,
            )
        value = ComplianceAssessment(
            workspace_id=principal.workspace_id,
            kind=data.kind.value,
            artifact_ref=data.artifact_ref,
            artifact_hash=data.artifact_hash,
            control_version=data.control_version,
            decision=result.decision,
            verifier=result.verifier,
            findings=redact_safe_metadata(list(result.findings)),
            evidence_hash=result.evidence_hash,
            verified_at=result.verified_at,
            expires_at=result.expires_at,
            requested_by=principal.subject_id,
        )
        self._session.add(value)
        await self._session.flush()
        await self._record(
            principal=principal,
            action="security.compliance.assessed",
            target_type="compliance_assessment",
            target_id=value.id,
            details={"kind": value.kind, "decision": value.decision},
        )
        return value

    async def list_compliance_assessments(
        self, principal: Principal
    ) -> list[ComplianceAssessment]:
        await self._scope(principal.workspace_id)
        return list(
            await self._session.scalars(
                select(ComplianceAssessment)
                .where(ComplianceAssessment.workspace_id == principal.workspace_id)
                .order_by(ComplianceAssessment.verified_at.desc())
            )
        )


def worker_principal(workspace_id: UUID) -> Principal:
    return Principal(
        subject_id=UUID(int=0),
        workspace_id=workspace_id,
        session_id=None,
        permissions=frozenset(),
        authentication_method="worker",
    )
