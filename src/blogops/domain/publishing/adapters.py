"""Official WordPress, Ghost Admin and Google Blogger API adapters.

The adapters deliberately reject redirects and pin freshly validated public DNS answers.
They never accept arbitrary endpoint templates and never log request/response bodies.
"""

from __future__ import annotations

import asyncio
import base64
from datetime import UTC, datetime
import hashlib
import hmac
import json
import socket
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, urlencode, urlsplit

import aiohttp
from aiohttp.abc import AbstractResolver

from blogops.core.errors import AppError
from blogops.domain.publishing.enums import PublishingProvider, PublishVisibility, RetryClass
from blogops.domain.publishing.providers import (
    ConnectionContext,
    MediaBinary,
    ProviderCall,
    ProviderDiagnostic,
    ProviderFailure,
    PublishDocument,
    RemotePost,
    SecretMaterial,
    UploadedMedia,
)
from blogops.domain.publishing.rules import canonical_hash, classify_retry, redact_metadata
from blogops.domain.publishing.security import (
    validate_resolved_addresses,
    validate_site_url,
)

if TYPE_CHECKING:
    from blogops.domain.publishing.providers import ProviderRegistry


MAX_PROVIDER_RESPONSE_BYTES = 5 * 1024 * 1024
WORDPRESS_POSTS_PATH = "/wp-json/wp/v2/posts"
WORDPRESS_MEDIA_PATH = "/wp-json/wp/v2/media"
GHOST_POSTS_PATH = "/ghost/api/admin/posts/"
GHOST_IMAGES_PATH = "/ghost/api/admin/images/upload/"
BLOGGER_API_BASE = "https://www.googleapis.com"
BLOGGER_SCOPE = "https://www.googleapis.com/auth/blogger"


class _PinnedResolver(AbstractResolver):
    def __init__(self, hostname: str, addresses: tuple[str, ...]) -> None:
        self.hostname = hostname
        self.addresses = addresses

    async def resolve(
        self, host: str, port: int = 0, family: int = socket.AF_INET
    ) -> list[dict[str, object]]:
        if host.rstrip(".").casefold() != self.hostname:
            raise OSError("publishing resolver hostname mismatch")
        return [
            {
                "hostname": host,
                "host": address,
                "port": port,
                "family": socket.AF_INET6 if ":" in address else socket.AF_INET,
                "proto": socket.IPPROTO_TCP,
                "flags": socket.AI_NUMERICHOST,
            }
            for address in self.addresses
        ]

    async def close(self) -> None:
        return None


class SafeOfficialTransport:
    """One-hop HTTPS transport. Every call resolves and pins the approved host anew."""

    def __init__(self, base_url: str, *, timeout_seconds: float = 30.0) -> None:
        self.site = validate_site_url(base_url)
        self.timeout_seconds = timeout_seconds

    async def _addresses(self) -> tuple[str, ...]:
        loop = asyncio.get_running_loop()
        try:
            results = await loop.getaddrinfo(
                self.site.hostname,
                self.site.port,
                family=socket.AF_UNSPEC,
                type=socket.SOCK_STREAM,
                proto=socket.IPPROTO_TCP,
            )
        except OSError as exc:
            raise ProviderFailure(
                code="PUBLISH_DNS_FAILED",
                detail="게시 사이트 DNS 확인에 실패했습니다.",
                retry_class=RetryClass.NETWORK,
            ) from exc
        addresses = tuple(sorted({item[4][0] for item in results}))
        try:
            validate_resolved_addresses(addresses)
        except AppError as exc:
            raise ProviderFailure(
                code=exc.code,
                detail=exc.message,
                retry_class=RetryClass.FINAL,
            ) from exc
        return addresses

    async def request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str],
        query: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        form: aiohttp.FormData | None = None,
    ) -> tuple[int, dict[str, str], Any]:
        if not path.startswith("/") or "//" in path:
            raise ProviderFailure(
                code="PUBLISH_OFFICIAL_PATH_INVALID",
                detail="공식 API 상대 경로가 올바르지 않습니다.",
                retry_class=RetryClass.FINAL,
                method=method,
                endpoint_path=path,
            )
        endpoint = f"{self.site.normalized.rstrip('/')}{path}"
        if query:
            endpoint = f"{endpoint}?{urlencode(query)}"
        addresses = await self._addresses()
        connector = aiohttp.TCPConnector(
            resolver=_PinnedResolver(self.site.hostname, addresses),
            use_dns_cache=False,
            limit=1,
        )
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        safe_headers = {**headers, "User-Agent": "BlogOpsPublisher/1.0"}
        try:
            async with aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                trust_env=False,
                headers=safe_headers,
            ) as client:
                async with client.request(
                    method,
                    endpoint,
                    json=json_body if form is None else None,
                    data=form,
                    allow_redirects=False,
                ) as response:
                    if 300 <= response.status <= 399:
                        raise ProviderFailure(
                            code="PUBLISH_REDIRECT_BLOCKED",
                            detail="CMS API 리디렉션은 SSRF 보호를 위해 차단되었습니다.",
                            retry_class=RetryClass.FINAL,
                            status_code=response.status,
                            method=method,
                            endpoint_path=path,
                        )
                    content = bytearray()
                    async for chunk in response.content.iter_chunked(64 * 1024):
                        content.extend(chunk)
                        if len(content) > MAX_PROVIDER_RESPONSE_BYTES:
                            raise ProviderFailure(
                                code="PUBLISH_RESPONSE_TOO_LARGE",
                                detail="CMS 응답이 허용 크기를 초과했습니다.",
                                retry_class=RetryClass.FINAL,
                                status_code=response.status,
                                method=method,
                                endpoint_path=path,
                            )
                    selected_headers = {
                        key.casefold(): value
                        for key, value in response.headers.items()
                        if key.casefold() in {"etag", "last-modified", "retry-after", "x-request-id"}
                    }
                    if response.status >= 400:
                        retry_class = classify_retry(
                            network_error=False, status_code=response.status
                        )
                        retry_after = _retry_after(selected_headers.get("retry-after"))
                        raise ProviderFailure(
                            code=(
                                "PUBLISH_PROVIDER_RETRYABLE"
                                if retry_class is not RetryClass.FINAL
                                else "PUBLISH_PROVIDER_REJECTED"
                            ),
                            detail=f"공식 CMS API 요청이 실패했습니다({response.status}).",
                            retry_class=retry_class,
                            status_code=response.status,
                            retry_after_seconds=retry_after,
                            method=method,
                            endpoint_path=path,
                            response_metadata={"headers": selected_headers},
                        )
                    if not content:
                        body: Any = {}
                    else:
                        try:
                            parsed = json.loads(content.decode("utf-8"))
                        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                            raise ProviderFailure(
                                code="PUBLISH_PROVIDER_RESPONSE_INVALID",
                                detail="CMS가 유효한 JSON을 반환하지 않았습니다.",
                                retry_class=RetryClass.FINAL,
                                status_code=response.status,
                                method=method,
                                endpoint_path=path,
                            ) from exc
                        if not isinstance(parsed, (dict, list)):
                            raise ProviderFailure(
                                code="PUBLISH_PROVIDER_RESPONSE_INVALID",
                                detail="CMS JSON 응답 형식이 올바르지 않습니다.",
                                retry_class=RetryClass.FINAL,
                                status_code=response.status,
                                method=method,
                                endpoint_path=path,
                            )
                        body = parsed
                    return response.status, selected_headers, body
        except ProviderFailure:
            raise
        except (TimeoutError, aiohttp.ClientError, OSError) as exc:
            raise ProviderFailure(
                code="PUBLISH_PROVIDER_NETWORK",
                detail="CMS 네트워크 요청에 실패했습니다.",
                retry_class=RetryClass.NETWORK,
                method=method,
                endpoint_path=path,
            ) from exc


