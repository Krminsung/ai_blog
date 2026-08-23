"""Celery consumer and enqueue boundary for repurposing jobs."""

from __future__ import annotations

import asyncio
from typing import Protocol
from uuid import UUID

from celery import shared_task
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.core.errors import AppError
from blogops.db.session import apply_workspace_scope, get_database
from blogops.domain.jobs.state import JobState, StepState
from blogops.domain.repurpose.models import (
    RepurposeInputSnapshot,
    RepurposeSnapshotCitation,
    RepurposeSnapshotClaim,
)
from blogops.domain.repurpose.providers import (
    ModelGatewayRegistry,
    RepurposeGenerationRequest,
)
from blogops.domain.repurpose.repository import RepurposeRepository
from blogops.domain.repurpose.service import RepurposeService


class RepurposeJobExecutor(Protocol):
    async def execute(
        self, session: AsyncSession, *, workspace_id: UUID, job_id: UUID
    ) -> None: ...


class ApprovedModelRepurposeExecutor:
    def __init__(self, registry: ModelGatewayRegistry) -> None:
        self.registry = registry

    async def execute(
        self, session: AsyncSession, *, workspace_id: UUID, job_id: UUID
    ) -> None:
        repo = RepurposeRepository(session, workspace_id)
        job = await repo.job(job_id)
        gateway = self.registry.require(
            job.model_provider, job.model_name, job.model_version
        )
        service = RepurposeService(session)
        for item in await repo.job_items(job.id):
            if item.state == StepState.SUCCEEDED.value:
                continue
            snapshot = await session.scalar(
                select(RepurposeInputSnapshot).where(
                    RepurposeInputSnapshot.workspace_id == workspace_id,
                    RepurposeInputSnapshot.id == item.snapshot_id,
                )
            )
            if snapshot is None:
                raise AppError(
                    "REPURPOSE_SNAPSHOT_NOT_FOUND",
                    "리퍼포징 입력 스냅샷이 없습니다.",
                    404,
                )
            claims = list(
                await session.scalars(
                    select(RepurposeSnapshotClaim)
                    .where(
                        RepurposeSnapshotClaim.workspace_id == workspace_id,
                        RepurposeSnapshotClaim.snapshot_id == snapshot.id,
                    )
                    .order_by(RepurposeSnapshotClaim.claim_key)
                )
            )
            claim_link_ids = [row.id for row in claims]
            citations = (
                list(
                    await session.scalars(
                        select(RepurposeSnapshotCitation)
                        .where(
                            RepurposeSnapshotCitation.workspace_id == workspace_id,
                            RepurposeSnapshotCitation.snapshot_claim_id.in_(
                                claim_link_ids
                            ),
                        )
                        .order_by(RepurposeSnapshotCitation.id)
                    )
                )
                if claim_link_ids
                else []
            )
            outputs = list(
                await gateway.generate(
                    RepurposeGenerationRequest(
                        workspace_id=workspace_id,
                        job_id=job.id,
                        item_id=item.id,
                        source_snapshot={
                            "content_id": str(snapshot.content_id),
                            "content_version_id": str(snapshot.content_version_id),
                            "content_hash": snapshot.source_content_hash,
                            "title": snapshot.source_title,
                            "document": snapshot.source_document,
                            "plain_text": snapshot.source_plain_text,
                            "claim_lineage_hash": snapshot.claim_lineage_hash,
                            "citation_lineage_hash": snapshot.citation_lineage_hash,
                            "claims": [
                                {
                                    "claim_id": str(row.claim_id),
                                    "claim_key": row.claim_key,
                                    "claim_hash": row.claim_hash,
                                    "statement": row.statement,
                                    "status": row.status,
                                }
                                for row in claims
                            ],
                            "citations": [row.citation_snapshot for row in citations],
                        },
                        template_snapshot={
                            **snapshot.template_snapshot,
                            "platform_policy": snapshot.platform_policy_snapshot,
                            "disclosure_policy": snapshot.disclosure_policy_snapshot,
                            "safety_policy": snapshot.safety_policy_snapshot,
                            "pii_policy": snapshot.pii_policy_snapshot,
                            "approval_policy": snapshot.approval_policy_snapshot,
                        },
                        model_config=dict(job.request_snapshot["model_config"]),
                        model_config_hash=job.model_config_hash,
                        variant_count=item.variant_count,
                    )
                )
            )
            if len(outputs) != item.variant_count:
                raise AppError(
                    "REPURPOSE_VARIANT_COUNT_MISMATCH",
                    "모델이 요청된 수와 다른 변형 수를 반환했습니다.",
                    502,
                )
            for variant_no, output in enumerate(outputs, start=1):
                await service.record_variant(
                    workspace_id=workspace_id,
                    item_id=item.id,
                    variant_no=variant_no,
                    generated=output,
                    actual_cost=output.actual_cost,
                )


_executor: RepurposeJobExecutor | None = None


def configure_repurpose_executor(executor: RepurposeJobExecutor) -> None:
    global _executor
    _executor = executor


async def _run_job(workspace_id: UUID, job_id: UUID) -> str:
    database = get_database()
    try:
        async with database.session_factory() as session:
            async with session.begin():
                await apply_workspace_scope(session, workspace_id)
                service = RepurposeService(session)
                row = await service.finalize_cancellation(
                    workspace_id=workspace_id, job_id=job_id
                )
                if row.state != JobState.QUEUED.value:
                    return row.state
                if _executor is None:
                    row = await service.fail_runtime(
                        workspace_id=workspace_id,
                        job_id=job_id,
                        code="REPURPOSE_RUNTIME_UNAVAILABLE",
                        detail="approved repurposing model runtime is not configured",
                        retryable=True,
                    )
                    return row.state
                row = await service.mark_generating(
                    workspace_id=workspace_id, job_id=job_id
                )
                if row.state == JobState.CANCELLED.value:
                    return row.state
                try:
                    await _executor.execute(
                        session, workspace_id=workspace_id, job_id=job_id
                    )
                except AppError as exc:
                    row = await service.fail_runtime(
                        workspace_id=workspace_id,
                        job_id=job_id,
                        code=exc.code,
                        detail=exc.message,
                        retryable=_is_retryable_runtime_error(exc),
                    )
                    return row.state
                except Exception as exc:
                    row = await service.fail_runtime(
                        workspace_id=workspace_id,
                        job_id=job_id,
                        code="REPURPOSE_MODEL_FAILED",
                        detail=type(exc).__name__,
                        retryable=True,
                    )
                    return row.state
                row = await service.finalize_cancellation(
                    workspace_id=workspace_id, job_id=job_id
                )
                if row.state == JobState.CANCELLED.value:
                    return row.state
                row = await service.complete_job(workspace_id=workspace_id, job_id=job_id)
                return row.state
    finally:
        await database.close()
        get_database.cache_clear()


@shared_task(name="repurpose.process")
def process_repurpose_job_task(workspace_id: str, job_id: str) -> str:
    return asyncio.run(_run_job(UUID(workspace_id), UUID(job_id)))


def enqueue_repurpose_job(workspace_id: UUID, job_id: UUID) -> None:
    process_repurpose_job_task.apply_async(args=(str(workspace_id), str(job_id)), countdown=1)


def _is_retryable_runtime_error(error: Exception) -> bool:
    if not isinstance(error, AppError):
        return True
    return error.status_code in {408, 425, 429} or error.status_code >= 500
