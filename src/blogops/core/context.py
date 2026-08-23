"""Request, actor and tenant context isolated with context variables."""

from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


request_id_context: ContextVar[str | None] = ContextVar("request_id", default=None)
principal_context: ContextVar["Principal | None"] = ContextVar("principal", default=None)


class PrincipalKind(StrEnum):
    """Server-verified kind of authenticated principal."""

    UNKNOWN = "UNKNOWN"
    USER_SESSION = "USER_SESSION"
    API_KEY = "API_KEY"


@dataclass(frozen=True, slots=True)
class Principal:
    """Authenticated identity after session/API-key validation.

    Headers never directly construct this object. The authentication layer added in stage 2
    resolves membership and permissions from trusted server-side state first.
    """

    subject_id: UUID
    workspace_id: UUID
    session_id: UUID | None
    permissions: frozenset[str]
    authentication_method: str
    kind: PrincipalKind = PrincipalKind.UNKNOWN
    mfa_verified_at: datetime | None = None

    @property
    def has_platform_assurance(self) -> bool:
        """Only a user session with server-side MFA evidence may act platform-wide."""

        return (
            self.kind == PrincipalKind.USER_SESSION
            and self.session_id is not None
            and self.mfa_verified_at is not None
        )


def bind_request_id(request_id: str) -> Token[str | None]:
    return request_id_context.set(request_id)


def reset_request_id(token: Token[str | None]) -> None:
    request_id_context.reset(token)


def bind_principal(principal: Principal) -> Token[Principal | None]:
    return principal_context.set(principal)


def reset_principal(token: Token[Principal | None]) -> None:
    principal_context.reset(token)
