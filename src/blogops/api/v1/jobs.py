"""Cross-domain job status and control endpoints."""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Header, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.core.context import Principal
from blogops.core.errors import AppError
from blogops.core.permissions import Permission, get_principal
from blogops.db.session import get_job_session
from blogops.domain.analytics.enums import AnalyticsCommandKind
from blogops.domain.analytics.models import AnalyticsReportRun, AnalyticsSyncRun
from blogops.domain.analytics.schemas import JobCommandCreate as AnalyticsJobCommandCreate
from blogops.domain.analytics.service import AnalyticsService
from blogops.domain.analytics.tasks import (
    enqueue_analytics_report,
    enqueue_analytics_sync,
)
from blogops.domain.b2b.models import ClientProvisioningRequest
from blogops.domain.billing.models import PaymentCommand
from blogops.domain.bulk.models import BulkJob
from blogops.domain.bulk.schemas import BulkCommandRequest
from blogops.domain.bulk.service import BulkService
from blogops.domain.bulk.tasks import enqueue_bulk_job
from blogops.domain.developer.models import WebhookDelivery
from blogops.domain.generation.models import GenerationJob
from blogops.domain.generation.service import GenerationService
from blogops.domain.generation.snapshots import SQLAlchemyGenerationSnapshotResolver
from blogops.domain.generation.tasks import (
    enqueue_generation_job,
    settle_generation_terminal,
)
from blogops.domain.jobs.state import JobState
from blogops.domain.keywords.models import KeywordResearchJob
from blogops.domain.keywords.services import request_cancel, request_retry
from blogops.domain.keywords.tasks import enqueue_keyword_job
from blogops.domain.knowledge.models import KnowledgeJob
from blogops.domain.media.models import MediaOperationJob
from blogops.domain.media.schemas import MediaJobCommandRequest
from blogops.domain.media.service import MediaService
from blogops.domain.media.tasks import enqueue_media_operation
from blogops.domain.operations.models import (
    BackupRun,
    GAAssessment,
    RecoveryExercise,
)
from blogops.domain.publishing.models import PublishJob, PublishingConnectionJob
from blogops.domain.publishing.references import SQLAlchemyPublishingReadinessResolver
from blogops.domain.publishing.schemas import CancelPublishCreate, RetryPublishCreate
from blogops.domain.publishing.service import PublishingService
from blogops.domain.publishing.tasks import enqueue_publish_job
from blogops.domain.repurpose.enums import RepurposeCommandKind
from blogops.domain.repurpose.models import RepurposeJob
from blogops.domain.repurpose.schemas import (
    RepurposeJobCommandCreate,
)
from blogops.domain.repurpose.service import RepurposeService
from blogops.domain.repurpose.tasks import enqueue_repurpose_job
from blogops.domain.research.models import ResearchRun
from blogops.domain.security.models import CopyrightCase, PrivacyRequest, RetentionSweep

router = APIRouter(prefix="/jobs", tags=["jobs"])
JobSession = Annotated[AsyncSession, Depends(get_job_session)]
AuthenticatedPrincipal = Annotated[Principal, Depends(get_principal)]
OptionalIdempotencyKey = Annotated[
    str | None,
    Header(alias="Idempotency-Key", min_length=1, max_length=255),
]
ResolvedJob = (
    KeywordResearchJob
    | KnowledgeJob
    | GenerationJob
    | ResearchRun
    | MediaOperationJob
    | BulkJob
    | AnalyticsSyncRun
    | AnalyticsReportRun
    | RepurposeJob
    | PublishJob
    | PublishingConnectionJob
    | PaymentCommand
    | ClientProvisioningRequest
    | WebhookDelivery
    | RetentionSweep
    | PrivacyRequest
    | CopyrightCase
    | BackupRun
    | RecoveryExercise
    | GAAssessment
)


class JobView(BaseModel):
    job_id: UUID
    kind: str
    state: str
    progress_percent: float | None = None
    attempt: int
    error_code: str | None = None
    result: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None


