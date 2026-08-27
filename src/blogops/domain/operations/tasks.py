"""Celery entry points for backup, recovery, and GA evidence verification."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import UUID

from celery import shared_task

from blogops.core.errors import AppError
from blogops.core.retries import capped_exponential_delay
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

_PERMANENT_OPERATION_ERROR_CODES = frozenset(
    {
        "BACKUP_CONTROLLER_UNAVAILABLE",
        "BACKUP_RESULT_INVALID",
        "GA_EVIDENCE_DUPLICATE",
        "GA_EVIDENCE_HASH_INVALID",
        "GA_EVIDENCE_INCOMPLETE",
        "GA_EVIDENCE_INVALID",
        "GA_EVIDENCE_METRIC_INVALID",
        "GA_EVIDENCE_POLICY_INVALID",
        "GA_EVIDENCE_VERIFIER_UNAVAILABLE",
        "GA_RUNTIME_UNAVAILABLE",
        "OPERATIONS_GA_POLICY_UNAVAILABLE",
        "RECOVERY_CONTROLLER_UNAVAILABLE",
        "RECOVERY_INPUT_MISSING",
        "RECOVERY_ISOLATION_INVALID",
        "RECOVERY_RESULT_INVALID",
    }
)


@dataclass(frozen=True, slots=True)
class OperationsTaskOutcome:
    state: str
    should_retry: bool = False


def _is_retryable_operations_error(error: Exception) -> bool:
    if isinstance(error, AppError):
        if error.code in _PERMANENT_OPERATION_ERROR_CODES:
            return False
        return error.status_code in {408, 429} or error.status_code >= 500
    return isinstance(error, (TimeoutError, ConnectionError))


def _retry_delay(retries: int) -> int:
    return capped_exponential_delay(base_seconds=5, maximum_seconds=300, exponent=retries)


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


async def _run_backup(
    run_id: UUID, *, retry_allowed: bool
) -> OperationsTaskOutcome:
    database = get_database()
    try:
        if _backup_controller is None:
            async with database.session_factory() as session:
                async with session.begin():
                    value = await OperationsService(session).fail_backup(
                        run_id, code="BACKUP_CONTROLLER_UNAVAILABLE"
                    )
                    return OperationsTaskOutcome(value.state)
        try:
            async with database.session_factory() as session:
                async with session.begin():
                    value = await OperationsService(session).execute_backup(
                        run_id, controller=_backup_controller
                    )
                    return OperationsTaskOutcome(value.state)
        except Exception as exc:
            retryable = _is_retryable_operations_error(exc)
            should_retry = retryable and retry_allowed
            code = exc.code if isinstance(exc, AppError) else "BACKUP_EXECUTION_FAILED"
            async with database.session_factory() as session:
                async with session.begin():
                    value, changed_to_retry = await OperationsService(
                        session
                    ).record_backup_attempt_error(
                        run_id,
                        code=code,
                        retry=should_retry,
                        retry_exhausted=retryable and not retry_allowed,
                    )
            return OperationsTaskOutcome(
                value.state, should_retry=should_retry and changed_to_retry
            )
    finally:
        await database.close()
        get_database.cache_clear()


async def _run_recovery(
    exercise_id: UUID, *, retry_allowed: bool
) -> OperationsTaskOutcome:
    database = get_database()
    try:
        if _recovery_controller is None:
            async with database.session_factory() as session:
                async with session.begin():
                    value = await OperationsService(session).fail_recovery(
                        exercise_id, code="RECOVERY_CONTROLLER_UNAVAILABLE"
                    )
                    return OperationsTaskOutcome(value.state)
        try:
            async with database.session_factory() as session:
                async with session.begin():
                    value = await OperationsService(session).execute_recovery(
                        exercise_id, controller=_recovery_controller
                    )
                    return OperationsTaskOutcome(value.state)
        except Exception as exc:
            retryable = _is_retryable_operations_error(exc)
            should_retry = retryable and retry_allowed
            code = exc.code if isinstance(exc, AppError) else "RECOVERY_EXECUTION_FAILED"
            async with database.session_factory() as session:
                async with session.begin():
                    value, changed_to_retry = await OperationsService(
                        session
                    ).record_recovery_attempt_error(
                        exercise_id,
                        code=code,
                        retry=should_retry,
                        retry_exhausted=retryable and not retry_allowed,
                    )
            return OperationsTaskOutcome(
                value.state, should_retry=should_retry and changed_to_retry
            )
    finally:
        await database.close()
        get_database.cache_clear()


async def _run_ga(
    assessment_id: UUID, *, retry_allowed: bool
) -> OperationsTaskOutcome:
    database = get_database()
    try:
        if _ga_verifier is None or _operations_policy is None:
            async with database.session_factory() as session:
                async with session.begin():
                    value = await OperationsService(session).fail_ga_assessment(
                        assessment_id, code="GA_RUNTIME_UNAVAILABLE"
                    )
                    return OperationsTaskOutcome(value.state)
        try:
            async with database.session_factory() as session:
                async with session.begin():
                    value = await OperationsService(session).execute_ga_assessment(
                        assessment_id,
                        verifier=_ga_verifier,
                        policy=_operations_policy,
                    )
                    return OperationsTaskOutcome(value.state)
        except Exception as exc:
            retryable = _is_retryable_operations_error(exc)
            should_retry = retryable and retry_allowed
            code = exc.code if isinstance(exc, AppError) else "GA_VERIFICATION_FAILED"
            async with database.session_factory() as session:
                async with session.begin():
                    value, changed_to_retry = await OperationsService(
                        session
                    ).record_ga_attempt_error(
                        assessment_id,
                        code=code,
                        retry=should_retry,
                        retry_exhausted=retryable and not retry_allowed,
                    )
            return OperationsTaskOutcome(
                value.state, should_retry=should_retry and changed_to_retry
            )
    finally:
        await database.close()
        get_database.cache_clear()


@shared_task(bind=True, name="operations.backup.process", max_retries=3)
def process_backup_task(task, run_id: str) -> str:
    outcome = asyncio.run(
        _run_backup(
            UUID(run_id), retry_allowed=task.request.retries < task.max_retries
        )
    )
    if outcome.should_retry:
        raise task.retry(countdown=_retry_delay(task.request.retries))
    return outcome.state


@shared_task(bind=True, name="operations.recovery.process", max_retries=3)
def process_recovery_task(task, exercise_id: str) -> str:
    outcome = asyncio.run(
        _run_recovery(
            UUID(exercise_id),
            retry_allowed=task.request.retries < task.max_retries,
        )
    )
    if outcome.should_retry:
        raise task.retry(countdown=_retry_delay(task.request.retries))
    return outcome.state


@shared_task(bind=True, name="operations.ga.process", max_retries=3)
def process_ga_assessment_task(task, assessment_id: str) -> str:
    outcome = asyncio.run(
        _run_ga(
            UUID(assessment_id),
            retry_allowed=task.request.retries < task.max_retries,
        )
    )
    if outcome.should_retry:
        raise task.retry(countdown=_retry_delay(task.request.retries))
    return outcome.state


def enqueue_backup(run_id: UUID) -> None:
    process_backup_task.apply_async(args=(str(run_id),), countdown=1)


def enqueue_recovery(exercise_id: UUID) -> None:
    process_recovery_task.apply_async(args=(str(exercise_id),), countdown=1)


def enqueue_ga_assessment(assessment_id: UUID) -> None:
    process_ga_assessment_task.apply_async(args=(str(assessment_id),), countdown=1)
