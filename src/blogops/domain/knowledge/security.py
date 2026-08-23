"""Network-source validation; fetchers must re-check resolved addresses on every redirect."""

import ipaddress
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from blogops.core.errors import AppError

_BLOCKED_HOSTS = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "metadata.google.internal",
        "metadata.google.internal.",
        "instance-data",
    }
)
_BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".home", ".lan")


def _is_public_address(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


@dataclass(frozen=True, slots=True)
class ValidatedURL:
    normalized: str
    hostname: str
    port: int


def validate_source_url(value: str) -> ValidatedURL:
    if len(value) > 2_048:
        raise AppError("SOURCE_URL_TOO_LONG", "URL이 허용 길이를 초과했습니다.", 422)
    parsed = urlsplit(value.strip())
    if parsed.scheme.lower() not in {"http", "https"}:
        raise AppError("SOURCE_URL_SCHEME_BLOCKED", "HTTP 또는 HTTPS URL만 허용됩니다.", 422)
    if parsed.username or parsed.password:
        raise AppError("SOURCE_URL_CREDENTIAL_BLOCKED", "인증정보가 포함된 URL은 허용되지 않습니다.", 422)
    if not parsed.hostname:
        raise AppError("SOURCE_URL_INVALID", "호스트가 없는 URL입니다.", 422)
    try:
        hostname = parsed.hostname.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise AppError("SOURCE_URL_INVALID", "호스트 이름이 올바르지 않습니다.", 422) from exc
    if hostname in _BLOCKED_HOSTS or hostname.endswith(_BLOCKED_SUFFIXES):
        raise AppError("SOURCE_URL_PRIVATE_HOST", "내부 네트워크 URL은 허용되지 않습니다.", 422)
    try:
        if not _is_public_address(hostname):
            raise AppError("SOURCE_URL_PRIVATE_HOST", "내부 네트워크 URL은 허용되지 않습니다.", 422)
    except ValueError:
        pass
    try:
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    except ValueError as exc:
        raise AppError("SOURCE_URL_INVALID_PORT", "URL 포트가 올바르지 않습니다.", 422) from exc
    expected_port = 443 if parsed.scheme.lower() == "https" else 80
    if port != expected_port:
        raise AppError("SOURCE_URL_PORT_BLOCKED", "허용되지 않은 URL 포트입니다.", 422)
    netloc = hostname
    normalized = urlunsplit((parsed.scheme.lower(), netloc, parsed.path or "/", parsed.query, ""))
    return ValidatedURL(normalized=normalized, hostname=hostname, port=port)


def validate_resolved_addresses(addresses: list[str]) -> None:
    if not addresses or any(not _is_public_address(value) for value in addresses):
        raise AppError(
            "SOURCE_URL_DNS_REBINDING_BLOCKED",
            "URL이 내부 또는 확인할 수 없는 주소로 해석되었습니다.",
            422,
        )