def _require(principal: Principal, permission: Permission) -> None:
    if permission.value not in principal.permissions:
        raise AppError(
            code="PERMISSION_DENIED",
            message="이 작업을 조회하거나 변경할 권한이 없습니다.",
            status_code=403,
            fields=[{"path": "permissions", "reason": permission.value}],
        )


def _authorize_stage9_job(
    principal: Principal, kind: str, job: ResolvedJob
) -> bool:
    if kind == "PRIVACY_REQUEST" and isinstance(job, PrivacyRequest):
        if (
            job.requested_by != principal.subject_id
            and Permission.PRIVACY_READ.value not in principal.permissions
        ):
            raise AppError("PERMISSION_DENIED", "이 작업을 조회할 권한이 없습니다.", 403)
        return True
    if kind == "COPYRIGHT_CASE" and isinstance(job, CopyrightCase):
        if (
            job.reported_by != principal.subject_id
            and Permission.SECURITY_READ.value not in principal.permissions
        ):
            raise AppError("PERMISSION_DENIED", "이 작업을 조회할 권한이 없습니다.", 403)
        return True
    if kind == "RETENTION_SWEEP":
        _require(principal, Permission.PRIVACY_READ)
        return True
    if kind in {"BACKUP_RUN", "RECOVERY_EXERCISE", "GA_ASSESSMENT"}:
        if "platform:operate" not in principal.permissions:
            raise AppError("PERMISSION_DENIED", "플랫폼 작업 조회 권한이 없습니다.", 403)
        return True
    return False


