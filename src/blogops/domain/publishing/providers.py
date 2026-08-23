"""Fail-closed external provider, secret and media contracts used only by workers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Generic, Mapping, Protocol, TypeVar

from blogops.core.errors import AppError
from blogops.domain.publishing.enums import PublishingProvider, PublishVisibility, RetryClass
from blogops.domain.publishing.rules import redact_metadata, redact_text


T = TypeVar("T")


class SecretMaterial:
    """Short-lived secret values whose representation can never reveal their contents."""

    __slots__ = ("_values",)

    def __init__(self, values: Mapping[str, str]) -> None:
        self._values = dict(values)

    def require(self, key: str) -> str:
        value = self._values.get(key)
        if not value:
            raise ProviderFailure(
                code="PUBLISH_CREDENTIAL_FIELD_MISSING",
                detail="게시 자격 증명에 필요한 필드가 없습니다.",
                retry_class=RetryClass.FINAL,
            )
        return value

    def optional(self, key: str) -> str | None:
        return self._values.get(key)

    def expires_at(self) -> datetime | None:
        value = self.optional("expires_at")
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ProviderFailure(
                code="PUBLISH_CREDENTIAL_EXPIRY_INVALID",
                detail="Credential 만료 시각 형식이 올바르지 않습니다.",
                retry_class=RetryClass.FINAL,
            ) from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ProviderFailure(
                code="PUBLISH_CREDENTIAL_EXPIRY_INVALID",
                detail="Credential 만료 시각에는 시간대 정보가 필요합니다.",
                retry_class=RetryClass.FINAL,
            )
        return parsed.astimezone(UTC)

    def __repr__(self) -> str:
        return "SecretMaterial([REDACTED])"

    __str__ = __repr__


class SecretResolver(Protocol):
    async def resolve(self, secret_ref: str) -> SecretMaterial: ...

    async def refresh(self, secret_ref: str) -> SecretMaterial: ...

    async def revoke(self, secret_ref: str) -> None: ...


class UnavailableSecretResolver:
    async def resolve(self, secret_ref: str) -> SecretMaterial:
        del secret_ref
        raise AppError(
            "PUBLISH_SECRET_RESOLVER_UNAVAILABLE",
            "게시 Secret Resolver가 구성되지 않았습니다.",
            503,
        )

    async def revoke(self, secret_ref: str) -> None:
        del secret_ref
        raise AppError(
            "PUBLISH_SECRET_RESOLVER_UNAVAILABLE",
            "게시 Secret Resolver가 구성되지 않았습니다.",
            503,
        )

    async def refresh(self, secret_ref: str) -> SecretMaterial:
        del secret_ref
        raise AppError(
            "PUBLISH_SECRET_REFRESH_UNAVAILABLE",
            "게시 Credential Refresh Resolver가 구성되지 않았습니다.",
            503,
        )


@dataclass(frozen=True, slots=True)
class MediaBinary:
    placement_key: str
    filename: str
    mime_type: str
    content: bytes = field(repr=False)
    alt_text: str = ""
    caption: str | None = None


class MediaBinaryResolver(Protocol):
    async def resolve(self, object_ref: str, *, expected_hash: str) -> bytes: ...


class UnavailableMediaBinaryResolver:
    async def resolve(self, object_ref: str, *, expected_hash: str) -> bytes:
        del object_ref, expected_hash
        raise AppError(
            "PUBLISH_MEDIA_RESOLVER_UNAVAILABLE",
            "게시 미디어 저장소 Resolver가 구성되지 않았습니다.",
            503,
        )


@dataclass(frozen=True, slots=True)
class ConnectionContext:
    provider: PublishingProvider
    site_url: str
    site_timezone: str
    remote_site_id: str | None
    official_contract: str
    api_version: str
    safe_config: dict[str, Any]
    site_settings: dict[str, Any]


@dataclass(frozen=True, slots=True)
class PublishDocument:
    title: str
    html: str
    plain_text: str
    visibility: PublishVisibility
    scheduled_at_utc: datetime | None
    idempotency_marker: str
    options: dict[str, Any]
    media_urls: dict[str, str]


@dataclass(frozen=True, slots=True)
class ProviderDiagnostic:
    checks: list[dict[str, Any]]
    capabilities: list[str]
    site_settings: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RemotePost:
    remote_id: str
    remote_url: str
    state: str
    etag: str | None
    updated_at: datetime | None
    snapshot: dict[str, Any]
    remote_hash: str


@dataclass(frozen=True, slots=True)
class UploadedMedia:
    remote_id: str
    remote_url: str
    placement_key: str


@dataclass(frozen=True, slots=True)
class ProviderCall(Generic[T]):
    value: T
    method: str
    endpoint_path: str
    status_code: int
    provider_request_id: str | None
    request_metadata: dict[str, Any] = field(default_factory=dict)
    response_metadata: dict[str, Any] = field(default_factory=dict)

    def safe_request_metadata(self) -> dict[str, Any]:
        return redact_metadata(self.request_metadata)

    def safe_response_metadata(self) -> dict[str, Any]:
        return redact_metadata(self.response_metadata)


class ProviderFailure(Exception):
    def __init__(
        self,
        *,
        code: str,
        detail: str,
        retry_class: RetryClass,
        status_code: int | None = None,
        retry_after_seconds: int | None = None,
        method: str = "UNKNOWN",
        endpoint_path: str = "",
        request_metadata: dict[str, Any] | None = None,
        response_metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = redact_text(detail)
        self.retry_class = retry_class
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
        self.method = method
        self.endpoint_path = endpoint_path[:1_000]
        self.request_metadata = redact_metadata(request_metadata or {})
        self.response_metadata = redact_metadata(response_metadata or {})


class CMSProvider(Protocol):
    provider: PublishingProvider
    official_contract: str

    async def diagnose(
        self, connection: ConnectionContext, secret: SecretMaterial
    ) -> ProviderCall[ProviderDiagnostic]: ...

    async def refresh(
        self, connection: ConnectionContext, secret: SecretMaterial
    ) -> ProviderCall[ProviderDiagnostic]: ...

    async def sync_settings(
        self, connection: ConnectionContext, secret: SecretMaterial
    ) -> ProviderCall[ProviderDiagnostic]: ...

    async def find_by_marker(
        self,
        connection: ConnectionContext,
        secret: SecretMaterial,
        marker: str,
    ) -> ProviderCall[RemotePost | None]: ...

    async def create_post(
        self,
        connection: ConnectionContext,
        secret: SecretMaterial,
        document: PublishDocument,
    ) -> ProviderCall[RemotePost]: ...

    async def get_post(
        self,
        connection: ConnectionContext,
        secret: SecretMaterial,
        remote_id: str,
    ) -> ProviderCall[RemotePost]: ...

    async def update_post(
        self,
        connection: ConnectionContext,
        secret: SecretMaterial,
        remote: RemotePost,
        document: PublishDocument,
    ) -> ProviderCall[RemotePost]: ...

    async def delete_post(
        self,
        connection: ConnectionContext,
        secret: SecretMaterial,
        remote: RemotePost,
        *,
        force: bool,
    ) -> ProviderCall[RemotePost]: ...

    async def cancel_scheduled(
        self,
        connection: ConnectionContext,
        secret: SecretMaterial,
        remote: RemotePost,
    ) -> ProviderCall[RemotePost]: ...

    async def upload_media(
        self,
        connection: ConnectionContext,
        secret: SecretMaterial,
        media: MediaBinary,
    ) -> ProviderCall[UploadedMedia]: ...

    async def restore_snapshot(
        self,
        connection: ConnectionContext,
        secret: SecretMaterial,
        remote: RemotePost,
        snapshot: dict[str, Any],
    ) -> ProviderCall[RemotePost]: ...


class ProviderRegistry:
    def __init__(self, providers: list[CMSProvider] | None = None) -> None:
        self._providers = {
            (item.provider, item.official_contract): item for item in (providers or [])
        }

    def require(
        self, provider: PublishingProvider, official_contract: str
    ) -> CMSProvider:
        value = self._providers.get((provider, official_contract))
        if value is None:
            raise AppError(
                "PUBLISH_PROVIDER_UNAVAILABLE",
                "요청한 공식 게시 provider가 worker에 구성되지 않았습니다.",
                503,
                fields=[
                    {"path": "provider", "reason": provider.value},
                    {"path": "official_contract", "reason": official_contract},
                ],
            )
        return value


_provider_registry: ProviderRegistry | None = None
_secret_resolver: SecretResolver | None = None
_media_resolver: MediaBinaryResolver | None = None


def configure_worker_providers(
    *,
    registry: ProviderRegistry,
    secrets: SecretResolver,
    media: MediaBinaryResolver,
) -> None:
    """Worker bootstrap hook; API request code must never call this as a fallback."""

    global _provider_registry, _secret_resolver, _media_resolver
    _provider_registry = registry
    _secret_resolver = secrets
    _media_resolver = media


def get_provider_registry() -> ProviderRegistry:
    if _provider_registry is not None:
        return _provider_registry
    from blogops.domain.publishing.adapters import official_provider_registry

    return official_provider_registry()


def get_secret_resolver() -> SecretResolver:
    return _secret_resolver or UnavailableSecretResolver()


def get_media_resolver() -> MediaBinaryResolver:
    return _media_resolver or UnavailableMediaBinaryResolver()
