"""Tenant-filtered publishing persistence helpers."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from blogops.core.errors import AppError
from blogops.domain.publishing.models import (
    NaverPublishPackage,
    PublicationPolicy,
    PublishedPost,
    PublishingConnection,
    PublishingConnectionJob,
    PublishJob,
    RemotePostSnapshot,
)


class PublishingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def connection(
        self, workspace_id: UUID, connection_id: UUID, *, for_update: bool = False
    ) -> PublishingConnection:
        query = select(PublishingConnection).where(
            PublishingConnection.workspace_id == workspace_id,
            PublishingConnection.id == connection_id,
        )
        if for_update:
            query = query.with_for_update()
        value = await self.session.scalar(query)
        if value is None:
            raise _not_found("PUBLISHING_CONNECTION", "게시 연결")
        return value

    async def connection_job(
        self, workspace_id: UUID, job_id: UUID, *, for_update: bool = False
    ) -> PublishingConnectionJob:
        query = select(PublishingConnectionJob).where(
            PublishingConnectionJob.workspace_id == workspace_id,
            PublishingConnectionJob.id == job_id,
        )
        if for_update:
            query = query.with_for_update()
        value = await self.session.scalar(query)
        if value is None:
            raise _not_found("PUBLISHING_CONNECTION_JOB", "게시 연결 작업")
        return value

    async def policy(self, workspace_id: UUID, policy_id: UUID) -> PublicationPolicy:
        value = await self.session.scalar(
            select(PublicationPolicy).where(
                PublicationPolicy.workspace_id == workspace_id,
                PublicationPolicy.id == policy_id,
            )
        )
        if value is None:
            raise _not_found("PUBLISHING_POLICY", "게시 정책")
        return value

    async def latest_policy(self, workspace_id: UUID) -> PublicationPolicy:
        value = await self.session.scalar(
            select(PublicationPolicy)
            .where(PublicationPolicy.workspace_id == workspace_id)
            .order_by(PublicationPolicy.version.desc())
            .limit(1)
        )
        if value is None:
            raise AppError(
                "PUBLISHING_POLICY_REQUIRED",
                "버전이 지정된 워크스페이스 게시 정책이 필요합니다.",
                409,
            )
        return value

    async def publish_job(
        self, workspace_id: UUID, job_id: UUID, *, for_update: bool = False
    ) -> PublishJob:
        query = select(PublishJob).where(
            PublishJob.workspace_id == workspace_id,
            PublishJob.id == job_id,
        )
        if for_update:
            query = query.with_for_update()
        value = await self.session.scalar(query)
        if value is None:
            raise _not_found("PUBLISH_JOB", "게시 작업")
        return value

    async def published_post(
        self, workspace_id: UUID, post_id: UUID, *, for_update: bool = False
    ) -> PublishedPost:
        query = select(PublishedPost).where(
            PublishedPost.workspace_id == workspace_id,
            PublishedPost.id == post_id,
        )
        if for_update:
            query = query.with_for_update()
        value = await self.session.scalar(query)
        if value is None:
            raise _not_found("PUBLISHED_POST", "게시물 연결")
        return value

    async def naver_package(
        self, workspace_id: UUID, package_id: UUID
    ) -> NaverPublishPackage:
        value = await self.session.scalar(
            select(NaverPublishPackage).where(
                NaverPublishPackage.workspace_id == workspace_id,
                NaverPublishPackage.id == package_id,
            )
        )
        if value is None:
            raise _not_found("NAVER_PACKAGE", "네이버 수동 게시 패키지")
        return value

    async def remote_snapshot(
        self, workspace_id: UUID, snapshot_id: UUID
    ) -> RemotePostSnapshot:
        value = await self.session.scalar(
            select(RemotePostSnapshot).where(
                RemotePostSnapshot.workspace_id == workspace_id,
                RemotePostSnapshot.id == snapshot_id,
            )
        )
        if value is None:
            raise _not_found("REMOTE_SNAPSHOT", "원격 게시물 스냅샷")
        return value

    async def flush(self, resource: str) -> None:
        try:
            await self.session.flush()
        except StaleDataError as exc:
            raise AppError(
                "OPTIMISTIC_LOCK_CONFLICT",
                "다른 요청이 먼저 리소스를 변경했습니다. 최신 값을 다시 조회해 주세요.",
                409,
                fields=[{"path": "resource", "reason": resource}],
            ) from exc
        except IntegrityError as exc:
            raise AppError(
                "PUBLISHING_CONFLICT",
                "같은 게시 요청, 원격 게시물 또는 버전이 이미 존재합니다.",
                409,
                fields=[{"path": "resource", "reason": resource}],
            ) from exc


def _not_found(code: str, label: str) -> AppError:
    return AppError(f"{code}_NOT_FOUND", f"{label}을(를) 찾을 수 없습니다.", 404)