class WordPressOfficialAdapter:
    provider = PublishingProvider.WORDPRESS
    official_contract = "wordpress-rest-v2"

    def _transport(self, connection: ConnectionContext) -> SafeOfficialTransport:
        return SafeOfficialTransport(connection.site_url)

    def _headers(self, secret: SecretMaterial) -> dict[str, str]:
        token = secret.optional("access_token")
        if token:
            return {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        username = secret.require("username")
        password = secret.require("application_password")
        encoded = base64.b64encode(f"{username}:{password}".encode()).decode()
        return {"Authorization": f"Basic {encoded}", "Accept": "application/json"}

    async def diagnose(
        self, connection: ConnectionContext, secret: SecretMaterial
    ) -> ProviderCall[ProviderDiagnostic]:
        index_status, _index_headers, index = await self._transport(connection).request(
            "GET", "/wp-json", headers=self._headers(secret)
        )
        path = "/wp-json/wp/v2/users/me"
        status, headers, body = await self._transport(connection).request(
            "GET", path, headers=self._headers(secret), query={"context": "edit"}
        )
        settings_path = "/wp-json/wp/v2/settings"
        settings_status, settings_headers, settings = await self._transport(
            connection
        ).request("GET", settings_path, headers=self._headers(secret))
        raw_capabilities = (
            body.get("capabilities") if isinstance(body.get("capabilities"), dict) else {}
        )
        capabilities = ["draft", "update"] if raw_capabilities.get("edit_posts") else []
        if raw_capabilities.get("publish_posts"):
            capabilities.extend(["publish", "future"])
        if raw_capabilities.get("delete_posts"):
            capabilities.append("delete")
        if raw_capabilities.get("upload_files"):
            capabilities.append("media")
        if raw_capabilities.get("manage_categories"):
            capabilities.append("taxonomy_create")
        routes = index.get("routes") if isinstance(index, dict) else {}
        for capability, route_path in (
            ("categories", "/wp/v2/categories"),
            ("tags", "/wp/v2/tags"),
            ("authors", "/wp/v2/users"),
        ):
            if route_path in routes:
                capabilities.append(capability)
        remote_timezone = settings.get("timezone")
        value = ProviderDiagnostic(
            checks=[
                {"key": "authentication", "ok": bool(body.get("id"))},
                {
                    "key": "api_index",
                    "ok": index_status == 200 and "/wp/v2/posts" in routes,
                    "path": "/wp-json",
                    "contract": self.official_contract,
                },
                {"key": "draft_permission", "ok": "draft" in capabilities},
                {"key": "publish_permission", "ok": "publish" in capabilities},
                {"key": "update_permission", "ok": "update" in capabilities},
                {"key": "delete_permission", "ok": "delete" in capabilities},
                {"key": "media", "ok": "media" in capabilities, "path": WORDPRESS_MEDIA_PATH},
                {
                    "key": "timezone",
                    "ok": bool(remote_timezone)
                    and remote_timezone == connection.site_timezone,
                    "configured": connection.site_timezone,
                    "remote": remote_timezone,
                },
            ],
            capabilities=capabilities,
            site_settings={
                "user_id": body.get("id"),
                "user_name": body.get("name"),
                "timezone": remote_timezone,
                **{
                    key: settings.get(key)
                    for key in (
                        "title",
                        "url",
                        "date_format",
                        "time_format",
                    )
                },
            },
        )
        return _call(
            value,
            "GET",
            settings_path,
            settings_status,
            settings_headers,
            {"authentication_path": path, "api_index_status": index_status},
        )

    async def refresh(self, connection: ConnectionContext, secret: SecretMaterial) -> ProviderCall[ProviderDiagnostic]:
        return await self.diagnose(connection, secret)

    async def sync_settings(self, connection: ConnectionContext, secret: SecretMaterial) -> ProviderCall[ProviderDiagnostic]:
        diagnosed = await self.diagnose(connection, secret)
        path = diagnosed.endpoint_path
        status = diagnosed.status_code
        headers: dict[str, str] = {}
        body = diagnosed.value.site_settings
        collections: dict[str, list[dict[str, Any]]] = {}
        for key, collection_path in {
            "categories": "/wp-json/wp/v2/categories",
            "tags": "/wp-json/wp/v2/tags",
            "authors": "/wp-json/wp/v2/users",
        }.items():
            _item_status, _item_headers, items = await self._transport(
                connection
            ).request(
                "GET",
                collection_path,
                headers=self._headers(secret),
                query={"context": "edit", "per_page": "100"},
            )
            raw_items = items if isinstance(items, list) else []
            collections[key] = [
                {
                    field: item.get(field)
                    for field in ("id", "name", "slug")
                    if field in item
                }
                for item in raw_items
                if isinstance(item, dict)
            ]
        value = ProviderDiagnostic(
            checks=[
                *diagnosed.value.checks,
                {"key": "settings", "ok": True},
                {"key": "categories_sync", "ok": True, "count": len(collections["categories"])},
                {"key": "tags_sync", "ok": True, "count": len(collections["tags"])},
                {"key": "authors_sync", "ok": True, "count": len(collections["authors"])},
            ],
            capabilities=list(
                dict.fromkeys(
                    [
                        *diagnosed.value.capabilities,
                        "categories",
                        "tags",
                        "authors",
                        "timezone",
                    ]
                )
            ),
            site_settings={
                **{
                    key: body.get(key)
                    for key in (
                        "title",
                        "url",
                        "timezone",
                        "date_format",
                        "time_format",
                    )
                },
                **collections,
            },
        )
        return _call(value, "GET", path, status, headers, {})

    async def find_by_marker(self, connection: ConnectionContext, secret: SecretMaterial, marker: str) -> ProviderCall[RemotePost | None]:
        path = WORDPRESS_POSTS_PATH
        status, headers, body = await self._transport(connection).request(
            "GET",
            path,
            headers=self._headers(secret),
            query={"slug": marker, "context": "edit", "per_page": "1"},
        )
        items = body if isinstance(body, list) else body.get("items")
        value = (
            _wordpress_remote(
                items[0], headers, allowed_meta=_wordpress_meta_allowlist(connection)
            )
            if items
            else None
        )
        return _call(value, "GET", path, status, headers, {"slug": marker})

    async def create_post(self, connection: ConnectionContext, secret: SecretMaterial, document: PublishDocument) -> ProviderCall[RemotePost]:
        path = WORDPRESS_POSTS_PATH
        payload = _wordpress_payload(document, creating=True)
        category_ids = _wordpress_integer_ids(
            document.options.get("category_ids", []), "categories"
        )
        category_ids.extend(
            await self._taxonomy_ids(
                connection,
                secret,
                names=document.options.get("category_names", []),
                taxonomy="categories",
                create_missing=bool(
                    document.options.get("create_missing_taxonomy")
                ),
            )
        )
        payload["categories"] = list(dict.fromkeys(category_ids))
        payload["tags"] = await self._taxonomy_ids(
            connection,
            secret,
            names=document.options.get("tags", []),
            taxonomy="tags",
            create_missing=bool(document.options.get("create_missing_taxonomy")),
        )
        author_id = await self._author_id(connection, secret, document)
        if author_id is not None:
            payload["author"] = author_id
        status, headers, body = await self._transport(connection).request(
            "POST", path, headers=self._headers(secret), json_body=payload
        )
        return _call(
            _wordpress_remote(
                body, headers, allowed_meta=_wordpress_meta_allowlist(connection)
            ),
            "POST",
            path,
            status,
            headers,
            {"fields": sorted(payload), "marker": document.idempotency_marker},
        )

    async def get_post(self, connection: ConnectionContext, secret: SecretMaterial, remote_id: str) -> ProviderCall[RemotePost]:
        path = f"{WORDPRESS_POSTS_PATH}/{quote(remote_id, safe='')}"
        status, headers, body = await self._transport(connection).request(
            "GET", path, headers=self._headers(secret), query={"context": "edit"}
        )
        return _call(
            _wordpress_remote(
                body, headers, allowed_meta=_wordpress_meta_allowlist(connection)
            ),
            "GET",
            path,
            status,
            headers,
            {},
        )

    async def update_post(self, connection: ConnectionContext, secret: SecretMaterial, remote: RemotePost, document: PublishDocument) -> ProviderCall[RemotePost]:
        path = f"{WORDPRESS_POSTS_PATH}/{quote(remote.remote_id, safe='')}"
        payload = _wordpress_payload(document, creating=False)
        category_ids = _wordpress_integer_ids(
            document.options.get("category_ids", []), "categories"
        )
        category_ids.extend(
            await self._taxonomy_ids(
                connection,
                secret,
                names=document.options.get("category_names", []),
                taxonomy="categories",
                create_missing=bool(
                    document.options.get("create_missing_taxonomy")
                ),
            )
        )
        payload["categories"] = list(dict.fromkeys(category_ids))
        payload["tags"] = await self._taxonomy_ids(
            connection,
            secret,
            names=document.options.get("tags", []),
            taxonomy="tags",
            create_missing=bool(document.options.get("create_missing_taxonomy")),
        )
        author_id = await self._author_id(connection, secret, document)
        if author_id is not None:
            payload["author"] = author_id
        status, headers, body = await self._transport(connection).request(
            "POST", path, headers=self._headers(secret), json_body=payload
        )
        return _call(_wordpress_remote(body, headers, allowed_meta=_wordpress_meta_allowlist(connection)), "POST", path, status, headers, {"fields": sorted(payload)})

    async def delete_post(self, connection: ConnectionContext, secret: SecretMaterial, remote: RemotePost, *, force: bool) -> ProviderCall[RemotePost]:
        path = f"{WORDPRESS_POSTS_PATH}/{quote(remote.remote_id, safe='')}"
        status, headers, body = await self._transport(connection).request(
            "DELETE", path, headers=self._headers(secret), query={"force": str(force).lower()}
        )
        previous = body.get("previous") if isinstance(body.get("previous"), dict) else body
        return _call(_wordpress_remote(previous, headers, deleted=True, allowed_meta=_wordpress_meta_allowlist(connection)), "DELETE", path, status, headers, {"force": force})

    async def cancel_scheduled(self, connection: ConnectionContext, secret: SecretMaterial, remote: RemotePost) -> ProviderCall[RemotePost]:
        path = f"{WORDPRESS_POSTS_PATH}/{quote(remote.remote_id, safe='')}"
        status, headers, body = await self._transport(connection).request(
            "POST",
            path,
            headers=self._headers(secret),
            json_body={"status": "draft"},
        )
        return _call(
            _wordpress_remote(
                body, headers, allowed_meta=_wordpress_meta_allowlist(connection)
            ),
            "POST",
            path,
            status,
            headers,
            {"status": "draft"},
        )

    async def upload_media(self, connection: ConnectionContext, secret: SecretMaterial, media: MediaBinary) -> ProviderCall[UploadedMedia]:
        form = aiohttp.FormData()
        form.add_field("file", media.content, filename=media.filename, content_type=media.mime_type)
        form.add_field("alt_text", media.alt_text)
        if media.caption:
            form.add_field("caption", media.caption)
        status, headers, body = await self._transport(connection).request(
            "POST", WORDPRESS_MEDIA_PATH, headers=self._headers(secret), form=form
        )
        remote_id = _required_string(body, "id")
        remote_url = _required_url(body.get("source_url"))
        value = UploadedMedia(remote_id, remote_url, media.placement_key)
        return _call(value, "POST", WORDPRESS_MEDIA_PATH, status, headers, {"mime_type": media.mime_type, "size": len(media.content)})

    async def restore_snapshot(self, connection: ConnectionContext, secret: SecretMaterial, remote: RemotePost, snapshot: dict[str, Any]) -> ProviderCall[RemotePost]:
        path = f"{WORDPRESS_POSTS_PATH}/{quote(remote.remote_id, safe='')}"
        payload = {key: snapshot[key] for key in ("title", "content", "excerpt", "status", "slug", "categories", "tags", "author", "featured_media", "comment_status", "meta") if key in snapshot}
        for key in ("title", "content", "excerpt"):
            if isinstance(payload.get(key), dict):
                payload[key] = payload[key].get("raw", "")
        status, headers, body = await self._transport(connection).request("POST", path, headers=self._headers(secret), json_body=payload)
        return _call(_wordpress_remote(body, headers, allowed_meta=_wordpress_meta_allowlist(connection)), "POST", path, status, headers, {"rollback_fields": sorted(payload)})

    async def _taxonomy_ids(
        self,
        connection: ConnectionContext,
        secret: SecretMaterial,
        *,
        names: list[Any],
        taxonomy: str,
        create_missing: bool,
    ) -> list[int]:
        path = f"/wp-json/wp/v2/{taxonomy}"
        ids: list[int] = []
        for name in names:
            status, _headers, body = await self._transport(connection).request(
                "GET",
                path,
                headers=self._headers(secret),
                query={"search": str(name), "context": "edit", "per_page": "100"},
            )
            del status
            items = body if isinstance(body, list) else body.get("items", [])
            exact_items = [
                item
                for item in items
                if isinstance(item, dict)
                and str(item.get("name", "")).casefold() == str(name).casefold()
            ]
            exact = min(
                exact_items,
                key=lambda item: str(item.get("id", "")),
                default=None,
            )
            if exact is None:
                if not create_missing:
                    raise ProviderFailure(
                        code="WORDPRESS_TAXONOMY_NOT_FOUND",
                        detail="WordPress taxonomy 항목이 없고 자동 생성 정책이 비활성화되어 있습니다.",
                        retry_class=RetryClass.FINAL,
                        method="GET",
                        endpoint_path=path,
                    )
                _created_status, _created_headers, exact = await self._transport(
                    connection
                ).request(
                    "POST",
                    path,
                    headers=self._headers(secret),
                    json_body={"name": str(name)},
                )
            try:
                ids.append(int(exact["id"]))
            except (KeyError, TypeError, ValueError) as exc:
                raise ProviderFailure(
                    code="WORDPRESS_TAXONOMY_RESPONSE_INVALID",
                    detail="WordPress taxonomy 응답에 유효한 ID가 없습니다.",
                    retry_class=RetryClass.FINAL,
                    endpoint_path=path,
                ) from exc
        return list(dict.fromkeys(ids))

    async def _author_id(
        self,
        connection: ConnectionContext,
        secret: SecretMaterial,
        document: PublishDocument,
    ) -> int | None:
        value = document.options.get("remote_author_id")
        if value is None:
            return None
        try:
            author_id = int(value)
        except (TypeError, ValueError) as exc:
            raise ProviderFailure(
                code="WORDPRESS_AUTHOR_ID_INVALID",
                detail="WordPress 작성자는 원격 정수 User ID여야 합니다.",
                retry_class=RetryClass.FINAL,
            ) from exc
        path = f"/wp-json/wp/v2/users/{author_id}"
        status, _headers, body = await self._transport(connection).request(
            "GET",
            path,
            headers=self._headers(secret),
            query={"context": "edit"},
        )
        del status
        if str(body.get("id")) != str(author_id):
            raise ProviderFailure(
                code="WORDPRESS_AUTHOR_NOT_FOUND",
                detail="권한이 있는 WordPress 작성자 매핑을 확인할 수 없습니다.",
                retry_class=RetryClass.FINAL,
                endpoint_path=path,
            )
        return author_id


class GhostOfficialAdapter:
    provider = PublishingProvider.GHOST
    official_contract = "ghost-admin-api"

    def _transport(self, connection: ConnectionContext) -> SafeOfficialTransport:
        return SafeOfficialTransport(connection.site_url)

    def _headers(self, connection: ConnectionContext, secret: SecretMaterial) -> dict[str, str]:
        key = secret.require("admin_api_key")
        try:
            key_id, raw_secret = key.split(":", 1)
            secret_bytes = bytes.fromhex(raw_secret)
        except (ValueError, TypeError) as exc:
            raise ProviderFailure(code="GHOST_ADMIN_KEY_INVALID", detail="Ghost Admin API key 형식이 올바르지 않습니다.", retry_class=RetryClass.FINAL) from exc
        now = int(time.time())
        header = _base64url(json.dumps({"alg": "HS256", "kid": key_id, "typ": "JWT"}).encode())
        payload = _base64url(json.dumps({"iat": now, "exp": now + 300, "aud": "/admin/"}).encode())
        signature = _base64url(hmac.new(secret_bytes, f"{header}.{payload}".encode(), hashlib.sha256).digest())
        return {
            "Authorization": f"Ghost {header}.{payload}.{signature}",
            "Accept-Version": connection.api_version,
            "Accept": "application/json",
        }

    async def diagnose(self, connection: ConnectionContext, secret: SecretMaterial) -> ProviderCall[ProviderDiagnostic]:
        path = "/ghost/api/admin/site/"
        status, headers, body = await self._transport(connection).request("GET", path, headers=self._headers(connection, secret))
        site = body.get("site") if isinstance(body.get("site"), dict) else {}
        remote_timezone = site.get("timezone")
        value = ProviderDiagnostic(
            checks=[{"key": "authentication", "ok": True}, {"key": "api", "ok": True}, {"key": "media", "ok": True, "path": GHOST_IMAGES_PATH}, {"key": "timezone", "ok": bool(connection.site_timezone) and (remote_timezone is None or remote_timezone == connection.site_timezone), "source": "remote" if remote_timezone else "workspace_configuration", "configured": connection.site_timezone, "remote": remote_timezone}],
            capabilities=["draft", "publish", "future", "update", "delete", "media", "lexical", "html", "tags", "taxonomy_create", "authors", "newsletter", "visibility"],
            site_settings=redact_metadata(site),
        )
        return _call(value, "GET", path, status, headers, {})

    async def refresh(self, connection: ConnectionContext, secret: SecretMaterial) -> ProviderCall[ProviderDiagnostic]:
        return await self.diagnose(connection, secret)

    async def sync_settings(self, connection: ConnectionContext, secret: SecretMaterial) -> ProviderCall[ProviderDiagnostic]:
        diagnosed = await self.diagnose(connection, secret)
        collections: dict[str, list[dict[str, Any]]] = {}
        status = diagnosed.status_code
        headers: dict[str, str] = {}
        path = "/ghost/api/admin/site/"
        for key, collection_path, wrapper in (
            ("tags", "/ghost/api/admin/tags/", "tags"),
            ("authors", "/ghost/api/admin/users/", "users"),
            ("newsletters", "/ghost/api/admin/newsletters/", "newsletters"),
        ):
            path = collection_path
            status, headers, body = await self._transport(connection).request(
                "GET",
                collection_path,
                headers=self._headers(connection, secret),
                query={"limit": "all"},
            )
            raw_items = body.get(wrapper)
            collections[key] = [
                {
                    field: item.get(field)
                    for field in ("id", "name", "slug", "status")
                    if field in item
                }
                for item in (raw_items if isinstance(raw_items, list) else [])
                if isinstance(item, dict)
            ]
        value = ProviderDiagnostic(
            checks=[
                *diagnosed.value.checks,
                *[
                    {"key": f"{key}_sync", "ok": True, "count": len(items)}
                    for key, items in collections.items()
                ],
            ],
            capabilities=diagnosed.value.capabilities,
            site_settings={**diagnosed.value.site_settings, **collections},
        )
        return _call(value, "GET", path, status, headers, {"incremental": True})

    async def find_by_marker(self, connection: ConnectionContext, secret: SecretMaterial, marker: str) -> ProviderCall[RemotePost | None]:
        path = f"{GHOST_POSTS_PATH}slug/{quote(marker, safe='')}/"
        try:
            status, headers, body = await self._transport(connection).request(
                "GET",
                path,
                headers=self._headers(connection, secret),
                query={"formats": "html,lexical", "include": "authors,tags"},
            )
        except ProviderFailure as exc:
            if exc.status_code == 404:
                return ProviderCall(None, "GET", path, 404, None, {"slug": marker}, {})
            raise
        posts = body.get("posts")
        value = _ghost_remote(posts[0], headers) if isinstance(posts, list) and posts else None
        return _call(value, "GET", path, status, headers, {"slug": marker})

    async def create_post(self, connection: ConnectionContext, secret: SecretMaterial, document: PublishDocument) -> ProviderCall[RemotePost]:
        payload = {"posts": [_ghost_payload(document, creating=True)]}
        query = _ghost_write_query(document, connection)
        status, headers, body = await self._transport(connection).request("POST", GHOST_POSTS_PATH, headers=self._headers(connection, secret), query=query, json_body=payload)
        return _call(_ghost_wrapped_remote(body, headers), "POST", GHOST_POSTS_PATH, status, headers, {"fields": sorted(payload["posts"][0]), "marker": document.idempotency_marker})

    async def get_post(self, connection: ConnectionContext, secret: SecretMaterial, remote_id: str) -> ProviderCall[RemotePost]:
        path = f"{GHOST_POSTS_PATH}{quote(remote_id, safe='')}/"
        status, headers, body = await self._transport(connection).request(
            "GET",
            path,
            headers=self._headers(connection, secret),
            query={"formats": "html,lexical", "include": "authors,tags"},
        )
        return _call(_ghost_wrapped_remote(body, headers), "GET", path, status, headers, {})

    async def update_post(self, connection: ConnectionContext, secret: SecretMaterial, remote: RemotePost, document: PublishDocument) -> ProviderCall[RemotePost]:
        path = f"{GHOST_POSTS_PATH}{quote(remote.remote_id, safe='')}/"
        payload = _ghost_payload(document, creating=False)
        updated_at = remote.snapshot.get("updated_at")
        if not updated_at:
            raise ProviderFailure(code="GHOST_UPDATED_AT_REQUIRED", detail="Ghost 수정에는 updated_at 충돌 토큰이 필요합니다.", retry_class=RetryClass.FINAL, method="PUT", endpoint_path=path)
        payload["updated_at"] = updated_at
        status, headers, body = await self._transport(connection).request("PUT", path, headers=self._headers(connection, secret), query=_ghost_write_query(document, connection), json_body={"posts": [payload]})
        return _call(_ghost_wrapped_remote(body, headers), "PUT", path, status, headers, {"fields": sorted(payload)})

    async def delete_post(self, connection: ConnectionContext, secret: SecretMaterial, remote: RemotePost, *, force: bool) -> ProviderCall[RemotePost]:
        del force
        path = f"{GHOST_POSTS_PATH}{quote(remote.remote_id, safe='')}/"
        status, headers, _body = await self._transport(connection).request("DELETE", path, headers=self._headers(connection, secret))
        deleted = RemotePost(remote.remote_id, remote.remote_url, "deleted", remote.etag, remote.updated_at, remote.snapshot, remote.remote_hash)
        return _call(deleted, "DELETE", path, status, headers, {})

    async def cancel_scheduled(self, connection: ConnectionContext, secret: SecretMaterial, remote: RemotePost) -> ProviderCall[RemotePost]:
        path = f"{GHOST_POSTS_PATH}{quote(remote.remote_id, safe='')}/"
        updated_at = remote.snapshot.get("updated_at")
        if not updated_at:
            raise ProviderFailure(
                code="GHOST_UPDATED_AT_REQUIRED",
                detail="Ghost 예약 취소에는 updated_at 충돌 토큰이 필요합니다.",
                retry_class=RetryClass.FINAL,
                method="PUT",
                endpoint_path=path,
            )
        status, headers, body = await self._transport(connection).request(
            "PUT",
            path,
            headers=self._headers(connection, secret),
            json_body={"posts": [{"status": "draft", "updated_at": updated_at}]},
        )
        return _call(
            _ghost_wrapped_remote(body, headers),
            "PUT",
            path,
            status,
            headers,
            {"status": "draft", "updated_at_present": True},
        )

    async def upload_media(self, connection: ConnectionContext, secret: SecretMaterial, media: MediaBinary) -> ProviderCall[UploadedMedia]:
        form = aiohttp.FormData()
        form.add_field("file", media.content, filename=media.filename, content_type=media.mime_type)
        form.add_field("purpose", "image")
        status, headers, body = await self._transport(connection).request("POST", GHOST_IMAGES_PATH, headers=self._headers(connection, secret), form=form)
        images = body.get("images")
        if not isinstance(images, list) or not images or not isinstance(images[0], dict):
            raise ProviderFailure(code="GHOST_IMAGE_RESPONSE_INVALID", detail="Ghost Image API 응답이 올바르지 않습니다.", retry_class=RetryClass.FINAL, status_code=status, method="POST", endpoint_path=GHOST_IMAGES_PATH)
        url = _required_url(images[0].get("url"))
        return _call(UploadedMedia(canonical_hash(url), url, media.placement_key), "POST", GHOST_IMAGES_PATH, status, headers, {"mime_type": media.mime_type, "size": len(media.content)})

    async def restore_snapshot(self, connection: ConnectionContext, secret: SecretMaterial, remote: RemotePost, snapshot: dict[str, Any]) -> ProviderCall[RemotePost]:
        path = f"{GHOST_POSTS_PATH}{quote(remote.remote_id, safe='')}/"
        updated_at = remote.snapshot.get("updated_at")
        if not updated_at:
            raise ProviderFailure(
                code="GHOST_UPDATED_AT_REQUIRED",
                detail="Ghost rollback에는 현재 updated_at 충돌 토큰이 필요합니다.",
                retry_class=RetryClass.FINAL,
                method="PUT",
                endpoint_path=path,
            )
        payload = {
            key: snapshot[key]
            for key in (
                "title",
                "slug",
                "html",
                "status",
                "visibility",
                "feature_image",
                "feature_image_alt",
                "feature_image_caption",
                "canonical_url",
                "tags",
                "authors",
                "published_at",
            )
            if key in snapshot
        }
        if isinstance(payload.get("tags"), list):
            payload["tags"] = [
                ({"id": str(item["id"])} if item.get("id") else {"name": str(item["name"])})
                for item in payload["tags"]
                if isinstance(item, dict) and (item.get("id") or item.get("name"))
            ]
        if isinstance(payload.get("authors"), list):
            payload["authors"] = [
                {"id": str(item["id"])}
                for item in payload["authors"]
                if isinstance(item, dict) and item.get("id")
            ]
        payload["updated_at"] = updated_at
        status, headers, body = await self._transport(connection).request(
            "PUT",
            path,
            headers=self._headers(connection, secret),
            query={"source": "html"},
            json_body={"posts": [payload]},
        )
        return _call(
            _ghost_wrapped_remote(body, headers),
            "PUT",
            path,
            status,
            headers,
            {"rollback_fields": sorted(payload)},
        )


class BloggerOfficialAdapter:
    provider = PublishingProvider.BLOGGER
    official_contract = "google-blogger-v3"

    def _transport(self) -> SafeOfficialTransport:
        return SafeOfficialTransport(BLOGGER_API_BASE)

    def _headers(self, secret: SecretMaterial, etag: str | None = None) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {secret.require('access_token')}", "Accept": "application/json"}
        if etag:
            headers["If-Match"] = etag
        return headers

    def _blog(self, connection: ConnectionContext) -> str:
        if not connection.remote_site_id:
            raise ProviderFailure(code="BLOGGER_BLOG_ID_REQUIRED", detail="Blogger blog ID가 없습니다.", retry_class=RetryClass.FINAL)
        return quote(connection.remote_site_id, safe="")

    async def diagnose(self, connection: ConnectionContext, secret: SecretMaterial) -> ProviderCall[ProviderDiagnostic]:
        path = f"/blogger/v3/blogs/{self._blog(connection)}"
        status, headers, body = await self._transport().request("GET", path, headers=self._headers(secret))
        value = ProviderDiagnostic(
            checks=[{"key": "oauth_scope", "ok": True, "scope": BLOGGER_SCOPE}, {"key": "api", "ok": True}, {"key": "blog", "ok": bool(body.get("id"))}, {"key": "timezone", "ok": bool(connection.site_timezone), "source": "workspace_configuration", "configured": connection.site_timezone}],
            capabilities=["draft", "publish", "future", "revert", "update", "delete", "labels", "etag"],
            site_settings={key: body.get(key) for key in ("id", "name", "url", "locale")},
        )
        return _call(value, "GET", path, status, headers, {})

    async def refresh(self, connection: ConnectionContext, secret: SecretMaterial) -> ProviderCall[ProviderDiagnostic]:
        return await self.diagnose(connection, secret)

    async def sync_settings(self, connection: ConnectionContext, secret: SecretMaterial) -> ProviderCall[ProviderDiagnostic]:
        return await self.diagnose(connection, secret)

    async def find_by_marker(self, connection: ConnectionContext, secret: SecretMaterial, marker: str) -> ProviderCall[RemotePost | None]:
        path = f"/blogger/v3/blogs/{self._blog(connection)}/posts/search"
        status, headers, body = await self._transport().request(
            "GET",
            path,
            headers=self._headers(secret),
            query={"q": marker, "fetchBodies": "true", "view": "ADMIN"},
        )
        items = body.get("items")
        expected_marker = f"<!-- blogops:{marker} -->"
        exact = next(
            (
                item
                for item in (items if isinstance(items, list) else [])
                if isinstance(item, dict)
                and expected_marker in str(item.get("content", ""))
            ),
            None,
        )
        value = _blogger_remote(exact, headers) if exact is not None else None
        return _call(value, "GET", path, status, headers, {"marker": marker})

    async def create_post(self, connection: ConnectionContext, secret: SecretMaterial, document: PublishDocument) -> ProviderCall[RemotePost]:
        path = f"/blogger/v3/blogs/{self._blog(connection)}/posts"
        payload = {"kind": "blogger#post", "title": document.title, "content": _with_marker(document.html, document.idempotency_marker), "labels": document.options.get("tags", [])}
        status, headers, body = await self._transport().request(
            "POST",
            path,
            headers=self._headers(secret),
            query={"isDraft": "true"},
            json_body=payload,
        )
        draft = _blogger_remote(body, headers)
        if document.visibility is PublishVisibility.DRAFT:
            return _call(draft, "POST", path, status, headers, {"fields": sorted(payload), "marker": document.idempotency_marker, "is_draft": True})
        return await self._publish_draft(
            connection,
            secret,
            draft,
            publish_date=document.scheduled_at_utc,
            prior_path=path,
        )

    async def get_post(self, connection: ConnectionContext, secret: SecretMaterial, remote_id: str) -> ProviderCall[RemotePost]:
        path = f"/blogger/v3/blogs/{self._blog(connection)}/posts/{quote(remote_id, safe='')}"
        status, headers, body = await self._transport().request("GET", path, headers=self._headers(secret), query={"view": "ADMIN"})
        return _call(_blogger_remote(body, headers), "GET", path, status, headers, {})

    async def update_post(self, connection: ConnectionContext, secret: SecretMaterial, remote: RemotePost, document: PublishDocument) -> ProviderCall[RemotePost]:
        path = f"/blogger/v3/blogs/{self._blog(connection)}/posts/{quote(remote.remote_id, safe='')}"
        payload = {"title": document.title, "content": _with_marker(document.html, document.idempotency_marker), "labels": document.options.get("tags", [])}
        status, headers, body = await self._transport().request("PATCH", path, headers=self._headers(secret, remote.etag), json_body=payload)
        updated = _blogger_remote(body, headers)
        if document.visibility is PublishVisibility.DRAFT:
            if updated.state.casefold() not in {"draft", "scheduled"}:
                return await self.cancel_scheduled(connection, secret, updated)
            return _call(updated, "PATCH", path, status, headers, {"fields": sorted(payload), "if_match": bool(remote.etag)})
        if (
            document.visibility is PublishVisibility.PUBLISH
            and updated.state.casefold() in {"live", "published"}
        ):
            return _call(
                updated,
                "PATCH",
                path,
                status,
                headers,
                {"fields": sorted(payload), "if_match": bool(remote.etag)},
            )
        if (
            (
                document.visibility is PublishVisibility.SCHEDULED
                and updated.state.casefold() in {"live", "published", "scheduled"}
            )
            or (
                document.visibility is PublishVisibility.PUBLISH
                and updated.state.casefold() == "scheduled"
            )
        ):
            reverted = await self.cancel_scheduled(connection, secret, updated)
            updated = reverted.value
        return await self._publish_draft(
            connection,
            secret,
            updated,
            publish_date=document.scheduled_at_utc,
            prior_path=path,
        )

    async def delete_post(self, connection: ConnectionContext, secret: SecretMaterial, remote: RemotePost, *, force: bool) -> ProviderCall[RemotePost]:
        del force
        path = f"/blogger/v3/blogs/{self._blog(connection)}/posts/{quote(remote.remote_id, safe='')}"
        status, headers, _body = await self._transport().request("DELETE", path, headers=self._headers(secret, remote.etag))
        deleted = RemotePost(remote.remote_id, remote.remote_url, "deleted", remote.etag, remote.updated_at, remote.snapshot, remote.remote_hash)
        return _call(deleted, "DELETE", path, status, headers, {"if_match": bool(remote.etag)})

    async def cancel_scheduled(self, connection: ConnectionContext, secret: SecretMaterial, remote: RemotePost) -> ProviderCall[RemotePost]:
        path = f"/blogger/v3/blogs/{self._blog(connection)}/posts/{quote(remote.remote_id, safe='')}/revert"
        status, headers, body = await self._transport().request("POST", path, headers=self._headers(secret, remote.etag))
        return _call(_blogger_remote(body, headers), "POST", path, status, headers, {"if_match": bool(remote.etag)})

    async def upload_media(self, connection: ConnectionContext, secret: SecretMaterial, media: MediaBinary) -> ProviderCall[UploadedMedia]:
        del connection, secret, media
        raise ProviderFailure(code="BLOGGER_MEDIA_UPLOAD_UNSUPPORTED", detail="Blogger v3에는 일반 게시 이미지 업로드 endpoint가 없어 외부 공개 URL만 지원합니다.", retry_class=RetryClass.FINAL, method="POST", endpoint_path="/blogger/v3")

    async def restore_snapshot(self, connection: ConnectionContext, secret: SecretMaterial, remote: RemotePost, snapshot: dict[str, Any]) -> ProviderCall[RemotePost]:
        path = f"/blogger/v3/blogs/{self._blog(connection)}/posts/{quote(remote.remote_id, safe='')}"
        payload = {
            key: snapshot[key]
            for key in ("title", "content", "labels")
            if key in snapshot
        }
        status, headers, body = await self._transport().request(
            "PATCH",
            path,
            headers=self._headers(secret, remote.etag),
            json_body=payload,
        )
        updated = _blogger_remote(body, headers)
        desired = str(snapshot.get("status", "DRAFT")).upper()
        if desired == "DRAFT":
            if updated.state.casefold() not in {"draft"}:
                return await self.cancel_scheduled(connection, secret, updated)
            return _call(
                updated,
                "PATCH",
                path,
                status,
                headers,
                {"rollback_fields": sorted(payload), "if_match": bool(remote.etag)},
            )
        if updated.state.casefold() in {"live", "published", "scheduled"}:
            reverted = await self.cancel_scheduled(connection, secret, updated)
            updated = reverted.value
        return await self._publish_draft(
            connection,
            secret,
            updated,
            publish_date=(
                _parse_datetime(snapshot.get("published"))
                if desired == "SCHEDULED"
                else None
            ),
            prior_path=path,
        )

    async def _publish_draft(
        self,
        connection: ConnectionContext,
        secret: SecretMaterial,
        remote: RemotePost,
        *,
        publish_date: datetime | None,
        prior_path: str,
    ) -> ProviderCall[RemotePost]:
        path = f"/blogger/v3/blogs/{self._blog(connection)}/posts/{quote(remote.remote_id, safe='')}/publish"
        query = (
            {"publishDate": publish_date.isoformat().replace("+00:00", "Z")}
            if publish_date
            else None
        )
        status, headers, body = await self._transport().request(
            "POST", path, headers=self._headers(secret, remote.etag), query=query
        )
        return _call(
            _blogger_remote(body, headers),
            "POST",
            path,
            status,
            headers,
            {
                "draft_path": prior_path,
                "publish_date": publish_date.isoformat() if publish_date else None,
                "if_match": bool(remote.etag),
            },
        )


def official_provider_registry() -> ProviderRegistry:
    from blogops.domain.publishing.providers import ProviderRegistry

    return ProviderRegistry(
        [WordPressOfficialAdapter(), GhostOfficialAdapter(), BloggerOfficialAdapter()]
    )


def _call(value: Any, method: str, path: str, status: int, headers: dict[str, str], request: dict[str, Any]) -> ProviderCall[Any]:
    return ProviderCall(
        value=value,
        method=method,
        endpoint_path=path,
        status_code=status,
        provider_request_id=headers.get("x-request-id"),
        request_metadata=redact_metadata(request),
        response_metadata={"etag_present": bool(headers.get("etag"))},
    )


def _retry_after(value: str | None) -> int | None:
    if value and value.isdigit():
        return min(int(value), 86_400)
    return None


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if value is None or str(value).strip() == "":
        raise ProviderFailure(code="PUBLISH_REMOTE_ID_MISSING", detail="CMS 응답에 원격 ID가 없습니다.", retry_class=RetryClass.FINAL)
    return str(value)


def _required_url(value: Any) -> str:
    if not isinstance(value, str):
        raise ProviderFailure(code="PUBLISH_REMOTE_URL_MISSING", detail="CMS 응답에 게시 URL이 없습니다.", retry_class=RetryClass.FINAL)
    try:
        return validate_site_url(value).normalized
    except AppError as exc:
        raise ProviderFailure(code="PUBLISH_REMOTE_URL_INVALID", detail="CMS가 안전하지 않은 게시 URL을 반환했습니다.", retry_class=RetryClass.FINAL) from exc


def _parse_datetime(value: Any, *, assume_utc: bool = False) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC) if assume_utc else None
    return parsed.astimezone(UTC)


