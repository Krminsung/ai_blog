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
from blogops.db.session import get_tenant_session
from blogops.domain.bulk.models import BulkJob
from blogops.domain.bulk.schemas import BulkCommandRequest
from blogops.domain.bulk.service import BulkService
from blogops.domain.bulk.tasks import enqueue_bulk_job
from blogops.domain.generation.models import GenerationJob
from blogops.domain.generation.providers import FailClosedBudgetEntitlementGateway
from blogops.domain.generation.service import GenerationService
from blogops.domain.generation.snapshots import SQLAlchemyGenerationSnapshotResolver
from blogops.domain.generation.tasks import enqueue_generation_job
from blogops.domain.keywords.models import KeywordResearchJob
from blogops.domain.keywords.services import request_cancel, request_retry
from blogops.domain.keywords.tasks import enqueue_keyword_job
from blogops.domain.knowledge.models import KnowledgeJob
from blogops.domain.media.models import MediaOperationJob
from blogops.domain.media.schemas import MediaJobCommandRequest
from blogops.domain.media.service import MediaService
from blogops.domain.media.tasks import enqueue_media_operation
from blogops.domain.research.models import ResearchRun

router = APIRouter(prefix="/jobs", tags=["jobs"])
TenantSession = Annotated[AsyncSession, Depends(get_tenant_session)]
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
    matches = [
        ("KEYWORD_RESEARCH", keyword_job),
        ("KNOWLEDGE", knowledge_job),
        ("CONTENT_GENERATION", generation_job),
        ("CONTENT_RESEARCH", research_run),
        ("MEDIA_OPERATION", media_job),
        ("BULK_CAMPAIGN", bulk_job),
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
    return GenerationService(
        session,
        snapshots=SQLAlchemyGenerationSnapshotResolver(session),
        budget=FailClosedBudgetEntitlementGateway(),
    )


@router.get("/{job_id}", response_model=JobView)
async def get_job(
    job_id: UUID,
    session: TenantSession,
    principal: AuthenticatedPrincipal,
) -> JobView:
    kind, job = await _resolve_job(session, principal, job_id)
    permission = {
        "KEYWORD_RESEARCH": Permission.KEYWORD_READ,
        "KNOWLEDGE": Permission.KNOWLEDGE_READ,
        "CONTENT_GENERATION": Permission.CONTENT_READ,
        "CONTENT_RESEARCH": Permission.CONTENT_READ,
        "MEDIA_OPERATION": Permission.MEDIA_READ,
        "BULK_CAMPAIGN": Permission.BULK_READ,
    }[kind]
    _require(principal, permission)
    return _view(kind, job)


@router.post("/{job_id}/cancel", response_model=JobView, status_code=status.HTTP_202_ACCEPTED)
async def cancel_job(
    job_id: UUID,
    background_tasks: BackgroundTasks,
    session: TenantSession,
    principal: AuthenticatedPrincipal,
    idempotency_key: OptionalIdempotencyKey = None,
) -> JobView:
    kind, job = await _resolve_job(session, principal, job_id)
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
        updated = await _generation_service(session).cancel_job(
            principal,
            job.id,
            idempotency_key=idempotency_key,
            reason="common jobs API cancellation",
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
    session: TenantSession,
    principal: AuthenticatedPrincipal,
    idempotency_key: OptionalIdempotencyKey = None,
) -> JobView:
    kind, job = await _resolve_job(session, principal, job_id)
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
