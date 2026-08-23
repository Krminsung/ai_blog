"""Content planning application service with immutable handoff snapshots."""

from __future__ import annotations

import csv
from calendar import monthrange
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
import io
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.core.context import Principal
from blogops.core.errors import AppError
from blogops.db.session import apply_workspace_scope
from blogops.domain.planning.enums import (
    AssignmentStatus,
    BriefEvent,
    BriefStatus,
    BudgetEnforcement,
    CalendarConflictResolution,
    CalendarEntryStatus,
    CampaignStatus,
    DecisionKind,
    IdeaStatus,
    IntentSource,
    ProposalStatus,
    RecurrenceFrequency,
    SpendDecision,
    TopicNodeStatus,
)
from blogops.domain.planning.models import (
    BriefDecision,
    BriefVersion,
    CalendarEntry,
    CalendarRecurrence,
    Campaign,
    CampaignSpendLedger,
    ContentBrief,
    ContentIdea,
    MonthlyPlanProposal,
    PlanningAssignment,
    PlanningBoardColumn,
    PlanningComment,
    TopicIntentRevision,
    TopicNode,
)
from blogops.domain.planning.providers import MonthlyPlanGenerator
from blogops.domain.planning.references import (
    ActiveMembershipResolver,
    PlanningReferenceResolver,
    ResolvedPlanningReferences,
    WorkspacePolicySnapshot,
)
from blogops.domain.planning.repository import PlanningRepository
from blogops.domain.planning.rules import (
    CalendarSlot,
    InvalidPlanningTransition,
    canonical_json_hash,
    evaluate_budget,
    idea_duplicate_key,
    resolve_calendar_slot,
    transition_brief_status,
)
from blogops.domain.planning.schemas import (
    ApprovalStage,
    AssignmentInput,
    AssignmentUpsert,
    BoardColumnCreate,
    BriefBoardMove,
    BriefCreate,
    BriefDecisionRequest,
    BriefPayload,
    BriefRead,
    BriefTransitionRequest,
    BriefVersionCreate,
    BriefVersionRead,
    CalendarEntryCreate,
    CalendarEntryMove,
    CalendarExportQuery,
    CampaignCreate,
    CampaignUpdate,
    CommentCreate,
    CommentResolve,
    IdeaBatchCreate,
    MonthlyPlanDecision,
    MonthlyPlanItem,
    MonthlyPlanProposalCreate,
    MonthlyPlanProposalRevise,
    ReferenceSelection,
    RecurrenceCreate,
    SpendDecisionRead,
    SpendRecordCreate,
    SuppressedIdea,
    TopicIntentUpdate,
    TopicMergeRequest,
    TopicNodeCreate,
    TopicNodeMove,
    TopicSplitRequest,
)
from blogops.services.audit import append_audit_log
from blogops.services.outbox import add_outbox_event


OUTBOX_SCHEMA_VERSION = "1"


@dataclass(slots=True)
class IdeaBatchResult:
    created: list[ContentIdea]
    suppressed: list[SuppressedIdea]


@dataclass(slots=True)
class ProposalApprovalResult:
    proposal: MonthlyPlanProposal
    calendar_entries: list[CalendarEntry]


