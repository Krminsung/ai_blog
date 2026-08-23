"""Celery boundary for durable client workspace provisioning."""

from __future__ import annotations

import asyncio
from uuid import UUID

from celery import shared_task

from blogops.core.errors import AppError
from blogops.db.session import apply_workspace_scope, get_database
from blogops.domain.b2b.providers import FailClosedB2BAdapters, WorkspaceProvisioner
from blogops.domain.b2b.service import B2BService

_workspace_provisioner: WorkspaceProvisioner | None = None


def configure_b2b_worker_runtime(*, provisioner: WorkspaceProvisioner) -> None:
    """Install the approved client workspace provisioner at worker bootstrap."""

    global _workspace_provisioner
    _workspace_provisioner = provisioner


async def _run_client_provisioning(workspace_id: UUID, request_id: UUID) -> str:
    database = get_database()
    try:
        async with database.session_factory() as session:
            async with session.begin():
                await apply_workspace_scope(session, workspace_id)
                service = B2BService(session)
                try:
                    value = await service.execute_client_provisioning(
                        workspace_id,
                        request_id,
                        provisioner=_workspace_provisioner or FailClosedB2BAdapters(),
                    )
                except AppError as exc:
                    value = await service.fail_client_provisioning(
                        workspace_id,
                        request_id,
                        error_code=exc.code,
                    )
                except Exception:
                    value = await service.fail_client_provisioning(
                        workspace_id,
                        request_id,
                        error_code="CLIENT_PROVISIONING_EXECUTION_FAILED",
                    )
                return value.state
    finally:
        await database.close()
        get_database.cache_clear()


@shared_task(name="b2b.client_provisioning.process")
def process_client_provisioning_task(workspace_id: str, request_id: str) -> str:
    return asyncio.run(_run_client_provisioning(UUID(workspace_id), UUID(request_id)))


def enqueue_client_provisioning(workspace_id: UUID, request_id: UUID) -> None:
    process_client_provisioning_task.apply_async(
        args=(str(workspace_id), str(request_id)),
        countdown=1,
    )
