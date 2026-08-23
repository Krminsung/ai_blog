"""Celery entry points for durable knowledge ingestion and deletion jobs."""

import asyncio
from uuid import UUID

from celery import shared_task

from blogops.core.config import get_settings
from blogops.db.session import apply_workspace_scope, get_database
from blogops.domain.knowledge.adapters import (
    ClamAVScanner,
    HashingEmbeddingProvider,
    get_safe_fetcher,
)
from blogops.domain.knowledge.enums import KnowledgeJobState
from blogops.domain.knowledge.services import process_knowledge_job
from blogops.domain.knowledge.storage import get_object_storage


async def _run_job(workspace_id: UUID, job_id: UUID) -> str:
    settings = get_settings()
    database = get_database()
    try:
        async with database.session_factory() as session:
            async with session.begin():
                await apply_workspace_scope(session, workspace_id)
                job = await process_knowledge_job(
                    session,
                    workspace_id=workspace_id,
                    job_id=job_id,
                    storage=get_object_storage(),
                    scanner=ClamAVScanner(
                        settings.clamav_host,
                        settings.clamav_port,
                        settings.clamav_timeout_seconds,
                    ),
                    embeddings=HashingEmbeddingProvider(),
                    fetcher=get_safe_fetcher(),
                    ocr=None,
                    max_upload_bytes=settings.knowledge_max_upload_bytes,
                    max_fetch_bytes=settings.knowledge_fetch_max_bytes,
                )
                return job.state
    finally:
        await database.close()
        get_database.cache_clear()


@shared_task(bind=True, name="knowledge.process", max_retries=2)
def process_knowledge_job_task(task, workspace_id: str, job_id: str) -> str:
    state = asyncio.run(_run_job(UUID(workspace_id), UUID(job_id)))
    if state == KnowledgeJobState.RETRYABLE_FAILED.value:
        raise task.retry(countdown=min(60, 5 * (2 ** task.request.retries)))
    return state


def enqueue_knowledge_job(workspace_id: UUID, job_id: UUID) -> None:
    # FastAPI schedules this after request dependency teardown; the short delay also keeps
    # broker delivery behind the database commit on high-latency deployments.
    process_knowledge_job_task.apply_async(
        args=(str(workspace_id), str(job_id)), countdown=1
    )
