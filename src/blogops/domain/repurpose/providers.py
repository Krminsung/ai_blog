"""Fail-closed model, budget, export, and official social delivery boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping, Protocol, Sequence
from uuid import UUID

from blogops.core.errors import AppError


@dataclass(frozen=True)
class RepurposeGenerationRequest:
    workspace_id: UUID
    job_id: UUID
    item_id: UUID
    source_snapshot: Mapping[str, Any]
    template_snapshot: Mapping[str, Any]
    model_config: Mapping[str, Any]
    model_config_hash: str
    variant_count: int


@dataclass(frozen=True)
class GeneratedVariant:
    document: Sequence[Mapping[str, Any]]
    plain_text: str
    claim_lineage: Sequence[Mapping[str, Any]]
    citation_lineage: Sequence[Mapping[str, Any]]
    disclosure_result: Mapping[str, Any]
    safety_result: Mapping[str, Any]
    pii_result: Mapping[str, Any]
    model_provenance: Mapping[str, Any]
    raw_object_ref: str | None
    raw_response_hash: str
    actual_cost: Decimal


class RepurposeModelGateway(Protocol):
    provider: str
    model: str
    model_version: str

    async def generate(
        self, request: RepurposeGenerationRequest
    ) -> Sequence[GeneratedVariant]: ...


@dataclass(frozen=True)
class BudgetReservation:
    reference: str
    reserved_amount: Decimal
    currency: str


class BudgetAuthorizationGateway(Protocol):
    async def reserve(
        self,
        *,
        workspace_id: UUID,
        actor_id: UUID,
        amount: Decimal,
        currency: str,
        request_hash: str,
    ) -> BudgetReservation: ...


@dataclass(frozen=True)
class StoredExport:
    object_ref: str
    object_hash: str
    media_type: str
    size_bytes: int


class RepurposeExportStore(Protocol):
    async def put(
        self,
        *,
        workspace_id: UUID,
        object_name: str,
        body: bytes,
        media_type: str,
    ) -> StoredExport: ...


@dataclass(frozen=True)
class SocialDeliveryResult:
    external_post_id: str
    response_metadata: Mapping[str, Any]


class OfficialSocialGateway(Protocol):
    provider: str
    official_contract: str

    async def publish(
        self,
        *,
        secret_ref: str,
        destination: Mapping[str, Any],
        text: str,
        document: Sequence[Mapping[str, Any]],
        idempotency_key: str,
    ) -> SocialDeliveryResult: ...


class ModelGatewayRegistry:
    def __init__(self, gateways: Sequence[RepurposeModelGateway] = ()) -> None:
        self._gateways = {
            (item.provider, item.model, item.model_version): item for item in gateways
        }

    def require(self, provider: str, model: str, model_version: str) -> RepurposeModelGateway:
        gateway = self._gateways.get((provider, model, model_version))
        if gateway is None:
            raise AppError(
                code="REPURPOSE_RUNTIME_UNAVAILABLE",
                message="승인된 리퍼포징 모델 런타임이 구성되지 않았습니다.",
                status_code=503,
            )
        return gateway


class OfficialSocialRegistry:
    def __init__(self, gateways: Sequence[OfficialSocialGateway] = ()) -> None:
        self._gateways: dict[str, OfficialSocialGateway] = {}
        for gateway in gateways:
            if not gateway.official_contract.strip():
                raise AppError(
                    code="OFFICIAL_SOCIAL_CONTRACT_REQUIRED",
                    message="공식 SNS API 계약 식별자가 필요합니다.",
                    status_code=422,
                )
            self._gateways[gateway.provider] = gateway

    def require(self, provider: str) -> OfficialSocialGateway:
        gateway = self._gateways.get(provider)
        if gateway is None:
            raise AppError(
                code="OFFICIAL_SOCIAL_RUNTIME_UNAVAILABLE",
                message="공식 SNS 전달 경계가 구성되지 않았습니다.",
                status_code=503,
            )
        return gateway


class FailClosedRepurposeBudgetGateway:
    async def reserve(
        self,
        *,
        workspace_id: UUID,
        actor_id: UUID,
        amount: Decimal,
        currency: str,
        request_hash: str,
    ) -> BudgetReservation:
        del workspace_id, actor_id, amount, currency, request_hash
        raise AppError(
            code="REPURPOSE_BUDGET_RUNTIME_UNAVAILABLE",
            message="비용 예약 경계가 구성되지 않았습니다.",
            status_code=503,
        )


class FailClosedRepurposeExportStore:
    async def put(
        self,
        *,
        workspace_id: UUID,
        object_name: str,
        body: bytes,
        media_type: str,
    ) -> StoredExport:
        del workspace_id, object_name, body, media_type
        raise AppError(
            code="REPURPOSE_EXPORT_RUNTIME_UNAVAILABLE",
            message="내보내기 저장소가 구성되지 않았습니다.",
            status_code=503,
        )
