"""External ports for developer credentials, DNS, rate limits and webhooks."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from blogops.core.errors import AppError
from blogops.domain.developer.security import RateLimitRule


class ApiKeySecrets(Protocol):
    async def pepper(self, version: str | None = None) -> tuple[str, bytes]: ...

    async def webhook_secret(self, secret_ref: str, version: str) -> bytes: ...


class WorkspaceApiPolicy(Protocol):
    async def allowed_scopes(self, workspace_id: UUID) -> frozenset[str]: ...

    async def allowed_webhook_events(self, workspace_id: UUID) -> frozenset[str]: ...


class DnsResolver(Protocol):
    async def resolve_public(self, hostname: str) -> list[str]: ...


class WebhookOwnershipVerifier(Protocol):
    async def verify(self, *, url: str, resolved_addresses: tuple[str, ...]) -> str: ...


class PrivateWebhookPayloads(Protocol):
    async def read(self, object_ref: str) -> bytes: ...


@dataclass(frozen=True, slots=True)
class WebhookTransportRequest:
    url: str
    resolved_addresses: tuple[str, ...]
    headers: dict[str, str]
    body: bytes
    allow_redirects: bool = False


@dataclass(frozen=True, slots=True)
class WebhookTransportResult:
    status_code: int | None
    headers_masked: dict[str, str]
    body_hash: str | None
    body_preview_masked: str | None
    duration_ms: int
    transport_error_code: str | None = None


class WebhookTransport(Protocol):
    async def send(self, request: WebhookTransportRequest) -> WebhookTransportResult: ...


class RateLimitStore(Protocol):
    async def consume(
        self, *, rules: tuple[RateLimitRule, ...], request_id: str
    ) -> tuple[bool, dict[str, int]]: ...


class FailClosedDeveloperAdapters:
    async def pepper(self, version: str | None = None) -> tuple[str, bytes]:
        del version
        raise AppError("API_KEY_HASHER_UNAVAILABLE", "API Key 해시 비밀이 구성되지 않았습니다.", 503)

    async def webhook_secret(self, secret_ref: str, version: str) -> bytes:
        del secret_ref, version
        raise AppError("WEBHOOK_SECRET_UNAVAILABLE", "Webhook Secret을 읽을 수 없습니다.", 503)

    async def allowed_scopes(self, workspace_id: UUID) -> frozenset[str]:
        del workspace_id
        raise AppError("API_SCOPE_POLICY_UNAVAILABLE", "API Scope 정책이 구성되지 않았습니다.", 503)

    async def allowed_webhook_events(self, workspace_id: UUID) -> frozenset[str]:
        del workspace_id
        raise AppError(
            "WEBHOOK_EVENT_POLICY_UNAVAILABLE",
            "Webhook 이벤트 정책이 구성되지 않았습니다.",
            503,
        )

    async def resolve_public(self, hostname: str) -> list[str]:
        del hostname
        raise AppError("WEBHOOK_DNS_UNAVAILABLE", "Webhook DNS 검증기를 사용할 수 없습니다.", 503)

    async def verify(self, *, url: str, resolved_addresses: tuple[str, ...]) -> str:
        del url, resolved_addresses
        raise AppError("WEBHOOK_OWNERSHIP_UNVERIFIED", "Endpoint 소유권을 검증할 수 없습니다.", 503)

    async def read(self, object_ref: str) -> bytes:
        del object_ref
        raise AppError("WEBHOOK_PAYLOAD_UNAVAILABLE", "Webhook Payload를 읽을 수 없습니다.", 503)

    async def send(self, request: WebhookTransportRequest) -> WebhookTransportResult:
        del request
        raise AppError("WEBHOOK_TRANSPORT_UNAVAILABLE", "Webhook 전송기가 구성되지 않았습니다.", 503)

    async def consume(
        self, *, rules: tuple[RateLimitRule, ...], request_id: str
    ) -> tuple[bool, dict[str, int]]:
        del rules, request_id
        raise AppError("API_RATE_LIMITER_UNAVAILABLE", "Rate Limiter가 구성되지 않았습니다.", 503)