class PlanningService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        references: PlanningReferenceResolver,
        memberships: ActiveMembershipResolver,
        monthly_generator: MonthlyPlanGenerator,
    ) -> None:
        self.session = session
        self.repo = PlanningRepository(session)
        self.references = references
        self.memberships = memberships
        self.monthly_generator = monthly_generator

    async def create_campaign(self, principal: Principal, data: CampaignCreate) -> Campaign:
        await self._scope(principal.workspace_id)
        _zone(data.timezone)
        duplicate = await self.session.scalar(
            select(Campaign.id).where(
                Campaign.workspace_id == principal.workspace_id,
                Campaign.name == data.name,
            )
        )
        if duplicate is not None:
            raise AppError(
                code="CAMPAIGN_NAME_EXISTS",
                message="같은 이름의 캠페인이 이미 있습니다.",
                status_code=409,
            )
        policy = await self.references.workspace_policy(principal.workspace_id)
        brand_snapshot: dict[str, Any] | None = None
        brand_hash: str | None = None
        if data.brand_id is not None:
            resolved = await self.references.resolve(
                principal.workspace_id,
                ReferenceSelection(
                    brand_id=data.brand_id,
                    primary_keyword_text=data.name,
                ),
            )
            brand_snapshot = resolved.brand_snapshot
            brand_hash = canonical_json_hash(brand_snapshot)
        campaign = Campaign(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            name=data.name,
            description=data.description,
            objective=data.objective,
            brand_id=data.brand_id,
            brand_snapshot=brand_snapshot,
            brand_snapshot_hash=brand_hash,
            channels=list(dict.fromkeys(data.channels)),
            start_date=data.start_date,
            end_date=data.end_date,
            timezone=data.timezone,
            budget_limits=_budget_limits(data.budget_limits),
            budget_enforcement=data.budget_enforcement.value,
            generation_policy_snapshot=policy.generation_policy,
            generation_policy_hash=policy.generation_policy_hash,
            approval_policy_snapshot=policy.approval_policy,
            approval_policy_hash=policy.approval_policy_hash,
            status=CampaignStatus.DRAFT.value,
            created_by=principal.subject_id,
            lock_version=1,
        )
        self.session.add(campaign)
        await self.repo.flush("campaign")
        await self._record_change(
            principal,
            action="planning.campaign.created",
            aggregate_type="campaign",
            aggregate_id=campaign.id,
            details={"policy_hashes": _campaign_policy_hashes(campaign)},
        )
        return campaign

    async def list_campaigns(
        self,
        principal: Principal,
        *,
        include_archived: bool,
        limit: int,
        offset: int,
    ) -> list[Campaign]:
        query = select(Campaign).where(Campaign.workspace_id == principal.workspace_id)
        if not include_archived:
            query = query.where(Campaign.status != CampaignStatus.ARCHIVED.value)
        return list(
            await self.session.scalars(
                query.order_by(Campaign.start_date.desc(), Campaign.id).limit(limit).offset(offset)
            )
        )

    async def get_campaign(self, principal: Principal, campaign_id: UUID) -> Campaign:
        return await self.repo.campaign(principal.workspace_id, campaign_id)

    async def update_campaign(
        self, principal: Principal, campaign_id: UUID, data: CampaignUpdate
    ) -> Campaign:
        campaign = await self.repo.campaign(
            principal.workspace_id, campaign_id, for_update=True
        )
        _assert_lock("campaign", data.expected_lock_version, campaign.lock_version)
        mutable = data.model_fields_set.difference({"expected_lock_version"})
        for field_name in mutable:
            value = getattr(data, field_name)
            if field_name in {"name", "objective", "channels", "timezone"} and value is None:
                raise _null_error(field_name)
            if field_name == "budget_limits" and value is not None:
                value = _budget_limits(value)
            elif field_name in {"budget_enforcement", "status"} and value is not None:
                value = value.value
            elif field_name == "channels" and value is not None:
                value = list(dict.fromkeys(value))
            setattr(campaign, field_name, value)
        _zone(campaign.timezone)
        if campaign.end_date < campaign.start_date:
            raise AppError(
                code="CAMPAIGN_DATES_INVALID",
                message="캠페인 종료일은 시작일보다 빠를 수 없습니다.",
                status_code=422,
            )
        await self.repo.flush("campaign")
        await self._record_change(
            principal,
            action="planning.campaign.updated",
            aggregate_type="campaign",
            aggregate_id=campaign.id,
            details={"fields": sorted(mutable), "lock_version": campaign.lock_version},
        )
        return campaign

    async def record_campaign_spend(
        self, principal: Principal, campaign_id: UUID, data: SpendRecordCreate
    ) -> SpendDecisionRead:
        campaign = await self.repo.campaign(
            principal.workspace_id, campaign_id, for_update=True
        )
        _assert_lock("campaign", data.expected_campaign_lock_version, campaign.lock_version)
        limit_payload = campaign.budget_limits.get(data.category.value)
        if limit_payload is None:
            limit = Decimal("999999999999999.9999")
        else:
            if limit_payload["currency"] != data.currency:
                raise AppError(
                    code="CAMPAIGN_BUDGET_CURRENCY_MISMATCH",
                    message="캠페인 예산 통화와 지출 통화가 다릅니다.",
                    status_code=422,
                )
            limit = Decimal(str(limit_payload["amount"]))
        spent = await self.session.scalar(
            select(func.coalesce(func.sum(CampaignSpendLedger.amount), 0)).where(
                CampaignSpendLedger.workspace_id == principal.workspace_id,
                CampaignSpendLedger.campaign_id == campaign.id,
                CampaignSpendLedger.category == data.category.value,
                CampaignSpendLedger.currency == data.currency,
            )
        )
        result = evaluate_budget(
            spent=Decimal(str(spent)),
            requested=data.amount,
            limit=limit,
            enforcement=BudgetEnforcement(campaign.budget_enforcement),
        )
        ledger: CampaignSpendLedger | None = None
        if result.decision in {SpendDecision.ALLOW, SpendDecision.WARN}:
            ledger = CampaignSpendLedger(
                id=uuid4(),
                workspace_id=principal.workspace_id,
                campaign_id=campaign.id,
                category=data.category.value,
                amount=data.amount,
                currency=data.currency,
                source_ref=data.source_ref,
                details=data.details,
                recorded_by=principal.subject_id,
            )
            self.session.add(ledger)
        elif result.decision is SpendDecision.PAUSE:
            campaign.status = CampaignStatus.PAUSED.value
        await self.repo.flush("campaign_spend")
        await self._record_change(
            principal,
            action="planning.campaign.budget_evaluated",
            aggregate_type="campaign",
            aggregate_id=campaign.id,
            details={
                "category": data.category.value,
                "decision": result.decision.value,
                "projected": str(result.projected),
                "limit": str(result.limit),
                "source_ref": data.source_ref,
            },
        )
        return SpendDecisionRead(
            decision=result.decision.value,
            projected=result.projected,
            limit=result.limit,
            ledger_id=ledger.id if ledger else None,
        )

    async def create_topic_node(
        self, principal: Principal, data: TopicNodeCreate
    ) -> TopicNode:
        await self._scope(principal.workspace_id)
        campaign_id = data.campaign_id
        if data.campaign_id is not None:
            await self.repo.campaign(principal.workspace_id, data.campaign_id)
        if data.parent_id is not None:
            parent = await self.repo.topic_node(principal.workspace_id, data.parent_id)
            if parent.status != TopicNodeStatus.ACTIVE.value:
                raise _inactive_error("토픽 부모")
            if campaign_id is None:
                campaign_id = parent.campaign_id
            elif parent.campaign_id != campaign_id:
                raise AppError(
                    code="TOPIC_CAMPAIGN_MISMATCH",
                    message="부모와 자식 토픽은 같은 캠페인에 속해야 합니다.",
                    status_code=422,
                )
        resolved = await self.references.resolve(
            principal.workspace_id,
            ReferenceSelection(
                primary_keyword_id=data.keyword_id,
                keyword_cluster_id=data.keyword_cluster_id,
                primary_keyword_text=data.keyword_text or data.name,
            ),
        )
        node = TopicNode(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            campaign_id=campaign_id,
            parent_id=data.parent_id,
            node_kind=data.node_kind.value,
            name=data.name,
            description=data.description,
            keyword_id=data.keyword_id,
            keyword_cluster_id=data.keyword_cluster_id,
            keyword_snapshot=resolved.keyword_snapshot,
            keyword_snapshot_hash=canonical_json_hash(resolved.keyword_snapshot),
            search_intent=data.search_intent.value,
            intent_source=data.intent_source.value,
            journey_stage=data.journey_stage.value,
            cta_recommendation=data.cta_recommendation,
            existing_content_refs=data.existing_content_refs,
            internal_link_recommendations=data.internal_link_recommendations,
            content_gap_summary=data.content_gap_summary,
            seasonality=data.seasonality,
            refresh_interval_days=data.refresh_interval_days,
            sort_order=data.sort_order,
            status=TopicNodeStatus.ACTIVE.value,
            created_by=principal.subject_id,
            lock_version=1,
        )
        self.session.add(node)
        await self.repo.flush("topic_node")
        await self._record_change(
            principal,
            action="planning.topic.created",
            aggregate_type="topic_cluster",
            aggregate_id=node.id,
            details={"parent_id": str(node.parent_id) if node.parent_id else None},
        )
        return node

    async def list_topic_nodes(
        self, principal: Principal, *, campaign_id: UUID | None, include_archived: bool
    ) -> list[TopicNode]:
        query = select(TopicNode).where(TopicNode.workspace_id == principal.workspace_id)
        if campaign_id is not None:
            query = query.where(TopicNode.campaign_id == campaign_id)
        if not include_archived:
            query = query.where(TopicNode.status == TopicNodeStatus.ACTIVE.value)
        return list(
            await self.session.scalars(
                query.order_by(TopicNode.parent_id.nullsfirst(), TopicNode.sort_order, TopicNode.id)
            )
        )

    async def move_topic_node(
        self, principal: Principal, node_id: UUID, data: TopicNodeMove
    ) -> TopicNode:
        node = await self.repo.topic_node(principal.workspace_id, node_id, for_update=True)
        _assert_lock("topic_node", data.expected_lock_version, node.lock_version)
        if node.status != TopicNodeStatus.ACTIVE.value:
            raise _inactive_error("토픽")
        if data.parent_id == node.id:
            raise _topic_cycle_error()
        if data.parent_id is not None:
            parent = await self.repo.topic_node(principal.workspace_id, data.parent_id)
            await self._assert_not_descendant(node.id, parent)
            if parent.campaign_id != node.campaign_id:
                raise AppError(
                    code="TOPIC_CAMPAIGN_MISMATCH",
                    message="부모와 자식 토픽은 같은 캠페인에 속해야 합니다.",
                    status_code=422,
                )
        node.parent_id = data.parent_id
        node.sort_order = data.sort_order
        await self.repo.flush("topic_node")
        await self._record_change(
            principal,
            action="planning.topic.moved",
            aggregate_type="topic_cluster",
            aggregate_id=node.id,
            details={"parent_id": str(node.parent_id) if node.parent_id else None},
        )
        return node

    async def revise_topic_intent(
        self, principal: Principal, node_id: UUID, data: TopicIntentUpdate
    ) -> TopicNode:
        node = await self.repo.topic_node(principal.workspace_id, node_id, for_update=True)
        _assert_lock("topic_node", data.expected_lock_version, node.lock_version)
        revision = TopicIntentRevision(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            topic_node_id=node.id,
            previous_intent=node.search_intent,
            revised_intent=data.search_intent.value,
            previous_journey_stage=node.journey_stage,
            revised_journey_stage=data.journey_stage.value,
            reason=data.reason,
            revised_by=principal.subject_id,
        )
        node.search_intent = data.search_intent.value
        node.journey_stage = data.journey_stage.value
        node.intent_source = IntentSource.USER.value
        self.session.add(revision)
        await self.repo.flush("topic_intent")
        await self._record_change(
            principal,
            action="planning.topic.intent_revised",
            aggregate_type="topic_cluster",
            aggregate_id=node.id,
            details={"revision_id": str(revision.id)},
        )
        return node

    async def merge_topic_nodes(
        self,
        principal: Principal,
        target_node_id: UUID,
        data: TopicMergeRequest,
    ) -> TopicNode:
        target = await self.repo.topic_node(
            principal.workspace_id, target_node_id, for_update=True
        )
        _assert_lock("topic_node", data.expected_target_lock_version, target.lock_version)
        if target.status != TopicNodeStatus.ACTIVE.value:
            raise _inactive_error("병합 대상 토픽")
        if target.id in set(data.source_node_ids):
            raise AppError(
                code="TOPIC_MERGE_SELF",
                message="병합 대상은 원본 목록에 포함될 수 없습니다.",
                status_code=422,
            )
        for source_id in data.source_node_ids:
            source = await self.repo.topic_node(
                principal.workspace_id, source_id, for_update=True
            )
            _assert_lock(
                "topic_node", data.expected_source_versions[source_id], source.lock_version
            )
            if source.status != TopicNodeStatus.ACTIVE.value:
                raise _inactive_error("병합 원본 토픽")
            if source.campaign_id != target.campaign_id:
                raise AppError(
                    code="TOPIC_CAMPAIGN_MISMATCH",
                    message="같은 캠페인의 토픽만 병합할 수 있습니다.",
                    status_code=422,
                )
            await self._assert_not_descendant(source.id, target)
            children = list(
                await self.session.scalars(
                    select(TopicNode)
                    .where(
                        TopicNode.workspace_id == principal.workspace_id,
                        TopicNode.parent_id == source.id,
                    )
                    .with_for_update()
                )
            )
            for child in children:
                child.parent_id = target.id
            source.status = TopicNodeStatus.MERGED.value
            source.merged_into_id = target.id
        await self.repo.flush("topic_merge")
        await self._record_change(
            principal,
            action="planning.topic.merged",
            aggregate_type="topic_cluster",
            aggregate_id=target.id,
            details={"source_node_ids": [str(item) for item in data.source_node_ids]},
        )
        return target

    async def split_topic_node(
        self, principal: Principal, source_id: UUID, data: TopicSplitRequest
    ) -> TopicNode:
        source = await self.repo.topic_node(principal.workspace_id, source_id, for_update=True)
        _assert_lock("topic_node", data.expected_source_lock_version, source.lock_version)
        new_data = data.new_node.model_copy(
            update={
                "campaign_id": source.campaign_id,
                "parent_id": source.parent_id,
            }
        )
        new_node = await self.create_topic_node(principal, new_data)
        if data.child_node_ids:
            children = list(
                await self.session.scalars(
                    select(TopicNode)
                    .where(
                        TopicNode.workspace_id == principal.workspace_id,
                        TopicNode.id.in_(data.child_node_ids),
                        TopicNode.parent_id == source.id,
                    )
                    .with_for_update()
                )
            )
            if len(children) != len(set(data.child_node_ids)):
                raise AppError(
                    code="TOPIC_SPLIT_CHILD_INVALID",
                    message="선택한 하위 토픽이 원본 토픽에 속하지 않습니다.",
                    status_code=422,
                )
            for child in children:
                child.parent_id = new_node.id
        await self.repo.flush("topic_split")
        await self._record_change(
            principal,
            action="planning.topic.split",
            aggregate_type="topic_cluster",
            aggregate_id=source.id,
            details={"new_node_id": str(new_node.id)},
        )
        return new_node

    async def create_ideas(
        self, principal: Principal, data: IdeaBatchCreate
    ) -> IdeaBatchResult:
        await self._scope(principal.workspace_id)
        campaign_id = data.campaign_id
        if data.campaign_id is not None:
            await self.repo.campaign(principal.workspace_id, data.campaign_id)
        if data.topic_node_id is not None:
            topic = await self.repo.topic_node(principal.workspace_id, data.topic_node_id)
            if campaign_id is None:
                campaign_id = topic.campaign_id
            elif topic.campaign_id != campaign_id:
                raise AppError(
                    code="IDEA_TOPIC_CAMPAIGN_MISMATCH",
                    message="아이디어의 토픽과 캠페인이 일치하지 않습니다.",
                    status_code=422,
                )
        candidates: list[tuple[int, Any, str]] = []
        representative_index: dict[str, int] = {}
        suppressed: list[SuppressedIdea] = []
        for index, candidate in enumerate(data.candidates):
            key = idea_duplicate_key(
                title=candidate.title,
                intent=candidate.search_intent.value,
                keyword_cluster_id=candidate.keyword_cluster_id,
                semantic_group_key=candidate.semantic_group_key,
                primary_keyword=candidate.primary_keyword_text,
            )
            if key in representative_index:
                suppressed.append(
                    SuppressedIdea(
                        candidate_index=index,
                        duplicate_key=key,
                        reason="BATCH_DUPLICATE",
                    )
                )
                continue
            representative_index[key] = index
            candidates.append((index, candidate, key))
        existing = await self.repo.existing_ideas_by_keys(
            principal.workspace_id, {item[2] for item in candidates}
        )
        created: list[ContentIdea] = []
        created_by_key: dict[str, ContentIdea] = {}
        for index, candidate, key in candidates:
            prior = existing.get(key)
            if prior is not None:
                suppressed.append(
                    SuppressedIdea(
                        candidate_index=index,
                        duplicate_key=key,
                        reason="EXISTING_IDEA",
                        representative_idea_id=prior.id,
                    )
                )
                continue
            selection = ReferenceSelection(
                brand_id=data.brand_id,
                persona_id=data.persona_id,
                product_ids=data.product_ids,
                knowledge_source_ids=data.knowledge_source_ids,
                primary_keyword_id=candidate.primary_keyword_id,
                keyword_cluster_id=candidate.keyword_cluster_id,
                primary_keyword_text=candidate.primary_keyword_text or candidate.title,
            )
            resolved = await self.references.resolve(principal.workspace_id, selection)
            snapshot = {
                **resolved.snapshot,
                "semantic_group_key": candidate.semantic_group_key,
                "source_signals": candidate.source_signals,
                "performance_signals": candidate.performance_signals,
            }
            idea = ContentIdea(
                id=uuid4(),
                workspace_id=principal.workspace_id,
                campaign_id=campaign_id,
                topic_node_id=data.topic_node_id,
                title=candidate.title,
                rationale=candidate.rationale,
                primary_keyword_id=candidate.primary_keyword_id,
                keyword_cluster_id=candidate.keyword_cluster_id,
                search_intent=candidate.search_intent.value,
                journey_stage=candidate.journey_stage.value,
                recommended_cta=candidate.recommended_cta,
                source_signals=candidate.source_signals,
                performance_signals=candidate.performance_signals,
                reference_snapshot=snapshot,
                reference_snapshot_hash=canonical_json_hash(snapshot),
                duplicate_key=key,
                status=IdeaStatus.SUGGESTED.value,
                created_by=principal.subject_id,
                lock_version=1,
            )
            self.session.add(idea)
            created.append(idea)
            created_by_key[key] = idea
        await self.repo.flush("content_ideas")
        for item in suppressed:
            if item.reason == "BATCH_DUPLICATE" and item.representative_idea_id is None:
                representative = created_by_key.get(item.duplicate_key) or existing.get(
                    item.duplicate_key
                )
                if representative is not None:
                    item.representative_idea_id = representative.id
        await self._record_change(
            principal,
            action="planning.ideas.created",
            aggregate_type="content_idea_batch",
            aggregate_id=uuid4(),
            details={
                "input_count": len(data.candidates),
                "created_count": len(created),
                "suppressed_count": len(suppressed),
            },
        )
        return IdeaBatchResult(created=created, suppressed=suppressed)

    async def list_ideas(
        self,
        principal: Principal,
        *,
        campaign_id: UUID | None,
        status: IdeaStatus | None,
        limit: int,
        offset: int,
    ) -> list[ContentIdea]:
        query = select(ContentIdea).where(ContentIdea.workspace_id == principal.workspace_id)
        if campaign_id is not None:
            query = query.where(ContentIdea.campaign_id == campaign_id)
        if status is not None:
            query = query.where(ContentIdea.status == status.value)
        return list(
            await self.session.scalars(
                query.order_by(ContentIdea.created_at.desc(), ContentIdea.id)
                .limit(limit)
                .offset(offset)
            )
        )

    async def ensure_default_board_columns(
        self, principal: Principal
    ) -> list[PlanningBoardColumn]:
        await self._scope(principal.workspace_id)
        columns = list(
            await self.session.scalars(
                select(PlanningBoardColumn)
                .where(PlanningBoardColumn.workspace_id == principal.workspace_id)
                .order_by(PlanningBoardColumn.position, PlanningBoardColumn.id)
            )
        )
        if columns:
            return columns
        defaults = (
            ("backlog", "백로그", "BACKLOG"),
            ("active", "진행 중", "ACTIVE"),
            ("review", "검토", "REVIEW"),
            ("done", "완료", "DONE"),
        )
        columns = [
            PlanningBoardColumn(
                id=uuid4(),
                workspace_id=principal.workspace_id,
                key=key,
                name=name,
                kind=kind,
                position=position,
                is_system=True,
                lock_version=1,
            )
            for position, (key, name, kind) in enumerate(defaults)
        ]
        self.session.add_all(columns)
        await self.repo.flush("board_columns")
        await self._record_change(
            principal,
            action="planning.board.defaults_created",
            aggregate_type="planning_board",
            aggregate_id=principal.workspace_id,
            details={"column_ids": [str(item.id) for item in columns]},
        )
        return columns

    async def list_board_columns(
        self, principal: Principal
    ) -> list[PlanningBoardColumn]:
        return list(
            await self.session.scalars(
                select(PlanningBoardColumn)
                .where(PlanningBoardColumn.workspace_id == principal.workspace_id)
                .order_by(PlanningBoardColumn.position, PlanningBoardColumn.id)
            )
        )

    async def create_board_column(
        self, principal: Principal, data: BoardColumnCreate
    ) -> PlanningBoardColumn:
        await self._scope(principal.workspace_id)
        column = PlanningBoardColumn(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            key=data.key,
            name=data.name,
            kind=data.kind,
            color=data.color,
            position=data.position,
            is_system=False,
            lock_version=1,
        )
        self.session.add(column)
        await self.repo.flush("board_column")
        await self._record_change(
            principal,
            action="planning.board.column_created",
            aggregate_type="planning_board_column",
            aggregate_id=column.id,
            details={"key": column.key, "position": column.position},
        )
        return column

    async def move_brief_on_board(
        self, principal: Principal, brief_id: UUID, data: BriefBoardMove
    ) -> ContentBrief:
        brief = await self.repo.brief(principal.workspace_id, brief_id, for_update=True)
        _assert_lock("content_brief", data.expected_brief_lock_version, brief.lock_version)
        column = await self.repo.board_column(principal.workspace_id, data.board_column_id)
        brief.board_column_id = column.id
        await self.repo.flush("content_brief")
        await self._record_change(
            principal,
            action="planning.brief.board_moved",
            aggregate_type="content_brief",
            aggregate_id=brief.id,
            details={
                "board_column_id": str(column.id),
                "lifecycle_status": brief.status,
            },
        )
        return brief

    async def create_brief(self, principal: Principal, data: BriefCreate) -> BriefRead:
        await self._scope(principal.workspace_id)
        campaign: Campaign | None = None
        idea: ContentIdea | None = None
        topic: TopicNode | None = None
        if data.campaign_id is not None:
            campaign = await self.repo.campaign(principal.workspace_id, data.campaign_id)
        if data.idea_id is not None:
            idea = await self.repo.idea(principal.workspace_id, data.idea_id, for_update=True)
            if idea.status == IdeaStatus.DISMISSED.value:
                raise _inactive_error("콘텐츠 아이디어")
            if campaign is not None and idea.campaign_id not in {None, campaign.id}:
                raise AppError(
                    code="BRIEF_IDEA_CAMPAIGN_MISMATCH",
                    message="브리프와 아이디어의 캠페인이 일치하지 않습니다.",
                    status_code=422,
                )
        if data.topic_node_id is not None:
            topic = await self.repo.topic_node(principal.workspace_id, data.topic_node_id)
            if campaign is not None and topic.campaign_id not in {None, campaign.id}:
                raise AppError(
                    code="BRIEF_TOPIC_CAMPAIGN_MISMATCH",
                    message="브리프와 토픽의 캠페인이 일치하지 않습니다.",
                    status_code=422,
                )
        board_column_id = data.board_column_id
        if board_column_id is not None:
            await self.repo.board_column(principal.workspace_id, board_column_id)
        else:
            columns = await self.ensure_default_board_columns(principal)
            board_column_id = columns[0].id
        assignee_ids = {item.user_id for item in data.assignments}
        await self.memberships.require_active(principal.workspace_id, assignee_ids)
        policy = await self.references.workspace_policy(principal.workspace_id)
        resolved = await self.references.resolve(
            principal.workspace_id, data.payload.references
        )
        approval_stages = await self._approval_stages(
            principal.workspace_id, data.payload, policy
        )
        brief = ContentBrief(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            campaign_id=data.campaign_id,
            idea_id=data.idea_id,
            topic_node_id=data.topic_node_id,
            current_version_id=None,
            board_column_id=board_column_id,
            status=BriefStatus.DRAFT.value,
            approval_stage_index=0,
            next_refresh_at=None,
            created_by=principal.subject_id,
            lock_version=1,
        )
        self.session.add(brief)
        await self.repo.flush("content_brief")
        version = self._new_brief_version(
            principal,
            brief=brief,
            version_number=1,
            payload=data.payload,
            resolved=resolved,
            policy=policy,
            approval_stages=approval_stages,
        )
        self.session.add(version)
        await self.repo.flush("content_brief_version")
        brief.current_version_id = version.id
        brief.next_refresh_at = _next_refresh_at(version)
        if idea is not None:
            idea.status = IdeaStatus.PROMOTED.value
        await self._replace_assignments(principal, brief, data.assignments)
        await self.repo.flush("content_brief")
        await self._record_change(
            principal,
            action="planning.brief.created",
            aggregate_type="content_brief",
            aggregate_id=brief.id,
            details={
                "version_id": str(version.id),
                "snapshot_hash": version.snapshot_hash,
                "policy_hashes": {
                    "generation": version.generation_policy_hash,
                    "approval": version.approval_policy_hash,
                },
            },
        )
        return _brief_read(brief, version)

    async def list_briefs(
        self,
        principal: Principal,
        *,
        campaign_id: UUID | None,
        status: BriefStatus | None,
        board_column_id: UUID | None,
        limit: int,
        offset: int,
    ) -> list[BriefRead]:
        query = select(ContentBrief).where(
            ContentBrief.workspace_id == principal.workspace_id
        )
        if campaign_id is not None:
            query = query.where(ContentBrief.campaign_id == campaign_id)
        if status is not None:
            query = query.where(ContentBrief.status == status.value)
        if board_column_id is not None:
            query = query.where(ContentBrief.board_column_id == board_column_id)
        briefs = list(
            await self.session.scalars(
                query.order_by(ContentBrief.updated_at.desc(), ContentBrief.id)
                .limit(limit)
                .offset(offset)
            )
        )
        versions = await self._versions_by_ids(
            principal.workspace_id,
            {item.current_version_id for item in briefs if item.current_version_id is not None},
        )
        return [
            _brief_read(item, versions.get(item.current_version_id)) for item in briefs
        ]

    async def get_brief(self, principal: Principal, brief_id: UUID) -> BriefRead:
        brief = await self.repo.brief(principal.workspace_id, brief_id)
        version = await self._brief_version(
            principal.workspace_id, brief, brief.current_version_id
        )
        return _brief_read(brief, version)

    async def list_brief_versions(
        self, principal: Principal, brief_id: UUID
    ) -> list[BriefVersionRead]:
        await self.repo.brief(principal.workspace_id, brief_id)
        versions = list(
            await self.session.scalars(
                select(BriefVersion)
                .where(
                    BriefVersion.workspace_id == principal.workspace_id,
                    BriefVersion.brief_id == brief_id,
                )
                .order_by(BriefVersion.version_number.desc())
            )
        )
        return [BriefVersionRead.model_validate(item) for item in versions]

    async def create_brief_version(
        self, principal: Principal, brief_id: UUID, data: BriefVersionCreate
    ) -> BriefRead:
        brief = await self.repo.brief(principal.workspace_id, brief_id, for_update=True)
        _assert_lock("content_brief", data.expected_lock_version, brief.lock_version)
        if BriefStatus(brief.status) not in {
            BriefStatus.DRAFT,
            BriefStatus.REVISION_REQUESTED,
            BriefStatus.REJECTED,
        }:
            raise AppError(
                code="BRIEF_VERSION_STATE_INVALID",
                message="초안 또는 수정 요청 상태에서만 새 버전을 만들 수 있습니다.",
                status_code=409,
            )
        if data.assignments is not None:
            await self.memberships.require_active(
                principal.workspace_id, {item.user_id for item in data.assignments}
            )
        latest_number = await self.session.scalar(
            select(func.coalesce(func.max(BriefVersion.version_number), 0)).where(
                BriefVersion.workspace_id == principal.workspace_id,
                BriefVersion.brief_id == brief.id,
            )
        )
        policy = await self.references.workspace_policy(principal.workspace_id)
        resolved = await self.references.resolve(
            principal.workspace_id, data.payload.references
        )
        approval_stages = await self._approval_stages(
            principal.workspace_id, data.payload, policy
        )
        version = self._new_brief_version(
            principal,
            brief=brief,
            version_number=int(latest_number or 0) + 1,
            payload=data.payload,
            resolved=resolved,
            policy=policy,
            approval_stages=approval_stages,
        )
        self.session.add(version)
        await self.repo.flush("content_brief_version")
        brief.current_version_id = version.id
        brief.status = BriefStatus.DRAFT.value
        brief.approval_stage_index = 0
        brief.next_refresh_at = _next_refresh_at(version)
        if data.assignments is not None:
            await self._replace_assignments(principal, brief, data.assignments)
        await self.repo.flush("content_brief")
        await self._record_change(
            principal,
            action="planning.brief.version_created",
            aggregate_type="content_brief",
            aggregate_id=brief.id,
            details={
                "version_id": str(version.id),
                "version_number": version.version_number,
                "snapshot_hash": version.snapshot_hash,
            },
        )
        return _brief_read(brief, version)

    async def transition_brief(
        self,
        principal: Principal,
        brief_id: UUID,
        data: BriefTransitionRequest,
        *,
        event: BriefEvent,
    ) -> BriefRead:
        brief = await self.repo.brief(principal.workspace_id, brief_id, for_update=True)
        _assert_lock("content_brief", data.expected_lock_version, brief.lock_version)
        version = await self._brief_version(
            principal.workspace_id, brief, brief.current_version_id
        )
        if version is None:
            raise AppError(
                code="BRIEF_VERSION_REQUIRED",
                message="브리프 상태를 변경하려면 현재 버전이 필요합니다.",
                status_code=409,
            )
        if event is BriefEvent.SUBMIT:
            await self._validate_brief_facts(version, require_unexpired=False)
        old_status = BriefStatus(brief.status)
        try:
            new_status = transition_brief_status(old_status, event)
        except InvalidPlanningTransition as exc:
            raise _transition_error(old_status, event) from exc
        brief.status = new_status.value
        if event in {BriefEvent.SUBMIT, BriefEvent.REVISE}:
            brief.approval_stage_index = 0
        await self.repo.flush("content_brief")
        await self._record_change(
            principal,
            action="planning.brief.transitioned",
            aggregate_type="content_brief",
            aggregate_id=brief.id,
            details={
                "event": event.value,
                "from_status": old_status.value,
                "to_status": new_status.value,
                "comment": data.comment,
            },
        )
        return _brief_read(brief, version)

    async def decide_brief(
        self, principal: Principal, brief_id: UUID, data: BriefDecisionRequest
    ) -> BriefRead:
        brief = await self.repo.brief(principal.workspace_id, brief_id, for_update=True)
        _assert_lock("content_brief", data.expected_lock_version, brief.lock_version)
        if brief.status != BriefStatus.WAITING_REVIEW.value:
            raise AppError(
                code="BRIEF_NOT_WAITING_REVIEW",
                message="검토 대기 상태의 브리프만 승인 또는 반려할 수 있습니다.",
                status_code=409,
            )
        version = await self._brief_version(
            principal.workspace_id, brief, brief.current_version_id
        )
        if version is None:
            raise AppError(
                code="BRIEF_VERSION_REQUIRED",
                message="승인할 브리프 버전이 없습니다.",
                status_code=409,
            )
        stages = [ApprovalStage.model_validate(item) for item in version.approval_stages]
        if not stages or brief.approval_stage_index >= len(stages):
            raise AppError(
                code="BRIEF_APPROVAL_STAGE_INVALID",
                message="현재 승인 단계 구성이 올바르지 않습니다.",
                status_code=409,
            )
        stage = stages[brief.approval_stage_index]
        if stage.approver_user_ids and principal.subject_id not in stage.approver_user_ids:
            raise AppError(
                code="BRIEF_APPROVER_NOT_ALLOWED",
                message="현재 단계의 승인자로 지정되지 않았습니다.",
                status_code=403,
            )
        if stage.require_mfa and principal.authentication_method not in {
            "mfa",
            "totp",
            "webauthn",
        }:
            raise AppError(
                code="BRIEF_APPROVAL_MFA_REQUIRED",
                message="현재 승인 단계에는 다중 인증이 필요합니다.",
                status_code=403,
            )
        duplicate_decision = await self.session.scalar(
            select(BriefDecision.id).where(
                BriefDecision.workspace_id == principal.workspace_id,
                BriefDecision.brief_id == brief.id,
                BriefDecision.brief_version_id == version.id,
                BriefDecision.stage_key == stage.key,
                BriefDecision.decided_by == principal.subject_id,
                BriefDecision.decision == DecisionKind.APPROVE.value,
            )
        )
        if data.decision is DecisionKind.APPROVE and duplicate_decision is not None:
            raise AppError(
                code="BRIEF_APPROVAL_DUPLICATE",
                message="같은 버전과 단계에 이미 승인했습니다.",
                status_code=409,
            )
        old_status = BriefStatus(brief.status)
        if data.decision is DecisionKind.REQUEST_CHANGES:
            event = BriefEvent.REQUEST_CHANGES
            new_status = transition_brief_status(old_status, event)
            brief.status = new_status.value
            brief.approval_stage_index = 0
        elif data.decision is DecisionKind.REJECT:
            event = BriefEvent.REJECT
            new_status = transition_brief_status(old_status, event)
            brief.status = new_status.value
            brief.approval_stage_index = 0
        else:
            prior_approvals = int(
                await self.session.scalar(
                    select(func.count(BriefDecision.id)).where(
                        BriefDecision.workspace_id == principal.workspace_id,
                        BriefDecision.brief_id == brief.id,
                        BriefDecision.brief_version_id == version.id,
                        BriefDecision.stage_key == stage.key,
                        BriefDecision.decision == DecisionKind.APPROVE.value,
                    )
                )
                or 0
            )
            stage_complete = prior_approvals + 1 >= stage.required_approvals
            final_stage = brief.approval_stage_index == len(stages) - 1
            event = (
                BriefEvent.APPROVE_FINAL
                if stage_complete and final_stage
                else BriefEvent.APPROVE_STAGE
            )
            new_status = transition_brief_status(old_status, event)
            if stage_complete and not final_stage:
                brief.approval_stage_index += 1
            brief.status = new_status.value
        decision = BriefDecision(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            brief_id=brief.id,
            brief_version_id=version.id,
            stage_key=stage.key,
            decision=data.decision.value,
            from_status=old_status.value,
            to_status=brief.status,
            comment=data.comment,
            decided_by=principal.subject_id,
        )
        self.session.add(decision)
        await self.repo.flush("brief_decision")
        await self._record_change(
            principal,
            action=(
                "planning.brief.approved"
                if brief.status == BriefStatus.APPROVED.value
                else "planning.brief.decision_recorded"
            ),
            aggregate_type="content_brief",
            aggregate_id=brief.id,
            details={
                "brief_version_id": str(version.id),
                "snapshot_hash": version.snapshot_hash,
                "stage_key": stage.key,
                "decision": data.decision.value,
                "status": brief.status,
            },
        )
        return _brief_read(brief, version)

    async def upsert_assignments(
        self, principal: Principal, brief_id: UUID, data: AssignmentUpsert
    ) -> list[PlanningAssignment]:
        brief = await self.repo.brief(principal.workspace_id, brief_id, for_update=True)
        _assert_lock(
            "content_brief", data.expected_brief_lock_version, brief.lock_version
        )
        await self.memberships.require_active(
            principal.workspace_id, {item.user_id for item in data.assignments}
        )
        assignments = await self._replace_assignments(
            principal, brief, data.assignments
        )
        # Assignment changes are part of the brief aggregate and advance its lock.
        brief.approval_stage_index = brief.approval_stage_index
        brief.lock_version += 1
        await self.repo.flush("brief_assignments")
        await self._record_change(
            principal,
            action="planning.brief.assignments_replaced",
            aggregate_type="content_brief",
            aggregate_id=brief.id,
            details={"assignment_ids": [str(item.id) for item in assignments]},
        )
        return assignments

    async def list_assignments(
        self, principal: Principal, brief_id: UUID
    ) -> list[PlanningAssignment]:
        await self.repo.brief(principal.workspace_id, brief_id)
        return list(
            await self.session.scalars(
                select(PlanningAssignment)
                .where(
                    PlanningAssignment.workspace_id == principal.workspace_id,
                    PlanningAssignment.brief_id == brief_id,
                    PlanningAssignment.status != AssignmentStatus.CANCELLED.value,
                )
                .order_by(PlanningAssignment.stage, PlanningAssignment.due_at.nulls_last())
            )
        )

    async def create_comment(
        self, principal: Principal, data: CommentCreate
    ) -> PlanningComment:
        await self._validate_comment_target(
            principal.workspace_id, data.target_type, data.target_id
        )
        if data.parent_comment_id is not None:
            parent = await self.repo.comment(
                principal.workspace_id, data.parent_comment_id
            )
            if parent.target_type != data.target_type or parent.target_id != data.target_id:
                raise AppError(
                    code="COMMENT_PARENT_TARGET_MISMATCH",
                    message="답글은 같은 대상의 코멘트에만 연결할 수 있습니다.",
                    status_code=422,
                )
        comment = PlanningComment(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            target_type=data.target_type,
            target_id=data.target_id,
            parent_comment_id=data.parent_comment_id,
            body=data.body,
            author_id=principal.subject_id,
            lock_version=1,
        )
        self.session.add(comment)
        await self.repo.flush("planning_comment")
        await self._record_change(
            principal,
            action="planning.comment.created",
            aggregate_type=data.target_type.lower(),
            aggregate_id=data.target_id,
            details={"comment_id": str(comment.id)},
        )
        return comment

    async def list_comments(
        self,
        principal: Principal,
        *,
        target_type: str,
        target_id: UUID,
    ) -> list[PlanningComment]:
        await self._validate_comment_target(
            principal.workspace_id, target_type, target_id
        )
        return list(
            await self.session.scalars(
                select(PlanningComment)
                .where(
                    PlanningComment.workspace_id == principal.workspace_id,
                    PlanningComment.target_type == target_type,
                    PlanningComment.target_id == target_id,
                )
                .order_by(PlanningComment.created_at, PlanningComment.id)
            )
        )

    async def resolve_comment(
        self, principal: Principal, comment_id: UUID, data: CommentResolve
    ) -> PlanningComment:
        comment = await self.repo.comment(
            principal.workspace_id, comment_id, for_update=True
        )
        _assert_lock("planning_comment", data.expected_lock_version, comment.lock_version)
        if data.resolved:
            comment.resolved_at = datetime.now(UTC)
            comment.resolved_by = principal.subject_id
        else:
            comment.resolved_at = None
            comment.resolved_by = None
        await self.repo.flush("planning_comment")
        await self._record_change(
            principal,
            action="planning.comment.resolution_changed",
            aggregate_type=comment.target_type.lower(),
            aggregate_id=comment.target_id,
            details={"comment_id": str(comment.id), "resolved": data.resolved},
        )
        return comment

    async def generation_input(
        self, principal: Principal, brief_id: UUID
    ) -> dict[str, Any]:
        brief = await self.repo.brief(principal.workspace_id, brief_id)
        if brief.status not in {
            BriefStatus.APPROVED.value,
            BriefStatus.SCHEDULED.value,
        }:
            raise AppError(
                code="BRIEF_NOT_APPROVED",
                message="승인된 브리프만 콘텐츠 생성 입력으로 사용할 수 있습니다.",
                status_code=409,
            )
        version = await self._brief_version(
            principal.workspace_id, brief, brief.current_version_id
        )
        if version is None:
            raise AppError(
                code="BRIEF_VERSION_REQUIRED",
                message="승인된 브리프 버전이 없습니다.",
                status_code=409,
            )
        approval = await self.session.scalar(
            select(BriefDecision)
            .where(
                BriefDecision.workspace_id == principal.workspace_id,
                BriefDecision.brief_id == brief.id,
                BriefDecision.brief_version_id == version.id,
                BriefDecision.to_status == BriefStatus.APPROVED.value,
                BriefDecision.decision == DecisionKind.APPROVE.value,
            )
            .order_by(BriefDecision.decided_at.desc())
        )
        if approval is None:
            raise AppError(
                code="BRIEF_APPROVAL_PROOF_MISSING",
                message="현재 버전의 최종 승인 기록이 없습니다.",
                status_code=409,
            )
        await self._validate_brief_facts(version, require_unexpired=True)
        return {
            "brief_id": brief.id,
            "brief_version_id": version.id,
            "version_number": version.version_number,
            "snapshot_hash": version.snapshot_hash,
            "reference_snapshot_hash": version.reference_snapshot_hash,
            "generation_policy_hash": version.generation_policy_hash,
            "approved_at": approval.decided_at,
            "payload": _brief_generation_payload(version),
        }

    async def create_calendar_entry(
        self, principal: Principal, data: CalendarEntryCreate
    ) -> CalendarEntry:
        return await self._create_calendar_entry(
            principal,
            data,
            recurrence_id=None,
            allow_repeated_brief=False,
            record_change=True,
        )

    async def list_calendar_entries(
        self,
        principal: Principal,
        *,
        starts_at: datetime,
        ends_at: datetime,
        campaign_id: UUID | None,
        channel: str | None,
        include_cancelled: bool,
    ) -> list[CalendarEntry]:
        query = select(CalendarEntry).where(
            CalendarEntry.workspace_id == principal.workspace_id,
            CalendarEntry.scheduled_at >= starts_at.astimezone(UTC),
            CalendarEntry.scheduled_at < ends_at.astimezone(UTC),
        )
        if campaign_id is not None:
            query = query.where(CalendarEntry.campaign_id == campaign_id)
        if channel is not None:
            query = query.where(CalendarEntry.channel == channel)
        if not include_cancelled:
            query = query.where(
                CalendarEntry.status != CalendarEntryStatus.CANCELLED.value
            )
        return list(
            await self.session.scalars(
                query.order_by(CalendarEntry.scheduled_at, CalendarEntry.id)
            )
        )

    async def move_calendar_entry(
        self, principal: Principal, entry_id: UUID, data: CalendarEntryMove
    ) -> CalendarEntry:
        entry = await self.repo.calendar_entry(
            principal.workspace_id, entry_id, for_update=True
        )
        _assert_lock("calendar_entry", data.expected_lock_version, entry.lock_version)
        if entry.status in {
            CalendarEntryStatus.CANCELLED.value,
            CalendarEntryStatus.PUBLISHED.value,
        }:
            raise _inactive_error("캘린더 항목")
        policy = await self.references.workspace_policy(principal.workspace_id)
        scheduled, warnings = await self._resolve_calendar_time(
            workspace_id=principal.workspace_id,
            channel=entry.channel,
            requested=data.scheduled_at,
            display_timezone=data.timezone,
            resolution=data.conflict_resolution,
            policy=policy,
            excluding_id=entry.id,
        )
        entry.scheduled_at = scheduled.astimezone(UTC)
        entry.timezone = data.timezone
        entry.conflict_warnings = warnings
        await self.repo.flush("calendar_entry")
        await self._record_change(
            principal,
            action="planning.calendar.moved",
            aggregate_type="calendar_entry",
            aggregate_id=entry.id,
            details={
                "scheduled_at": entry.scheduled_at.isoformat(),
                "workspace_timezone": policy.timezone,
                "warnings": warnings,
            },
        )
        return entry

    async def cancel_calendar_entry(
        self,
        principal: Principal,
        entry_id: UUID,
        *,
        expected_lock_version: int,
    ) -> CalendarEntry:
        entry = await self.repo.calendar_entry(
            principal.workspace_id, entry_id, for_update=True
        )
        _assert_lock("calendar_entry", expected_lock_version, entry.lock_version)
        entry.status = CalendarEntryStatus.CANCELLED.value
        if entry.brief_id is not None:
            brief = await self.repo.brief(
                principal.workspace_id, entry.brief_id, for_update=True
            )
            if brief.status == BriefStatus.SCHEDULED.value:
                remaining = await self.session.scalar(
                    select(func.count(CalendarEntry.id)).where(
                        CalendarEntry.workspace_id == principal.workspace_id,
                        CalendarEntry.brief_id == brief.id,
                        CalendarEntry.id != entry.id,
                        CalendarEntry.status != CalendarEntryStatus.CANCELLED.value,
                    )
                )
                if int(remaining or 0) == 0:
                    brief.status = transition_brief_status(
                        BriefStatus.SCHEDULED, BriefEvent.UNSCHEDULE
                    ).value
        await self.repo.flush("calendar_entry")
        await self._record_change(
            principal,
            action="planning.calendar.cancelled",
            aggregate_type="calendar_entry",
            aggregate_id=entry.id,
            details={},
        )
        return entry

    async def create_recurrence(
        self,
        principal: Principal,
        data: RecurrenceCreate,
        *,
        max_occurrences: int = 366,
    ) -> tuple[CalendarRecurrence, list[CalendarEntry]]:
        if data.campaign_id is not None:
            await self.repo.campaign(principal.workspace_id, data.campaign_id)
        if data.brief_id is not None:
            brief = await self.repo.brief(principal.workspace_id, data.brief_id)
            if brief.status not in {
                BriefStatus.APPROVED.value,
                BriefStatus.SCHEDULED.value,
            }:
                raise AppError(
                    code="RECURRENCE_BRIEF_NOT_APPROVED",
                    message="승인된 브리프만 반복 일정에 연결할 수 있습니다.",
                    status_code=409,
                )
            if data.campaign_id is not None and brief.campaign_id not in {
                None,
                data.campaign_id,
            }:
                raise AppError(
                    code="RECURRENCE_CAMPAIGN_MISMATCH",
                    message="반복 일정과 브리프의 캠페인이 일치하지 않습니다.",
                    status_code=422,
                )
        _zone(data.timezone)
        recurrence = CalendarRecurrence(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            campaign_id=data.campaign_id,
            brief_id=data.brief_id,
            frequency=data.frequency.value,
            interval=data.interval,
            timezone=data.timezone,
            starts_at=data.starts_at.astimezone(UTC),
            ends_at=data.ends_at.astimezone(UTC) if data.ends_at else None,
            recurrence_config=data.recurrence_config,
            exception_dates=sorted({item.isoformat() for item in data.exception_dates}),
            active=True,
            created_by=principal.subject_id,
            lock_version=1,
        )
        self.session.add(recurrence)
        await self.repo.flush("calendar_recurrence")
        occurrence_times = _recurrence_times(data, max_occurrences=max_occurrences)
        entries: list[CalendarEntry] = []
        for scheduled_at in occurrence_times:
            entry = await self._create_calendar_entry(
                principal,
                CalendarEntryCreate(
                    campaign_id=data.campaign_id,
                    brief_id=data.brief_id,
                    title=data.title,
                    channel=data.channel,
                    language=data.language,
                    timezone=data.timezone,
                    scheduled_at=scheduled_at,
                    conflict_resolution=data.conflict_resolution,
                ),
                recurrence_id=recurrence.id,
                allow_repeated_brief=True,
                record_change=False,
            )
            entries.append(entry)
        await self._record_change(
            principal,
            action="planning.calendar.recurrence_created",
            aggregate_type="calendar_recurrence",
            aggregate_id=recurrence.id,
            details={
                "occurrence_count": len(entries),
                "entry_ids": [str(item.id) for item in entries],
            },
        )
        return recurrence, entries

    async def create_monthly_proposal(
        self, principal: Principal, data: MonthlyPlanProposalCreate
    ) -> MonthlyPlanProposal:
        campaign: Campaign | None = None
        if data.campaign_id is not None:
            campaign = await self.repo.campaign(principal.workspace_id, data.campaign_id)
            if data.month > campaign.end_date.replace(day=1) or data.month < campaign.start_date.replace(day=1):
                raise AppError(
                    code="PROPOSAL_OUTSIDE_CAMPAIGN",
                    message="월간 계획안의 월이 캠페인 기간 밖에 있습니다.",
                    status_code=422,
                )
        _zone(data.timezone)
        policy = await self.references.workspace_policy(principal.workspace_id)
        seed_snapshots: list[dict[str, Any]] = []
        for seed in data.seeds:
            resolved = await self.references.resolve(
                principal.workspace_id,
                ReferenceSelection(
                    primary_keyword_id=seed.primary_keyword_id,
                    keyword_cluster_id=seed.keyword_cluster_id,
                    primary_keyword_text=seed.topic,
                ),
            )
            seed_snapshots.append(
                {
                    "seed": seed.model_dump(mode="json"),
                    "reference_snapshot": resolved.snapshot,
                    "reference_snapshot_hash": resolved.snapshot_hash,
                }
            )
        generated = await self.monthly_generator.generate(
            data, generation_policy=policy.generation_policy
        )
        items = [MonthlyPlanItem.model_validate(item) for item in generated.items]
        await self._validate_monthly_items(
            principal.workspace_id, data.month, data.timezone, items
        )
        item_payload = [item.model_dump(mode="json") for item in items]
        proposal_hash = _proposal_hash(item_payload, 1)
        proposal = MonthlyPlanProposal(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            campaign_id=data.campaign_id,
            month=data.month,
            goal=data.goal,
            requested_budget=_budget_limits(data.requested_budget),
            seed_snapshot={
                "seeds": seed_snapshots,
                "generation_metadata": generated.generation_metadata,
                "campaign_brand_snapshot": campaign.brand_snapshot if campaign else None,
                "timezone": data.timezone,
            },
            proposed_items=item_payload,
            provider=generated.provider,
            provider_version=generated.provider_version,
            generation_policy_snapshot=policy.generation_policy,
            generation_policy_hash=policy.generation_policy_hash,
            approval_policy_snapshot=policy.approval_policy,
            approval_policy_hash=policy.approval_policy_hash,
            proposal_version=1,
            proposal_hash=proposal_hash,
            status=ProposalStatus.PENDING_APPROVAL.value,
            created_by=principal.subject_id,
            lock_version=1,
        )
        self.session.add(proposal)
        await self.repo.flush("monthly_plan_proposal")
        await self._record_change(
            principal,
            action="planning.monthly_proposal.created",
            aggregate_type="monthly_plan_proposal",
            aggregate_id=proposal.id,
            details={
                "status": ProposalStatus.PENDING_APPROVAL.value,
                "proposal_version": 1,
                "proposal_hash": proposal_hash,
                "materialized_calendar_count": 0,
                "policy_hashes": {
                    "generation": policy.generation_policy_hash,
                    "approval": policy.approval_policy_hash,
                },
            },
        )
        return proposal

    async def list_monthly_proposals(
        self,
        principal: Principal,
        *,
        month: date | None,
        status: ProposalStatus | None,
        limit: int,
        offset: int,
    ) -> list[MonthlyPlanProposal]:
        query = select(MonthlyPlanProposal).where(
            MonthlyPlanProposal.workspace_id == principal.workspace_id
        )
        if month is not None:
            query = query.where(MonthlyPlanProposal.month == month)
        if status is not None:
            query = query.where(MonthlyPlanProposal.status == status.value)
        return list(
            await self.session.scalars(
                query.order_by(
                    MonthlyPlanProposal.month.desc(),
                    MonthlyPlanProposal.created_at.desc(),
                )
                .limit(limit)
                .offset(offset)
            )
        )

    async def get_monthly_proposal(
        self, principal: Principal, proposal_id: UUID
    ) -> MonthlyPlanProposal:
        return await self.repo.proposal(principal.workspace_id, proposal_id)

    async def revise_monthly_proposal(
        self,
        principal: Principal,
        proposal_id: UUID,
        data: MonthlyPlanProposalRevise,
    ) -> MonthlyPlanProposal:
        proposal = await self.repo.proposal(
            principal.workspace_id, proposal_id, for_update=True
        )
        _assert_lock("monthly_plan_proposal", data.expected_lock_version, proposal.lock_version)
        _assert_hash(
            "monthly_plan_proposal", data.expected_proposal_hash, proposal.proposal_hash
        )
        if proposal.status != ProposalStatus.PENDING_APPROVAL.value:
            raise AppError(
                code="PROPOSAL_NOT_PENDING",
                message="승인 대기 중인 월간 계획안만 수정할 수 있습니다.",
                status_code=409,
            )
        await self._validate_monthly_items(
            principal.workspace_id,
            proposal.month,
            proposal.seed_snapshot.get("timezone", "") or _proposal_timezone(data.proposed_items),
            data.proposed_items,
        )
        proposal.proposal_version += 1
        proposal.proposed_items = [
            item.model_dump(mode="json") for item in data.proposed_items
        ]
        proposal.proposal_hash = _proposal_hash(
            proposal.proposed_items, proposal.proposal_version
        )
        await self.repo.flush("monthly_plan_proposal")
        await self._record_change(
            principal,
            action="planning.monthly_proposal.revised",
            aggregate_type="monthly_plan_proposal",
            aggregate_id=proposal.id,
            details={
                "proposal_version": proposal.proposal_version,
                "proposal_hash": proposal.proposal_hash,
            },
        )
        return proposal

    async def approve_monthly_proposal(
        self,
        principal: Principal,
        proposal_id: UUID,
        data: MonthlyPlanDecision,
    ) -> ProposalApprovalResult:
        proposal = await self.repo.proposal(
            principal.workspace_id, proposal_id, for_update=True
        )
        _assert_lock("monthly_plan_proposal", data.expected_lock_version, proposal.lock_version)
        _assert_proposal_identity(proposal, data)
        if proposal.status != ProposalStatus.PENDING_APPROVAL.value:
            raise AppError(
                code="PROPOSAL_NOT_PENDING",
                message="승인 대기 중인 월간 계획안만 승인할 수 있습니다.",
                status_code=409,
            )
        items = [MonthlyPlanItem.model_validate(item) for item in proposal.proposed_items]
        assignees = {user_id for item in items for user_id in item.assignee_user_ids}
        await self.memberships.require_active(principal.workspace_id, assignees)
        entries: list[CalendarEntry] = []
        for item in items:
            idea_id: UUID | None = None
            if item.brief_id is None:
                idea = await self._find_or_create_proposal_idea(
                    principal, proposal, item
                )
                idea_id = idea.id
            entry = await self._create_calendar_entry(
                principal,
                CalendarEntryCreate(
                    campaign_id=proposal.campaign_id,
                    idea_id=idea_id,
                    brief_id=item.brief_id,
                    title=item.title,
                    channel=item.channel,
                    language=item.language,
                    timezone=item.timezone,
                    scheduled_at=item.scheduled_at,
                    conflict_resolution=data.conflict_resolution,
                ),
                recurrence_id=None,
                allow_repeated_brief=False,
                record_change=False,
            )
            entries.append(entry)
            if item.brief_id is not None and item.assignee_user_ids:
                brief = await self.repo.brief(
                    principal.workspace_id, item.brief_id, for_update=True
                )
                await self._merge_write_assignments(
                    principal, brief, item.assignee_user_ids
                )
        now = datetime.now(UTC)
        proposal.status = ProposalStatus.APPROVED.value
        proposal.approved_by = principal.subject_id
        proposal.approved_at = now
        proposal.approved_version = proposal.proposal_version
        proposal.approved_hash = proposal.proposal_hash
        await self.repo.flush("monthly_plan_approval")
        await self._record_change(
            principal,
            action="planning.monthly_proposal.approved",
            aggregate_type="monthly_plan_proposal",
            aggregate_id=proposal.id,
            details={
                "approved_by": str(principal.subject_id),
                "approved_at": now.isoformat(),
                "approved_version": proposal.approved_version,
                "approved_hash": proposal.approved_hash,
                "calendar_entry_ids": [str(item.id) for item in entries],
            },
        )
        return ProposalApprovalResult(proposal=proposal, calendar_entries=entries)

    async def reject_monthly_proposal(
        self,
        principal: Principal,
        proposal_id: UUID,
        data: MonthlyPlanDecision,
    ) -> MonthlyPlanProposal:
        proposal = await self.repo.proposal(
            principal.workspace_id, proposal_id, for_update=True
        )
        _assert_lock("monthly_plan_proposal", data.expected_lock_version, proposal.lock_version)
        _assert_proposal_identity(proposal, data)
        if proposal.status != ProposalStatus.PENDING_APPROVAL.value:
            raise AppError(
                code="PROPOSAL_NOT_PENDING",
                message="승인 대기 중인 월간 계획안만 반려할 수 있습니다.",
                status_code=409,
            )
        if not data.comment:
            raise AppError(
                code="PROPOSAL_REJECTION_REASON_REQUIRED",
                message="월간 계획안을 반려할 때는 사유가 필요합니다.",
                status_code=422,
            )
        proposal.status = ProposalStatus.REJECTED.value
        proposal.rejected_by = principal.subject_id
        proposal.rejected_at = datetime.now(UTC)
        proposal.rejection_reason = data.comment
        await self.repo.flush("monthly_plan_rejection")
        await self._record_change(
            principal,
            action="planning.monthly_proposal.rejected",
            aggregate_type="monthly_plan_proposal",
            aggregate_id=proposal.id,
            details={
                "proposal_version": proposal.proposal_version,
                "proposal_hash": proposal.proposal_hash,
                "reason": data.comment,
            },
        )
        return proposal

    async def export_calendar_csv(
        self, principal: Principal, data: CalendarExportQuery
    ) -> str:
        entries = await self.list_calendar_entries(
            principal,
            starts_at=data.starts_at,
            ends_at=data.ends_at,
            campaign_id=data.campaign_id,
            channel=data.channel,
            include_cancelled=False,
        )
        zone = _zone(data.timezone)
        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "id",
                "title",
                "channel",
                "language",
                "scheduled_at",
                "timezone",
                "status",
                "campaign_id",
                "brief_id",
                "brief_version_id",
                "brief_snapshot_hash",
            ]
        )
        for entry in entries:
            writer.writerow(
                [
                    entry.id,
                    entry.title_snapshot,
                    entry.channel,
                    entry.language,
                    entry.scheduled_at.astimezone(zone).isoformat(),
                    data.timezone,
                    entry.status,
                    entry.campaign_id or "",
                    entry.brief_id or "",
                    entry.brief_version_id or "",
                    entry.brief_snapshot_hash or "",
                ]
            )
        return buffer.getvalue()

    async def export_calendar_ics(
        self, principal: Principal, data: CalendarExportQuery
    ) -> str:
        entries = await self.list_calendar_entries(
            principal,
            starts_at=data.starts_at,
            ends_at=data.ends_at,
            campaign_id=data.campaign_id,
            channel=data.channel,
            include_cancelled=False,
        )
        zone = _zone(data.timezone)
        lines = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//BlogOps//Content Planning//KO",
            "CALSCALE:GREGORIAN",
            f"X-WR-TIMEZONE:{_ics_escape(data.timezone)}",
        ]
        for entry in entries:
            local = entry.scheduled_at.astimezone(zone)
            lines.extend(
                [
                    "BEGIN:VEVENT",
                    f"UID:{entry.id}@blogops",
                    f"DTSTAMP:{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}",
                    (
                        f"DTSTART;TZID={_ics_escape(data.timezone)}:"
                        f"{local.strftime('%Y%m%dT%H%M%S')}"
                    ),
                    f"SUMMARY:{_ics_escape(entry.title_snapshot)}",
                    f"CATEGORIES:{_ics_escape(entry.channel)}",
                    f"X-BLOGOPS-SNAPSHOT-HASH:{entry.brief_snapshot_hash or ''}",
                    "END:VEVENT",
                ]
            )
        lines.append("END:VCALENDAR")
        return "\r\n".join(lines) + "\r\n"

    async def _scope(self, workspace_id: UUID) -> None:
        await apply_workspace_scope(self.session, workspace_id)

    async def _record_change(
        self,
        principal: Principal,
        *,
        action: str,
        aggregate_type: str,
        aggregate_id: UUID,
        details: dict[str, Any],
    ) -> None:
        await append_audit_log(
            self.session,
            workspace_id=principal.workspace_id,
            actor_id=principal.subject_id,
            action=action,
            target_type=aggregate_type,
            target_id=str(aggregate_id),
            details=details,
        )
        await add_outbox_event(
            self.session,
            workspace_id=principal.workspace_id,
            aggregate_type=aggregate_type,
            aggregate_id=str(aggregate_id),
            event_type=action,
            schema_version=OUTBOX_SCHEMA_VERSION,
            payload={
                "workspace_id": str(principal.workspace_id),
                "actor_id": str(principal.subject_id),
                "aggregate_id": str(aggregate_id),
                **details,
            },
        )

    async def _assert_not_descendant(
        self, moving_node_id: UUID, proposed_parent: TopicNode
    ) -> None:
        cursor: TopicNode | None = proposed_parent
        visited: set[UUID] = set()
        while cursor is not None:
            if cursor.id == moving_node_id:
                raise _topic_cycle_error()
            if cursor.id in visited:
                raise _topic_cycle_error()
            visited.add(cursor.id)
            if cursor.parent_id is None:
                return
            cursor = await self.repo.topic_node(cursor.workspace_id, cursor.parent_id)
        return

    async def _approval_stages(
        self,
        workspace_id: UUID,
        payload: BriefPayload,
        policy: WorkspacePolicySnapshot,
    ) -> list[ApprovalStage]:
        if payload.approval_stages:
            stages = payload.approval_stages
        else:
            raw_stages = policy.approval_policy.get(
                "stages", policy.approval_policy.get("approval_stages", [])
            )
            stages = []
            if isinstance(raw_stages, list):
                for index, raw in enumerate(raw_stages):
                    if not isinstance(raw, dict):
                        continue
                    normalized = {
                        "key": raw.get("key") or f"stage-{index + 1}",
                        "name": raw.get("name") or raw.get("label") or f"승인 {index + 1}",
                        "required_approvals": raw.get(
                            "required_approvals", raw.get("required", 1)
                        ),
                        "approver_user_ids": raw.get(
                            "approver_user_ids", raw.get("approvers", [])
                        ),
                        "require_mfa": raw.get("require_mfa", False),
                    }
                    try:
                        stages.append(ApprovalStage.model_validate(normalized))
                    except Exception as exc:
                        raise AppError(
                            code="WORKSPACE_APPROVAL_POLICY_INVALID",
                            message="워크스페이스 승인 정책의 단계 구성이 올바르지 않습니다.",
                            status_code=409,
                            fields=[
                                {
                                    "path": f"approval_policy.stages.{index}",
                                    "reason": type(exc).__name__,
                                }
                            ],
                        ) from exc
            if not stages:
                stages = [
                    ApprovalStage(
                        key="final",
                        name="최종 승인",
                        required_approvals=1,
                        approver_user_ids=[],
                    )
                ]
        approver_ids = {
            user_id for stage in stages for user_id in stage.approver_user_ids
        }
        await self.memberships.require_active(workspace_id, approver_ids)
        return stages

    def _new_brief_version(
        self,
        principal: Principal,
        *,
        brief: ContentBrief,
        version_number: int,
        payload: BriefPayload,
        resolved: ResolvedPlanningReferences,
        policy: WorkspacePolicySnapshot,
        approval_stages: list[ApprovalStage],
    ) -> BriefVersion:
        required_facts = [
            item.model_dump(mode="json") for item in payload.required_facts
        ]
        banned_claims = _combined_banned_claims(payload, resolved)
        stage_payload = [item.model_dump(mode="json") for item in approval_stages]
        content = {
            "version_number": version_number,
            "template_ref": payload.template_ref,
            "title": payload.title,
            "objective": payload.objective,
            "audience_snapshot": resolved.audience_snapshot,
            "search_intent": payload.search_intent.value,
            "journey_stage": payload.journey_stage.value,
            "keyword_snapshot": resolved.keyword_snapshot,
            "questions": payload.questions,
            "knowledge_source_snapshot": resolved.knowledge_source_snapshot,
            "competitor_gap_summary": payload.competitor_gap_summary,
            "required_facts": required_facts,
            "banned_claims": banned_claims,
            "outline": [item.model_dump(mode="json") for item in payload.outline],
            "cta_plan": [item.model_dump(mode="json") for item in payload.cta_plan],
            "internal_link_plan": [
                item.model_dump(mode="json") for item in payload.internal_link_plan
            ],
            "image_plan": [
                item.model_dump(mode="json") for item in payload.image_plan
            ],
            "approval_stages": stage_payload,
            "channel": payload.channel,
            "language": payload.language,
            "tone": payload.tone,
            "target_length_min": payload.target_length_min,
            "target_length_max": payload.target_length_max,
            "disclosures": payload.disclosures,
            "reference_snapshot": resolved.snapshot,
            "reference_snapshot_hash": resolved.snapshot_hash,
            "generation_policy_snapshot": policy.generation_policy,
            "generation_policy_hash": policy.generation_policy_hash,
            "approval_policy_snapshot": policy.approval_policy,
            "approval_policy_hash": policy.approval_policy_hash,
        }
        return BriefVersion(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            brief_id=brief.id,
            snapshot_hash=canonical_json_hash(content),
            created_by=principal.subject_id,
            **content,
        )

    async def _replace_assignments(
        self,
        principal: Principal,
        brief: ContentBrief,
        requested: list[AssignmentInput],
    ) -> list[PlanningAssignment]:
        identities = [(item.stage.value, item.user_id) for item in requested]
        if len(identities) != len(set(identities)):
            raise AppError(
                code="ASSIGNMENT_DUPLICATE",
                message="같은 단계와 사용자의 담당자 지정이 중복되었습니다.",
                status_code=422,
            )
        existing = list(
            await self.session.scalars(
                select(PlanningAssignment)
                .where(
                    PlanningAssignment.workspace_id == principal.workspace_id,
                    PlanningAssignment.brief_id == brief.id,
                )
                .with_for_update()
            )
        )
        by_identity = {(item.stage, item.user_id): item for item in existing}
        requested_identities = set(identities)
        result: list[PlanningAssignment] = []
        for assignment in existing:
            if (assignment.stage, assignment.user_id) not in requested_identities:
                assignment.status = AssignmentStatus.CANCELLED.value
        for item in requested:
            identity = (item.stage.value, item.user_id)
            assignment = by_identity.get(identity)
            if assignment is None:
                assignment = PlanningAssignment(
                    id=uuid4(),
                    workspace_id=principal.workspace_id,
                    brief_id=brief.id,
                    stage=item.stage.value,
                    user_id=item.user_id,
                    assigned_by=principal.subject_id,
                    lock_version=1,
                )
                self.session.add(assignment)
            assignment.due_at = item.due_at.astimezone(UTC) if item.due_at else None
            assignment.sla_seconds = item.sla_seconds
            assignment.status = AssignmentStatus.PENDING.value
            assignment.completed_at = None
            result.append(assignment)
        return result

    async def _merge_write_assignments(
        self,
        principal: Principal,
        brief: ContentBrief,
        user_ids: list[UUID],
    ) -> None:
        existing = list(
            await self.session.scalars(
                select(PlanningAssignment)
                .where(
                    PlanningAssignment.workspace_id == principal.workspace_id,
                    PlanningAssignment.brief_id == brief.id,
                    PlanningAssignment.stage == "WRITE",
                    PlanningAssignment.user_id.in_(set(user_ids)),
                )
                .with_for_update()
            )
        )
        by_user = {item.user_id: item for item in existing}
        changed = False
        for user_id in dict.fromkeys(user_ids):
            assignment = by_user.get(user_id)
            if assignment is None:
                assignment = PlanningAssignment(
                    id=uuid4(),
                    workspace_id=principal.workspace_id,
                    brief_id=brief.id,
                    stage="WRITE",
                    user_id=user_id,
                    status=AssignmentStatus.PENDING.value,
                    assigned_by=principal.subject_id,
                    lock_version=1,
                )
                self.session.add(assignment)
                changed = True
            elif assignment.status == AssignmentStatus.CANCELLED.value:
                assignment.status = AssignmentStatus.PENDING.value
                assignment.assigned_by = principal.subject_id
                changed = True
        if changed and brief.status == BriefStatus.SCHEDULED.value:
            brief.lock_version += 1

    async def _brief_version(
        self,
        workspace_id: UUID,
        brief: ContentBrief,
        version_id: UUID | None,
    ) -> BriefVersion | None:
        if version_id is None:
            return None
        version = await self.session.scalar(
            select(BriefVersion).where(
                BriefVersion.workspace_id == workspace_id,
                BriefVersion.id == version_id,
                BriefVersion.brief_id == brief.id,
            )
        )
        if version is None:
            raise AppError(
                code="BRIEF_VERSION_NOT_FOUND",
                message="현재 브리프 버전을 찾을 수 없습니다.",
                status_code=409,
            )
        return version

    async def _versions_by_ids(
        self, workspace_id: UUID, version_ids: set[UUID]
    ) -> dict[UUID, BriefVersion]:
        if not version_ids:
            return {}
        versions = list(
            await self.session.scalars(
                select(BriefVersion).where(
                    BriefVersion.workspace_id == workspace_id,
                    BriefVersion.id.in_(version_ids),
                )
            )
        )
        return {item.id: item for item in versions}

    async def _validate_brief_facts(
        self, version: BriefVersion, *, require_unexpired: bool
    ) -> None:
        if not require_unexpired:
            return
        now = datetime.now(UTC)
        expirations = _snapshot_expirations(version)
        expired = [item for item in expirations if item[1] <= now]
        if expired:
            raise AppError(
                code="BRIEF_REQUIRED_FACT_EXPIRED",
                message="필수 사실 또는 가격 스냅샷이 만료되어 새 브리프 버전이 필요합니다.",
                status_code=409,
                fields=[
                    {"path": path, "reason": expires_at.isoformat()}
                    for path, expires_at in expired
                ],
            )

    async def _validate_comment_target(
        self, workspace_id: UUID, target_type: str, target_id: UUID
    ) -> None:
        if target_type == "BRIEF":
            await self.repo.brief(workspace_id, target_id)
        elif target_type == "TOPIC_NODE":
            await self.repo.topic_node(workspace_id, target_id)
        elif target_type == "CALENDAR_ENTRY":
            await self.repo.calendar_entry(workspace_id, target_id)
        else:
            raise AppError(
                code="COMMENT_TARGET_INVALID",
                message="지원하지 않는 코멘트 대상입니다.",
                status_code=422,
            )

    async def _resolve_calendar_time(
        self,
        *,
        workspace_id: UUID,
        channel: str,
        requested: datetime,
        display_timezone: str,
        resolution: CalendarConflictResolution,
        policy: WorkspacePolicySnapshot,
        excluding_id: UUID | None,
    ) -> tuple[datetime, list[dict[str, Any]]]:
        _zone(display_timezone)
        workspace_zone = _zone(policy.timezone)
        calendar_policy = policy.generation_policy.get("calendar", {})
        if not isinstance(calendar_policy, dict):
            calendar_policy = {}
        spacing_minutes = _bounded_int(
            calendar_policy.get(
                "minimum_spacing_minutes",
                policy.generation_policy.get("minimum_calendar_spacing_minutes", 60),
            ),
            default=60,
            minimum=0,
            maximum=43_200,
        )
        maximum_per_day = _bounded_int(
            calendar_policy.get(
                "maximum_per_channel_per_local_day",
                policy.generation_policy.get("maximum_calendar_items_per_day", 3),
            ),
            default=3,
            minimum=1,
            maximum=1_000,
        )
        requested_utc = requested.astimezone(UTC)
        # The auto-spread horizon is 31 days; this window captures every potentially
        # conflicting item while the repository still constrains the query by channel.
        existing_rows = await self.repo.calendar_entries_for_channel(
            workspace_id,
            channel,
            starts_at=requested_utc - timedelta(days=32),
            ends_at=requested_utc + timedelta(days=32),
            excluding_id=excluding_id,
        )
        slots = [
            CalendarSlot(entry_id=item.id, scheduled_at=item.scheduled_at)
            for item in existing_rows
        ]
        try:
            scheduled, conflict = resolve_calendar_slot(
                requested_utc,
                slots,
                resolution=resolution,
                minimum_spacing=timedelta(minutes=spacing_minutes),
                maximum_per_local_day=maximum_per_day,
                timezone=policy.timezone,
            )
        except ValueError as exc:
            raise AppError(
                code="CALENDAR_SLOT_UNAVAILABLE",
                message="자동 분산 범위 안에서 사용 가능한 캘린더 시간을 찾지 못했습니다.",
                status_code=409,
            ) from exc
        if conflict is not None and resolution is CalendarConflictResolution.BLOCK:
            raise AppError(
                code="CALENDAR_CONFLICT",
                message="같은 채널의 일정 간격 또는 현지 일일 한도를 위반합니다.",
                status_code=409,
                fields=[
                    {"path": "kind", "reason": conflict.kind},
                    {
                        "path": "conflicting_entry_ids",
                        "reason": ",".join(
                            str(item) for item in conflict.conflicting_entry_ids
                        ),
                    },
                    {
                        "path": "workspace_local_date",
                        "reason": requested_utc.astimezone(workspace_zone).date().isoformat(),
                    },
                ],
            )
        warnings: list[dict[str, Any]] = []
        if conflict is not None:
            warnings.append(
                {
                    "kind": conflict.kind,
                    "resolution": resolution.value,
                    "workspace_timezone": policy.timezone,
                    "requested_at": requested_utc.isoformat(),
                    "resolved_at": scheduled.astimezone(UTC).isoformat(),
                    "conflicting_entry_ids": [
                        str(item) for item in conflict.conflicting_entry_ids
                    ],
                    "day_count": conflict.day_count,
                }
            )
        return scheduled.astimezone(UTC), warnings

    async def _create_calendar_entry(
        self,
        principal: Principal,
        data: CalendarEntryCreate,
        *,
        recurrence_id: UUID | None,
        allow_repeated_brief: bool,
        record_change: bool,
    ) -> CalendarEntry:
        await self._scope(principal.workspace_id)
        campaign: Campaign | None = None
        idea: ContentIdea | None = None
        brief: ContentBrief | None = None
        version: BriefVersion | None = None
        campaign_id = data.campaign_id
        if campaign_id is not None:
            campaign = await self.repo.campaign(principal.workspace_id, campaign_id)
        if data.idea_id is not None:
            idea = await self.repo.idea(principal.workspace_id, data.idea_id)
            if campaign_id is None:
                campaign_id = idea.campaign_id
            elif idea.campaign_id not in {None, campaign_id}:
                raise AppError(
                    code="CALENDAR_IDEA_CAMPAIGN_MISMATCH",
                    message="캘린더 항목과 아이디어의 캠페인이 일치하지 않습니다.",
                    status_code=422,
                )
        if data.brief_id is not None:
            brief = await self.repo.brief(
                principal.workspace_id, data.brief_id, for_update=True
            )
            if brief.status not in {
                BriefStatus.APPROVED.value,
                BriefStatus.SCHEDULED.value,
            }:
                raise AppError(
                    code="CALENDAR_BRIEF_NOT_APPROVED",
                    message="승인된 브리프만 캘린더에 배치할 수 있습니다.",
                    status_code=409,
                )
            version = await self._brief_version(
                principal.workspace_id, brief, brief.current_version_id
            )
            if version is None:
                raise AppError(
                    code="BRIEF_VERSION_REQUIRED",
                    message="캘린더에 연결할 브리프 버전이 없습니다.",
                    status_code=409,
                )
            if data.channel != version.channel or data.language != version.language:
                raise AppError(
                    code="CALENDAR_BRIEF_CHANNEL_MISMATCH",
                    message="캘린더의 채널과 언어는 승인된 브리프 버전과 같아야 합니다.",
                    status_code=422,
                )
            if campaign_id is None:
                campaign_id = brief.campaign_id
            elif brief.campaign_id not in {None, campaign_id}:
                raise AppError(
                    code="CALENDAR_BRIEF_CAMPAIGN_MISMATCH",
                    message="캘린더 항목과 브리프의 캠페인이 일치하지 않습니다.",
                    status_code=422,
                )
            if not allow_repeated_brief:
                duplicate = await self.session.scalar(
                    select(CalendarEntry.id).where(
                        CalendarEntry.workspace_id == principal.workspace_id,
                        CalendarEntry.brief_id == brief.id,
                        CalendarEntry.status != CalendarEntryStatus.CANCELLED.value,
                    )
                )
                if duplicate is not None:
                    raise AppError(
                        code="CALENDAR_BRIEF_DUPLICATE",
                        message="이 브리프는 이미 활성 캘린더 항목에 배치되어 있습니다.",
                        status_code=409,
                    )
        if campaign is None and campaign_id is not None:
            campaign = await self.repo.campaign(principal.workspace_id, campaign_id)
        if campaign is not None and data.channel not in campaign.channels:
            raise AppError(
                code="CALENDAR_CAMPAIGN_CHANNEL_INVALID",
                message="캠페인에 허용되지 않은 채널입니다.",
                status_code=422,
            )
        if idea is not None and brief is not None and brief.idea_id not in {
            None,
            idea.id,
        }:
            raise AppError(
                code="CALENDAR_IDEA_BRIEF_MISMATCH",
                message="아이디어와 브리프 연결이 일치하지 않습니다.",
                status_code=422,
            )
        policy = await self.references.workspace_policy(principal.workspace_id)
        scheduled_at, warnings = await self._resolve_calendar_time(
            workspace_id=principal.workspace_id,
            channel=data.channel,
            requested=data.scheduled_at,
            display_timezone=data.timezone,
            resolution=data.conflict_resolution,
            policy=policy,
            excluding_id=None,
        )
        title = (
            version.title
            if version is not None
            else idea.title
            if idea is not None
            else data.title
        )
        assert title is not None
        entry = CalendarEntry(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            campaign_id=campaign_id,
            idea_id=data.idea_id,
            brief_id=data.brief_id,
            brief_version_id=version.id if version is not None else None,
            recurrence_id=recurrence_id,
            title_snapshot=title,
            brief_snapshot_hash=version.snapshot_hash if version is not None else None,
            channel=data.channel,
            language=data.language,
            timezone=data.timezone,
            scheduled_at=scheduled_at,
            due_at=data.due_at.astimezone(UTC) if data.due_at else None,
            status=CalendarEntryStatus.PLANNED.value,
            conflict_warnings=warnings,
            created_by=principal.subject_id,
            lock_version=1,
        )
        self.session.add(entry)
        if brief is not None and brief.status == BriefStatus.APPROVED.value:
            brief.status = transition_brief_status(
                BriefStatus.APPROVED, BriefEvent.SCHEDULE
            ).value
        await self.repo.flush("calendar_entry")
        if record_change:
            await self._record_change(
                principal,
                action="planning.calendar.created",
                aggregate_type="calendar_entry",
                aggregate_id=entry.id,
                details={
                    "brief_version_id": (
                        str(entry.brief_version_id) if entry.brief_version_id else None
                    ),
                    "brief_snapshot_hash": entry.brief_snapshot_hash,
                    "scheduled_at": entry.scheduled_at.isoformat(),
                    "workspace_timezone": policy.timezone,
                    "warnings": warnings,
                },
            )
        return entry

    async def _validate_monthly_items(
        self,
        workspace_id: UUID,
        month: date,
        _proposal_timezone: str,
        items: list[MonthlyPlanItem],
    ) -> None:
        duplicate_keys: set[str] = set()
        assignees: set[UUID] = set()
        for index, item in enumerate(items):
            zone = _zone(item.timezone)
            if item.scheduled_at.astimezone(zone).date().replace(day=1) != month:
                raise AppError(
                    code="PROPOSAL_ITEM_MONTH_MISMATCH",
                    message="제안 항목의 현지 일정이 계획안 월에 속하지 않습니다.",
                    status_code=422,
                    fields=[
                        {"path": f"proposed_items.{index}.scheduled_at", "reason": str(month)}
                    ],
                )
            key = idea_duplicate_key(
                title=item.title,
                intent=item.search_intent.value,
                keyword_cluster_id=item.keyword_cluster_id,
                semantic_group_key=item.semantic_group_key,
                primary_keyword=item.title,
            )
            if key in duplicate_keys:
                raise AppError(
                    code="PROPOSAL_ITEM_DUPLICATE",
                    message="월간 계획안에 의미적으로 중복된 항목이 있습니다.",
                    status_code=422,
                    fields=[
                        {"path": f"proposed_items.{index}", "reason": key}
                    ],
                )
            duplicate_keys.add(key)
            assignees.update(item.assignee_user_ids)
            if item.brief_id is not None:
                brief = await self.repo.brief(workspace_id, item.brief_id)
                if brief.status not in {
                    BriefStatus.APPROVED.value,
                    BriefStatus.SCHEDULED.value,
                }:
                    raise AppError(
                        code="PROPOSAL_BRIEF_NOT_APPROVED",
                        message="월간 계획에는 승인된 브리프만 넣을 수 있습니다.",
                        status_code=409,
                    )
        await self.memberships.require_active(workspace_id, assignees)

    async def _find_or_create_proposal_idea(
        self,
        principal: Principal,
        proposal: MonthlyPlanProposal,
        item: MonthlyPlanItem,
    ) -> ContentIdea:
        key = idea_duplicate_key(
            title=item.title,
            intent=item.search_intent.value,
            keyword_cluster_id=item.keyword_cluster_id,
            semantic_group_key=item.semantic_group_key,
            primary_keyword=item.title,
        )
        existing = await self.repo.existing_ideas_by_keys(
            principal.workspace_id, {key}
        )
        if key in existing:
            return existing[key]
        resolved = await self.references.resolve(
            principal.workspace_id,
            ReferenceSelection(
                primary_keyword_id=item.primary_keyword_id,
                keyword_cluster_id=item.keyword_cluster_id,
                primary_keyword_text=item.title,
            ),
        )
        idea = ContentIdea(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            campaign_id=proposal.campaign_id,
            title=item.title,
            primary_keyword_id=item.primary_keyword_id,
            keyword_cluster_id=item.keyword_cluster_id,
            search_intent=item.search_intent.value,
            journey_stage=item.journey_stage.value,
            recommended_cta={},
            source_signals=[
                {
                    "source": "MONTHLY_PLAN_PROPOSAL",
                    "proposal_id": str(proposal.id),
                    "proposal_version": proposal.proposal_version,
                    "proposal_hash": proposal.proposal_hash,
                }
            ],
            performance_signals={},
            reference_snapshot=resolved.snapshot,
            reference_snapshot_hash=resolved.snapshot_hash,
            duplicate_key=key,
            status=IdeaStatus.SUGGESTED.value,
            created_by=principal.subject_id,
            lock_version=1,
        )
        self.session.add(idea)
        await self.repo.flush("proposal_content_idea")
        return idea


