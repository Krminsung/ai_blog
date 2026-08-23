"""Celery entry points for backup, recovery, and GA evidence verification."""

from __future__ import annotations

import asyncio
from uuid import UUID

from celery import shared_task

from blogops.core.errors import AppError
from blogops.db.session import get_database
from blogops.domain.operations.providers import (
    BackupController,
    GAEvidenceVerifier,
    OperationsPolicy,
    RecoveryController,
)
from blogops.domain.operations.service import OperationsService

_backup_controller: BackupController | None = None
_recovery_controller: RecoveryController | None = None
_ga_verifier: GAEvidenceVerifier | None = None
_operations_policy: OperationsPolicy | None = None


def configure_operations_runtime(
    *,
    backup_controller: BackupController,
    recovery_controller: RecoveryController,
    ga_verifier: GAEvidenceVerifier,
    policy: OperationsPolicy,
) -> None:
    global _backup_controller, _recovery_controller, _ga_verifier, _operations_policy
    _backup_controller = backup_controller
    _recovery_controller = recovery_controller
    _ga_verifier = ga_verifier
    _operations_policy = policy


async def _run_backup(run_id: UUID) -> str:
    database = get_database()
    try:
        async with database.session_factory() as session:
            async with session.begin():
                service = OperationsService(session)
                if _backup_controller is None:
                    value = await service.fail_backup(
                        run_id, code="BACKUP_CONTROLLER_UNAVAILABLE"
                    )
                    return value.state
                try:
                    value = await service.execute_backup(
                        run_id, controller=_backup_controller
                    )
                except AppError as exc:
                    value = await service.fail_backup(run_id, code=exc.code)
                except Exception:
                    value = await service.fail_backup(
                        run_id, code="BACKUP_EXECUTION_FAILED"
                    )
                return value.state
    finally:
        await database.close()
        get_database.cache_clear()


async def _run_recovery(exercise_id: UUID) -> str:
    database = get_database()
    try:
        async with database.session_factory() as session:
            async with session.begin():
                service = OperationsService(session)
                if _recovery_controller is None:
                    value = await service.fail_recovery(
                        exercise_id, code="RECOVERY_CONTROLLER_UNAVAILABLE"
                    )
                    return value.state
                try:
                    value = await service.execute_recovery(
                        exercise_id, controller=_recovery_controller
                    )
                except AppError as exc:
                    value = await service.fail_recovery(exercise_id, code=exc.code)
                except Exception:
                    value = await service.fail_recovery(
                        exercise_id, code="RECOVERY_EXECUTION_FAILED"
                    )
                return value.state
    finally:
        await database.close()
        get_database.cache_clear()


async def _run_ga(assessment_id: UUID) -> str:
    database = get_database()
    try:
        async with database.session_factory() as session:
            async with session.begin():
                service = OperationsService(session)
                if _ga_verifier is None or _operations_policy is None:
                    value = await service.fail_ga_assessment(
                        assessment_id, code="GA_RUNTIME_UNAVAILABLE"
                    )
                    return value.state
                try:
                    value = await service.execute_ga_assessment(
                        assessment_id,
                        verifier=_ga_verifier,
                        policy=_operations_policy,
                    )
                except AppError as exc:
                    value = await service.fail_ga_assessment(
                        assessment_id, code=exc.code
                    )
                except Exception:
                    value = await service.fail_ga_assessment(
                        assessment_id, code="GA_VERIFICATION_FAILED"
                    )
                return value.state
    finally:
        await database.close()
        get_database.cache_clear()


@shared_task(name="operations.backup.process")
def process_backup_task(run_id: str) -> str:
    return asyncio.run(_run_backup(UUID(run_id)))


@shared_task(name="operations.recovery.process")
def process_recovery_task(exercise_id: str) -> str:
    return asyncio.run(_run_recovery(UUID(exercise_id)))


@shared_task(name="operations.ga.process")
def process_ga_assessment_task(assessment_id: str) -> str:
    return asyncio.run(_run_ga(UUID(assessment_id)))


def enqueue_backup(run_id: UUID) -> None:
    process_backup_task.apply_async(args=(str(run_id),), countdown=1)


def enqueue_recovery(exercise_id: UUID) -> None:
    process_recovery_task.apply_async(args=(str(exercise_id),), countdown=1)


def enqueue_ga_assessment(assessment_id: UUID) -> None:
    process_ga_assessment_task.apply_async(args=(str(assessment_id),), countdown=1)
