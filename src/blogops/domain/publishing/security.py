"""Outbound CMS URL validation and DNS rebinding protections."""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
from urllib.parse import urlsplit, urlunsplit

from blogops.core.errors import AppError


_BLOCKED_HOSTS = frozenset(
    {"localhost", "metadata.google.internal", "instance-data", "metadata.azure.internal"}
)
_BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".home", ".lan")
_SECRET_REF_PREFIXES = ("aws-sm://", "gcp-sm://", "azure-kv://", "vault://", "kms://")


def is_public_ip(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return address.is_global and not address.is_multicast


@dataclass(frozen=True, slots=True)
class SafeSiteURL:
    normalized: str
    hostname: str
    port: int


def validate_site_url(value: str, *, require_https: bool = True) -> SafeSiteURL:
    if len(value) > 2_048:
        raise AppError("PUBLISH_SITE_URL_TOO_LONG", "사이트 URL이 허용 길이를 초과했습니다.", 422)
    parsed = urlsplit(value.strip())
    schemes = {"https"} if require_https else {"http", "https"}
    if parsed.scheme.lower() not in schemes:
        raise AppError("PUBLISH_SITE_URL_SCHEME", "게시 사이트는 HTTPS URL이어야 합니다.", 422)
    if parsed.username or parsed.password or not parsed.hostname:
        raise AppError("PUBLISH_SITE_URL_INVALID", "인증정보가 없는 유효한 사이트 URL이 필요합니다.", 422)
    if parsed.query or parsed.fragment:
        raise AppError(
            "PUBLISH_SITE_URL_INVALID",
            "사이트 URL에는 쿼리나 프래그먼트를 사용할 수 없습니다.",
            422,
        )
    try:
        hostname = parsed.hostname.rstrip(".").encode("idna").decode("ascii").lower()
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    except (UnicodeError, ValueError) as exc:
        raise AppError("PUBLISH_SITE_URL_INVALID", "사이트 URL 형식이 올바르지 않습니다.", 422) from exc
    if hostname in _BLOCKED_HOSTS or hostname.endswith(_BLOCKED_SUFFIXES):
        raise AppError("PUBLISH_SITE_PRIVATE", "내부 네트워크 사이트는 연결할 수 없습니다.", 422)
    try:
        if not is_public_ip(hostname):
            raise AppError("PUBLISH_SITE_PRIVATE", "내부 네트워크 사이트는 연결할 수 없습니다.", 422)
    except ValueError:
        pass
    expected_port = 443 if parsed.scheme.lower() == "https" else 80
    if port != expected_port:
        raise AppError("PUBLISH_SITE_PORT", "표준 HTTPS 포트만 허용됩니다.", 422)
    path = parsed.path.rstrip("/")
    netloc = f"[{hostname}]" if ":" in hostname else hostname
    normalized = urlunsplit((parsed.scheme.lower(), netloc, path, "", ""))
    return SafeSiteURL(normalized=normalized, hostname=hostname, port=port)


def validate_resolved_addresses(addresses: tuple[str, ...]) -> None:
    if not addresses or any(not is_public_ip(value) for value in addresses):
        raise AppError(
            "PUBLISH_DNS_REBINDING_BLOCKED",
            "게시 사이트가 내부 또는 확인할 수 없는 주소로 해석되었습니다.",
            422,
        )


def validate_secret_ref(value: str) -> str:
    normalized = value.strip()
    if not normalized.startswith(_SECRET_REF_PREFIXES) or len(normalized) > 512:
        raise AppError(
            "PUBLISH_SECRET_REF_INVALID",
            "KMS 또는 Secret Manager의 불투명 참조만 허용됩니다.",
            422,
        )
    if any(character.isspace() for character in normalized):
        raise AppError("PUBLISH_SECRET_REF_INVALID", "Secret 참조 형식이 올바르지 않습니다.", 422)
    return normalized