def _budget_limits(items: list[Any]) -> dict[str, dict[str, str]]:
    return {
        item.category.value: {
            "amount": str(item.amount),
            "currency": item.currency,
        }
        for item in items
    }


def _campaign_policy_hashes(campaign: Campaign) -> dict[str, str]:
    return {
        "generation": campaign.generation_policy_hash,
        "approval": campaign.approval_policy_hash,
    }


def _assert_lock(resource: str, expected: int, actual: int) -> None:
    if expected != actual:
        raise AppError(
            code="OPTIMISTIC_LOCK_CONFLICT",
            message="다른 요청이 먼저 리소스를 변경했습니다. 최신 값을 다시 조회해 주세요.",
            status_code=409,
            fields=[
                {"path": "resource", "reason": resource},
                {"path": "expected_lock_version", "reason": str(expected)},
                {"path": "actual_lock_version", "reason": str(actual)},
            ],
        )


def _assert_hash(resource: str, expected: str, actual: str) -> None:
    if expected != actual:
        raise AppError(
            code="SNAPSHOT_HASH_CONFLICT",
            message="대상 스냅샷이 변경되었습니다. 최신 값을 다시 조회해 주세요.",
            status_code=409,
            fields=[
                {"path": "resource", "reason": resource},
                {"path": "expected_hash", "reason": expected},
                {"path": "actual_hash", "reason": actual},
            ],
        )


