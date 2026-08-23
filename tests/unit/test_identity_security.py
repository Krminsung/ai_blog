from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from cryptography.exceptions import InvalidTag

from blogops.domain.identity.security import (
    InvalidAccessToken,
    PasswordManager,
    SecretEnvelope,
    TokenManager,
    generate_totp_secret,
    invitation_workspace_id,
    totp_code,
    verify_totp,
)


def test_password_hash_is_argon2id_and_verifies() -> None:
    manager = PasswordManager()
    password_hash = manager.hash("Correct-Horse-Battery-7!")

    assert password_hash.startswith("$argon2id$")
    assert manager.verify(password_hash, "Correct-Horse-Battery-7!")
    assert not manager.verify(password_hash, "incorrect-password")


def test_opaque_tokens_are_only_comparable_by_keyed_digest() -> None:
    manager = TokenManager("test-secret-key-with-enough-entropy")

    raw_token, digest = manager.issue_opaque("rt")

    assert raw_token not in digest
    assert manager.matches(raw_token, digest)
    assert not manager.matches(f"{raw_token}x", digest)


def test_access_token_rejects_tampering_and_expiry() -> None:
    manager = TokenManager("test-secret-key-with-enough-entropy")
    now = datetime(2026, 8, 23, tzinfo=UTC)
    user_id = uuid4()
    workspace_id = uuid4()
    session_id = uuid4()
    access_token, _expires_in = manager.issue_access_token(
        user_id=user_id,
        workspace_id=workspace_id,
        session_id=session_id,
        authentication_methods=["password"],
        ttl=timedelta(minutes=5),
        now=now,
    )

    claims = manager.decode_access_token(access_token, now=now + timedelta(minutes=1))
    assert claims.user_id == user_id
    assert claims.workspace_id == workspace_id
    assert claims.session_id == session_id

    with pytest.raises(InvalidAccessToken):
        manager.decode_access_token(f"{access_token[:-1]}x", now=now)
    with pytest.raises(InvalidAccessToken):
        manager.decode_access_token(access_token, now=now + timedelta(minutes=6))


def test_totp_rejects_replay_of_the_same_time_step() -> None:
    secret = generate_totp_secret()
    now = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
    code, step = totp_code(secret, at=now)

    assert verify_totp(secret, code, at=now) == step
    assert verify_totp(secret, code, at=now, last_used_step=step) is None


def test_mfa_secret_envelope_binds_ciphertext_to_factor_context() -> None:
    envelope = SecretEnvelope("test-secret-key-with-enough-entropy")
    ciphertext = envelope.encrypt("TOTP-SEED", context="mfa:factor:user")

    assert "TOTP-SEED" not in ciphertext
    assert envelope.decrypt(ciphertext, context="mfa:factor:user") == "TOTP-SEED"
    with pytest.raises(InvalidTag):
        envelope.decrypt(ciphertext, context="mfa:different:user")


def test_invitation_token_carries_only_public_workspace_selector() -> None:
    manager = TokenManager("test-secret-key-with-enough-entropy")
    workspace_id = uuid4()
    invitation_id = uuid4()
    token, _digest = manager.derive_opaque(f"inv.{workspace_id}", invitation_id)

    assert invitation_workspace_id(token) == workspace_id