def _with_marker(html: str, marker: str) -> str:
    return f"<!-- blogops:{marker} -->\n{html}"


def _wordpress_status(visibility: PublishVisibility) -> str:
    return {
        PublishVisibility.DRAFT: "draft",
        PublishVisibility.PUBLISH: "publish",
        PublishVisibility.SCHEDULED: "future",
        PublishVisibility.PENDING_REVIEW: "pending",
        PublishVisibility.PRIVATE: "private",
    }[visibility]


def _wordpress_payload(document: PublishDocument, *, creating: bool) -> dict[str, Any]:
    options = document.options
    payload: dict[str, Any] = {
        "title": document.title,
        "content": _with_marker(
            _wordpress_block_markup(document.html), document.idempotency_marker
        ),
        "status": _wordpress_status(document.visibility),
    }
    if creating:
        payload["slug"] = document.idempotency_marker
    for source, target in (("excerpt", "excerpt"), ("remote_author_id", "author"), ("comment_status", "comment_status"), ("allowed_meta", "meta")):
        if options.get(source) is not None:
            payload[target] = options[source]
    if document.scheduled_at_utc:
        payload["date_gmt"] = document.scheduled_at_utc.isoformat().replace("+00:00", "Z")
    if options.get("featured_media_remote_id"):
        payload["featured_media"] = int(options["featured_media_remote_id"])
    return payload


