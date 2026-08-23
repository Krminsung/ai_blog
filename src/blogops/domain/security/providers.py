"""Trusted policy and external execution ports for security workflows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from blogops.core.errors import AppError


class DataRightsPolicy(Protocol):
    async def retention_bounds(
        self, workspace_id: UUID
    ) -> tuple[dict[str, int], dict[str, int | None]]: ...

    async def request_sla_days(self, *, workspace_id: UUID, request_kind: str) -> int: ...

    async def maximum_export_downloads(self, workspace_id: UUID) -> int: ...


@dataclass(frozen=True, slots=True)
class RetentionDispositionResult:
    data_class: str
    target_system: str
    cutoff_at: datetime
    disposition: str
    affected_records: int
    held_records: int
    passed: bool
    evidence_object_ref: str
    evidence_hash: str
    completed_at: datetime


class RetentionExecutor(Protocol):
    async def execute_retention_sweep(
        self,
        *,
        workspace_id: UUID,
        sweep_id: UUID,
        policy_snapshot: dict[str, Any],
        legal_hold_snapshot: tuple[dict[str, Any], ...],
        idempotency_key: str,
    ) -> tuple[RetentionDispositionResult, ...]: ...


@dataclass(frozen=True, slots=True)
class IncidentDeadlines:
    containment_due_at: datetime
    notification_due_at: datetime | None
    policy_version: str


class SecurityIncidentPolicy(Protocol):
    async def deadlines(
        self,
        *,
        workspace_id: UUID,
        severity: str,
        incident_type: str,
        detected_at: datetime,
        affected_data_classes: tuple[str, ...],
    ) -> IncidentDeadlines: ...


@dataclass(frozen=True, slots=True)
class IdentityVerificationResult:
    passed: bool
    provider_reference: str
    assurance_level: str
    evidence_hash: str
    verified_at: datetime
    failure_code: str | None = None


class SubjectIdentityVerifier(Protocol):
    async def verify_subject(
        self,
        *,
        workspace_id: UUID,
        request_id: UUID,
        subject_locator_ref: str,
        verification_token: str,
    ) -> IdentityVerificationResult: ...


@dataclass(frozen=True, slots=True)
class VerifiedDeletionWebhook:
    workspace_id: UUID
    provider_event_id: str
    subject_locator_ref: str
    data_classes: tuple[str, ...]
    occurred_at: datetime
    signature_key_version: str
    assurance_level: str
    evidence_hash: str


class DataDeletionWebhookVerifier(Protocol):
    async def verify_deletion_webhook(
        self,
        *,
        provider: str,
        body: bytes,
        headers: dict[str, str],
    ) -> VerifiedDeletionWebhook: ...


@dataclass(frozen=True, slots=True)
class PlannedPrivacyAction:
    kind: str
    data_classes: tuple[str, ...]
    target_system: str
    target_locator_ref: str
    sequence: int
    plan_metadata: dict[str, Any]


class DataRightsPlanner(Protocol):
    async def plan_request(
        self,
        *,
        workspace_id: UUID,
        request_id: UUID,
        request_kind: str,
        subject_locator_ref: str,
        data_classes: tuple[str, ...],
        retention_policy_snapshot: dict[str, Any],
    ) -> tuple[PlannedPrivacyAction, ...]: ...


@dataclass(frozen=True, slots=True)
class PrivacyActionExecutionResult:
    provider_operation_ref: str
    affected_records: int
    result_manifest: dict[str, Any]
    result_manifest_hash: str
    evidence_object_ref: str
    completed_at: datetime
    backup_erasure_due_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ExportArtifactResult:
    object_ref: str
    content_hash: str
    size_bytes: int
    manifest: dict[str, Any]
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class DownloadGrant:
    download_url: str
    expires_at: datetime
    delivery_reference: str


@dataclass(frozen=True, slots=True)
class BackupErasureVerificationResult:
    provider_reference: str
    verifier: str
    completed_at: datetime
    evidence_hash: str


class DataRightsExecutor(Protocol):
    async def execute_action(
        self,
        *,
        workspace_id: UUID,
        request_id: UUID,
        action_id: UUID,
        kind: str,
        target_system: str,
        target_locator_ref: str,
        data_classes: tuple[str, ...],
        idempotency_key: str,
    ) -> PrivacyActionExecutionResult: ...

    async def build_export(
        self,
        *,
        workspace_id: UUID,
        request_id: UUID,
        subject_locator_ref: str,
        data_classes: tuple[str, ...],
        idempotency_key: str,
    ) -> ExportArtifactResult: ...

    async def issue_download(
        self,
        *,
        workspace_id: UUID,
        artifact_id: UUID,
        object_ref: str,
        idempotency_key: str,
    ) -> DownloadGrant: ...

    async def verify_backup_erasure(
        self,
        *,
        workspace_id: UUID,
        certificate_id: UUID,
        evidence_object_ref: str,
        evidence_hash: str,
    ) -> BackupErasureVerificationResult: ...


@dataclass(frozen=True, slots=True)
class CopyrightTriageResult:
    accepted: bool
    decision_code: str
    temporary_action: str | None
    policy_version: str
    evidence_hash: str
    response_due_at: datetime


@dataclass(frozen=True, slots=True)
class CopyrightActionResult:
    action: str
    provider_reference: str
    evidence_object_ref: str
    evidence_hash: str
    completed_at: datetime


@dataclass(frozen=True, slots=True)
class CounterNoticeVerificationResult:
    passed: bool
    provider_reference: str
    assurance_level: str
    evidence_hash: str
    verified_at: datetime


class CopyrightEnforcementAdapter(Protocol):
    async def triage_notice(
        self,
        *,
        workspace_id: UUID,
        case_id: UUID,
        target_refs: tuple[dict[str, Any], ...],
        evidence_object_refs: tuple[str, ...],
    ) -> CopyrightTriageResult: ...

    async def apply_action(
        self,
        *,
        workspace_id: UUID,
        case_id: UUID,
        action: str,
        target_refs: tuple[dict[str, Any], ...],
        idempotency_key: str,
    ) -> CopyrightActionResult: ...

    async def verify_counter_notice(
        self,
        *,
        workspace_id: UUID,
        case_id: UUID,
        respondent_contact_ref: str,
        statement_object_ref: str,
        statement_hash: str,
    ) -> CounterNoticeVerificationResult: ...


@dataclass(frozen=True, slots=True)
class IncidentNotificationResult:
    provider_message_ref: str
    delivered_at: datetime
    evidence_hash: str


class IncidentNotificationAdapter(Protocol):
    async def notify(
        self,
        *,
        workspace_id: UUID,
        incident_id: UUID,
        audience: str,
        destination_ref: str,
        template_version: str,
        safe_payload: dict[str, Any],
        idempotency_key: str,
    ) -> IncidentNotificationResult: ...


@dataclass(frozen=True, slots=True)
class ComplianceVerificationResult:
    decision: str
    verifier: str
    verified_at: datetime
    expires_at: datetime | None
    findings: tuple[dict[str, Any], ...]
    evidence_hash: str


class ComplianceEvidenceVerifier(Protocol):
    async def verify_assessment(
        self,
        *,
        workspace_id: UUID,
        kind: str,
        artifact_ref: str,
        artifact_hash: str,
        control_version: str,
    ) -> ComplianceVerificationResult: ...


class FailClosedSecurityAdapters:
    async def retention_bounds(
        self, workspace_id: UUID
    ) -> tuple[dict[str, int], dict[str, int | None]]:
        del workspace_id
        raise AppError(
            "RETENTION_AUTHORITY_UNAVAILABLE",
            "보존 기간 법률·계약 정책이 구성되지 않았습니다.",
            503,
        )

    async def request_sla_days(self, *, workspace_id: UUID, request_kind: str) -> int:
        del workspace_id, request_kind
        raise AppError(
            "DATA_RIGHTS_POLICY_UNAVAILABLE",
            "데이터 권리 요청 SLA 정책이 구성되지 않았습니다.",
            503,
        )

    async def maximum_export_downloads(self, workspace_id: UUID) -> int:
        del workspace_id
        raise AppError(
            "DATA_EXPORT_POLICY_UNAVAILABLE",
            "데이터 내보내기 정책이 구성되지 않았습니다.",
            503,
        )

    async def execute_retention_sweep(
        self, **_kwargs: Any
    ) -> tuple[RetentionDispositionResult, ...]:
        raise AppError(
            "RETENTION_EXECUTOR_UNAVAILABLE",
            "자동 보존 만료 실행기가 구성되지 않았습니다.",
            503,
        )

    async def deadlines(self, **_kwargs: Any) -> IncidentDeadlines:
        raise AppError(
            "SECURITY_INCIDENT_POLICY_UNAVAILABLE",
            "보안 사건 대응·통지 기한 정책이 구성되지 않았습니다.",
            503,
        )

    async def verify_subject(
        self,
        *,
        workspace_id: UUID,
        request_id: UUID,
        subject_locator_ref: str,
        verification_token: str,
    ) -> IdentityVerificationResult:
        del workspace_id, request_id, subject_locator_ref, verification_token
        raise AppError(
            "SUBJECT_VERIFIER_UNAVAILABLE",
            "정보주체 신원 확인기가 구성되지 않았습니다.",
            503,
        )

    async def verify_deletion_webhook(self, **_kwargs: Any) -> VerifiedDeletionWebhook:
        raise AppError(
            "DATA_DELETION_WEBHOOK_VERIFIER_UNAVAILABLE",
            "플랫폼 삭제 요청 서명 검증기가 구성되지 않았습니다.",
            503,
        )

    async def plan_request(
        self,
        *,
        workspace_id: UUID,
        request_id: UUID,
        request_kind: str,
        subject_locator_ref: str,
        data_classes: tuple[str, ...],
        retention_policy_snapshot: dict[str, Any],
    ) -> tuple[PlannedPrivacyAction, ...]:
        del (
            workspace_id,
            request_id,
            request_kind,
            subject_locator_ref,
            data_classes,
            retention_policy_snapshot,
        )
        raise AppError(
            "DATA_RIGHTS_PLANNER_UNAVAILABLE",
            "데이터 권리 실행 계획기가 구성되지 않았습니다.",
            503,
        )

    async def execute_action(self, **_kwargs: Any) -> PrivacyActionExecutionResult:
        raise AppError(
            "DATA_RIGHTS_EXECUTOR_UNAVAILABLE",
            "데이터 권리 실행기가 구성되지 않았습니다.",
            503,
        )

    async def build_export(self, **_kwargs: Any) -> ExportArtifactResult:
        raise AppError(
            "DATA_EXPORT_EXECUTOR_UNAVAILABLE",
            "데이터 내보내기 실행기가 구성되지 않았습니다.",
            503,
        )

    async def issue_download(self, **_kwargs: Any) -> DownloadGrant:
        raise AppError(
            "DATA_EXPORT_DELIVERY_UNAVAILABLE",
            "보안 다운로드 제공자가 구성되지 않았습니다.",
            503,
        )

    async def verify_backup_erasure(
        self, **_kwargs: Any
    ) -> BackupErasureVerificationResult:
        raise AppError(
            "BACKUP_ERASURE_VERIFIER_UNAVAILABLE",
            "백업 삭제 증거 검증기가 구성되지 않았습니다.",
            503,
        )

    async def triage_notice(self, **_kwargs: Any) -> CopyrightTriageResult:
        raise AppError(
            "COPYRIGHT_REVIEW_UNAVAILABLE",
            "저작권 신고 검토기가 구성되지 않았습니다.",
            503,
        )

    async def apply_action(self, **_kwargs: Any) -> CopyrightActionResult:
        raise AppError(
            "COPYRIGHT_ACTION_UNAVAILABLE",
            "저작권 임시조치 실행기가 구성되지 않았습니다.",
            503,
        )

    async def verify_counter_notice(
        self, **_kwargs: Any
    ) -> CounterNoticeVerificationResult:
        raise AppError(
            "COPYRIGHT_COUNTER_NOTICE_VERIFIER_UNAVAILABLE",
            "저작권 이의 제기 신원 검증기가 구성되지 않았습니다.",
            503,
        )

    async def notify(self, **_kwargs: Any) -> IncidentNotificationResult:
        raise AppError(
            "INCIDENT_NOTIFICATION_UNAVAILABLE",
            "침해 통지 제공자가 구성되지 않았습니다.",
            503,
        )

    async def verify_assessment(self, **_kwargs: Any) -> ComplianceVerificationResult:
        raise AppError(
            "COMPLIANCE_VERIFIER_UNAVAILABLE",
            "규정 준수 증거 검증기가 구성되지 않았습니다.",
            503,
        )
