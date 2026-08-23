"""Payment-provider ports. Unwired provider paths fail closed."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from blogops.core.errors import AppError


@dataclass(frozen=True, slots=True)
class ProviderCheckoutRequest:
    command_id: UUID
    workspace_id: UUID
    operation: str
    request_snapshot: dict[str, Any]
    idempotency_key: str
    return_url: str


@dataclass(frozen=True, slots=True)
class ProviderCheckoutResult:
    provider_request_ref: str
    checkout_url: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class VerifiedPaymentEvent:
    provider_event_id: str
    event_type: str
    workspace_id: UUID
    occurred_at: datetime
    normalized_payload: dict[str, Any]
    signature_key_version: str


class PaymentGateway(Protocol):
    async def create_checkout(self, request: ProviderCheckoutRequest) -> ProviderCheckoutResult: ...

    async def verify_event(
        self, *, headers: dict[str, str], body: bytes
    ) -> VerifiedPaymentEvent: ...


class PaymentPayloadArchive(Protocol):
    async def store(
        self, *, provider: str, provider_event_id: str, body: bytes
    ) -> str: ...


class PaymentGatewayRegistry:
    def __init__(self, adapters: Mapping[str, PaymentGateway] | None = None) -> None:
        self._adapters = dict(adapters or {})

    def resolve(self, provider: str) -> PaymentGateway:
        value = self._adapters.get(provider)
        if value is None:
            raise AppError(
                "PAYMENT_PROVIDER_UNAVAILABLE",
                "요청한 결제 공급자 Adapter가 구성되지 않았습니다.",
                503,
            )
        return value


class FailClosedPaymentGateway:
    async def create_checkout(
        self, request: ProviderCheckoutRequest
    ) -> ProviderCheckoutResult:
        del request
        raise AppError(
            "PAYMENT_PROVIDER_UNAVAILABLE",
            "결제 공급자 Adapter가 구성되지 않아 요청을 진행할 수 없습니다.",
            503,
        )

    async def store(
        self, *, provider: str, provider_event_id: str, body: bytes
    ) -> str:
        del provider, provider_event_id, body
        raise AppError(
            "PAYMENT_PAYLOAD_ARCHIVE_UNAVAILABLE",
            "검증된 결제 Payload 보관소가 구성되지 않았습니다.",
            503,
        )

    async def verify_event(
        self, *, headers: dict[str, str], body: bytes
    ) -> VerifiedPaymentEvent:
        del headers, body
        raise AppError(
            "PAYMENT_WEBHOOK_VERIFIER_UNAVAILABLE",
            "결제 Webhook 검증기가 구성되지 않았습니다.",
            503,
        )
