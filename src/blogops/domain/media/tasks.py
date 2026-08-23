"""Celery boundaries for quarantined uploads and durable media operations."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from celery import shared_task
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.core.config import get_settings
from blogops.core.context import Principal
from blogops.core.errors import AppError
from blogops.db.session import apply_workspace_scope, get_database
from blogops.domain.jobs.state import (
    TERMINAL_JOB_STATES,
    JobState,
    ensure_job_transition,
)
from blogops.domain.knowledge.adapters import ClamAVScanner
from blogops.domain.media.models import MediaOperationJob, MediaProviderConnection
from blogops.domain.media.providers import MediaBudgetGate, MediaInspector
from blogops.domain.media.service import MediaService
from blogops.domain.media.storage import get_private_object_storage


class MediaOperationExecutor(Protocol):
    """Approved runtime responsible for a fully durable terminal media outcome."""

    async def execute(
        self,
        session: AsyncSession,
        *,
        workspace_id: UUID,
        job_id: UUID,
    ) -> None: ...


class MediaBudgetGateFactory(Protocol):
    """Build a transaction-bound budget gate for a worker database session."""

    def __call__(self, session: AsyncSession) -> MediaBudgetGate: ...


_media_inspector: MediaInspector | None = None
_media_operation_executor: MediaOperationExecutor | None = None
_media_budget_gate_factory: MediaBudgetGateFactory | None = None


def configure_media_inspector(inspector: MediaInspector) -> None:
    global _media_inspector
    _media_inspector = inspector


def configure_media_operation_executor(executor: MediaOperationExecutor) -> None:
    global _media_operation_executor
    _media_operation_executor = executor


def configure_media_budget_gate_factory(factory: MediaBudgetGateFactory) -> None:
    global _media_budget_gate_factory
    _media_budget_gate_factory = factory


def _budget_gate(session: AsyncSession) -> MediaBudgetGate:
    if _media_budget_gate_factory is None:
        raise AppError(
            "MEDIA_BUDGET_SETTLEMENT_UNAVAILABLE",
            "비용 Hold 정산기가 구성되지 않아 이미지 작업을 종료하지 않았습니다.",
            503,
        )
    return _media_budget_gate_factory(session)


def _worker_principal(*, workspace_id: UUID, subject_id: UUID) -> Principal:
    return Principal(
        subject_id=subject_id,
        workspace_id=workspace_id,
        session_id=None,
        permissions=frozenset(),
        authentication_method="worker",
    )


async def _block_upload(workspace_id: UUID, asset_id: UUID, error_code: str) -> str:
    database = get_database()
    async with database.session_factory() as session:
        async with session.begin():
            await apply_workspace_scope(session, workspace_id)
            service = MediaService(session)
            asset = await service._asset(workspace_id, asset_id, for_update=True)
            principal = _worker_principal(
                workspace_id=workspace_id,
                subject_id=asset.created_by,
            )
            asset = await service.block_quarantined_upload(
                principal,
                asset_id,
                error_code=error_code,
            )
            return asset.state


async def _run_upload(workspace_id: UUID, asset_id: UUID) -> str:
    settings = get_settings()
    database = get_database()
    try:
        if _media_inspector is None:
            return await _block_upload(
                workspace_id,
                asset_id,
                "MEDIA_INSPECTOR_UNAVAILABLE",
            )
        async with database.session_factory() as session:
            async with session.begin():
                await apply_workspace_scope(session, workspace_id)
                service = MediaService(session)
                asset = await service._asset(workspace_id, asset_id, for_update=True)
                principal = _worker_principal(
                    workspace_id=workspace_id,
                    subject_id=asset.created_by,
                )
                asset = await service.process_quarantined_upload(
                    principal,
                    asset_id,
                    storage=get_private_object_storage(),
                    scanner=ClamAVScanner(
                        settings.clamav_host,
                        settings.clamav_port,
                        settings.clamav_timeout_seconds,
                    ),
                    inspector=_media_inspector,
                    max_upload_bytes=settings.knowledge_max_upload_bytes,
                    scanner_name="clamav",
                    scanner_version="clamd-instream-v1",
                    inspection_policy={"private_exif": "remove", "pii": "fail_closed"},
                )
                return asset.state
    except AppError as exc:
        return await _block_upload(workspace_id, asset_id, exc.code)
    finally:
        await database.close()
        get_database.cache_clear()


async def _operation_job(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    job_id: UUID,
    for_update: bool,
) -> MediaOperationJob:
    query = select(MediaOperationJob).where(
        MediaOperationJob.workspace_id == workspace_id,
        MediaOperationJob.id == job_id,
    )
    if for_update:
        query = query.with_for_update()
    job = await session.scalar(query)
    if job is None:
        raise AppError("MEDIA_JOB_NOT_FOUND", "이미지 작업을 찾을 수 없습니다.", 404)
    return job


def _set_failure(
    job: MediaOperationJob,
    *,
    code: str,
    detail: str,
    retryable: bool,
) -> None:
    current = JobState(job.state)
    if current in TERMINAL_JOB_STATES:
        return
    if current is JobState.CANCEL_REQUESTED:
        ensure_job_transition(current, JobState.CANCELLED)
        job.state = JobState.CANCELLED.value
        job.finished_at = datetime.now(UTC)
        return
    if current is JobState.QUEUED:
        ensure_job_transition(current, JobState.VALIDATING)
        current = JobState.VALIDATING
        job.state = current.value
    if (
        retryable
        and job.attempt < job.max_attempts
        and JobState.RETRYABLE_FAILED in _allowed_targets(current)
    ):
        ensure_job_transition(current, JobState.RETRYABLE_FAILED)
        job.state = JobState.RETRYABLE_FAILED.value
        job.error_code = code
        job.error_detail = detail
        return
    if (
        JobState.FINAL_FAILED not in _allowed_targets(current)
        and JobState.RETRYABLE_FAILED in _allowed_targets(current)
    ):
        ensure_job_transition(current, JobState.RETRYABLE_FAILED)
        current = JobState.RETRYABLE_FAILED
        job.state = current.value
    if JobState.FINAL_FAILED in _allowed_targets(current):
        ensure_job_transition(current, JobState.FINAL_FAILED)
        job.state = JobState.FINAL_FAILED.value
        job.finished_at = datetime.now(UTC)
    else:
        raise AppError(
            "MEDIA_JOB_STATE_INVALID",
            "이미지 작업을 안전한 실패 상태로 전환할 수 없습니다.",
            409,
        )
    if code == "MEDIA_BUDGET_LIMIT_EXCEEDED":
        job.budget_kill_switch_triggered = True
    job.error_code = code
    job.error_detail = detail


def _allowed_targets(state: JobState) -> frozenset[JobState]:
    from blogops.domain.jobs.state import ALLOWED_JOB_TRANSITIONS

    return ALLOWED_JOB_TRANSITIONS[state]


def _advance_to_media(job: MediaOperationJob) -> bool:
    current = JobState(job.state)
    if current in TERMINAL_JOB_STATES:
        return False
    if current is JobState.CANCEL_REQUESTED:
        ensure_job_transition(current, JobState.CANCELLED)
        job.state = JobState.CANCELLED.value
        job.finished_at = datetime.now(UTC)
        return False
    if current is JobState.RETRYABLE_FAILED:
        ensure_job_transition(current, JobState.QUEUED)
        current = JobState.QUEUED
        job.state = current.value
    sequence = (
        JobState.VALIDATING,
        JobState.RESEARCHING,
        JobState.PLANNING,
        JobState.GENERATING,
        JobState.VERIFYING,
        JobState.OPTIMIZING,
        JobState.CREATING_MEDIA,
    )
    for target in sequence:
        if current is target:
            continue
        if target not in _allowed_targets(current):
            break
        ensure_job_transition(current, target)
        job.state = target.value
        current = target
    if current is not JobState.CREATING_MEDIA:
        return False
    if job.attempt >= job.max_attempts:
        _set_failure(
            job,
            code="MEDIA_RETRY_LIMIT_REACHED",
            detail="media operation retry limit reached",
            retryable=False,
        )
        return False
    job.attempt += 1
    job.started_at = job.started_at or datetime.now(UTC)
    return True


def _retryable_error(error: AppError) -> bool:
    permanent_suffixes = (
        "_UNAVAILABLE",
        "_INVALID",
        "_MISSING",
        "_MISMATCH",
        "_EXCEEDED",
    )
    return (
        error.status_code == 429 or error.status_code >= 500
    ) and not error.code.endswith(permanent_suffixes)


async def _update_provider_outcome(
    session: AsyncSession,
    job: MediaOperationJob,
    *,
    succeeded: bool,
    error_code: str | None = None,
) -> None:
    if job.provider_connection_id is None:
        return
    connection = await session.scalar(
        select(MediaProviderConnection)
        .where(
            MediaProviderConnection.workspace_id == job.workspace_id,
            MediaProviderConnection.id == job.provider_connection_id,
        )
        .with_for_update()
    )
    if connection is None:
        raise AppError(
            "MEDIA_PROVIDER_NOT_FOUND",
            "이미지 공급자 연결을 찾을 수 없습니다.",
            404,
        )
    if succeeded:
        connection.consecutive_failures = 0
        connection.last_error_code = None
        connection.last_success_at = datetime.now(UTC)
        connection.circuit_open_until = None
        return
    connection.consecutive_failures += 1
    connection.last_error_code = error_code
    policy = connection.config_json.get("policy_snapshot", {})
    circuit = policy.get("circuit_breaker", {}) if isinstance(policy, dict) else {}
    try:
        threshold = max(1, int(circuit.get("failure_threshold", 5)))
        cooldown_seconds = max(1, int(circuit.get("cooldown_seconds", 60)))
    except (TypeError, ValueError) as exc:
        raise AppError(
            "MEDIA_PROVIDER_POLICY_INVALID",
            "이미지 공급자 Circuit Breaker 정책이 올바르지 않습니다.",
            503,
        ) from exc
    if connection.consecutive_failures >= threshold:
        connection.circuit_open_until = datetime.now(UTC) + timedelta(
            seconds=cooldown_seconds
        )


async def _restore_provider_quota(
    session: AsyncSession,
    job: MediaOperationJob,
) -> None:
    if (
        job.provider_connection_id is None
        or job.attempt != 0
        or not job.provider_quota_reserved
        or job.provider_quota_released
    ):
        return
    connection = await session.scalar(
        select(MediaProviderConnection)
        .where(
            MediaProviderConnection.workspace_id == job.workspace_id,
            MediaProviderConnection.id == job.provider_connection_id,
        )
        .with_for_update()
    )
    if (
        connection is not None
        and connection.daily_quota is not None
        and connection.quota_remaining is not None
    ):
        connection.quota_remaining = min(
            connection.daily_quota,
            connection.quota_remaining + 1,
        )
        job.provider_quota_released = True


def _validate_success(job: MediaOperationJob) -> None:
    if job.actual_cost is None:
        raise AppError(
            "MEDIA_ACTUAL_COST_MISSING",
            "실제 비용이 기록되지 않아 이미지 작업을 확정할 수 없습니다.",
            409,
        )
    if job.budget_limit is None or job.actual_cost > job.budget_limit:
        raise AppError(
            "MEDIA_BUDGET_LIMIT_EXCEEDED",
            "실제 이미지 비용이 승인된 최대 비용을 초과했습니다.",
            409,
        )
    if job.result_asset_id is None or job.result_version_id is None:
        raise AppError(
            "MEDIA_RESULT_VERSION_MISSING",
            "정확한 결과 이미지 버전이 기록되지 않았습니다.",
            409,
        )
    required_evidence = {
        "provider",
        "provider_version",
        "output_hashes",
        "safety_metadata",
        "provenance",
    }
    if not isinstance(job.result_json, dict) or not required_evidence.issubset(
        job.result_json
    ):
        raise AppError(
            "MEDIA_RESULT_EVIDENCE_MISSING",
            "공급자·안전·계보 증거가 없어 이미지 작업을 확정할 수 없습니다.",
            409,
        )


async def _settle_terminal(
    session: AsyncSession,
    job: MediaOperationJob,
) -> None:
    state = JobState(job.state)
    if state not in TERMINAL_JOB_STATES:
        return
    if not job.budget_reservation_ref:
        raise AppError(
            "MEDIA_BUDGET_RESERVATION_MISSING",
            "비용 Hold 참조가 없어 이미지 작업을 종료하지 않았습니다.",
            409,
        )
    gate = _budget_gate(session)
    event_id = f"media-job:{job.id}:{state.value}"
    if state is JobState.SUCCEEDED:
        _validate_success(job)
        await gate.finalize(
            workspace_id=job.workspace_id,
            actor_id=job.requested_by,
            reservation_ref=job.budget_reservation_ref,
            actual_cost=job.actual_cost,
            currency=job.currency,
            terminal_event_id=event_id,
        )
        return
    await gate.release(
        workspace_id=job.workspace_id,
        actor_id=job.requested_by,
        reservation_ref=job.budget_reservation_ref,
        actual_cost=job.actual_cost or Decimal("0"),
        currency=job.currency,
        terminal_event_id=event_id,
        failure_class=state.value,
    )


async def _persist_operation_failure(
    workspace_id: UUID,
    job_id: UUID,
    *,
    code: str,
    detail: str,
    retryable: bool,
    attempted: bool,
) -> str:
    database = get_database()
    async with database.session_factory() as session:
        async with session.begin():
            await apply_workspace_scope(session, workspace_id)
            job = await _operation_job(
                session,
                workspace_id=workspace_id,
                job_id=job_id,
                for_update=True,
            )
            if attempted and JobState(job.state) not in TERMINAL_JOB_STATES:
                _advance_to_media(job)
            _set_failure(
                job,
                code=code,
                detail=detail,
                retryable=retryable,
            )
            if attempted:
                await _update_provider_outcome(
                    session,
                    job,
                    succeeded=False,
                    error_code=code,
                )
            else:
                await _restore_provider_quota(session, job)
            await _settle_terminal(session, job)
            return job.state


async def _run_operation(workspace_id: UUID, job_id: UUID) -> str:
    database = get_database()
    operation_attempted = False
    try:
        if _media_operation_executor is None:
            return await _persist_operation_failure(
                workspace_id,
                job_id,
                code="MEDIA_RUNTIME_UNAVAILABLE",
                detail="approved media operation executor is not configured",
                retryable=False,
                attempted=False,
            )
        async with database.session_factory() as session:
            async with session.begin():
                await apply_workspace_scope(session, workspace_id)
                job = await _operation_job(
                    session,
                    workspace_id=workspace_id,
                    job_id=job_id,
                    for_update=True,
                )
                if not _advance_to_media(job):
                    if (
                        JobState(job.state) is JobState.CANCELLED
                        and job.attempt == 0
                    ):
                        await _restore_provider_quota(session, job)
                    await _settle_terminal(session, job)
                    return job.state
                operation_attempted = True
                await _media_operation_executor.execute(
                    session,
                    workspace_id=workspace_id,
                    job_id=job_id,
                )
                await session.flush()
                if JobState(job.state) not in TERMINAL_JOB_STATES:
                    raise AppError(
                        "MEDIA_EXECUTOR_INCOMPLETE",
                        "이미지 실행기가 영속적인 종료 상태를 기록하지 않았습니다.",
                        503,
                    )
                if JobState(job.state) is JobState.SUCCEEDED:
                    _validate_success(job)
                    await _update_provider_outcome(session, job, succeeded=True)
                else:
                    await _update_provider_outcome(
                        session,
                        job,
                        succeeded=False,
                        error_code=job.error_code or "MEDIA_PROVIDER_FAILED",
                    )
                await _settle_terminal(session, job)
                return job.state
    except AppError as exc:
        state = await _persist_operation_failure(
            workspace_id,
            job_id,
            code=exc.code,
            detail="approved media executor failed",
            retryable=_retryable_error(exc),
            attempted=operation_attempted,
        )
        if state == JobState.RETRYABLE_FAILED.value:
            enqueue_media_operation(workspace_id, job_id)
        return state
    except Exception:
        state = await _persist_operation_failure(
            workspace_id,
            job_id,
            code="MEDIA_RUNTIME_ERROR",
            detail="approved media executor failed",
            retryable=True,
            attempted=operation_attempted,
        )
        if state == JobState.RETRYABLE_FAILED.value:
            enqueue_media_operation(workspace_id, job_id)
        return state
    finally:
        await database.close()
        get_database.cache_clear()


@shared_task(name="media.scan_upload")
def process_media_upload_task(workspace_id: str, asset_id: str) -> str:
    return asyncio.run(_run_upload(UUID(workspace_id), UUID(asset_id)))


@shared_task(name="media.process_operation")
def process_media_operation_task(workspace_id: str, job_id: str) -> str:
    return asyncio.run(_run_operation(UUID(workspace_id), UUID(job_id)))


def enqueue_media_upload(workspace_id: UUID, asset_id: UUID) -> None:
    process_media_upload_task.apply_async(
        args=(str(workspace_id), str(asset_id)),
        countdown=1,
    )


def enqueue_media_operation(workspace_id: UUID, job_id: UUID) -> None:
    process_media_operation_task.apply_async(
        args=(str(workspace_id), str(job_id)),
        countdown=1,
    )
