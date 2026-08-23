"""Provider-neutral boundaries for OAuth/OIDC/SAML and SCIM integrations.

Adapters implement protocol-specific network behavior outside this domain. The identity domain
persists only normalized claims, mappings and opaque secret-manager references.
"""

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from blogops.domain.identity.enums import FederationProtocol


@dataclass(frozen=True, slots=True)
class FederatedAuthorizationRequest:
    authorization_url: str
    state: str
    nonce: str | None = None


@dataclass(frozen=True, slots=True)
class FederatedIdentityClaims:
    issuer: str
    subject: str
    email: str | None
    email_verified: bool
    display_name: str | None
    groups: tuple[str, ...]
    attributes: dict[str, Any]


class FederationAdapter(Protocol):
    protocol: FederationProtocol

    async def begin_authorization(
        self,
        *,
        connection_id: UUID,
        redirect_uri: str,
        login_hint: str | None,
    ) -> FederatedAuthorizationRequest: ...

    async def consume_response(
        self,
        *,
        connection_id: UUID,
        response_parameters: dict[str, str],
        expected_state: str,
        expected_nonce: str | None,
    ) -> FederatedIdentityClaims: ...


@dataclass(frozen=True, slots=True)
class SCIMUserPayload:
    external_id: str
    user_name: str
    display_name: str
    active: bool
    groups: tuple[str, ...]
    attributes: dict[str, Any]


@dataclass(frozen=True, slots=True)
class SCIMGroupPayload:
    external_id: str
    display_name: str
    member_external_ids: tuple[str, ...]
    attributes: dict[str, Any]


class SecretReferenceResolver(Protocol):
    """Resolves external-provider credentials without storing them in identity tables."""

    async def resolve(self, reference: str) -> str: ...