async def _resolve_job(
    session: AsyncSession, principal: Principal, job_id: UUID
) -> tuple[str, ResolvedJob]:
    keyword_job = await session.scalar(
        select(KeywordResearchJob).where(
            KeywordResearchJob.workspace_id == principal.workspace_id,
            KeywordResearchJob.id == job_id,
        )
    )
    knowledge_job = await session.scalar(
        select(KnowledgeJob).where(
            KnowledgeJob.workspace_id == principal.workspace_id,
            KnowledgeJob.id == job_id,
        )
    )
    generation_job = await session.scalar(
        select(GenerationJob).where(
            GenerationJob.workspace_id == principal.workspace_id,
            GenerationJob.id == job_id,
        )
    )
    research_run = await session.scalar(
        select(ResearchRun).where(
            ResearchRun.workspace_id == principal.workspace_id,
            ResearchRun.id == job_id,
        )
    )
    media_job = await session.scalar(
        select(MediaOperationJob).where(
            MediaOperationJob.workspace_id == principal.workspace_id,
            MediaOperationJob.id == job_id,
        )
    )
    bulk_job = await session.scalar(
        select(BulkJob).where(
            BulkJob.workspace_id == principal.workspace_id,
            BulkJob.id == job_id,
        )
    )
    analytics_sync = await session.scalar(
        select(AnalyticsSyncRun).where(
            AnalyticsSyncRun.workspace_id == principal.workspace_id,
            AnalyticsSyncRun.id == job_id,
        )
    )
    analytics_report = await session.scalar(
        select(AnalyticsReportRun).where(
            AnalyticsReportRun.workspace_id == principal.workspace_id,
            AnalyticsReportRun.id == job_id,
        )
    )
    repurpose_job = await session.scalar(
        select(RepurposeJob).where(
            RepurposeJob.workspace_id == principal.workspace_id,
            RepurposeJob.id == job_id,
        )
    )
    publish_job = await session.scalar(
        select(PublishJob).where(
            PublishJob.workspace_id == principal.workspace_id,
            PublishJob.id == job_id,
        )
    )
    publishing_connection_job = await session.scalar(
        select(PublishingConnectionJob).where(
            PublishingConnectionJob.workspace_id == principal.workspace_id,
            PublishingConnectionJob.id == job_id,
        )
    )
    payment_command = await session.scalar(
        select(PaymentCommand).where(
            PaymentCommand.workspace_id == principal.workspace_id,
            PaymentCommand.id == job_id,
        )
    )
    provisioning_request = await session.scalar(
        select(ClientProvisioningRequest).where(
            ClientProvisioningRequest.workspace_id == principal.workspace_id,
            ClientProvisioningRequest.id == job_id,
        )
    )
    webhook_delivery = await session.scalar(
        select(WebhookDelivery).where(
            WebhookDelivery.workspace_id == principal.workspace_id,
            WebhookDelivery.id == job_id,
        )
    )
    retention_sweep = await session.scalar(
        select(RetentionSweep).where(
            RetentionSweep.workspace_id == principal.workspace_id,
            RetentionSweep.id == job_id,
        )
    )
    privacy_request = await session.scalar(
        select(PrivacyRequest).where(
            PrivacyRequest.workspace_id == principal.workspace_id,
            PrivacyRequest.id == job_id,
        )
    )
    copyright_case = await session.scalar(
        select(CopyrightCase).where(
            CopyrightCase.workspace_id == principal.workspace_id,
            CopyrightCase.id == job_id,
        )
    )
    backup_run = await session.scalar(select(BackupRun).where(BackupRun.id == job_id))
    recovery_exercise = await session.scalar(
        select(RecoveryExercise).where(RecoveryExercise.id == job_id)
    )
    ga_assessment = await session.scalar(
        select(GAAssessment).where(GAAssessment.id == job_id)
    )
    matches = [
        ("KEYWORD_RESEARCH", keyword_job),
        ("KNOWLEDGE", knowledge_job),
        ("CONTENT_GENERATION", generation_job),
        ("CONTENT_RESEARCH", research_run),
        ("MEDIA_OPERATION", media_job),
        ("BULK_CAMPAIGN", bulk_job),
        ("ANALYTICS_SYNC", analytics_sync),
        ("ANALYTICS_REPORT", analytics_report),
        ("REPURPOSE_JOB", repurpose_job),
        ("PUBLISH_JOB", publish_job),
        ("PUBLISHING_CONNECTION_JOB", publishing_connection_job),
        ("PAYMENT_COMMAND", payment_command),
        ("CLIENT_PROVISIONING", provisioning_request),
        ("WEBHOOK_DELIVERY", webhook_delivery),
        ("RETENTION_SWEEP", retention_sweep),
        ("PRIVACY_REQUEST", privacy_request),
        ("COPYRIGHT_CASE", copyright_case),
        ("BACKUP_RUN", backup_run),
        ("RECOVERY_EXERCISE", recovery_exercise),
        ("GA_ASSESSMENT", ga_assessment),
    ]
    found = [(kind, job) for kind, job in matches if job is not None]
    if not found:
        raise AppError(code="JOB_NOT_FOUND", message="작업을 찾을 수 없습니다.", status_code=404)
    if len(found) > 1:
        raise AppError(
            code="JOB_ID_AMBIGUOUS",
            message="작업 식별자가 둘 이상의 작업 유형과 충돌했습니다.",
            status_code=409,
        )
    kind, job = found[0]
    return kind, job  # type: ignore[return-value]