def _wordpress_block_markup(html: str) -> str:
    return f"<!-- wp:html -->\n{html}\n<!-- /wp:html -->"


def _wordpress_remote(
    body: dict[str, Any],
    headers: dict[str, str],
    deleted: bool = False,
    *,
    allowed_meta: frozenset[str] = frozenset(),
) -> RemotePost:
    remote_id = _required_string(body, "id")
    url = _required_url(body.get("link"))
    snapshot = {key: body.get(key) for key in ("id", "date_gmt", "modified_gmt", "slug", "status", "link", "title", "content", "excerpt", "author", "featured_media", "comment_status", "categories", "tags")}
    raw_meta = body.get("meta") if isinstance(body.get("meta"), dict) else {}
    snapshot["meta"] = {
        key: value for key, value in raw_meta.items() if key in allowed_meta
    }
    return RemotePost(remote_id, url, "deleted" if deleted else str(body.get("status", "draft")), headers.get("etag"), _parse_datetime(body.get("modified_gmt"), assume_utc=True), snapshot, canonical_hash(snapshot))


def _wordpress_meta_allowlist(connection: ConnectionContext) -> frozenset[str]:
    values = connection.safe_config.get("rest_meta_allowlist", [])
    return frozenset(str(item) for item in values) if isinstance(values, list) else frozenset()


