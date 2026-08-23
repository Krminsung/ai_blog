"""Tenant-filtered persistence repository for the planning application service."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from blogops.core.errors import AppError
from blogops.domain.planning.models import (
    CalendarEntry,
    Campaign,
    ContentBrief,
    ContentIdea,
    MonthlyPlanProposal,
    PlanningBoardColumn,
    PlanningComment,
    TopicNode,
)


class PlanningRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def campaign(
        self, workspace_id: UUID, campaign_id: UUID, *, for_update: bool = False
    ) -> Campaign:
        query = select(Campaign).where(
            Campaign.workspace_id == workspace_id, Campaign.id == campaign_id
        )
        if for_update:
            query = query.with_for_update()
        value = await self.session.scalar(query)
        if value is None:
            raise _not_found("CAMPAIGN", "캠페인")
        return value

    async def topic_node(
        self, workspace_id: UUID, node_id: UUID, *, for_update: bool = False
    ) -> TopicNode:
        query = select(TopicNode).where(
            TopicNode.workspace_id == workspace_id, TopicNode.id == node_id
        )
        if for_update:
            query = query.with_for_update()
        value = await self.session.scalar(query)
        if value is None:
            raise _not_found("TOPIC_NODE", "토픽 노드")
        return value

    async def idea(
        self, workspace_id: UUID, idea_id: UUID, *, for_update: bool = False
    ) -> ContentIdea:
        query = select(ContentIdea).where(
            ContentIdea.workspace_id == workspace_id, ContentIdea.id == idea_id
        )
        if for_update:
            query = query.with_for_update()
        value = await self.session.scalar(query)
        if value is None:
            raise _not_found("CONTENT_IDEA", "콘텐츠 아이디어")
        return value

    async def brief(
        self, workspace_id: UUID, brief_id: UUID, *, for_update: bool = False
    ) -> ContentBrief:
        query = select(ContentBrief).where(
            ContentBrief.workspace_id == workspace_id, ContentBrief.id == brief_id
        )
        if for_update:
            query = query.with_for_update()
        value = await self.session.scalar(query)
        if value is None:
            raise _not_found("CONTENT_BRIEF", "콘텐츠 브리프")
        return value

    async def board_column(self, workspace_id: UUID, column_id: UUID) -> PlanningBoardColumn:
        value = await self.session.scalar(
            select(PlanningBoardColumn).where(
                PlanningBoardColumn.workspace_id == workspace_id,
                PlanningBoardColumn.id == column_id,
            )
        )
        if value is None:
            raise _not_found("BOARD_COLUMN", "보드 상태")
        return value

    async def comment(
        self, workspace_id: UUID, comment_id: UUID, *, for_update: bool = False
    ) -> PlanningComment:
        query = select(PlanningComment).where(
            PlanningComment.workspace_id == workspace_id,
            PlanningComment.id == comment_id,
        )
        if for_update:
            query = query.with_for_update()
        value = await self.session.scalar(query)
        if value is None:
            raise _not_found("PLANNING_COMMENT", "코멘트")
        return value

    async def calendar_entry(
        self, workspace_id: UUID, entry_id: UUID, *, for_update: bool = False
    ) -> CalendarEntry:
        query = select(CalendarEntry).where(
            CalendarEntry.workspace_id == workspace_id,
            CalendarEntry.id == entry_id,
        )
        if for_update:
            query = query.with_for_update()
        value = await self.session.scalar(query)
        if value is None:
            raise _not_found("CALENDAR_ENTRY", "캘린더 항목")
        return value

    async def proposal(
        self, workspace_id: UUID, proposal_id: UUID, *, for_update: bool = False
    ) -> MonthlyPlanProposal:
        query = select(MonthlyPlanProposal).where(
            MonthlyPlanProposal.workspace_id == workspace_id,
            MonthlyPlanProposal.id == proposal_id,
        )
        if for_update:
            query = query.with_for_update()
        value = await self.session.scalar(query)
        if value is None:
            raise _not_found("MONTHLY_PLAN_PROPOSAL", "월간 계획안")
        return value

    async def existing_ideas_by_keys(
        self, workspace_id: UUID, duplicate_keys: set[str]
    ) -> dict[str, ContentIdea]:
        if not duplicate_keys:
            return {}
        ideas = list(
            await self.session.scalars(
                select(ContentIdea).where(
                    ContentIdea.workspace_id == workspace_id,
                    ContentIdea.duplicate_key.in_(duplicate_keys),
                )
            )
        )
        return {item.duplicate_key: item for item in ideas}

    async def calendar_entries_for_channel(
        self,
        workspace_id: UUID,
        channel: str,
        *,
        starts_at: datetime,
        ends_at: datetime,
        excluding_id: UUID | None = None,
    ) -> list[CalendarEntry]:
        query = select(CalendarEntry).where(
            CalendarEntry.workspace_id == workspace_id,
            CalendarEntry.channel == channel,
            CalendarEntry.scheduled_at >= starts_at,
            CalendarEntry.scheduled_at < ends_at,
            CalendarEntry.status != "CANCELLED",
        )
        if excluding_id is not None:
            query = query.where(CalendarEntry.id != excluding_id)
        return list(await self.session.scalars(query.order_by(CalendarEntry.scheduled_at)))

    async def flush(self, resource: str) -> None:
        try:
            await self.session.flush()
        except StaleDataError as exc:
            raise AppError(
                code="OPTIMISTIC_LOCK_CONFLICT",
                message="다른 요청이 먼저 리소스를 변경했습니다. 최신 값을 다시 조회해 주세요.",
                status_code=409,
                fields=[{"path": "resource", "reason": resource}],
            ) from exc
        except IntegrityError as exc:
            raise AppError(
                code="PLANNING_CONFLICT",
                message="같은 식별자, 버전 또는 계획 항목이 이미 존재합니다.",
                status_code=409,
                fields=[{"path": "resource", "reason": resource}],
            ) from exc


def _not_found(code: str, label: str) -> AppError:
    return AppError(
        code=f"{code}_NOT_FOUND",
        message=f"{label}을(를) 찾을 수 없습니다.",
        status_code=404,
    )