def _assert_proposal_identity(
    proposal: MonthlyPlanProposal, data: MonthlyPlanDecision
) -> None:
    if data.expected_proposal_version != proposal.proposal_version:
        raise AppError(
            code="PROPOSAL_VERSION_CONFLICT",
            message="월간 계획안 버전이 변경되었습니다.",
            status_code=409,
            fields=[
                {
                    "path": "expected_proposal_version",
                    "reason": str(data.expected_proposal_version),
                },
                {
                    "path": "actual_proposal_version",
                    "reason": str(proposal.proposal_version),
                },
            ],
        )
    _assert_hash(
        "monthly_plan_proposal",
        data.expected_proposal_hash,
        proposal.proposal_hash,
    )


def _null_error(field_name: str) -> AppError:
    return AppError(
        code="CAMPAIGN_FIELD_NULL",
        message="필수 캠페인 필드는 null로 변경할 수 없습니다.",
        status_code=422,
        fields=[{"path": field_name, "reason": "null"}],
    )


def _inactive_error(label: str) -> AppError:
    return AppError(
        code="PLANNING_RESOURCE_INACTIVE",
        message=f"비활성 상태의 {label}은(는) 변경할 수 없습니다.",
        status_code=409,
    )


def _topic_cycle_error() -> AppError:
    return AppError(
        code="TOPIC_TREE_CYCLE",
        message="토픽 트리에 순환 관계를 만들 수 없습니다.",
        status_code=422,
    )