def _view(kind: str, job: ResolvedJob) -> JobView:
    if isinstance(job, RetentionSweep):
        return JobView(
            job_id=job.id,
            kind=kind,
            state=job.state,
            attempt=0,
            error_code=job.failure_code,
            result={"policy_version_id": str(job.policy_version_id)},
            created_at=job.created_at,
            updated_at=job.updated_at,
            finished_at=job.completed_at,
        )
    if isinstance(job, PrivacyRequest):
        return JobView(
            job_id=job.id,
            kind=kind,
            state=job.state,
            attempt=0,
            error_code=job.failure_code or job.rejection_code,
            result={"request_kind": job.kind},
            created_at=job.created_at,
            updated_at=job.updated_at,
            finished_at=job.completed_at,
        )
    if isinstance(job, CopyrightCase):
        return JobView(
            job_id=job.id,
            kind=kind,
            state=job.state,
            attempt=0,
            error_code=job.failure_code,
            result=None,
            created_at=job.created_at,
            updated_at=job.updated_at,
            finished_at=job.resolved_at,
        )
    if isinstance(job, BackupRun):
        return JobView(
            job_id=job.id,
            kind=kind,
            state=job.state,
            attempt=job.attempt_count,
            error_code=job.failure_code,
            result={"policy_version_id": str(job.policy_version_id)},
            created_at=job.created_at,
            updated_at=job.updated_at,
            finished_at=job.completed_at,
        )
    if isinstance(job, RecoveryExercise):
        return JobView(
            job_id=job.id,
            kind=kind,
            state=job.state,
            attempt=0,
            error_code=job.failure_code,
            result={"backup_evidence_id": str(job.backup_evidence_id)},
            created_at=job.created_at,
            updated_at=job.updated_at,
            finished_at=job.completed_at,
        )
    if isinstance(job, GAAssessment):
        return JobView(
            job_id=job.id,
            kind=kind,
            state=job.state,
            attempt=0,
            error_code=job.failure_code,
            result={
                "release_ref": job.release_ref,
                "decision_hash": job.decision_hash,
            },
            created_at=job.created_at,
            updated_at=job.updated_at,
            finished_at=job.verified_at,
        )
    if isinstance(job, PaymentCommand):
        return JobView(
            job_id=job.id,
            kind=kind,
            state=job.state,
            attempt=0,
            error_code=job.error_code,
            result={"checkout_url": job.checkout_url} if job.checkout_url else None,
            created_at=job.created_at,
            updated_at=job.updated_at,
        )
    if isinstance(job, ClientProvisioningRequest):
        result = None
        if job.provisioned_workspace_id is not None:
            result = {
                "provisioned_workspace_id": str(job.provisioned_workspace_id),
                "provider_operation_ref": job.provider_operation_ref,
            }
        return JobView(
            job_id=job.id,
            kind=kind,
            state=job.state,
            attempt=0,
            error_code=job.error_code,
            result=result,
            created_at=job.created_at,
            updated_at=job.updated_at,
        )
    if isinstance(job, WebhookDelivery):
        finished_at = job.delivered_at or job.dead_lettered_at
        return JobView(
            job_id=job.id,
            kind=kind,
            state=job.state,
            attempt=job.attempt_count,
            error_code=job.last_error_code,
            result=(
                {"delivered_at": job.delivered_at.isoformat()}
                if job.delivered_at
                else None
            ),
            created_at=job.created_at,
            updated_at=job.updated_at,
            finished_at=finished_at,
        )
    if isinstance(job, AnalyticsSyncRun):
        return JobView(
            job_id=job.id,
            kind=kind,
            state=job.state,
            attempt=job.attempt,
            error_code=job.error_code,
            result=job.result,
            created_at=job.created_at,
            updated_at=job.updated_at,
            finished_at=job.finished_at,
        )
    if isinstance(job, AnalyticsReportRun):
        return JobView(
            job_id=job.id,
            kind=kind,
            state=job.state,
            attempt=job.attempt,
            error_code=job.error_code,
            created_at=job.created_at,
            updated_at=job.updated_at,
            finished_at=job.finished_at,
        )
    if isinstance(job, RepurposeJob):
        return JobView(
            job_id=job.id,
            kind=kind,
            state=job.state,
            attempt=job.attempt,
            error_code=job.error_code,
            result={
                "item_count": job.item_count,
                "variant_count": job.variant_count,
                "actual_cost": str(job.actual_cost),
                "budget_currency": job.budget_currency,
            },
            created_at=job.created_at,
            updated_at=job.updated_at,
            finished_at=job.finished_at,
        )
    if isinstance(job, PublishJob):
        return JobView(
            job_id=job.id,
            kind=kind,
            state=job.state,
            attempt=job.attempt,
            error_code=job.error_code,
            result=job.result_json,
            created_at=job.created_at,
            updated_at=job.updated_at,
            finished_at=job.finished_at,
        )
    if isinstance(job, PublishingConnectionJob):
        return JobView(
            job_id=job.id,
            kind=kind,
            state=job.state,
            attempt=job.attempt,
            error_code=job.error_code,
            result=job.safe_result_json,
            created_at=job.created_at,
            updated_at=job.updated_at,
            finished_at=job.finished_at,
        )
    if isinstance(job, KeywordResearchJob):
        return JobView(
            job_id=job.id,
            kind=kind,
            state=job.state,
            progress_percent=job.progress_percent,
            attempt=job.attempt,
            error_code=job.error_code,
            result=job.result_json,
            created_at=job.created_at,
            updated_at=job.updated_at,
            finished_at=job.finished_at,
        )
    if isinstance(job, KnowledgeJob):
        return JobView(
            job_id=job.id,
            kind=kind,
            state=job.state,
            attempt=job.attempt,
            error_code=job.error_code,
            result=job.result_json,
            created_at=job.created_at,
            updated_at=job.updated_at,
        )
    if isinstance(job, GenerationJob):
        return JobView(
            job_id=job.id,
            kind=kind,
            state=job.state,
            attempt=job.attempt,
            error_code=job.error_code,
            result=job.result,
            created_at=job.created_at,
            updated_at=job.updated_at,
            finished_at=job.finished_at,
        )
    if isinstance(job, MediaOperationJob):
        return JobView(
            job_id=job.id,
            kind=kind,
            state=job.state,
            attempt=job.attempt,
            error_code=job.error_code,
            result=job.result_json,
            created_at=job.created_at,
            updated_at=job.updated_at,
            finished_at=job.finished_at,
        )
    if isinstance(job, BulkJob):
        return JobView(
            job_id=job.id,
            kind=kind,
            state=job.state,
            progress_percent=float(job.progress_percent),
            attempt=0,
            error_code=job.error_code,
            result={
                "processed_rows": job.processed_rows,
                "succeeded_rows": job.succeeded_rows,
                "review_rows": job.review_rows,
                "failed_rows": job.failed_rows,
                "cancelled_rows": job.cancelled_rows,
                "result_manifest_ref": job.result_manifest_ref,
            },
            created_at=job.created_at,
            updated_at=job.updated_at,
            finished_at=job.finished_at,
        )
    return JobView(
        job_id=job.id,
        kind=kind,
        state=job.state,
        attempt=0,
        error_code=job.error_code,
        result=(
            {"approved_source_set_hash": job.approved_source_set_hash}
            if job.approved_source_set_hash
            else None
        ),
        created_at=job.created_at,
        updated_at=job.updated_at,
        finished_at=job.finished_at,
    )


