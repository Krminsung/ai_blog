"""Celery consumer boundary for durable bulk campaign jobs."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from celery import shared_task
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.core.config import get_settings
from blogops.core.errors import AppError
from blogops.db.session import apply_workspace_scope, get_database
from blogops.domain.bulk.enums import BulkInputKind, BulkRowState
from blogops.domain.bulk.models import BulkInputFile, BulkJob, BulkRow
from blogops.domain.bulk.providers import BulkBudgetGate
from blogops.domain.bulk.service import BulkService
from blogops.domain.jobs.state import TERMINAL_JOB_STATES, JobState, ensure_job_transition
from blogops.domain.media.rules import validate_private_object_ref
from blogops.domain.media.storage import get_private_object_storage


class BulkJobExecutor(Protocol):
    """Approved runtime that drives generation, quality, approval and delivery.

    The implementation must call ``BulkService.authorize_next_row`` immediately
    before every row and stop when that server-side gate returns false.
    """

    async def execute(
        self,
        session: AsyncSession,
        *,
        workspace_id: UUID,
        job_id: UUID,
    ) -> None: ...


class BulkBudgetGateFactory(Protocol):
    """Build a transaction-bound budget gate for a worker database session."""

    def __call__(self, session: AsyncSession) -> BulkBudgetGate: ...


_executor: BulkJobExecutor | None = None
_budget_gate_factory: BulkBudgetGateFactory | None = None


@dataclass(frozen=True, slots=True)
class _MaterializationRequest:
    object_ref: str
    size_bytes: int


def configure_bulk_job_executor(executor: BulkJobExecutor) -> None:
    global _executor
    _executor = executor


def configure_bulk_budget_gate_factory(factory: BulkBudgetGateFactory) -> None:
    global _budget_gate_factory
    _budget_gate_factory = factory


def _budget_gate(session: AsyncSession) -> BulkBudgetGate:
    if _budget_gate_factory is not None:
        return _budget_gate_factory(session)

    from blogops.domain.billing.adapters import create_bulk_budget_gate

    return create_bulk_budget_gate(session)


async def _job(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    job_id: UUID,
    for_update: bool,
) -> BulkJob:
    query = select(BulkJob).where(
        BulkJob.workspace_id == workspace_id,
        BulkJob.id == job_id,
    )
    if for_update:
        query = query.with_for_update()
    value = await session.scalar(query)
    if value is None:
        raise AppError("BULK_JOB_NOT_FOUND", "대량 작업을 찾을 수 없습니다.", 404)
    return value


async def _prepare_job(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    job_id: UUID,
) -> tuple[BulkJob, bool]:
    value = await _job(
        session,
        workspace_id=workspace_id,
        job_id=job_id,
        for_update=True,
    )
    current = JobState(value.state)
    if current in TERMINAL_JOB_STATES:
        return value, False
    if current is JobState.CANCEL_REQUESTED:
        ensure_job_transition(current, JobState.CANCELLED)
        value.state = JobState.CANCELLED.value
        value.finished_at = datetime.now(UTC)
        rows = list(
            await session.scalars(
                select(BulkRow)
                .where(
                    BulkRow.workspace_id == workspace_id,
                    BulkRow.job_id == job_id,
                    BulkRow.state.in_(
                        {
                            BulkRowState.PENDING.value,
                            BulkRowState.READY.value,
                            BulkRowState.QUEUED.value,
                        }
                    ),
                )
                .with_for_update()
            )
        )
        for row in rows:
            row.state = BulkRowState.CANCELLED.value
        await BulkService(session)._refresh_progress(value)
        return value, False
    effective_maximum = min(value.maximum_cost, value.authorized_cost)
    if value.actual_cost + value.held_cost >= effective_maximum:
        value.budget_kill_switch_triggered = True
        value.pause_requested = True
        value.pause_requested_at = value.pause_requested_at or datetime.now(UTC)
        value.error_code = "BUDGET_KILL_SWITCH"
        value.error_detail = "maximum_cost_reached"
        return value, False
    if value.budget_kill_switch_triggered or value.pause_requested:
        return value, False

    target_rows = value.sample_size if value.dry_run and value.sample_size else value.total_rows
    ingested_rows = int(
        await session.scalar(
            select(func.count(BulkRow.id)).where(
                BulkRow.workspace_id == workspace_id,
                BulkRow.job_id == job_id,
            )
        )
        or 0
    )
    if ingested_rows < target_rows:
        if current is JobState.QUEUED:
            ensure_job_transition(current, JobState.VALIDATING)
            current = JobState.VALIDATING
            value.state = current.value
        if current is JobState.VALIDATING:
            ensure_job_transition(current, JobState.WAITING_INPUT)
            value.state = JobState.WAITING_INPUT.value
        return value, False

    if current is JobState.WAITING_INPUT:
        ensure_job_transition(current, JobState.QUEUED)
        current = JobState.QUEUED
        value.state = current.value
    if current is JobState.RETRYABLE_FAILED:
        ensure_job_transition(current, JobState.QUEUED)
        current = JobState.QUEUED
        value.state = current.value
    if current is JobState.QUEUED:
        ensure_job_transition(current, JobState.VALIDATING)
        value.state = JobState.VALIDATING.value
    value.started_at = value.started_at or datetime.now(UTC)
    return value, True


async def _materialization_request(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    job_id: UUID,
) -> _MaterializationRequest | None:
    value = await _job(
        session,
        workspace_id=workspace_id,
        job_id=job_id,
        for_update=False,
    )
    current = JobState(value.state)
    if (
        current in TERMINAL_JOB_STATES
        or current is JobState.CANCEL_REQUESTED
        or value.pause_requested
        or value.budget_kill_switch_triggered
        or value.actual_cost + value.held_cost
        >= min(value.maximum_cost, value.authorized_cost)
    ):
        return None
    if current not in {
        JobState.QUEUED,
        JobState.VALIDATING,
        JobState.WAITING_INPUT,
    }:
        return None
    input_file = await session.scalar(
        select(BulkInputFile).where(
            BulkInputFile.workspace_id == workspace_id,
            BulkInputFile.id == value.input_file_id,
        )
    )
    if input_file is None:
        raise AppError("BULK_INPUT_NOT_FOUND", "대량 입력 Snapshot을 찾을 수 없습니다.", 404)
    if input_file.input_kind != BulkInputKind.CSV.value:
        raise AppError(
            "BULK_INPUT_MATERIALIZER_UNAVAILABLE",
            "CSV 외 입력 형식의 승인된 행 변환기가 구성되지 않았습니다.",
            503,
        )
    validate_private_object_ref(
        input_file.object_ref,
        workspace_id=str(workspace_id),
        namespace="bulk",
    )
    return _MaterializationRequest(
        object_ref=input_file.object_ref,
        size_bytes=input_file.size_bytes,
    )


async def _materialize_rows(
    *,
    workspace_id: UUID,
    job_id: UUID,
) -> None:
    settings = get_settings()
    database = get_database()
    async with database.session_factory() as session:
        async with session.begin():
            await apply_workspace_scope(session, workspace_id)
            request = await _materialization_request(
                session,
                workspace_id=workspace_id,
                job_id=job_id,
            )
    if request is None:
        return
    if request.size_bytes < 1 or request.size_bytes > settings.knowledge_max_upload_bytes:
        raise AppError(
            "BULK_INPUT_SIZE_POLICY_VIOLATION",
            "입력 Snapshot이 현재 서버 용량 정책을 충족하지 않습니다.",
            409,
        )
    content = await get_private_object_storage().get_bytes(
        request.object_ref,
        max_bytes=settings.knowledge_max_upload_bytes,
    )
    async with database.session_factory() as session:
        async with session.begin():
            await apply_workspace_scope(session, workspace_id)
            await BulkService(session).materialize_csv_snapshot(
                workspace_id=workspace_id,
                job_id=job_id,
                content=content,
            )


async def _fail_job(
    session: AsyncSession,
    value: BulkJob,
    *,
    code: str,
    detail: str,
) -> None:
    current = JobState(value.state)
    if current in TERMINAL_JOB_STATES:
        return
    if current is JobState.QUEUED:
        ensure_job_transition(current, JobState.VALIDATING)
        current = JobState.VALIDATING
        value.state = current.value
    if current is JobState.WAITING_INPUT:
        ensure_job_transition(current, JobState.QUEUED)
        ensure_job_transition(JobState.QUEUED, JobState.VALIDATING)
        current = JobState.VALIDATING
        value.state = current.value
    if current is not JobState.VALIDATING:
        value.error_code = code
        value.error_detail = detail
        return
    ensure_job_transition(current, JobState.FINAL_FAILED)
    value.state = JobState.FINAL_FAILED.value
    value.error_code = code
    value.error_detail = detail
    value.finished_at = datetime.now(UTC)
    rows = list(
        await session.scalars(
            select(BulkRow)
            .where(
                BulkRow.workspace_id == value.workspace_id,
                BulkRow.job_id == value.id,
                BulkRow.state.in_(
                    {
                        BulkRowState.PENDING.value,
                        BulkRowState.READY.value,
                        BulkRowState.QUEUED.value,
                        BulkRowState.PROCESSING.value,
                    }
                ),
            )
            .with_for_update()
        )
    )
    for row in rows:
        row.state = BulkRowState.FINAL_FAILED.value
        row.last_error_code = code
        row.last_error_detail = detail
    await BulkService(session)._refresh_progress(value)


def _validate_success(value: BulkJob) -> None:
    target_rows = (
        value.sample_size if value.dry_run and value.sample_size else value.total_rows
    )
    if value.processed_rows != target_rows:
        raise AppError(
            "BULK_RESULT_INCOMPLETE",
            "처리 대상 행 전체의 결과가 기록되지 않았습니다.",
            409,
        )
    if value.held_cost != 0:
        raise AppError(
            "BULK_COST_HOLD_LEAK",
            "행별 임시 Hold가 남아 있어 대량 작업을 확정할 수 없습니다.",
            409,
        )
    if value.actual_cost > min(value.maximum_cost, value.authorized_cost):
        raise AppError(
            "BULK_BUDGET_LIMIT_EXCEEDED",
            "실제 대량 작업 비용이 승인된 최대 비용을 초과했습니다.",
            409,
        )


async def _settle_terminal(session: AsyncSession, value: BulkJob) -> None:
    current = JobState(value.state)
    if current not in TERMINAL_JOB_STATES:
        return
    if not value.budget_reservation_ref:
        raise AppError(
            "BULK_BUDGET_RESERVATION_MISSING",
            "비용 Hold 참조가 없어 대량 작업을 종료하지 않았습니다.",
            409,
        )
    gate = _budget_gate(session)
    event_id = f"bulk-job:{value.id}:{current.value}"
    if current is JobState.SUCCEEDED:
        _validate_success(value)
        await gate.finalize(
            workspace_id=value.workspace_id,
            actor_id=value.requested_by,
            reservation_ref=value.budget_reservation_ref,
            actual_cost=value.actual_cost,
            currency=value.currency,
            terminal_event_id=event_id,
        )
        return
    await gate.release(
        workspace_id=value.workspace_id,
        actor_id=value.requested_by,
        reservation_ref=value.budget_reservation_ref,
        actual_cost=value.actual_cost,
        currency=value.currency,
        terminal_event_id=event_id,
        failure_class=current.value,
    )


async def _persist_failure(
    workspace_id: UUID,
    job_id: UUID,
    *,
    code: str,
    detail: str,
) -> str:
    database = get_database()
    async with database.session_factory() as session:
        async with session.begin():
            await apply_workspace_scope(session, workspace_id)
            value = await _job(
                session,
                workspace_id=workspace_id,
                job_id=job_id,
                for_update=True,
            )
            await _fail_job(session, value, code=code, detail=detail)
            await _settle_terminal(session, value)
            return value.state


async def _run_job(workspace_id: UUID, job_id: UUID) -> str:
    database = get_database()
    try:
        await _materialize_rows(
            workspace_id=workspace_id,
            job_id=job_id,
        )
        async with database.session_factory() as session:
            async with session.begin():
                await apply_workspace_scope(session, workspace_id)
                value, ready = await _prepare_job(
                    session,
                    workspace_id=workspace_id,
                    job_id=job_id,
                )
                if not ready:
                    await _settle_terminal(session, value)
                    return value.state
                if _executor is None:
                    await _fail_job(
                        session,
                        value,
                        code="BULK_RUNTIME_UNAVAILABLE",
                        detail="approved bulk executor is not configured",
                    )
                    await _settle_terminal(session, value)
                    return value.state
                await _executor.execute(
                    session,
                    workspace_id=workspace_id,
                    job_id=job_id,
                )
                await session.flush()
                current = JobState(value.state)
                if current is JobState.CANCEL_REQUESTED:
                    value, _ready = await _prepare_job(
                        session,
                        workspace_id=workspace_id,
                        job_id=job_id,
                    )
                    await _settle_terminal(session, value)
                    return value.state
                if value.pause_requested or value.budget_kill_switch_triggered:
                    return value.state
                if current is JobState.VALIDATING:
                    await _fail_job(
                        session,
                        value,
                        code="BULK_EXECUTOR_INCOMPLETE",
                        detail="bulk executor returned without a durable outcome",
                    )
                await _settle_terminal(session, value)
                return value.state
    except AppError as exc:
        return await _persist_failure(
            workspace_id,
            job_id,
            code=exc.code,
            detail="approved bulk executor failed",
        )
    except Exception:
        return await _persist_failure(
            workspace_id,
            job_id,
            code="BULK_RUNTIME_ERROR",
            detail="approved bulk executor failed",
        )
    finally:
        await database.close()
        get_database.cache_clear()


@shared_task(name="bulk.process")
def process_bulk_job_task(workspace_id: str, job_id: str) -> str:
    return asyncio.run(_run_job(UUID(workspace_id), UUID(job_id)))


def enqueue_bulk_job(workspace_id: UUID, job_id: UUID) -> None:
    process_bulk_job_task.apply_async(
        args=(str(workspace_id), str(job_id)),
        countdown=1,
    )