def _transition_error(current: BriefStatus, event: BriefEvent) -> AppError:
    return AppError(
        code="BRIEF_TRANSITION_INVALID",
        message="현재 상태에서는 요청한 브리프 상태 변경을 수행할 수 없습니다.",
        status_code=409,
        fields=[
            {"path": "current_status", "reason": current.value},
            {"path": "event", "reason": event.value},
        ],
    )


def _brief_read(
    brief: ContentBrief, version: BriefVersion | None
) -> BriefRead:
    return BriefRead.model_validate(
        {
            "id": brief.id,
            "workspace_id": brief.workspace_id,
            "campaign_id": brief.campaign_id,
            "idea_id": brief.idea_id,
            "topic_node_id": brief.topic_node_id,
            "current_version_id": brief.current_version_id,
            "board_column_id": brief.board_column_id,
            "status": brief.status,
            "approval_stage_index": brief.approval_stage_index,
            "next_refresh_at": brief.next_refresh_at,
            "created_by": brief.created_by,
            "lock_version": brief.lock_version,
            "created_at": brief.created_at,
            "updated_at": brief.updated_at,
            "current_version": (
                BriefVersionRead.model_validate(version) if version is not None else None
            ),
        }
    )


def _combined_banned_claims(
    payload: BriefPayload, resolved: ResolvedPlanningReferences
) -> list[dict[str, Any]]:
    result = [item.model_dump(mode="json") for item in payload.banned_claims]
    brand = resolved.snapshot.get("brand")
    if isinstance(brand, dict):
        for field_name in ("banned_terms", "banned_claims", "banned_rules"):
            for item in brand.get(field_name, []) or []:
                result.append(
                    _normalize_banned_claim(
                        item,
                        source="BRAND",
                        reason=f"brand.{field_name}",
                    )
                )
    products = resolved.snapshot.get("products", [])
    if isinstance(products, list):
        for product in products:
            if not isinstance(product, dict):
                continue
            product_id = product.get("id", "unknown")
            for item in product.get("banned_claims", []) or []:
                result.append(
                    _normalize_banned_claim(
                        item,
                        source="PRODUCT",
                        reason=f"product:{product_id}",
                    )
                )
    unique: dict[str, dict[str, Any]] = {}
    for item in result:
        unique.setdefault(canonical_json_hash(item), item)
    return list(unique.values())