def _ghost_payload(document: PublishDocument, *, creating: bool) -> dict[str, Any]:
    options = document.options
    status = {PublishVisibility.DRAFT: "draft", PublishVisibility.PUBLISH: "published", PublishVisibility.SCHEDULED: "scheduled", PublishVisibility.PENDING_REVIEW: "draft", PublishVisibility.PRIVATE: "published"}[document.visibility]
    payload: dict[str, Any] = {
        "title": document.title,
        "html": _with_marker(document.html, document.idempotency_marker),
        "status": status,
        "tags": [{"name": str(name)} for name in options.get("tags", [])],
        "visibility": options.get("member_visibility") or "public",
    }
    if creating:
        payload["slug"] = document.idempotency_marker
    if document.scheduled_at_utc:
        payload["published_at"] = document.scheduled_at_utc.isoformat().replace("+00:00", "Z")
    if options.get("remote_author_id"):
        payload["authors"] = [{"id": str(options["remote_author_id"])}]
    if options.get("featured_media_url"):
        payload["feature_image"] = options["featured_media_url"]
        payload["feature_image_alt"] = options.get("featured_media_alt")
        payload["feature_image_caption"] = options.get("featured_media_caption")
    if options.get("canonical_url"):
        payload["canonical_url"] = options["canonical_url"]
    return payload


