"""Fail-closed boundaries for tabular imports and bulk budget reservations."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, AsyncIterator, Mapping, Protocol
from uuid import UUID

from blogops.core.errors import AppError


@dataclass(frozen=True, slots=True)
class ImportedRow:
    row_no: int
    values: Mapping[str, Any]


class TabularInputAdapter(Protocol):
    key: str
    version: str

    async def rows(
        self,
        *,
        object_ref: str,
        sheet: str | None,
        header_row: int,
        secret_ref: str | None,
    ) -> AsyncIterator[ImportedRow]: ...


class FailClosedTabularInputAdapter:
    key = "unconfigured"
    version = "0"

    async def rows(
        self,
        *,
        object_ref: str,
        sheet: str | None,
        header_row: int,
        secret_ref: str | None,
    ) -> AsyncIterator[ImportedRow]:
        raise AppError(
            "BULK_INPUT_ADAPTER_UNAVAILABLE",
            "승인된 표 입력 어댑터가 구성되지 않아 안전하게 중단했습니다.",
            503,
        )
        if False:  # pragma: no cover - keeps this method an async iterator
            yield ImportedRow(0, {})


@dataclass(frozen=True, slots=True)
class BulkBudgetReservation:
    reservation_ref: str
    authorized_amount: Decimal
    currency: str
    entitlement_snapshot: Mapping[str, Any]
    budget_policy_snapshot: Mapping[str, Any]


class BulkBudgetGate(Protocol):
    async def reserve(
        self,
        *,
        workspace_id: UUID,
        actor_id: UUID,
        job_key: str,
        estimated_cost: Decimal,
        maximum_cost: Decimal,
        currency: str,
    ) -> BulkBudgetReservation: ...

    async def finalize(
        self,
        *,
        workspace_id: UUID,
        actor_id: UUID,
        reservation_ref: str,
        actual_cost: Decimal,
        currency: str,
        terminal_event_id: str,
    ) -> object: ...

    async def release(
        self,
        *,
        workspace_id: UUID,
        actor_id: UUID,
        reservation_ref: str,
        actual_cost: Decimal,
        currency: str,
        terminal_event_id: str,
        failure_class: str,
    ) -> object: ...


class FailClosedBulkBudgetGate:
    async def reserve(
        self,
        *,
        workspace_id: UUID,
        actor_id: UUID,
        job_key: str,
        estimated_cost: Decimal,
        maximum_cost: Decimal,
        currency: str,
    ) -> BulkBudgetReservation:
        raise AppError(
            "BULK_BUDGET_GATE_UNAVAILABLE",
            "비용 Hold 서비스를 확인할 수 없어 대량 작업을 시작하지 않았습니다.",
            503,
        )

    async def finalize(
        self,
        *,
        workspace_id: UUID,
        actor_id: UUID,
        reservation_ref: str,
        actual_cost: Decimal,
        currency: str,
        terminal_event_id: str,
    ) -> object:
        raise AppError(
            "BULK_BUDGET_GATE_UNAVAILABLE",
            "비용 Hold 확정 서비스를 확인할 수 없어 작업을 종료하지 않았습니다.",
            503,
        )

    async def release(
        self,
        *,
        workspace_id: UUID,
        actor_id: UUID,
        reservation_ref: str,
        actual_cost: Decimal,
        currency: str,
        terminal_event_id: str,
        failure_class: str,
    ) -> object:
        raise AppError(
            "BULK_BUDGET_GATE_UNAVAILABLE",
            "비용 Hold 해제 서비스를 확인할 수 없어 작업을 종료하지 않았습니다.",
            503,
        )
