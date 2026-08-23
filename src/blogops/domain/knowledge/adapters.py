"""Fail-closed adapter contracts for external ingestion capabilities."""

import asyncio
import hashlib
import math
import re
import socket
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import aiohttp
from aiohttp.abc import AbstractResolver

from blogops.core.config import get_settings
from blogops.core.errors import AppError
from blogops.domain.knowledge.security import validate_resolved_addresses, validate_source_url


@dataclass(frozen=True, slots=True)
class FetchResponse:
    body: bytes
    final_url: str
    content_type: str
    etag: str | None
    last_modified: str | None


class SafeFetcher(Protocol):
    """Implementations must pin validated public DNS answers for each redirect hop."""

    async def fetch(self, url: str, *, max_bytes: int) -> FetchResponse: ...


class MalwareStatus(StrEnum):
    CLEAN = "CLEAN"
    INFECTED = "INFECTED"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class MalwareResult:
    status: MalwareStatus
    signature: str | None = None


class MalwareScanner(Protocol):
    async def scan(self, content: bytes) -> MalwareResult: ...


class OcrProvider(Protocol):
    async def extract(self, content: bytes, *, content_type: str) -> str: ...


class EmbeddingProvider(Protocol):
    model: str
    version: str

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class HashingEmbeddingProvider:
    """Deterministic, local baseline embedding that can be reindexed by a model provider."""

    model = "blogops-hashing-1536"
    version = "1"
    dimensions = 1_536
    _tokens = re.compile(r"[\w가-힣]+", re.UNICODE)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        tokens = [token.casefold() for token in self._tokens.findall(text)]
        features = tokens + [
            f"{left}\0{right}" for left, right in zip(tokens, tokens[1:], strict=False)
        ]
        for feature in features:
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=16).digest()
            index = int.from_bytes(digest[:8], "big") % self.dimensions
            vector[index] += 1.0 if digest[8] & 1 else -1.0
        norm = math.sqrt(sum(value * value for value in vector))
        return [value / norm for value in vector] if norm else vector


class UnavailableFetcher:
    async def fetch(self, url: str, *, max_bytes: int) -> FetchResponse:
        raise AppError(
            "FETCH_PROVIDER_UNAVAILABLE",
            "웹 수집 공급자가 구성되지 않았습니다.",
            503,
        )


class _PinnedResolver(AbstractResolver):
    """Resolve a single hostname to addresses validated immediately before connection."""

    def __init__(self, hostname: str, addresses: tuple[str, ...]) -> None:
        self.hostname = hostname
        self.addresses = addresses

    async def resolve(
        self, host: str, port: int = 0, family: int = socket.AF_INET
    ) -> list[dict[str, object]]:
        if host.casefold().rstrip(".") != self.hostname:
            raise OSError("resolver hostname mismatch")
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