def _generation_service(session: AsyncSession) -> GenerationService:
    from blogops.domain.billing.adapters import create_generation_budget_gateway

    return GenerationService(
        session,
        snapshots=SQLAlchemyGenerationSnapshotResolver(session),
        budget=create_generation_budget_gateway(session),
    )


def _publishing_service(session: AsyncSession) -> PublishingService:
    return PublishingService(
        session,
        readiness=SQLAlchemyPublishingReadinessResolver(session),
    )


@router.get("/{job_id}", response_model=JobView)
async def get_job(
    job_id: UUID,
    session: JobSession,
    principal: AuthenticatedPrincipal,
) -> JobView:
    kind, job = await _resolve_job(session, principal, job_id)
    if _authorize_stage9_job(principal, kind, job):
        return _view(kind, job)
    permission = {
        "KEYWORD_RESEARCH": Permission.KEYWORD_READ,
        "KNOWLEDGE": Permission.KNOWLEDGE_READ,
        "CONTENT_GENERATION": Permission.CONTENT_READ,
        "CONTENT_RESEARCH": Permission.CONTENT_READ,
        "MEDIA_OPERATION": Permission.MEDIA_READ,
        "BULK_CAMPAIGN": Permission.BULK_READ,
        "ANALYTICS_SYNC": Permission.CONTENT_READ,
        "ANALYTICS_REPORT": Permission.CONTENT_READ,
        "REPURPOSE_JOB": Permission.CONTENT_READ,
        "PUBLISH_JOB": Permission.CONTENT_READ,
        "PUBLISHING_CONNECTION_JOB": Permission.CONTENT_READ,
        "PAYMENT_COMMAND": Permission.BILLING_READ,
        "CLIENT_PROVISIONING": Permission.AGENCY_READ,
        "WEBHOOK_DELIVERY": Permission.API_MANAGE,
    }[kind]
    _require(principal, permission)
    return _view(kind, job)


