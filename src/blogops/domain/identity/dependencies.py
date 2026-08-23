"""FastAPI dependency wiring kept inside the identity module."""

from dataclasses import dataclass
from functools import lru_cache

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.core.config import get_settings
from blogops.core.context import Principal
from blogops.core.errors import AppError
from blogops.db.session import get_session
from blogops.domain.identity.security import PasswordManager, SecretEnvelope, TokenManager
from blogops.domain.identity.services import (
    EnterpriseIdentityService,
    IdentityService,
    WorkspaceService,
)


@dataclass(frozen=True, slots=True)
class IdentitySecurity:
    passwords: PasswordManager
    tokens: TokenManager
    envelope: SecretEnvelope


@lru_cache(maxsize=1)
def get_identity_security() -> IdentitySecurity:
    secret_key = get_settings().secret_key.get_secret_value()
    return IdentitySecurity(
        passwords=PasswordManager(),
        tokens=TokenManager(secret_key),
        envelope=SecretEnvelope(secret_key),
    )


def get_identity_service(
    session: AsyncSession = Depends(get_session),
    security: IdentitySecurity = Depends(get_identity_security),
) -> IdentityService:
    return IdentityService(
        session,
        passwords=security.passwords,
        tokens=security.tokens,
        envelope=security.envelope,
    )


def get_workspace_service(
    session: AsyncSession = Depends(get_session),
    security: IdentitySecurity = Depends(get_identity_security),
) -> WorkspaceService:
    return WorkspaceService(session, tokens=security.tokens)


def get_enterprise_identity_service(
    session: AsyncSession = Depends(get_session),
    security: IdentitySecurity = Depends(get_identity_security),
) -> EnterpriseIdentityService:
    workspaces = WorkspaceService(session, tokens=security.tokens)
    return EnterpriseIdentityService(session, tokens=security.tokens, workspaces=workspaces)


async def get_current_principal(
    request: Request,
    identity: IdentityService = Depends(get_identity_service),
) -> Principal:
    authorization = request.headers.get("Authorization", "")
    scheme, separator, credentials = authorization.partition(" ")
    if not separator or scheme.casefold() != "bearer" or not credentials:
        raise AppError(
            code="AUTHENTICATION_REQUIRED",
            message="Bearer 인증이 필요합니다.",
            status_code=401,
        )
    principal = await identity.resolve_principal(credentials.strip())
    request.state.principal = principal
    return principal


async def get_reconsent_principal(
    request: Request,
    identity: IdentityService = Depends(get_identity_service),
) -> Principal:
    """Authenticate a session while allowing only the terms re-consent route to proceed."""

    authorization = request.headers.get("Authorization", "")
    scheme, separator, credentials = authorization.partition(" ")
    if not separator or scheme.casefold() != "bearer" or not credentials:
        raise AppError(
            code="AUTHENTICATION_REQUIRED",
            message="Bearer 인증이 필요합니다.",
            status_code=401,
        )
    principal = await identity.resolve_principal(credentials.strip(), enforce_terms=False)
    request.state.principal = principal
    return principal


async def get_mfa_setup_principal(
    request: Request,
    identity: IdentityService = Depends(get_identity_service),
) -> Principal:
    """Allow a valid session to satisfy an MFA policy by enrolling or reauthenticating."""

    authorization = request.headers.get("Authorization", "")
    scheme, separator, credentials = authorization.partition(" ")
    if not separator or scheme.casefold() != "bearer" or not credentials:
        raise AppError(
            code="AUTHENTICATION_REQUIRED",
            message="Bearer 인증이 필요합니다.",
            status_code=401,
        )
    principal = await identity.resolve_principal(credentials.strip(), enforce_mfa=False)
    request.state.principal = principal
    return principal


def request_ip_hash(request: Request, security: IdentitySecurity) -> str | None:
    host = request.client.host if request.client is not None else None
    return security.tokens.identifier_digest(host)


def request_user_agent_hash(request: Request, security: IdentitySecurity) -> str | None:
    return security.tokens.identifier_digest(request.headers.get("User-Agent"))