def _normalize_banned_claim(
    item: Any, *, source: str, reason: str
) -> dict[str, Any]:
    if isinstance(item, dict):
        pattern = item.get("pattern") or item.get("claim") or item.get("term")
        return {
            "pattern": str(pattern or item),
            "reason": str(item.get("reason") or reason),
            "severity": str(item.get("severity") or "BLOCK"),
            "source": source,
            "source_payload": item,
        }
    return {
        "pattern": str(item),
        "reason": reason,
        "severity": "BLOCK",
        "source": source,
    }


def _brief_generation_payload(version: BriefVersion) -> dict[str, Any]:
    return {
        "title": version.title,
        "objective": version.objective,
        "audience": version.audience_snapshot,
        "search_intent": version.search_intent,
        "journey_stage": version.journey_stage,
        "keywords": version.keyword_snapshot,
        "questions": version.questions,
        "knowledge_sources": version.knowledge_source_snapshot,
        "competitor_gap_summary": version.competitor_gap_summary,
        "required_facts": version.required_facts,
        "banned_claims": version.banned_claims,
        "outline": version.outline,
        "cta_plan": version.cta_plan,
        "internal_link_plan": version.internal_link_plan,
        "image_plan": version.image_plan,
        "channel": version.channel,
        "language": version.language,
        "tone": version.tone,
        "target_length_min": version.target_length_min,
        "target_length_max": version.target_length_max,
        "disclosures": version.disclosures,
        "reference_snapshot": version.reference_snapshot,
        "generation_policy": version.generation_policy_snapshot,
    }