def _ghost_write_query(
    document: PublishDocument, connection: ConnectionContext
) -> dict[str, str]:
    query = {"source": "html"}
    newsletter = document.options.get("newsletter_id")
    if newsletter and document.options.get("send_newsletter") and document.visibility in {
        PublishVisibility.PUBLISH,
        PublishVisibility.SCHEDULED,
    }:
        synced = connection.site_settings.get("newsletters")
        matched = next(
            (
                item
                for item in (synced if isinstance(synced, list) else [])
                if isinstance(item, dict) and str(item.get("id")) == str(newsletter)
            ),
            None,
        )
        if matched is None or not matched.get("slug"):
            raise ProviderFailure(
                code="GHOST_NEWSLETTER_MAPPING_MISSING",
                detail="동기화된 Ghost Newsletter Slug 매핑이 없습니다.",
                retry_class=RetryClass.FINAL,
            )
        query["newsletter"] = str(matched["slug"])
    return query


def _wordpress_integer_ids(values: list[Any], field: str) -> list[int]:
    try:
        return [int(item) for item in values]
    except (TypeError, ValueError) as exc:
        raise ProviderFailure(
            code="WORDPRESS_TAXONOMY_ID_INVALID",
            detail=f"WordPress {field} 값은 원격 정수 ID여야 합니다.",
            retry_class=RetryClass.FINAL,
        ) from exc


