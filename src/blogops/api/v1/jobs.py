"""Cross-domain job status and control endpoints."""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.core.context import Principal
from blogops.core.errors import AppError
from blogops.core.permissions import Permission, get_principal
from blogops.db.session import get_tenant_session
from blogops.domain.keywords.models import KeywordResearchJob
from blogops.domain.keywords.services import request_cancel, request_retry
from blogops.domain.keywords.tasks import enqueue_keyword_job
from blogops.domain.knowledge.models import KnowledgeJob

router = APIRouter(prefix="/jobs", tags=["jobs"])
TenantSession = Annotated[AsyncSession, Depends(get_tenant_session)]
AuthenticatedPrincipal = Annotated[Principal, Depends(get_principal)]


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
) -> tuple[str, KeywordResearchJob | KnowledgeJob]:
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
    matches = [
        ("KEYWORD_RESEARCH", keyword_job),
        ("KNOWLEDGE", knowledge_job),
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


def _view(kind: str, job: KeywordResearchJob | KnowledgeJob) -> JobView:
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


@router.get("/{job_id}", response_model=JobView)
async def get_job(
    job_id: UUID,
    session: TenantSession,
    principal: AuthenticatedPrincipal,
) -> JobView:
    kind, job = await _resolve_job(session, principal, job_id)
    permission = (
        Permission.KEYWORD_READ if kind == "KEYWORD_RESEARCH" else Permission.KNOWLEDGE_READ
    )
    _require(principal, permission)
    return _view(kind, job)


@router.post("/{job_id}/cancel", response_model=JobView, status_code=status.HTTP_202_ACCEPTED)
async def cancel_job(
    job_id: UUID,
    session: TenantSession,
    principal: AuthenticatedPrincipal,
) -> JobView:
    kind, job = await _resolve_job(session, principal, job_id)
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
) -> JobView:
    kind, job = await _resolve_job(session, principal, job_id)
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