def _next_refresh_at(version: BriefVersion) -> datetime | None:
    expirations = [item[1] for item in _snapshot_expirations(version)]
    return min(expirations) if expirations else None


def _snapshot_expirations(version: BriefVersion) -> list[tuple[str, datetime]]:
    result: list[tuple[str, datetime]] = []
    for index, fact in enumerate(version.required_facts):
        if not isinstance(fact, dict):
            continue
        parsed = _parse_datetime(fact.get("expires_at"))
        if parsed is not None:
            result.append((f"required_facts.{index}.expires_at", parsed))
    products = version.reference_snapshot.get("products", [])
    if isinstance(products, list):
        for product_index, product in enumerate(products):
            if not isinstance(product, dict):
                continue
            prices = product.get("prices", [])
            if not isinstance(prices, list):
                continue
            for price_index, price in enumerate(prices):
                if not isinstance(price, dict):
                    continue
                parsed = _parse_datetime(price.get("valid_to"))
                if parsed is not None:
                    result.append(
                        (
                            (
                                f"reference_snapshot.products.{product_index}."
                                f"prices.{price_index}.valid_to"
                            ),
                            parsed,
                        )
                    )
    return result


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _proposal_hash(items: list[dict[str, Any]], version: int) -> str:
    return canonical_json_hash({"proposal_version": version, "items": items})


