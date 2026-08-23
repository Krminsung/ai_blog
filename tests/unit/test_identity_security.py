from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from cryptography.exceptions import InvalidTag

from blogops.core.context import Principal, PrincipalKind
from blogops.core.errors import AppError
from blogops.db.session import (
    ensure_platform_session_assurance,
    get_job_session,
    get_platform_session,
)
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
from blogops.domain.identity.services import (
    PRIVILEGED_MFA_ROLE_KEYS,
    _new_workspace_bundle,
    _validate_required_mfa_role_keys,
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


def test_privileged_roles_cannot_be_removed_from_workspace_mfa_policy() -> None:
    _workspace, roles, policy = _new_workspace_bundle(
        creator_id=uuid4(),
        name="MFA protected workspace",
        industry=None,
        country_code="KR",
        timezone="Asia/Seoul",
        default_locale="ko-KR",
        data_region="ap-northeast-2",
    )

    assert set(PRIVILEGED_MFA_ROLE_KEYS).issubset(policy.require_mfa_role_keys)
    for role_key in PRIVILEGED_MFA_ROLE_KEYS:
        assert {
            "privacy:read",
            "privacy:manage",
            "security:read",
            "security:manage",
        }.issubset(roles[role_key].permissions)

    with pytest.raises(AppError) as weakened:
        _validate_required_mfa_role_keys(["owner"])
    assert weakened.value.code == "PRIVILEGED_MFA_REQUIRED"
    assert weakened.value.status_code == 422

    assert _validate_required_mfa_role_keys(["owner", "admin", "admin"]) == [
        "owner",
        "admin",
    ]


def test_platform_assurance_is_fail_closed_and_rejects_api_keys() -> None:
    platform_permission = frozenset({"platform:operate"})
    default_principal = Principal(
        subject_id=uuid4(),
        workspace_id=uuid4(),
        session_id=uuid4(),
        permissions=platform_permission,
        authentication_method="password+totp_or_recovery",
        mfa_verified_at=datetime.now(UTC),
    )
    with pytest.raises(AppError) as unknown_kind:
        ensure_platform_session_assurance(default_principal)
    assert unknown_kind.value.code == "PLATFORM_USER_SESSION_REQUIRED"

    api_key = Principal(
        subject_id=uuid4(),
        workspace_id=uuid4(),
        session_id=None,
        permissions=platform_permission,
        authentication_method="api_key",
        kind=PrincipalKind.API_KEY,
        mfa_verified_at=datetime.now(UTC),
    )
    with pytest.raises(AppError) as forbidden_api_key:
        ensure_platform_session_assurance(api_key)
    assert forbidden_api_key.value.code == "PLATFORM_API_KEY_FORBIDDEN"


@pytest.mark.asyncio
@pytest.mark.parametrize("session_dependency", [get_platform_session, get_job_session])
async def test_platform_sessions_require_server_verified_mfa(session_dependency) -> None:
    principal = Principal(
        subject_id=uuid4(),
        workspace_id=uuid4(),
        session_id=uuid4(),
        permissions=frozenset({"platform:approve"}),
        authentication_method="password",
        kind=PrincipalKind.USER_SESSION,
    )

    with pytest.raises(AppError) as missing_mfa:
        await anext(session_dependency(principal))
    assert missing_mfa.value.code == "PLATFORM_MFA_REQUIRED"

    assured = Principal(
        subject_id=principal.subject_id,
        workspace_id=principal.workspace_id,
        session_id=principal.session_id,
        permissions=principal.permissions,
        authentication_method="password+totp_or_recovery",
        kind=PrincipalKind.USER_SESSION,
        mfa_verified_at=datetime.now(UTC),
    )
    ensure_platform_session_assurance(assured)