class AiohttpSafeFetcher:
    """HTTP fetcher that validates and pins public DNS answers for every redirect hop."""

    user_agent = "BlogOpsKnowledgeBot/1.0"

    def __init__(self, *, timeout_seconds: float, max_redirects: int) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_redirects = max_redirects

    async def _resolve(self, hostname: str, port: int) -> tuple[str, ...]:
        loop = asyncio.get_running_loop()
        try:
            results = await loop.getaddrinfo(
                hostname,
                port,
                family=socket.AF_UNSPEC,
                type=socket.SOCK_STREAM,
                proto=socket.IPPROTO_TCP,
            )
        except OSError as exc:
            raise AppError("SOURCE_URL_DNS_FAILED", "URL 호스트를 확인할 수 없습니다.", 422) from exc
        addresses = tuple(sorted({item[4][0] for item in results}))
        validate_resolved_addresses(list(addresses))
        return addresses

    async def _request(
        self, url: str, *, max_bytes: int, allow_not_found: bool = False
    ) -> tuple[int, dict[str, str], bytes]:
        validated = validate_source_url(url)
        addresses = await self._resolve(validated.hostname, validated.port)
        connector = aiohttp.TCPConnector(
            resolver=_PinnedResolver(validated.hostname, addresses),
            use_dns_cache=False,
            limit=1,
        )
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        try:
            async with aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                trust_env=False,
                headers={"User-Agent": self.user_agent, "Accept": "text/*,application/json,application/xml,application/pdf"},
            ) as client:
                async with client.get(validated.normalized, allow_redirects=False) as response:
                    if response.status == 404 and allow_not_found:
                        return response.status, dict(response.headers), b""
                    if response.status in {408, 425, 429} or response.status >= 500:
                        raise AppError(
                            "SOURCE_FETCH_RETRYABLE",
                            "원격 소스가 일시적으로 응답하지 않습니다.",
                            503,
                        )
                    if response.status >= 400:
                        raise AppError(
                            "SOURCE_FETCH_REJECTED",
                            f"원격 소스가 요청을 거부했습니다({response.status}).",
                            422,
                        )
                    declared = response.content_length
                    if declared is not None and declared > max_bytes:
                        raise AppError("SOURCE_TOO_LARGE", "원격 소스가 허용 크기를 초과했습니다.", 422)
                    content = bytearray()
                    async for chunk in response.content.iter_chunked(64 * 1024):
                        content.extend(chunk)
                        if len(content) > max_bytes:
                            raise AppError(
                                "SOURCE_TOO_LARGE", "원격 소스가 허용 크기를 초과했습니다.", 422
                            )
                    return response.status, dict(response.headers), bytes(content)
        except (TimeoutError, aiohttp.ClientError) as exc:
            raise AppError(
                "SOURCE_FETCH_RETRYABLE", "원격 소스 연결에 실패했습니다.", 503
            ) from exc

    async def _assert_robots_allowed(self, url: str) -> None:
        parsed = urlsplit(url)
        robots_url = f"{parsed.scheme}://{parsed.hostname}/robots.txt"
        status, _headers, body = await self._request(
            robots_url, max_bytes=512 * 1024, allow_not_found=True
        )
        if status == 404:
            return
        if status in {301, 302, 303, 307, 308}:
            raise AppError(
                "SOURCE_ROBOTS_UNVERIFIED",
                "robots 정책의 리디렉션을 안전하게 확인할 수 없습니다.",
                422,
            )
        robots = RobotFileParser()
        robots.set_url(robots_url)
        robots.parse(body.decode("utf-8", errors="replace").splitlines())
        if not robots.can_fetch(self.user_agent, url):
            raise AppError(
                "SOURCE_ROBOTS_BLOCKED", "이 URL은 robots 정책에 따라 수집할 수 없습니다.", 422
            )

    async def fetch(self, url: str, *, max_bytes: int) -> FetchResponse:
        current = validate_source_url(url).normalized
        visited: set[str] = set()
        for _hop in range(self.max_redirects + 1):
            if current in visited:
                raise AppError("SOURCE_REDIRECT_LOOP", "URL 리디렉션이 반복됩니다.", 422)
            visited.add(current)
            await self._assert_robots_allowed(current)
            status, headers, body = await self._request(current, max_bytes=max_bytes)
            if status in {301, 302, 303, 307, 308}:
                location = headers.get("Location") or headers.get("location")
                if not location:
                    raise AppError("SOURCE_REDIRECT_INVALID", "리디렉션 위치가 없습니다.", 422)
                current = validate_source_url(urljoin(current, location)).normalized
                continue
            return FetchResponse(
                body=body,
                final_url=current,
                content_type=(headers.get("Content-Type") or headers.get("content-type") or "application/octet-stream").split(";", 1)[0].strip().lower(),
                etag=headers.get("ETag") or headers.get("etag"),
                last_modified=headers.get("Last-Modified") or headers.get("last-modified"),
            )
        raise AppError("SOURCE_REDIRECT_LIMIT", "URL 리디렉션 횟수를 초과했습니다.", 422)


def get_safe_fetcher() -> SafeFetcher:
    settings = get_settings()
    return AiohttpSafeFetcher(
        timeout_seconds=settings.knowledge_fetch_timeout_seconds,
        max_redirects=settings.knowledge_fetch_max_redirects,
    )


class UnavailableOcr:
    async def extract(self, content: bytes, *, content_type: str) -> str:
        raise AppError("OCR_PROVIDER_UNAVAILABLE", "OCR 공급자가 구성되지 않았습니다.", 503)


class ClamAVScanner:
    """Minimal clamd INSTREAM client with bounded payload and timeout."""

    def __init__(self, host: str, port: int = 3310, timeout_seconds: float = 10.0) -> None:
        self.host = host
        self.port = port
        self.timeout_seconds = timeout_seconds

    async def scan(self, content: bytes) -> MalwareResult:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), timeout=self.timeout_seconds
            )
            writer.write(b"zINSTREAM\0")
            for offset in range(0, len(content), 64 * 1024):
                block = content[offset : offset + 64 * 1024]
                writer.write(len(block).to_bytes(4, "big"))
                writer.write(block)
            writer.write((0).to_bytes(4, "big"))
            await writer.drain()
            raw = await asyncio.wait_for(reader.readuntil(b"\0"), timeout=self.timeout_seconds)
            writer.close()
            await writer.wait_closed()
        except (OSError, TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            return MalwareResult(MalwareStatus.UNAVAILABLE)
        result = raw.rstrip(b"\0").decode("utf-8", errors="replace")
        if result.endswith(" OK"):
            return MalwareResult(MalwareStatus.CLEAN)
        if result.endswith(" FOUND"):
            signature = result.rsplit(":", 1)[-1].removesuffix(" FOUND").strip()
            return MalwareResult(MalwareStatus.INFECTED, signature=signature)
        return MalwareResult(MalwareStatus.UNAVAILABLE)