def _proposal_timezone(items: list[MonthlyPlanItem]) -> str:
    return items[0].timezone if items else "UTC"


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise AppError(
            code="TIMEZONE_INVALID",
            message="지원하지 않는 IANA 타임존입니다.",
            status_code=422,
            fields=[{"path": "timezone", "reason": name}],
        ) from exc


def _bounded_int(
    value: Any, *, default: int, minimum: int, maximum: int
) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, minimum), maximum)


def _recurrence_times(
    data: RecurrenceCreate, *, max_occurrences: int
) -> list[datetime]:
    if max_occurrences < 1 or max_occurrences > 1_000:
        raise AppError(
            code="RECURRENCE_LIMIT_INVALID",
            message="반복 일정 생성 개수는 1~1000이어야 합니다.",
            status_code=422,
        )
    zone = _zone(data.timezone)
    current = data.starts_at.astimezone(zone)
    end = data.ends_at.astimezone(zone) if data.ends_at is not None else None
    exceptions = set(data.exception_dates)
    result: list[datetime] = []
    while len(result) < max_occurrences:
        if end is not None and current > end:
            break
        if current.date() not in exceptions:
            result.append(current)
        if data.frequency is RecurrenceFrequency.WEEKLY:
            current += timedelta(weeks=data.interval)
        elif data.frequency is RecurrenceFrequency.MONTHLY:
            current = _add_local_months(current, data.interval)
        else:
            current = _add_local_months(current, 3 * data.interval)
    return result


def _add_local_months(value: datetime, months: int) -> datetime:
    month_index = value.year * 12 + value.month - 1 + months
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    day = min(value.day, monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _ics_escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r", "")
        .replace("\n", "\\n")
    )
