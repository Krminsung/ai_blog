"""RLS-scoped SQLAlchemy repository for keyword services."""

from datetime import UTC, datetime
from typing import Sequence
from uuid import UUID

from sqlalchemy import Select, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.core.errors import AppError
from blogops.domain.keywords.models import (
    Keyword,
    KeywordCluster,
    KeywordClusterMember,
    KeywordMetricSnapshot,
    KeywordProviderCall,
    KeywordProviderConnection,
    KeywordResearchItem,
    KeywordResearchJob,
    KeywordScoreProfile,
    KeywordScoreSnapshot,
)


class KeywordRepository:
    def __init__(self, session: AsyncSession, workspace_id: UUID) -> None:
        self.session = session
        self.workspace_id = workspace_id

    async def connection(
        self, connection_id: UUID, *, lock: bool = False
    ) -> KeywordProviderConnection:
        statement: Select[tuple[KeywordProviderConnection]] = select(
            KeywordProviderConnection
        ).where(
            KeywordProviderConnection.workspace_id == self.workspace_id,
            KeywordProviderConnection.id == connection_id,
        )
        if lock:
            statement = statement.with_for_update()
        connection = await self.session.scalar(statement)
        if connection is None:
            raise AppError(
                "KEYWORD_PROVIDER_CONNECTION_NOT_FOUND", "공급자 connection을 찾을 수 없습니다.", 404
            )
        return connection

    async def connections(self) -> list[KeywordProviderConnection]:
        return list(
            await self.session.scalars(
                select(KeywordProviderConnection)
                .where(KeywordProviderConnection.workspace_id == self.workspace_id)
                .order_by(
                    KeywordProviderConnection.provider,
                    KeywordProviderConnection.name,
                    KeywordProviderConnection.id,
                )
            )
        )

    async def idempotent_job(
        self, input_kind: str, requested_by: UUID, idempotency_key: str
    ) -> KeywordResearchJob | None:
        return await self.session.scalar(
            select(KeywordResearchJob).where(
                KeywordResearchJob.workspace_id == self.workspace_id,
                KeywordResearchJob.input_kind == input_kind,
                KeywordResearchJob.requested_by == requested_by,
                KeywordResearchJob.idempotency_key == idempotency_key,
            )
        )

    async def job(self, job_id: UUID, *, lock: bool = False) -> KeywordResearchJob:
        statement: Select[tuple[KeywordResearchJob]] = select(KeywordResearchJob).where(
            KeywordResearchJob.workspace_id == self.workspace_id,
            KeywordResearchJob.id == job_id,
        )
        if lock:
            statement = statement.with_for_update()
        job = await self.session.scalar(statement)
        if job is None:
            raise AppError("KEYWORD_JOB_NOT_FOUND", "키워드 작업을 찾을 수 없습니다.", 404)
        return job

    async def job_items(
        self, job_id: UUID, *, states: Sequence[str] | None = None, lock: bool = False
    ) -> list[KeywordResearchItem]:
        statement: Select[tuple[KeywordResearchItem]] = select(KeywordResearchItem).where(
            KeywordResearchItem.workspace_id == self.workspace_id,
            KeywordResearchItem.job_id == job_id,
        )
        if states:
            statement = statement.where(KeywordResearchItem.state.in_(states))
        statement = statement.order_by(KeywordResearchItem.row_no, KeywordResearchItem.id)
        if lock:
            statement = statement.with_for_update(skip_locked=True)
        return list(await self.session.scalars(statement))

    async def keyword(self, keyword_id: UUID) -> Keyword:
        keyword = await self.session.scalar(
            select(Keyword).where(
                Keyword.workspace_id == self.workspace_id,
                Keyword.id == keyword_id,
            )
        )
        if keyword is None:
            raise AppError("KEYWORD_NOT_FOUND", "키워드를 찾을 수 없습니다.", 404)
        return keyword

    async def keyword_by_normalized(
        self, normalized: str, language: str, region: str
    ) -> Keyword | None:
        return await self.session.scalar(
            select(Keyword).where(
                Keyword.workspace_id == self.workspace_id,
                Keyword.normalized == normalized,
                Keyword.language == language,
                Keyword.region == region,
            )
        )

    async def keywords_by_ids(self, keyword_ids: Sequence[UUID]) -> list[Keyword]:
        if not keyword_ids:
            return []
        items = list(
            await self.session.scalars(
                select(Keyword)
                .where(
                    Keyword.workspace_id == self.workspace_id,
                    Keyword.id.in_(keyword_ids),
                )
                .order_by(Keyword.normalized, Keyword.id)
            )
        )
        if len(items) != len(set(keyword_ids)):
            raise AppError(
                "KEYWORD_SET_INVALID",
                "요청한 키워드 일부가 없거나 현재 워크스페이스에 속하지 않습니다.",
                404,
            )
        return items

    async def list_keywords(
        self,
        *,
        limit: int,
        cursor: UUID | None,
        intent: str | None = None,
        region: str | None = None,
        excluded: bool | None = None,
        query: str | None = None,
    ) -> list[Keyword]:
        statement: Select[tuple[Keyword]] = select(Keyword).where(
            Keyword.workspace_id == self.workspace_id
        )
        if cursor:
            statement = statement.where(Keyword.id > cursor)
        if intent:
            statement = statement.where(Keyword.intent == intent)
        if region:
            statement = statement.where(Keyword.region == region)
        if excluded is not None:
            statement = statement.where(Keyword.is_excluded.is_(excluded))
        if query:
            escaped = query.replace("%", "\\%").replace("_", "\\_")
            statement = statement.where(Keyword.normalized.ilike(f"%{escaped}%", escape="\\"))
        return list(
            await self.session.scalars(statement.order_by(Keyword.id).limit(limit))
        )

    async def metric_history(
        self,
        keyword_id: UUID,
        *,
        provider: str | None = None,
        limit: int = 100,
    ) -> list[KeywordMetricSnapshot]:
        statement: Select[tuple[KeywordMetricSnapshot]] = select(
            KeywordMetricSnapshot
        ).where(
            KeywordMetricSnapshot.workspace_id == self.workspace_id,
            KeywordMetricSnapshot.keyword_id == keyword_id,
        )
        if provider:
            statement = statement.where(KeywordMetricSnapshot.provider == provider)
        return list(
            await self.session.scalars(
                statement.order_by(
                    KeywordMetricSnapshot.measured_at.desc(), KeywordMetricSnapshot.id.desc()
                ).limit(limit)
            )
        )

    async def cached_metric(
        self,
        *,
        keyword_id: UUID,
        provider_connection_id: UUID,
        provider: str,
        dimensions_hash: str,
        allow_stale: bool,
    ) -> KeywordMetricSnapshot | None:
        now = datetime.now(UTC)
        statement = select(KeywordMetricSnapshot).where(
            KeywordMetricSnapshot.workspace_id == self.workspace_id,
            KeywordMetricSnapshot.keyword_id == keyword_id,
            KeywordMetricSnapshot.provider_connection_id == provider_connection_id,
            KeywordMetricSnapshot.provider == provider,
            KeywordMetricSnapshot.dimensions_hash == dimensions_hash,
        )
        if not allow_stale:
            statement = statement.where(KeywordMetricSnapshot.expires_at > now)
        return await self.session.scalar(
            statement.order_by(
                KeywordMetricSnapshot.measured_at.desc(), KeywordMetricSnapshot.id.desc()
            ).limit(1)
        )

    async def latest_scores(
        self, keyword_ids: Sequence[UUID]
    ) -> dict[UUID, KeywordScoreSnapshot]:
        if not keyword_ids:
            return {}
        rows = list(
            await self.session.scalars(
                select(KeywordScoreSnapshot)
                .where(
                    KeywordScoreSnapshot.workspace_id == self.workspace_id,
                    KeywordScoreSnapshot.keyword_id.in_(keyword_ids),
                )
                .order_by(
                    KeywordScoreSnapshot.keyword_id,
                    KeywordScoreSnapshot.scored_at.desc(),
                    KeywordScoreSnapshot.id.desc(),
                )
            )
        )
        result: dict[UUID, KeywordScoreSnapshot] = {}
        for row in rows:
            result.setdefault(row.keyword_id, row)
        return result

    async def active_score_profile(self) -> KeywordScoreProfile | None:
        return await self.session.scalar(
            select(KeywordScoreProfile)
            .where(
                KeywordScoreProfile.workspace_id == self.workspace_id,
                KeywordScoreProfile.is_active.is_(True),
            )
            .order_by(KeywordScoreProfile.created_at.desc(), KeywordScoreProfile.id.desc())
            .limit(1)
        )

    async def score_profile(self, profile_id: UUID) -> KeywordScoreProfile:
        profile = await self.session.scalar(
            select(KeywordScoreProfile).where(
                KeywordScoreProfile.workspace_id == self.workspace_id,
                KeywordScoreProfile.id == profile_id,
            )
        )
        if profile is None:
            raise AppError("KEYWORD_SCORE_PROFILE_NOT_FOUND", "점수 프로필을 찾을 수 없습니다.", 404)
        return profile

    async def next_score_profile_version(self, name: str) -> int:
        latest = await self.session.scalar(
            select(func.coalesce(func.max(KeywordScoreProfile.version), 0)).where(
                KeywordScoreProfile.workspace_id == self.workspace_id,
                KeywordScoreProfile.name == name,
            )
        )
        return int(latest or 0) + 1

    async def clusters_for_keywords(
        self, keyword_ids: Sequence[UUID]
    ) -> list[tuple[KeywordCluster, KeywordClusterMember]]:
        rows = await self.session.execute(
            select(KeywordCluster, KeywordClusterMember)
            .join(
                KeywordClusterMember,
                and_(
                    KeywordClusterMember.workspace_id == KeywordCluster.workspace_id,
                    KeywordClusterMember.cluster_id == KeywordCluster.id,
                ),
            )
            .where(
                KeywordCluster.workspace_id == self.workspace_id,
                KeywordClusterMember.keyword_id.in_(keyword_ids),
            )
        )
        return list(rows.tuples())

    async def provider_call_summary(
        self, connection_id: UUID
    ) -> tuple[int, int, int, str | None]:
        call_count, cache_hits, errors = (
            await self.session.execute(
                select(
                    func.count(KeywordProviderCall.id),
                    func.count(KeywordProviderCall.id).filter(
                        KeywordProviderCall.cache_hit.is_(True)
                    ),
                    func.count(KeywordProviderCall.id).filter(
                        KeywordProviderCall.error_code.is_not(None)
                    ),
                ).where(
                    KeywordProviderCall.workspace_id == self.workspace_id,
                    KeywordProviderCall.connection_id == connection_id,
                )
            )
        ).one()
        last_error = await self.session.scalar(
            select(KeywordProviderCall.error_code)
            .where(
                KeywordProviderCall.workspace_id == self.workspace_id,
                KeywordProviderCall.connection_id == connection_id,
                KeywordProviderCall.error_code.is_not(None),
            )
            .order_by(KeywordProviderCall.started_at.desc())
            .limit(1)
        )
        return int(call_count), int(cache_hits), int(errors), last_error