def _ghost_wrapped_remote(body: dict[str, Any], headers: dict[str, str]) -> RemotePost:
    posts = body.get("posts")
    if not isinstance(posts, list) or not posts or not isinstance(posts[0], dict):
        raise ProviderFailure(code="GHOST_POST_RESPONSE_INVALID", detail="Ghost posts 응답이 올바르지 않습니다.", retry_class=RetryClass.FINAL)
    return _ghost_remote(posts[0], headers)


def _ghost_remote(body: dict[str, Any], headers: dict[str, str]) -> RemotePost:
    remote_id = _required_string(body, "id")
    url = _required_url(body.get("url"))
    snapshot = {key: body.get(key) for key in ("id", "uuid", "title", "slug", "html", "lexical", "status", "visibility", "feature_image", "feature_image_alt", "feature_image_caption", "canonical_url", "tags", "authors", "newsletter", "published_at", "updated_at", "url")}
    return RemotePost(remote_id, url, str(body.get("status", "draft")), headers.get("etag"), _parse_datetime(body.get("updated_at")), snapshot, canonical_hash(snapshot))


def _blogger_remote(body: dict[str, Any], headers: dict[str, str]) -> RemotePost:
    remote_id = _required_string(body, "id")
    url = _required_url(body.get("url"))
    snapshot = {key: body.get(key) for key in ("id", "published", "updated", "url", "title", "content", "labels", "status")}
    etag = str(body.get("etag")) if body.get("etag") else headers.get("etag")
    return RemotePost(remote_id, url, str(body.get("status", "DRAFT")).lower(), etag, _parse_datetime(body.get("updated")), snapshot, canonical_hash(snapshot))


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()
