"""Policy and external execution ports for platform operations."""

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from blogops.core.errors import AppError


class AdminOperationPolicy(Protocol):
    async def maximum_support_minutes(self, *, scopes: frozenset[str]) -> int: ...

    async def required_approvals(self, *, command_kind: str) -> int: ...


@dataclass(frozen=True, slots=True)
class CommandExecutionResult:
    result_ref: str
    safe_summary: dict[str, Any]


class AdminCommandExecutor(Protocol):
    async def execute(
        self, *, command_id: UUID, kind: str, secure_parameters_ref: str | None
    ) -> CommandExecutionResult: ...


class NotificationSender(Protocol):
    async def send(self, *, delivery_id: UUID) -> str: ...


class FailClosedAdminAdapters:
    async def maximum_support_minutes(self, *, scopes: frozenset[str]) -> int:
        del scopes
        raise AppError("ADMIN_ACCESS_POLICY_UNAVAILABLE", "운영자 접근 정책이 구성되지 않았습니다.", 503)

    async def required_approvals(self, *, command_kind: str) -> int:
        del command_kind
        raise AppError("ADMIN_APPROVAL_POLICY_UNAVAILABLE", "운영 명령 승인 정책이 구성되지 않았습니다.", 503)

    async def execute(
        self, *, command_id: UUID, kind: str, secure_parameters_ref: str | None
    ) -> CommandExecutionResult:
        del command_id, kind, secure_parameters_ref
        raise AppError("ADMIN_COMMAND_EXECUTOR_UNAVAILABLE", "운영 명령 실행기가 구성되지 않았습니다.", 503)

    async def send(self, *, delivery_id: UUID) -> str:
        del delivery_id
        raise AppError("NOTIFICATION_SENDER_UNAVAILABLE", "알림 전송기가 구성되지 않았습니다.", 503)
