"""Tenant-scoped content planning, approval and calendar API."""

from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.core.context import Principal
from blogops.core.permissions import Permission, require_permissions
from blogops.db.session import get_tenant_session
from blogops.domain.planning.enums import BriefEvent, BriefStatus, IdeaStatus, ProposalStatus
from blogops.domain.planning.providers import DeterministicMonthlyPlanGenerator
from blogops.domain.planning.references import (
    SQLAlchemyActiveMembershipResolver,
    SQLAlchemyPlanningReferenceResolver,
)
from blogops.domain.planning.schemas import (
    AssignmentRead,
    AssignmentUpsert,
    BoardColumnCreate,
    BoardColumnRead,
    BriefBoardMove,
    BriefCreate,
    BriefDecisionRequest,
    BriefRead,
    BriefTransitionRequest,
    BriefVersionCreate,
    BriefVersionRead,
    CalendarEntryCreate,
    CalendarEntryMove,
    CalendarEntryRead,
    CalendarExportQuery,
    CampaignCreate,
    CampaignRead,
    CampaignUpdate,
    CommentCreate,
    CommentRead,
    CommentResolve,
    ContentIdeaRead,
    GenerationBriefInput,
    IdeaBatchCreate,
    IdeaBatchRead,
    MonthlyPlanApprovalRead,
    MonthlyPlanDecision,
    MonthlyPlanProposalCreate,
    MonthlyPlanProposalRead,
    MonthlyPlanProposalRevise,
    RecurrenceCreate,
    RecurrenceRead,
    SpendDecisionRead,
    SpendRecordCreate,
    TopicIntentUpdate,
    TopicMergeRequest,
    TopicNodeCreate,
    TopicNodeMove,
    TopicNodeRead,
    TopicSplitRequest,
)
from blogops.domain.planning.service import PlanningService


router = APIRouter(tags=["planning"])
TenantSession = Annotated[AsyncSession, Depends(get_tenant_session)]
PlanningReader = Annotated[
    Principal, Depends(require_permissions(Permission.PLANNING_READ))
]
PlanningWriter = Annotated[
    Principal, Depends(require_permissions(Permission.PLANNING_WRITE))
]
PlanningApprover = Annotated[
    Principal, Depends(require_permissions(Permission.PLANNING_APPROVE))
]
PlanningExporter = Annotated[
    Principal, Depends(require_permissions(Permission.PLANNING_EXPORT))
]


def planning_service(session: TenantSession) -> PlanningService:
    return PlanningService(
        session,
        references=SQLAlchemyPlanningReferenceResolver(session),
        memberships=SQLAlchemyActiveMembershipResolver(session),
        monthly_generator=DeterministicMonthlyPlanGenerator(),
    )


Service = Annotated[PlanningService, Depends(planning_service)]


@router.post(
    "/campaigns", response_model=CampaignRead, status_code=status.HTTP_201_CREATED
)
async def create_campaign(
    data: CampaignCreate, principal: PlanningWriter, service: Service
) -> CampaignRead:
    return CampaignRead.model_validate(await service.create_campaign(principal, data))


