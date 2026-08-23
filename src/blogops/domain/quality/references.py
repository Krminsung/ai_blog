"""Stage-4 content and active-membership validation boundaries."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.core.errors import AppError
from blogops.domain.identity.enums import MembershipStatus
from blogops.domain.identity.models import Membership


@dataclass(frozen=True, slots=True)
class ContentVersionSnapshot:
    content_id: UUID
    content_version_id: UUID
    content_hash: str
    current_version_id: UUID | None
    title: str
    channel: str
    language: str
    content_state: str
    version_number: int

    @property
    def is_current(self) -> bool:
        return self.current_version_id == self.content_version_id


class ContentVersionResolver(Protocol):
    async def resolve(
        self, workspace_id: UUID, content_id: UUID, content_version_id: UUID
    ) -> ContentVersionSnapshot: ...

    async def current(
        self, workspace_id: UUID, content_id: UUID
    ) -> ContentVersionSnapshot: ...


class ActiveMembershipResolver(Protocol):
    async def require_active(self, workspace_id: UUID, user_ids: set[UUID]) -> None: ...


class SQLAlchemyContentVersionResolver:
    """Reads only the stable Stage-4 public table contract."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def resolve(
        self, workspace_id: UUID, content_id: UUID, content_version_id: UUID
    ) -> ContentVersionSnapshot:
        result = await self._session.execute(
            text(
                """
                SELECT c.id AS content_id, c.current_version_id, c.channel, c.language,
                       c.state AS content_state, v.id AS content_version_id,
                       v.version_number, v.title, v.content_hash
                FROM contents AS c
                JOIN content_versions AS v
                  ON v.workspace_id = c.workspace_id AND v.content_id = c.id
                WHERE c.workspace_id = :workspace_id
                  AND c.id = :content_id
                  AND v.id = :content_version_id
                  AND c.deleted_at IS NULL
                """
            ),
            {
                "workspace_id": str(workspace_id),
                "content_id": str(content_id),
                "content_version_id": str(content_version_id),
            },
        )
        row = result.mappings().one_or_none()
        if row is None:
            raise _content_reference_error(content_id, content_version_id)
        return _snapshot(row)

    async def current(
        self, workspace_id: UUID, content_id: UUID
    ) -> ContentVersionSnapshot:
        result = await self._session.execute(
            text(
                """
                SELECT c.id AS content_id, c.current_version_id, c.channel, c.language,
                       c.state AS content_state, v.id AS content_version_id,
                       v.version_number, v.title, v.content_hash
                FROM contents AS c
                JOIN content_versions AS v
                  ON v.workspace_id = c.workspace_id AND v.id = c.current_version_id
                WHERE c.workspace_id = :workspace_id
                  AND c.id = :content_id
                  AND c.deleted_at IS NULL
                """
            ),
            {"workspace_id": str(workspace_id), "content_id": str(content_id)},
        )
        row = result.mappings().one_or_none()
        if row is None:
            raise _content_reference_error(content_id, None)
        return _snapshot(row)


class SQLAlchemyActiveMembershipResolver:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def require_active(self, workspace_id: UUID, user_ids: set[UUID]) -> None:
        if not user_ids:
            return
        active = set(
            await self._session.scalars(
                select(Membership.user_id).where(
                    Membership.workspace_id == workspace_id,
                    Membership.user_id.in_(user_ids),
                    Membership.status == MembershipStatus.ACTIVE.value,
                )
            )
        )
        missing = sorted(user_ids.difference(active), key=str)
        if missing:
            raise AppError(
                code="QUALITY_MEMBER_INACTIVE",
                message="승인자와 멘션 대상은 활성 워크스페이스 멤버여야 합니다.",
                status_code=422,
                fields=[
                    {"path": "user_ids", "reason": str(user_id)} for user_id in missing
                ],
            )


def _snapshot(row: object) -> ContentVersionSnapshot:
    mapping = row
    return ContentVersionSnapshot(
        content_id=UUID(str(mapping["content_id"])),  # type: ignore[index]
        content_version_id=UUID(str(mapping["content_version_id"])),  # type: ignore[index]
        content_hash=str(mapping["content_hash"]),  # type: ignore[index]
        current_version_id=(
            UUID(str(mapping["current_version_id"]))  # type: ignore[index]
            if mapping["current_version_id"] is not None  # type: ignore[index]
            else None
        ),
        title=str(mapping["title"]),  # type: ignore[index]
        channel=str(mapping["channel"]),  # type: ignore[index]
        language=str(mapping["language"]),  # type: ignore[index]
        content_state=str(mapping["content_state"]),  # type: ignore[index]
        version_number=int(mapping["version_number"]),  # type: ignore[index]
    )


def _content_reference_error(
    content_id: UUID, content_version_id: UUID | None
) -> AppError:
    return AppError(
        code="CONTENT_VERSION_NOT_FOUND",
        message="같은 워크스페이스의 콘텐츠 버전을 찾을 수 없습니다.",
        status_code=404,
        fields=[
            {"path": "content_id", "reason": str(content_id)},
            {
                "path": "content_version_id",
                "reason": str(content_version_id) if content_version_id else "current",
            },
        ],
    )
