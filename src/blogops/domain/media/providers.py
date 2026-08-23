"""Fail-closed contracts for image generation, editing and stock imports."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping, Protocol
from uuid import UUID

from blogops.core.errors import AppError
from blogops.domain.media.enums import MediaOperation


@dataclass(frozen=True, slots=True)
class MediaProviderRequest:
    workspace_id: UUID
    job_id: UUID
    operation: MediaOperation
    input_snapshot: Mapping[str, Any]
    input_snapshot_hash: str
    source_object_refs: tuple[str, ...]
    secret_ref: str
    budget_reservation_ref: str


@dataclass(frozen=True, slots=True)
class MediaProviderResult:
    provider: str
    provider_version: str
    model: str | None
    model_version: str | None
    output_object_refs: tuple[str, ...]
    output_hashes: tuple[str, ...]
    safety_metadata: Mapping[str, Any]
    provenance: Mapping[str, Any]
    actual_cost: Decimal
    currency: str


class MediaProvider(Protocol):
    key: str
    capabilities: frozenset[MediaOperation]

    async def execute(self, request: MediaProviderRequest) -> MediaProviderResult: ...


@dataclass(frozen=True, slots=True)
class MediaInspectionResult:
    inspector: str
    inspector_version: str
    status: str
    detected_mime_type: str
    width: int
    height: int
    sanitized_content: bytes
    sanitized_metadata: Mapping[str, Any]
    removed_metadata_paths: tuple[str, ...]
    pii_findings: tuple[Mapping[str, Any], ...]
    face_findings: tuple[Mapping[str, Any], ...]
    trademark_findings: tuple[Mapping[str, Any], ...]
    safety_findings: tuple[Mapping[str, Any], ...]
    transformation_log: tuple[Mapping[str, Any], ...]


class MediaInspector(Protocol):
    async def inspect(
        self,
        content: bytes,
        *,
        declared_mime_type: str,
        policy_snapshot: Mapping[str, Any],
    ) -> MediaInspectionResult: ...


class FailClosedMediaInspector:
    async def inspect(
        self,
        content: bytes,
        *,
        declared_mime_type: str,
        policy_snapshot: Mapping[str, Any],
    ) -> MediaInspectionResult:
        raise AppError(
            code="MEDIA_INSPECTOR_UNAVAILABLE",
            message="EXIF·개인정보·안전 검사기가 구성되지 않아 승격을 중단했습니다.",
            status_code=503,
        )


@dataclass(frozen=True, slots=True)
class MediaBudgetReservation:
    reservation_ref: str
    authorized_amount: Decimal
    currency: str
    policy_snapshot: Mapping[str, Any]


class MediaBudgetGate(Protocol):
    async def reserve(
        self,
        *,
        workspace_id: UUID,
        actor_id: UUID,
        operation: MediaOperation,
        estimated_cost: Decimal,
        maximum_cost: Decimal,
        currency: str,
        idempotency_key: str,
    ) -> MediaBudgetReservation: ...

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


class FailClosedMediaBudgetGate:
    async def reserve(
        self,
        *,
        workspace_id: UUID,
        actor_id: UUID,
        operation: MediaOperation,
        estimated_cost: Decimal,
        maximum_cost: Decimal,
        currency: str,
        idempotency_key: str,
    ) -> MediaBudgetReservation:
        raise AppError(
            code="MEDIA_BUDGET_GATE_UNAVAILABLE",
            message="비용 Hold 서비스를 확인할 수 없어 이미지 작업을 시작하지 않았습니다.",
            status_code=503,
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
            code="MEDIA_BUDGET_GATE_UNAVAILABLE",
            message="비용 Hold 확정 서비스를 확인할 수 없어 작업을 종료하지 않았습니다.",
            status_code=503,
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
            code="MEDIA_BUDGET_GATE_UNAVAILABLE",
            message="비용 Hold 해제 서비스를 확인할 수 없어 작업을 종료하지 않았습니다.",
            status_code=503,
        )


class MediaProviderRegistry:
    """Explicit registry: there is deliberately no synthetic or implicit provider."""

    def __init__(self, adapters: Mapping[str, MediaProvider] | None = None) -> None:
        self._adapters = dict(adapters or {})

    def resolve(self, key: str, operation: MediaOperation) -> MediaProvider:
        adapter = self._adapters.get(key)
        if adapter is None:
            raise provider_unavailable(key)
        if operation not in adapter.capabilities:
            raise AppError(
                code="MEDIA_PROVIDER_CAPABILITY_UNAVAILABLE",
                message="선택한 이미지 공급자는 요청 작업을 지원하지 않습니다.",
                status_code=422,
                fields=[{"path": "operation", "reason": operation.value}],
            )
        return adapter


class FailClosedMediaProvider:
    key = "unconfigured"
    capabilities = frozenset(MediaOperation)

    async def execute(self, request: MediaProviderRequest) -> MediaProviderResult:
        raise provider_unavailable(request.operation.value)


def provider_unavailable(key: str) -> AppError:
    return AppError(
        code="MEDIA_PROVIDER_UNAVAILABLE",
        message="승인된 이미지 공급자가 구성되지 않아 안전하게 중단했습니다.",
        status_code=503,
        fields=[{"path": "provider", "reason": key}],
    )