@router.get("/campaigns", response_model=list[CampaignRead])
async def list_campaigns(
    principal: PlanningReader,
    service: Service,
    include_archived: bool = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[CampaignRead]:
    items = await service.list_campaigns(
        principal,
        include_archived=include_archived,
        limit=limit,
        offset=offset,
    )
    return [CampaignRead.model_validate(item) for item in items]


@router.get("/campaigns/{campaign_id}", response_model=CampaignRead)
async def get_campaign(
    campaign_id: UUID, principal: PlanningReader, service: Service
) -> CampaignRead:
    return CampaignRead.model_validate(await service.get_campaign(principal, campaign_id))


@router.patch("/campaigns/{campaign_id}", response_model=CampaignRead)
async def update_campaign(
    campaign_id: UUID,
    data: CampaignUpdate,
    principal: PlanningWriter,
    service: Service,
) -> CampaignRead:
    return CampaignRead.model_validate(
        await service.update_campaign(principal, campaign_id, data)
    )


@router.post("/campaigns/{campaign_id}/spend", response_model=SpendDecisionRead)
async def record_campaign_spend(
    campaign_id: UUID,
    data: SpendRecordCreate,
    principal: PlanningWriter,
    service: Service,
) -> SpendDecisionRead:
    return await service.record_campaign_spend(principal, campaign_id, data)


@router.post(
    "/topics", response_model=TopicNodeRead, status_code=status.HTTP_201_CREATED
)
async def create_topic(
    data: TopicNodeCreate, principal: PlanningWriter, service: Service
) -> TopicNodeRead:
    return TopicNodeRead.model_validate(await service.create_topic_node(principal, data))


@router.get("/topics", response_model=list[TopicNodeRead])
async def list_topics(
    principal: PlanningReader,
    service: Service,
    campaign_id: UUID | None = None,
    include_archived: bool = False,
) -> list[TopicNodeRead]:
    items = await service.list_topic_nodes(
        principal,
        campaign_id=campaign_id,
        include_archived=include_archived,
    )
    return [TopicNodeRead.model_validate(item) for item in items]


@router.post("/topics/{node_id}/move", response_model=TopicNodeRead)
async def move_topic(
    node_id: UUID,
    data: TopicNodeMove,
    principal: PlanningWriter,
    service: Service,
) -> TopicNodeRead:
    return TopicNodeRead.model_validate(
        await service.move_topic_node(principal, node_id, data)
    )


@router.post("/topics/{node_id}/intent", response_model=TopicNodeRead)
async def revise_topic_intent(
    node_id: UUID,
    data: TopicIntentUpdate,
    principal: PlanningWriter,
    service: Service,
) -> TopicNodeRead:
    return TopicNodeRead.model_validate(
        await service.revise_topic_intent(principal, node_id, data)
    )


@router.post("/topics/{node_id}/merge", response_model=TopicNodeRead)
async def merge_topics(
    node_id: UUID,
    data: TopicMergeRequest,
    principal: PlanningWriter,
    service: Service,
) -> TopicNodeRead:
    return TopicNodeRead.model_validate(
        await service.merge_topic_nodes(principal, node_id, data)
    )


@router.post(
    "/topics/{node_id}/split",
    response_model=TopicNodeRead,
    status_code=status.HTTP_201_CREATED,
)
async def split_topic(
    node_id: UUID,
    data: TopicSplitRequest,
    principal: PlanningWriter,
    service: Service,
) -> TopicNodeRead:
    return TopicNodeRead.model_validate(
        await service.split_topic_node(principal, node_id, data)
    )


@router.post(
    "/ideas/batch", response_model=IdeaBatchRead, status_code=status.HTTP_201_CREATED
)
async def create_ideas(
    data: IdeaBatchCreate, principal: PlanningWriter, service: Service
) -> IdeaBatchRead:
    result = await service.create_ideas(principal, data)
    return IdeaBatchRead(
        created=[ContentIdeaRead.model_validate(item) for item in result.created],
        suppressed=result.suppressed,
    )


@router.get("/ideas", response_model=list[ContentIdeaRead])
async def list_ideas(
    principal: PlanningReader,
    service: Service,
    campaign_id: UUID | None = None,
    idea_status: IdeaStatus | None = Query(default=None, alias="status"),
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ContentIdeaRead]:
    items = await service.list_ideas(
        principal,
        campaign_id=campaign_id,
        status=idea_status,
        limit=limit,
        offset=offset,
    )
    return [ContentIdeaRead.model_validate(item) for item in items]


@router.get("/board/columns", response_model=list[BoardColumnRead])
async def list_board_columns(
    principal: PlanningReader, service: Service
) -> list[BoardColumnRead]:
    items = await service.list_board_columns(principal)
    return [BoardColumnRead.model_validate(item) for item in items]


@router.post(
    "/board/columns",
    response_model=BoardColumnRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_board_column(
    data: BoardColumnCreate, principal: PlanningWriter, service: Service
) -> BoardColumnRead:
    return BoardColumnRead.model_validate(
        await service.create_board_column(principal, data)
    )


@router.post(
    "/briefs", response_model=BriefRead, status_code=status.HTTP_201_CREATED
)
async def create_brief(
    data: BriefCreate, principal: PlanningWriter, service: Service
) -> BriefRead:
    return await service.create_brief(principal, data)


@router.get("/briefs", response_model=list[BriefRead])
async def list_briefs(
    principal: PlanningReader,
    service: Service,
    campaign_id: UUID | None = None,
    brief_status: BriefStatus | None = Query(default=None, alias="status"),
    board_column_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[BriefRead]:
    return await service.list_briefs(
        principal,
        campaign_id=campaign_id,
        status=brief_status,
        board_column_id=board_column_id,
        limit=limit,
        offset=offset,
    )


@router.get("/briefs/{brief_id}", response_model=BriefRead)
async def get_brief(
    brief_id: UUID, principal: PlanningReader, service: Service
) -> BriefRead:
    return await service.get_brief(principal, brief_id)


@router.get("/briefs/{brief_id}/versions", response_model=list[BriefVersionRead])
async def list_brief_versions(
    brief_id: UUID, principal: PlanningReader, service: Service
) -> list[BriefVersionRead]:
    return await service.list_brief_versions(principal, brief_id)


@router.post(
    "/briefs/{brief_id}/versions",
    response_model=BriefRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_brief_version(
    brief_id: UUID,
    data: BriefVersionCreate,
    principal: PlanningWriter,
    service: Service,
) -> BriefRead:
    return await service.create_brief_version(principal, brief_id, data)


@router.post("/briefs/{brief_id}/submit", response_model=BriefRead)
async def submit_brief(
    brief_id: UUID,
    data: BriefTransitionRequest,
    principal: PlanningWriter,
    service: Service,
) -> BriefRead:
    return await service.transition_brief(
        principal, brief_id, data, event=BriefEvent.SUBMIT
    )


@router.post("/briefs/{brief_id}/archive", response_model=BriefRead)
async def archive_brief(
    brief_id: UUID,
    data: BriefTransitionRequest,
    principal: PlanningWriter,
    service: Service,
) -> BriefRead:
    return await service.transition_brief(
        principal, brief_id, data, event=BriefEvent.ARCHIVE
    )


@router.post("/briefs/{brief_id}/decisions", response_model=BriefRead)
async def decide_brief(
    brief_id: UUID,
    data: BriefDecisionRequest,
    principal: PlanningApprover,
    service: Service,
) -> BriefRead:
    return await service.decide_brief(principal, brief_id, data)


@router.put("/briefs/{brief_id}/assignments", response_model=list[AssignmentRead])
async def replace_assignments(
    brief_id: UUID,
    data: AssignmentUpsert,
    principal: PlanningWriter,
    service: Service,
) -> list[AssignmentRead]:
    items = await service.upsert_assignments(principal, brief_id, data)
    return [AssignmentRead.model_validate(item) for item in items]


@router.get("/briefs/{brief_id}/assignments", response_model=list[AssignmentRead])
async def list_assignments(
    brief_id: UUID, principal: PlanningReader, service: Service
) -> list[AssignmentRead]:
    items = await service.list_assignments(principal, brief_id)
    return [AssignmentRead.model_validate(item) for item in items]


@router.post("/briefs/{brief_id}/board", response_model=BriefRead)
async def move_brief_on_board(
    brief_id: UUID,
    data: BriefBoardMove,
    principal: PlanningWriter,
    service: Service,
) -> BriefRead:
    await service.move_brief_on_board(principal, brief_id, data)
    return await service.get_brief(principal, brief_id)


@router.get("/briefs/{brief_id}/generation-input", response_model=GenerationBriefInput)
async def get_generation_input(
    brief_id: UUID, principal: PlanningReader, service: Service
) -> GenerationBriefInput:
    return GenerationBriefInput.model_validate(
        await service.generation_input(principal, brief_id)
    )


@router.post(
    "/comments", response_model=CommentRead, status_code=status.HTTP_201_CREATED
)
async def create_comment(
    data: CommentCreate, principal: PlanningWriter, service: Service
) -> CommentRead:
    return CommentRead.model_validate(await service.create_comment(principal, data))


@router.get("/comments", response_model=list[CommentRead])
async def list_comments(
    target_type: Annotated[str, Query(pattern="^(BRIEF|TOPIC_NODE|CALENDAR_ENTRY)$")],
    target_id: UUID,
    principal: PlanningReader,
    service: Service,
) -> list[CommentRead]:
    items = await service.list_comments(
        principal, target_type=target_type, target_id=target_id
    )
    return [CommentRead.model_validate(item) for item in items]


@router.post("/comments/{comment_id}/resolve", response_model=CommentRead)
async def resolve_comment(
    comment_id: UUID,
    data: CommentResolve,
    principal: PlanningWriter,
    service: Service,
) -> CommentRead:
    return CommentRead.model_validate(
        await service.resolve_comment(principal, comment_id, data)
    )


@router.post(
    "/calendar", response_model=CalendarEntryRead, status_code=status.HTTP_201_CREATED
)
async def create_calendar_entry(
    data: CalendarEntryCreate, principal: PlanningWriter, service: Service
) -> CalendarEntryRead:
    return CalendarEntryRead.model_validate(
        await service.create_calendar_entry(principal, data)
    )


@router.get("/calendar", response_model=list[CalendarEntryRead])
async def list_calendar_entries(
    starts_at: datetime,
    ends_at: datetime,
    principal: PlanningReader,
    service: Service,
    campaign_id: UUID | None = None,
    channel: str | None = None,
    include_cancelled: bool = False,
) -> list[CalendarEntryRead]:
    items = await service.list_calendar_entries(
        principal,
        starts_at=starts_at,
        ends_at=ends_at,
        campaign_id=campaign_id,
        channel=channel,
        include_cancelled=include_cancelled,
    )
    return [CalendarEntryRead.model_validate(item) for item in items]


@router.post("/calendar/{entry_id}/move", response_model=CalendarEntryRead)
async def move_calendar_entry(
    entry_id: UUID,
    data: CalendarEntryMove,
    principal: PlanningWriter,
    service: Service,
) -> CalendarEntryRead:
    return CalendarEntryRead.model_validate(
        await service.move_calendar_entry(principal, entry_id, data)
    )


@router.delete("/calendar/{entry_id}", response_model=CalendarEntryRead)
async def cancel_calendar_entry(
    entry_id: UUID,
    expected_lock_version: Annotated[int, Query(ge=1)],
    principal: PlanningWriter,
    service: Service,
) -> CalendarEntryRead:
    return CalendarEntryRead.model_validate(
        await service.cancel_calendar_entry(
            principal,
            entry_id,
            expected_lock_version=expected_lock_version,
        )
    )


@router.post(
    "/calendar/recurrences", status_code=status.HTTP_201_CREATED
)
async def create_recurrence(
    data: RecurrenceCreate,
    principal: PlanningWriter,
    service: Service,
    max_occurrences: Annotated[int, Query(ge=1, le=1000)] = 366,
) -> dict[str, object]:
    recurrence, entries = await service.create_recurrence(
        principal, data, max_occurrences=max_occurrences
    )
    return {
        "recurrence": RecurrenceRead.model_validate(recurrence),
        "calendar_entries": [CalendarEntryRead.model_validate(item) for item in entries],
    }


@router.get("/calendar/export.csv")
async def export_calendar_csv(
    starts_at: datetime,
    ends_at: datetime,
    principal: PlanningExporter,
    service: Service,
    timezone: str = "Asia/Seoul",
    campaign_id: UUID | None = None,
    channel: str | None = None,
) -> Response:
    payload = await service.export_calendar_csv(
        principal,
        CalendarExportQuery(
            starts_at=starts_at,
            ends_at=ends_at,
            timezone=timezone,
            campaign_id=campaign_id,
            channel=channel,
        ),
    )
    return Response(
        content=payload,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="content-calendar.csv"'},
    )


@router.get("/calendar/export.ics")
async def export_calendar_ics(
    starts_at: datetime,
    ends_at: datetime,
    principal: PlanningExporter,
    service: Service,
    timezone: str = "Asia/Seoul",
    campaign_id: UUID | None = None,
    channel: str | None = None,
) -> Response:
    payload = await service.export_calendar_ics(
        principal,
        CalendarExportQuery(
            starts_at=starts_at,
            ends_at=ends_at,
            timezone=timezone,
            campaign_id=campaign_id,
            channel=channel,
        ),
    )
    return Response(
        content=payload,
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="content-calendar.ics"'},
    )


@router.post(
    "/monthly-proposals",
    response_model=MonthlyPlanProposalRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_monthly_proposal(
    data: MonthlyPlanProposalCreate,
    principal: PlanningWriter,
    service: Service,
) -> MonthlyPlanProposalRead:
    return MonthlyPlanProposalRead.model_validate(
        await service.create_monthly_proposal(principal, data)
    )


@router.get("/monthly-proposals", response_model=list[MonthlyPlanProposalRead])
async def list_monthly_proposals(
    principal: PlanningReader,
    service: Service,
    month: date | None = None,
    proposal_status: ProposalStatus | None = Query(default=None, alias="status"),
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[MonthlyPlanProposalRead]:
    items = await service.list_monthly_proposals(
        principal,
        month=month,
        status=proposal_status,
        limit=limit,
        offset=offset,
    )
    return [MonthlyPlanProposalRead.model_validate(item) for item in items]


@router.get("/monthly-proposals/{proposal_id}", response_model=MonthlyPlanProposalRead)
async def get_monthly_proposal(
    proposal_id: UUID, principal: PlanningReader, service: Service
) -> MonthlyPlanProposalRead:
    return MonthlyPlanProposalRead.model_validate(
        await service.get_monthly_proposal(principal, proposal_id)
    )


@router.put("/monthly-proposals/{proposal_id}", response_model=MonthlyPlanProposalRead)
async def revise_monthly_proposal(
    proposal_id: UUID,
    data: MonthlyPlanProposalRevise,
    principal: PlanningWriter,
    service: Service,
) -> MonthlyPlanProposalRead:
    return MonthlyPlanProposalRead.model_validate(
        await service.revise_monthly_proposal(principal, proposal_id, data)
    )


@router.post(
    "/monthly-proposals/{proposal_id}/approve",
    response_model=MonthlyPlanApprovalRead,
)
async def approve_monthly_proposal(
    proposal_id: UUID,
    data: MonthlyPlanDecision,
    principal: PlanningApprover,
    service: Service,
) -> MonthlyPlanApprovalRead:
    result = await service.approve_monthly_proposal(principal, proposal_id, data)
    return MonthlyPlanApprovalRead(
        proposal=MonthlyPlanProposalRead.model_validate(result.proposal),
        calendar_entries=[
            CalendarEntryRead.model_validate(item) for item in result.calendar_entries
        ],
    )


@router.post(
    "/monthly-proposals/{proposal_id}/reject",
    response_model=MonthlyPlanProposalRead,
)
async def reject_monthly_proposal(
    proposal_id: UUID,
    data: MonthlyPlanDecision,
    principal: PlanningApprover,
    service: Service,
) -> MonthlyPlanProposalRead:
    return MonthlyPlanProposalRead.model_validate(
        await service.reject_monthly_proposal(principal, proposal_id, data)
    )
