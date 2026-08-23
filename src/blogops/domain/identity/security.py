"""Cryptographic building blocks for the identity domain.

Only hashes or authenticated ciphertext leave this module for persistence. Raw verification,
reset, invitation, refresh and recovery tokens are returned exactly once to their caller.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import hmac
import json
import secrets
import struct
from typing import Final
from urllib.parse import quote
from uuid import UUID


ACCESS_TOKEN_VERSION: Final = "at1"
ACCESS_TOKEN_ISSUER: Final = "blogops"


def utc_now() -> datetime:
    return datetime.now(UTC)


def normalize_email(value: str) -> str:
    """Normalize the comparison/storage form without guessing provider-specific aliases."""

    email = value.strip().casefold()
    if len(email) > 320 or email.count("@") != 1:
        raise ValueError("유효한 이메일 주소가 필요합니다.")
    local, domain = email.rsplit("@", 1)
    if not local or not domain or "." not in domain or domain.startswith(".") or domain.endswith("."):
        raise ValueError("유효한 이메일 주소가 필요합니다.")
    try:
        normalized_domain = domain.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("유효한 이메일 주소가 필요합니다.") from exc
    return f"{local}@{normalized_domain}"


def validate_password(password: str, *, minimum_length: int = 12) -> None:
    if len(password) < minimum_length:
        raise ValueError(f"비밀번호는 {minimum_length}자 이상이어야 합니다.")
    if len(password.encode("utf-8")) > 1024:
        raise ValueError("비밀번호가 너무 깁니다.")
    categories = (
        any(character.islower() for character in password),
        any(character.isupper() for character in password),
        any(character.isdigit() for character in password),
        any(not character.isalnum() for character in password),
    )
    if sum(categories) < 3:
        raise ValueError("비밀번호는 영문 대·소문자, 숫자, 기호 중 세 종류 이상을 포함해야 합니다.")


class PasswordManager:
    """Argon2id password hashing with explicit resource parameters."""

    def __init__(self) -> None:
        try:
            from argon2 import PasswordHasher
            from argon2.low_level import Type
        except ImportError as exc:  # pragma: no cover - deployment dependency guard
            raise RuntimeError("argon2-cffi is required for password authentication") from exc
        self._hasher = PasswordHasher(
            time_cost=3,
            memory_cost=65_536,
            parallelism=4,
            hash_len=32,
            salt_len=16,
            type=Type.ID,
        )

    def hash(self, password: str, *, minimum_length: int = 12) -> str:
        validate_password(password, minimum_length=minimum_length)
        return str(self._hasher.hash(password))

    def verify(self, password_hash: str, password: str) -> bool:
        try:
            return bool(self._hasher.verify(password_hash, password))
        except Exception as exc:  # argon2 exceptions share no stdlib base beyond Exception
            if exc.__class__.__module__.startswith("argon2"):
                return False
            raise

    def needs_rehash(self, password_hash: str) -> bool:
        return bool(self._hasher.check_needs_rehash(password_hash))


class TokenManager:
    """Keyed hashing and single-display opaque token issuance."""

    def __init__(self, secret_key: str) -> None:
        if not secret_key:
            raise ValueError("secret_key must not be empty")
        self._hash_key = hmac.digest(secret_key.encode(), b"blogops-token-hash-v1", "sha256")
        self._access_key = hmac.digest(secret_key.encode(), b"blogops-access-signing-v1", "sha256")
        self._identifier_key = hmac.digest(
            secret_key.encode(), b"blogops-identifier-hash-v1", "sha256"
        )

    def issue_opaque(self, prefix: str, *, entropy_bytes: int = 32) -> tuple[str, str]:
        raw = f"{prefix}.{secrets.token_urlsafe(entropy_bytes)}"
        return raw, self.digest(raw)

    def derive_opaque(self, prefix: str, token_id: UUID) -> tuple[str, str]:
        """Create a reconstructable delivery token without persisting its raw form.

        Mail workers can derive the same value from the outbox token id and application key.
        Database disclosure alone remains insufficient to recover it.
        """

        proof = hmac.new(
            self._hash_key,
            f"derived:{prefix}:{token_id}".encode(),
            hashlib.sha256,
        ).digest()
        raw = f"{prefix}.{token_id}.{_b64encode(proof)}"
        return raw, self.digest(raw)

    def digest(self, raw: str) -> str:
        return hmac.new(self._hash_key, raw.encode(), hashlib.sha256).hexdigest()

    def identifier_digest(self, raw: str | None) -> str | None:
        if not raw:
            return None
        return hmac.new(self._identifier_key, raw.encode(), hashlib.sha256).hexdigest()

    def matches(self, raw: str, expected_digest: str) -> bool:
        return hmac.compare_digest(self.digest(raw), expected_digest)

    def issue_access_token(
        self,
        *,
        user_id: UUID,
        workspace_id: UUID,
        session_id: UUID,
        authentication_methods: list[str],
        ttl: timedelta = timedelta(minutes=15),
        now: datetime | None = None,
    ) -> tuple[str, int]:
        issued_at = now or utc_now()
        expires_at = issued_at + ttl
        claims = {
            "iss": ACCESS_TOKEN_ISSUER,
            "sub": str(user_id),
            "wid": str(workspace_id),
            "sid": str(session_id),
            "amr": authentication_methods,
            "iat": int(issued_at.timestamp()),
            "exp": int(expires_at.timestamp()),
            "jti": secrets.token_urlsafe(12),
        }
        payload = _b64encode(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
        signing_input = f"{ACCESS_TOKEN_VERSION}.{payload}"
        signature = _b64encode(hmac.digest(self._access_key, signing_input.encode(), "sha256"))
        return f"{signing_input}.{signature}", int(ttl.total_seconds())

    def decode_access_token(
        self, token: str, *, now: datetime | None = None
    ) -> AccessTokenClaims:
        try:
            version, payload, supplied_signature = token.split(".", 2)
        except ValueError as exc:
            raise InvalidAccessToken("malformed access token") from exc
        if version != ACCESS_TOKEN_VERSION:
            raise InvalidAccessToken("unsupported access token version")
        signing_input = f"{version}.{payload}"
        expected_signature = _b64encode(
            hmac.digest(self._access_key, signing_input.encode(), "sha256")
        )
        if not hmac.compare_digest(supplied_signature, expected_signature):
            raise InvalidAccessToken("invalid access token signature")
        try:
            data = json.loads(_b64decode(payload))
            claims = AccessTokenClaims(
                user_id=UUID(data["sub"]),
                workspace_id=UUID(data["wid"]),
                session_id=UUID(data["sid"]),
                authentication_methods=tuple(str(item) for item in data.get("amr", [])),
                issued_at=datetime.fromtimestamp(int(data["iat"]), tz=UTC),
                expires_at=datetime.fromtimestamp(int(data["exp"]), tz=UTC),
                token_id=str(data["jti"]),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise InvalidAccessToken("invalid access token claims") from exc
        if data.get("iss") != ACCESS_TOKEN_ISSUER:
            raise InvalidAccessToken("invalid access token issuer")
        observed_at = now or utc_now()
        if claims.expires_at <= observed_at:
            raise InvalidAccessToken("access token expired")
        if claims.issued_at > observed_at + timedelta(seconds=30):
            raise InvalidAccessToken("access token issued in the future")
        return claims


@dataclass(frozen=True, slots=True)
class AccessTokenClaims:
    user_id: UUID
    workspace_id: UUID
    session_id: UUID
    authentication_methods: tuple[str, ...]
    issued_at: datetime
    expires_at: datetime
    token_id: str


class InvalidAccessToken(ValueError):
    pass


class SecretEnvelope:
    """AES-GCM envelope for TOTP seeds; external IdP secrets remain in a secret manager."""

    def __init__(self, secret_key: str) -> None:
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        except ImportError as exc:  # pragma: no cover - deployment dependency guard
            raise RuntimeError("cryptography is required for MFA secret protection") from exc
        key = hmac.digest(secret_key.encode(), b"blogops-mfa-envelope-v1", "sha256")
        self._cipher = AESGCM(key)

    def encrypt(self, plaintext: str, *, context: str) -> str:
        nonce = secrets.token_bytes(12)
        ciphertext = self._cipher.encrypt(nonce, plaintext.encode(), context.encode())
        return _b64encode(nonce + ciphertext)

    def decrypt(self, ciphertext: str, *, context: str) -> str:
        encoded = _b64decode(ciphertext)
        if len(encoded) < 29:
            raise ValueError("invalid encrypted secret")
        plaintext = self._cipher.decrypt(encoded[:12], encoded[12:], context.encode())
        return plaintext.decode()


def generate_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp_code(secret: str, *, at: datetime | None = None, step_seconds: int = 30) -> tuple[str, int]:
    observed_at = at or utc_now()
    step = int(observed_at.timestamp()) // step_seconds
    padded_secret = secret + "=" * ((8 - len(secret) % 8) % 8)
    key = base64.b32decode(padded_secret, casefold=True)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()  # noqa: S324
    offset = digest[-1] & 0x0F
    dynamic = int.from_bytes(digest[offset : offset + 4], "big") & 0x7FFFFFFF
    return f"{dynamic % 1_000_000:06d}", step


def verify_totp(
    secret: str,
    supplied_code: str,
    *,
    at: datetime | None = None,
    allowed_drift_steps: int = 1,
    last_used_step: int | None = None,
) -> int | None:
    normalized = supplied_code.strip().replace(" ", "")
    if len(normalized) != 6 or not normalized.isdigit():
        return None
    observed_at = at or utc_now()
    for drift in range(-allowed_drift_steps, allowed_drift_steps + 1):
        candidate_time = observed_at + timedelta(seconds=drift * 30)
        candidate, step = totp_code(secret, at=candidate_time)
        if hmac.compare_digest(candidate, normalized) and (
            last_used_step is None or step > last_used_step
        ):
            return step
    return None


def provisioning_uri(*, secret: str, account_name: str, issuer: str = "BlogOps AI") -> str:
    label = quote(f"{issuer}:{account_name}", safe="")
    return (
        f"otpauth://totp/{label}?secret={quote(secret, safe='')}&issuer={quote(issuer, safe='')}"
        "&algorithm=SHA1&digits=6&period=30"
    )


def issue_recovery_codes(token_manager: TokenManager, *, count: int = 10) -> list[tuple[str, str]]:
    issued: list[tuple[str, str]] = []
    for _ in range(count):
        raw_hex = secrets.token_hex(6).upper()
        raw = f"{raw_hex[:4]}-{raw_hex[4:8]}-{raw_hex[8:]}"
        issued.append((raw, token_manager.digest(_normalize_recovery_code(raw))))
    return issued


def recovery_code_digest(token_manager: TokenManager, raw: str) -> str:
    return token_manager.digest(_normalize_recovery_code(raw))


def invitation_workspace_id(raw_token: str) -> UUID:
    try:
        marker, workspace_id, _remainder = raw_token.split(".", 2)
        if marker != "inv":
            raise ValueError
        return UUID(workspace_id)
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid invitation token") from exc


def _normalize_recovery_code(raw: str) -> str:
    return raw.replace("-", "").replace(" ", "").upper()


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * ((4 - len(value) % 4) % 4))
