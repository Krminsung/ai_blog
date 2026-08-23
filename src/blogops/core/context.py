"""Request, actor and tenant context isolated with context variables."""

from contextvars import ContextVar, Token
from dataclasses import dataclass
from uuid import UUID


request_id_context: ContextVar[str | None] = ContextVar("request_id", default=None)
principal_context: ContextVar["Principal | None"] = ContextVar("principal", default=None)


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


def bind_request_id(request_id: str) -> Token[str | None]:
    return request_id_context.set(request_id)


def reset_request_id(token: Token[str | None]) -> None:
    request_id_context.reset(token)


def bind_principal(principal: Principal) -> Token[Principal | None]:
    return principal_context.set(principal)


def reset_principal(token: Token[Principal | None]) -> None:
    principal_context.reset(token)