@router.post("/{job_id}/cancel", response_model=JobView, status_code=status.HTTP_202_ACCEPTED)
async def cancel_job(
    job_id: UUID,
    background_tasks: BackgroundTasks,
    session: JobSession,
    principal: AuthenticatedPrincipal,
    idempotency_key: OptionalIdempotencyKey = None,
) -> JobView:
    kind, job = await _resolve_job(session, principal, job_id)
    _authorize_stage9_job(principal, kind, job)
    if kind == "ANALYTICS_SYNC" and isinstance(job, AnalyticsSyncRun):
        _require(principal, Permission.CONTENT_WRITE)
        if idempotency_key is None:
            raise AppError(
                code="IDEMPOTENCY_KEY_REQUIRED",
                message="분석 작업 제어에는 Idempotency-Key가 필요합니다.",
                status_code=422,
            )
        updated = await AnalyticsService(session).command_sync(
            principal,
            job.id,
            AnalyticsJobCommandCreate(
                command=AnalyticsCommandKind.CANCEL,
                reason="common jobs API cancellation",
            ),
            idempotency_key=idempotency_key,
        )
        background_tasks.add_task(
            enqueue_analytics_sync,
            principal.workspace_id,
            updated.id,
        )
        return _view(kind, updated)
    if kind == "ANALYTICS_REPORT" and isinstance(job, AnalyticsReportRun):
        _require(principal, Permission.CONTENT_WRITE)
        if idempotency_key is None:
            raise AppError(
                code="IDEMPOTENCY_KEY_REQUIRED",
                message="분석 작업 제어에는 Idempotency-Key가 필요합니다.",
                status_code=422,
            )
        updated = await AnalyticsService(session).command_report(
            principal,
            job.id,
            AnalyticsJobCommandCreate(
                command=AnalyticsCommandKind.CANCEL,
                reason="common jobs API cancellation",
            ),
            idempotency_key=idempotency_key,
        )
        background_tasks.add_task(
            enqueue_analytics_report,
            principal.workspace_id,
            updated.id,
        )
        return _view(kind, updated)
    if kind == "REPURPOSE_JOB" and isinstance(job, RepurposeJob):
        _require(principal, Permission.CONTENT_WRITE)
        if idempotency_key is None:
            raise AppError(
                code="IDEMPOTENCY_KEY_REQUIRED",
                message="재가공 작업 제어에는 Idempotency-Key가 필요합니다.",
                status_code=422,
            )
        updated = await RepurposeService(session).command_job(
            principal,
            job.id,
            RepurposeJobCommandCreate(
                command=RepurposeCommandKind.CANCEL,
                reason="common jobs API cancellation",
            ),
            idempotency_key=idempotency_key,
        )
        background_tasks.add_task(
            enqueue_repurpose_job,
            principal.workspace_id,
            updated.id,
        )
        return _view(kind, updated)
    if kind == "PUBLISH_JOB" and isinstance(job, PublishJob):
        _require(principal, Permission.CONTENT_PUBLISH)
        updated = await _publishing_service(session).cancel_publish_job(
            principal,
            job.id,
            CancelPublishCreate(
                expected_lock_version=job.lock_version,
                reason="common jobs API cancellation",
            ),
        )
        background_tasks.add_task(
            enqueue_publish_job,
            principal.workspace_id,
            updated.id,
            None,
        )
        return _view(kind, updated)
    if kind == "MEDIA_OPERATION" and isinstance(job, MediaOperationJob):
        _require(principal, Permission.MEDIA_WRITE)
        if idempotency_key is None:
            raise AppError(
                code="IDEMPOTENCY_KEY_REQUIRED",
                message="이미지 작업 제어에는 Idempotency-Key가 필요합니다.",
                status_code=422,
            )
        updated = await MediaService(session).command_operation_job(
            principal,
            job.id,
            MediaJobCommandRequest(
                idempotency_key=idempotency_key,
                reason="common jobs API cancellation",
            ),
            command_kind="CANCEL",
        )
        background_tasks.add_task(
            enqueue_media_operation,
            principal.workspace_id,
            updated.id,
        )
        return _view(kind, updated)
    if kind == "BULK_CAMPAIGN" and isinstance(job, BulkJob):
        _require(principal, Permission.BULK_WRITE)
        if idempotency_key is None:
            raise AppError(
                code="IDEMPOTENCY_KEY_REQUIRED",
                message="대량 작업 제어에는 Idempotency-Key가 필요합니다.",
                status_code=422,
            )
        updated = await BulkService(session).cancel_job(
            principal,
            job.id,
            BulkCommandRequest(
                idempotency_key=idempotency_key,
                reason="common jobs API cancellation",
            ),
        )
        background_tasks.add_task(
            enqueue_bulk_job,
            principal.workspace_id,
            updated.id,
        )
        return _view(kind, updated)
    if kind == "CONTENT_GENERATION" and isinstance(job, GenerationJob):
        _require(principal, Permission.CONTENT_WRITE)
        if idempotency_key is None:
            raise AppError(
                code="IDEMPOTENCY_KEY_REQUIRED",
                message="콘텐츠 작업 제어에는 Idempotency-Key가 필요합니다.",
                status_code=422,
            )
        service = _generation_service(session)
        updated = await service.cancel_job(
            principal,
            job.id,
            idempotency_key=idempotency_key,
            reason="common jobs API cancellation",
        )
        if updated.state == JobState.CANCELLED.value:
            await settle_generation_terminal(
                session,
                service.budget,
                workspace_id=principal.workspace_id,
                job_id=updated.id,
            )
        elif updated.state == JobState.CANCEL_REQUESTED.value:
            background_tasks.add_task(
                enqueue_generation_job,
                principal.workspace_id,
                updated.id,
            )
        return _view(kind, updated)
    if kind != "KEYWORD_RESEARCH" or not isinstance(job, KeywordResearchJob):
        raise AppError(
            code="JOB_ACTION_UNSUPPORTED",
            message="이 작업 유형은 공통 취소 요청을 지원하지 않습니다.",
            status_code=409,
        )
    _require(principal, Permission.KEYWORD_WRITE)
    updated = await request_cancel(session, principal=principal, job_id=job.id)
    return _view(kind, updated)


