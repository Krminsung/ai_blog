"""Celery entry points for durable retention, privacy, and copyright execution."""

from __future__ import annotations

import asyncio
from uuid import UUID

from celery import shared_task

from blogops.core.errors import AppError
from blogops.db.session import apply_workspace_scope, get_database
from blogops.domain.security.providers import (
    CopyrightEnforcementAdapter,
    DataRightsExecutor,
    DataRightsPolicy,
    RetentionExecutor,
)
from blogops.domain.security.service import SecurityService

_privacy_executor: DataRightsExecutor | None = None
_privacy_policy: DataRightsPolicy | None = None
_retention_executor: RetentionExecutor | None = None
_copyright_adapter: CopyrightEnforcementAdapter | None = None


def configure_privacy_runtime(
    *, executor: DataRightsExecutor, policy: DataRightsPolicy
) -> None:
    global _privacy_executor, _privacy_policy
    _privacy_executor = executor
    _privacy_policy = policy


def configure_copyright_runtime(adapter: CopyrightEnforcementAdapter) -> None:
    global _copyright_adapter
    _copyright_adapter = adapter


def configure_retention_runtime(executor: RetentionExecutor) -> None:
    global _retention_executor
    _retention_executor = executor


async def _run_privacy(workspace_id: UUID, request_id: UUID) -> str:
    database = get_database()
    try:
        async with database.session_factory() as session:
            async with session.begin():
                await apply_workspace_scope(session, workspace_id)
                service = SecurityService(session)
                if _privacy_executor is None or _privacy_policy is None:
                    value = await service.fail_privacy_runtime(
                        workspace_id=workspace_id,
                        request_id=request_id,
                        code="DATA_RIGHTS_RUNTIME_UNAVAILABLE",
                    )
                    return value.state
                try:
                    value = await service.process_privacy_request(
                        workspace_id=workspace_id,
                        request_id=request_id,
                        executor=_privacy_executor,
                        policy=_privacy_policy,
                    )
                except AppError as exc:
                    value = await service.fail_privacy_runtime(
                        workspace_id=workspace_id,
                        request_id=request_id,
                        code=exc.code,
                    )
                except Exception:
                    value = await service.fail_privacy_runtime(
                        workspace_id=workspace_id,
                        request_id=request_id,
                        code="DATA_RIGHTS_EXECUTION_FAILED",
                    )
                return value.state
    finally:
        await database.close()
        get_database.cache_clear()


async def _run_copyright(workspace_id: UUID, case_id: UUID) -> str:
    database = get_database()
    try:
        async with database.session_factory() as session:
            async with session.begin():
                await apply_workspace_scope(session, workspace_id)
                if _copyright_adapter is None:
                    from blogops.domain.security.providers import FailClosedSecurityAdapters

                    adapter: CopyrightEnforcementAdapter = FailClosedSecurityAdapters()
                else:
                    adapter = _copyright_adapter
                value = await SecurityService(session).process_copyright_case(
                    workspace_id=workspace_id,
                    case_id=case_id,
                    adapter=adapter,
                )
                return value.state
    finally:
        await database.close()
        get_database.cache_clear()


async def _run_retention(workspace_id: UUID, sweep_id: UUID) -> str:
    database = get_database()
    try:
        async with database.session_factory() as session:
            async with session.begin():
                await apply_workspace_scope(session, workspace_id)
                service = SecurityService(session)
                if _retention_executor is None:
                    value = await service.fail_retention_sweep(
                        workspace_id=workspace_id,
                        sweep_id=sweep_id,
                        code="RETENTION_EXECUTOR_UNAVAILABLE",
                    )
                    return value.state
                try:
                    value = await service.execute_retention_sweep(
                        workspace_id=workspace_id,
                        sweep_id=sweep_id,
                        executor=_retention_executor,
                    )
                except AppError as exc:
                    value = await service.fail_retention_sweep(
                        workspace_id=workspace_id,
                        sweep_id=sweep_id,
                        code=exc.code,
                    )
                except Exception:
                    value = await service.fail_retention_sweep(
                        workspace_id=workspace_id,
                        sweep_id=sweep_id,
                        code="RETENTION_EXECUTION_FAILED",
                    )
                return value.state
    finally:
        await database.close()
        get_database.cache_clear()


@shared_task(name="security.privacy.process")
def process_privacy_request_task(workspace_id: str, request_id: str) -> str:
    return asyncio.run(_run_privacy(UUID(workspace_id), UUID(request_id)))


@shared_task(name="security.copyright.process")
def process_copyright_case_task(workspace_id: str, case_id: str) -> str:
    return asyncio.run(_run_copyright(UUID(workspace_id), UUID(case_id)))


@shared_task(name="security.retention.process")
def process_retention_sweep_task(workspace_id: str, sweep_id: str) -> str:
    return asyncio.run(_run_retention(UUID(workspace_id), UUID(sweep_id)))


def enqueue_privacy_request(workspace_id: UUID, request_id: UUID) -> None:
    process_privacy_request_task.apply_async(
        args=(str(workspace_id), str(request_id)), countdown=1
    )


def enqueue_copyright_case(workspace_id: UUID, case_id: UUID) -> None:
    process_copyright_case_task.apply_async(
        args=(str(workspace_id), str(case_id)), countdown=1
    )


def enqueue_retention_sweep(workspace_id: UUID, sweep_id: UUID) -> None:
    process_retention_sweep_task.apply_async(
        args=(str(workspace_id), str(sweep_id)), countdown=1
    )
