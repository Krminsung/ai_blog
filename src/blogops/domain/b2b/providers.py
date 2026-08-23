"""External policy and provisioning ports for B2B boundaries."""

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from blogops.core.errors import AppError


class PortalTokenSecrets(Protocol):
    async def pepper(self, version: str | None = None) -> tuple[str, bytes]: ...


class AgencyRelationshipAuthority(Protocol):
    async def authorize_client_relationship(
        self,
        *,
        agency_workspace_id: UUID,
        client_workspace_id: UUID,
        permissions: frozenset[str],
    ) -> str: ...


@dataclass(frozen=True, slots=True)
class ProvisioningResult:
    workspace_id: UUID
    operation_ref: str


class WorkspaceProvisioner(Protocol):
    async def provision(
        self, *, request_id: UUID, workspace_request: dict[str, Any]
    ) -> ProvisioningResult: ...


class WhiteLabelVerifier(Protocol):
    async def verify_domain(self, *, domain: str, challenge_hash: str) -> str: ...


class FailClosedB2BAdapters:
    async def pepper(self, version: str | None = None) -> tuple[str, bytes]:
        del version
        raise AppError("PORTAL_TOKEN_HASHER_UNAVAILABLE", "포털 Token 비밀이 구성되지 않았습니다.", 503)

    async def authorize_client_relationship(
        self,
        *,
        agency_workspace_id: UUID,
        client_workspace_id: UUID,
        permissions: frozenset[str],
    ) -> str:
        del agency_workspace_id, client_workspace_id, permissions
        raise AppError(
            "AGENCY_CLIENT_AUTHORITY_UNAVAILABLE",
            "고객 워크스페이스 동의와 대행사 Entitlement을 검증할 수 없습니다.",
            503,
        )

    async def provision(
        self, *, request_id: UUID, workspace_request: dict[str, Any]
    ) -> ProvisioningResult:
        del request_id, workspace_request
        raise AppError(
            "CLIENT_PROVISIONER_UNAVAILABLE",
            "고객 Workspace Provisioner가 구성되지 않았습니다.",
            503,
        )

    async def verify_domain(self, *, domain: str, challenge_hash: str) -> str:
        del domain, challenge_hash
        raise AppError("WHITE_LABEL_VERIFIER_UNAVAILABLE", "도메인 검증기가 구성되지 않았습니다.", 503)