@router.post("/{job_id}/retry", response_model=JobView, status_code=status.HTTP_202_ACCEPTED)
async def retry_job(
    job_id: UUID,
    background_tasks: BackgroundTasks,
    session: JobSession,
    principal: AuthenticatedPrincipal,
    idempotency_key: OptionalIdempotencyKey = None,
) -> JobView:
    kind, job = await _resolve_job(session, principal, job_id)
    _authorize_stage9_job(principal, kind, job)
    if kind == "ANALYTICS_SYNC" and isinstance(job, AnalyticsSyncRun):
        _require(principal, Permission.CONTENT_WRITE)
        if idempotency_key is None:
            raise AppError(
                code="IDEMPOTENCY_KEY_REQUIRED",
                message="분석 작업 제어에는 Idempotency-Key가 필요합니다.",
                status_code=422,
            )
        updated = await AnalyticsService(session).command_sync(
            principal,
            job.id,
            AnalyticsJobCommandCreate(
                command=AnalyticsCommandKind.RETRY,
                reason="common jobs API retry",
            ),
            idempotency_key=idempotency_key,
        )
        background_tasks.add_task(
            enqueue_analytics_sync,
            principal.workspace_id,
            updated.id,
        )
        return _view(kind, updated)
    if kind == "ANALYTICS_REPORT" and isinstance(job, AnalyticsReportRun):
        _require(principal, Permission.CONTENT_WRITE)
        if idempotency_key is None:
            raise AppError(
                code="IDEMPOTENCY_KEY_REQUIRED",
                message="분석 작업 제어에는 Idempotency-Key가 필요합니다.",
                status_code=422,
            )
        updated = await AnalyticsService(session).command_report(
            principal,
            job.id,
            AnalyticsJobCommandCreate(
                command=AnalyticsCommandKind.RETRY,
                reason="common jobs API retry",
            ),
            idempotency_key=idempotency_key,
        )
        background_tasks.add_task(
            enqueue_analytics_report,
            principal.workspace_id,
            updated.id,
        )
        return _view(kind, updated)
    if kind == "REPURPOSE_JOB" and isinstance(job, RepurposeJob):
        _require(principal, Permission.CONTENT_WRITE)
        if idempotency_key is None:
            raise AppError(
                code="IDEMPOTENCY_KEY_REQUIRED",
                message="재가공 작업 제어에는 Idempotency-Key가 필요합니다.",
                status_code=422,
            )
        updated = await RepurposeService(session).command_job(
            principal,
            job.id,
            RepurposeJobCommandCreate(
                command=RepurposeCommandKind.RETRY,
                reason="common jobs API retry",
            ),
            idempotency_key=idempotency_key,
        )
        background_tasks.add_task(
            enqueue_repurpose_job,
            principal.workspace_id,
            updated.id,
        )
        return _view(kind, updated)
    if kind == "PUBLISH_JOB" and isinstance(job, PublishJob):
        _require(principal, Permission.CONTENT_PUBLISH)
        updated = await _publishing_service(session).retry_publish_job(
            principal,
            job.id,
            RetryPublishCreate(
                expected_lock_version=job.lock_version,
                reason="common jobs API retry",
            ),
        )
        background_tasks.add_task(
            enqueue_publish_job,
            principal.workspace_id,
            updated.id,
            None,
        )
        return _view(kind, updated)
    if kind == "MEDIA_OPERATION" and isinstance(job, MediaOperationJob):
        _require(principal, Permission.MEDIA_WRITE)
        if idempotency_key is None:
            raise AppError(
                code="IDEMPOTENCY_KEY_REQUIRED",
                message="이미지 작업 제어에는 Idempotency-Key가 필요합니다.",
                status_code=422,
            )
        updated = await MediaService(session).command_operation_job(
            principal,
            job.id,
            MediaJobCommandRequest(
                idempotency_key=idempotency_key,
                reason="common jobs API retry",
            ),
            command_kind="RETRY",
        )
        background_tasks.add_task(
            enqueue_media_operation,
            principal.workspace_id,
            updated.id,
        )
        return _view(kind, updated)
    if kind == "CONTENT_GENERATION" and isinstance(job, GenerationJob):
        _require(principal, Permission.CONTENT_WRITE)
        if idempotency_key is None:
            raise AppError(
                code="IDEMPOTENCY_KEY_REQUIRED",
                message="콘텐츠 작업 제어에는 Idempotency-Key가 필요합니다.",
                status_code=422,
            )
        updated = await _generation_service(session).retry_job(
            principal,
            job.id,
            idempotency_key=idempotency_key,
            reason="common jobs API retry",
        )
        background_tasks.add_task(
            enqueue_generation_job,
            principal.workspace_id,
            updated.id,
        )
        return _view(kind, updated)
    if kind != "KEYWORD_RESEARCH" or not isinstance(job, KeywordResearchJob):
        raise AppError(
            code="JOB_ACTION_UNSUPPORTED",
            message="이 작업 유형은 공통 재시도 요청을 지원하지 않습니다.",
            status_code=409,
        )
    _require(principal, Permission.KEYWORD_WRITE)
    updated = await request_retry(session, principal=principal, job_id=job.id)
    background_tasks.add_task(enqueue_keyword_job, principal.workspace_id, updated.id)
    return _view(kind, updated)
