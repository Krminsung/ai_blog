"""Application services for idempotent generation and immutable content versions."""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Iterable, Mapping
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.core.context import Principal
from blogops.core.errors import AppError
from blogops.db.session import apply_workspace_scope
from blogops.domain.generation.enums import (
    CollaborationEventKind,
    CommandKind,
    ContentChangeKind,
    TemplateScope,
    VersionStatus,
)
from blogops.domain.generation.models import (
    ContentBlock,
    ContentCollaborationEvent,
    ContentFeedback,
    ContentItem,
    ContentTemplate,
    ContentVersion,
    GenerationInputSnapshot,
    GenerationJob,
    GenerationJobCommand,
    GenerationJobStep,
    TemplateVersion,
)
from blogops.domain.generation.providers import BudgetEntitlementGateway
from blogops.domain.generation.rules import (
    canonical_json_hash,
    content_document_hash,
    evaluate_generation_boundary,
    plan_generation_steps,
    require_allowed_tools,
)
from blogops.domain.generation.schemas import (
    CollaborationEventCreate,
    ContentBlockInput,
    ContentCreate,
    ContentFeedbackCreate,
    ContentJobCreate,
    ContentUpdate,
    ContentVersionCreate,
    RestoreVersionRequest,
    TemplateCreate,
    TemplateVersionCreate,
)
from blogops.domain.generation.snapshots import SQLAlchemyGenerationSnapshotResolver
from blogops.domain.jobs.state import (
    ALLOWED_JOB_TRANSITIONS,
    TERMINAL_JOB_STATES,
    JobState,
    StepState,
    ensure_job_transition,
)
from blogops.services.audit import append_audit_log
from blogops.services.outbox import add_outbox_event


OUTBOX_SCHEMA_VERSION = "1"


@dataclass(frozen=True, slots=True)
class JobCreationResult:
    job: GenerationJob
    created: bool


class GenerationService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        snapshots: SQLAlchemyGenerationSnapshotResolver,
        budget: BudgetEntitlementGateway,
    ) -> None:
        self.session = session
        self.snapshots = snapshots
        self.budget = budget

    async def create_job(
        self,
        principal: Principal,
        data: ContentJobCreate,
        *,
        idempotency_key: str,
    ) -> JobCreationResult:
        await self._scope(principal.workspace_id)
        request_hash = canonical_json_hash(data.model_dump(mode="json"))
        existing = await self.session.scalar(
            select(GenerationJob).where(
                GenerationJob.workspace_id == principal.workspace_id,
                GenerationJob.requested_by == principal.subject_id,
                GenerationJob.operation == data.operation.value,
                GenerationJob.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            if existing.request_hash != request_hash:
                raise _idempotency_conflict("Idempotency-Key")
            return JobCreationResult(existing, False)

        resolved = await self.snapshots.resolve(
            workspace_id=principal.workspace_id,
            actor_id=principal.subject_id,
            request_hash=request_hash,
            data=data,
        )
        source_ids = tuple(
            UUID(str(item["version_id"]))
            for item in resolved.snapshot.source_version_snapshots
        )
        boundary = evaluate_generation_boundary(
            data.content_type,
            data.type_input,
            source_version_ids=source_ids,
            approval_stages=resolved.brief_version.approval_stages,
            safety_policy=resolved.snapshot.safety_policy_snapshot,
        )
        if not boundary.may_generate:
            raise AppError(
                code="GENERATION_INPUT_INCOMPLETE",
                message="콘텐츠 유형·안전 정책에 필요한 입력을 보완해야 합니다.",
                status_code=422,
                fields=[
                    {"path": issue.path, "reason": issue.code}
                    for issue in boundary.issues
                    if issue.blocking
                ],
                remediation={
                    "questions": [issue.message for issue in boundary.issues if issue.blocking]
                },
            )
        tool_policy = resolved.snapshot.generation_policy_snapshot.get("tools", {})
        try:
            requested_tools = require_allowed_tools(
                data.requested_tools,
                tool_policy.get("allowed", []),
            )
        except ValueError as exc:
            raise AppError(
                code="TOOL_NOT_ALLOWED",
                message="고정된 생성 정책에서 허용하지 않은 도구가 포함되었습니다.",
                status_code=422,
                fields=[{"path": "requested_tools", "reason": str(exc)}],
            ) from exc

        authorization = await self.budget.authorize(
            workspace_id=principal.workspace_id,
            actor_id=principal.subject_id,
            operation=data.operation.value,
            input_snapshot_hash=resolved.snapshot.snapshot_hash,
            model_snapshot=resolved.snapshot.model_snapshot,
            requested_limits=data.requested_limits,
            idempotency_key=idempotency_key,
        )
        content = await self._job_content(principal, data, resolved)
        content.state = boundary.next_state.value
        content.updated_by = principal.subject_id
        job_id = uuid4()
        retry_policy = resolved.snapshot.generation_policy_snapshot.get("retry", {})
        max_attempts = retry_policy.get("max_attempts")
        job = GenerationJob(
            id=job_id,
            workspace_id=principal.workspace_id,
            requested_by=principal.subject_id,
            operation=data.operation.value,
            content_id=content.id,
            input_snapshot_id=resolved.snapshot.id,
            quality=data.quality.value,
            state=boundary.next_state.value,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            budget_reservation_ref=authorization.reservation_ref,
            entitlement_snapshot=dict(authorization.entitlement_snapshot),
            budget_snapshot=dict(authorization.budget_snapshot),
            estimated_cost=authorization.estimated_cost,
            currency=authorization.currency,
            estimate_breakdown=dict(authorization.estimate_breakdown),
            attempt=0,
            max_attempts=int(max_attempts) if max_attempts is not None else None,
            result={
                "required_approval_stages": list(boundary.required_approval_stages),
                "may_publish": boundary.may_publish,
                "requested_tools": list(requested_tools),
            },
        )
        steps = self._new_steps(
            workspace_id=principal.workspace_id,
            job_id=job_id,
            snapshot_hash=resolved.snapshot.snapshot_hash,
            outline=resolved.brief_version.outline,
            max_attempts=job.max_attempts,
        )
        self.session.add(resolved.snapshot)
        self.session.add_all((*resolved.source_links, *resolved.metric_links))
        self.session.add(content)
        self.session.add(job)
        self.session.add_all(steps)
        await self.session.flush()
        await self._record(
            principal,
            action="generation.job.created",
            target_type="generation_job",
            target_id=job.id,
            event_type="generation.job.queued",
            payload={
                "job_id": str(job.id),
                "content_id": str(content.id),
                "state": job.state,
                "snapshot_hash": resolved.snapshot.snapshot_hash,
            },
        )
        return JobCreationResult(job, True)

    async def get_job(self, principal: Principal, job_id: UUID) -> GenerationJob:
        return await self._job(principal.workspace_id, job_id)

    async def list_job_steps(
        self, principal: Principal, job_id: UUID
    ) -> list[GenerationJobStep]:
        await self._job(principal.workspace_id, job_id)
        return list(
            await self.session.scalars(
                select(GenerationJobStep)
                .where(
                    GenerationJobStep.workspace_id == principal.workspace_id,
                    GenerationJobStep.job_id == job_id,
                )
                .order_by(GenerationJobStep.ordinal, GenerationJobStep.id)
            )
        )

    async def cancel_job(
        self,
        principal: Principal,
        job_id: UUID,
        *,
        idempotency_key: str,
        reason: str | None,
    ) -> GenerationJob:
        return await self._command(
            principal,
            job_id,
            kind=CommandKind.CANCEL,
            idempotency_key=idempotency_key,
            reason=reason,
        )

    async def retry_job(
        self,
        principal: Principal,
        job_id: UUID,
        *,
        idempotency_key: str,
        reason: str | None,
    ) -> GenerationJob:
        return await self._command(
            principal,
            job_id,
            kind=CommandKind.RETRY,
            idempotency_key=idempotency_key,
            reason=reason,
        )

    async def create_content(self, principal: Principal, data: ContentCreate) -> ContentItem:
        await self._scope(principal.workspace_id)
        content = ContentItem(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            brief_id=data.brief_id,
            brand_id=data.brand_id,
            content_type=data.content_type.value,
            channel=data.channel,
            language=data.language,
            title=data.title,
            state=JobState.CREATED.value,
            metadata_json=dict(data.metadata_json),
            tags=list(dict.fromkeys(data.tags)),
            created_by=principal.subject_id,
            updated_by=principal.subject_id,
            lock_version=1,
        )
        self.session.add(content)
        await self.session.flush()
        if data.document:
            version = await self._append_version(
                principal,
                content,
                title=data.title,
                blocks=data.document,
                change_kind=ContentChangeKind.MANUAL,
                change_note=data.change_note,
                source_snapshot_hash=None,
            )
            content.current_version_id = version.id
        await self._record(
            principal,
            action="content.created",
            target_type="content",
            target_id=content.id,
            event_type="content.created",
            payload={"content_id": str(content.id)},
        )
        return content

    async def list_contents(
        self,
        principal: Principal,
        *,
        state: str | None,
        content_type: str | None,
        brand_id: UUID | None,
        author_id: UUID | None,
        query: str | None,
        include_archived: bool,
        limit: int,
        offset: int,
    ) -> list[ContentItem]:
        statement = select(ContentItem).where(
            ContentItem.workspace_id == principal.workspace_id,
            ContentItem.deleted_at.is_(None),
        )
        if not include_archived:
            statement = statement.where(ContentItem.archived_at.is_(None))
        if state:
            statement = statement.where(ContentItem.state == state)
        if content_type:
            statement = statement.where(ContentItem.content_type == content_type)
        if brand_id:
            statement = statement.where(ContentItem.brand_id == brand_id)
        if author_id:
            statement = statement.where(ContentItem.created_by == author_id)
        if query:
            current = ContentVersion
            statement = statement.outerjoin(
                current,
                (current.workspace_id == ContentItem.workspace_id)
                & (current.id == ContentItem.current_version_id),
            ).where(
                or_(
                    ContentItem.title.ilike(f"%{query}%"),
                    current.plain_text.ilike(f"%{query}%"),
                )
            )
        return list(
            await self.session.scalars(
                statement.order_by(ContentItem.updated_at.desc(), ContentItem.id)
                .limit(limit)
                .offset(offset)
            )
        )

    async def get_content(self, principal: Principal, content_id: UUID) -> ContentItem:
        return await self._content(principal.workspace_id, content_id)

    async def update_content(
        self,
        principal: Principal,
        content_id: UUID,
        data: ContentUpdate,
    ) -> ContentItem:
        content = await self._content(principal.workspace_id, content_id, for_update=True)
        if content.lock_version != data.expected_lock_version:
            raise _lock_conflict(data.expected_lock_version, content.lock_version)
        fields = data.model_fields_set.difference({"expected_lock_version", "archived"})
        for name in fields:
            value = getattr(data, name)
            if name == "tags" and value is not None:
                value = list(dict.fromkeys(value))
            setattr(content, name, value)
        if "archived" in data.model_fields_set:
            content.archived_at = datetime.now(UTC) if data.archived else None
        content.updated_by = principal.subject_id
        await self.session.flush()
        await append_audit_log(
            self.session,
            workspace_id=principal.workspace_id,
            actor_id=principal.subject_id,
            action="content.metadata.updated",
            target_type="content",
            target_id=str(content.id),
            details={"fields": sorted(data.model_fields_set)},
        )
        return content

    async def soft_delete_content(
        self, principal: Principal, content_id: UUID
    ) -> ContentItem:
        content = await self._content(principal.workspace_id, content_id, for_update=True)
        if content.retention_hold:
            raise AppError(
                code="CONTENT_RETENTION_HOLD",
                message="보존 잠금이 설정된 콘텐츠는 삭제할 수 없습니다.",
                status_code=409,
            )
        if content.deleted_at is None:
            content.deleted_at = datetime.now(UTC)
            content.updated_by = principal.subject_id
            await self._record(
                principal,
                action="content.trashed",
                target_type="content",
                target_id=content.id,
                event_type="content.trashed",
                payload={"content_id": str(content.id)},
            )
        return content

    async def list_versions(
        self, principal: Principal, content_id: UUID
    ) -> list[ContentVersion]:
        await self._content(principal.workspace_id, content_id, include_deleted=True)
        return list(
            await self.session.scalars(
                select(ContentVersion)
                .where(
                    ContentVersion.workspace_id == principal.workspace_id,
                    ContentVersion.content_id == content_id,
                )
                .order_by(ContentVersion.version_number.desc())
            )
        )

    async def get_version(
        self, principal: Principal, content_id: UUID, version_id: UUID
    ) -> ContentVersion:
        version = await self.session.scalar(
            select(ContentVersion).where(
                ContentVersion.workspace_id == principal.workspace_id,
                ContentVersion.content_id == content_id,
                ContentVersion.id == version_id,
            )
        )
        if version is None:
            raise _not_found("CONTENT_VERSION")
        return version

    async def create_version(
        self,
        principal: Principal,
        content_id: UUID,
        data: ContentVersionCreate,
    ) -> ContentVersion:
        content = await self._content(principal.workspace_id, content_id, for_update=True)
        current = await self._assert_current_version(
            principal.workspace_id,
            content,
            data.expected_current_version_id,
            data.expected_current_hash,
        )
        if data.change_kind is ContentChangeKind.AI_EDIT and current is not None:
            _assert_locked_facts_preserved(current.document, data.document)
        version = await self._append_version(
            principal,
            content,
            title=data.title,
            blocks=data.document,
            change_kind=data.change_kind,
            change_note=data.change_note,
            source_snapshot_hash=data.source_snapshot_hash,
        )
        content.current_version_id = version.id
        content.title = version.title
        content.updated_by = principal.subject_id
        await self._version_event(principal, content, version, "content.version.created")
        return version

    async def restore_version(
        self,
        principal: Principal,
        content_id: UUID,
        source_version_id: UUID,
        data: RestoreVersionRequest,
    ) -> ContentVersion:
        content = await self._content(principal.workspace_id, content_id, for_update=True)
        if content.current_version_id != data.expected_current_version_id:
            raise _lock_conflict_id(data.expected_current_version_id, content.current_version_id)
        source = await self.get_version(principal, content_id, source_version_id)
        blocks = [ContentBlockInput.model_validate(item) for item in source.document]
        version = await self._append_version(
            principal,
            content,
            title=source.title,
            blocks=blocks,
            change_kind=ContentChangeKind.RESTORE,
            change_note=data.note,
            source_snapshot_hash=source.source_snapshot_hash,
            restored_from_version_id=source.id,
        )
        content.current_version_id = version.id
        content.title = version.title
        content.updated_by = principal.subject_id
        await self._version_event(principal, content, version, "content.version.restored")
        return version

    async def add_feedback(
        self,
        principal: Principal,
        content_id: UUID,
        data: ContentFeedbackCreate,
    ) -> ContentFeedback:
        await self.get_version(principal, content_id, data.content_version_id)
        feedback = ContentFeedback(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            content_id=content_id,
            content_version_id=data.content_version_id,
            generation_job_id=data.generation_job_id,
            actor_id=principal.subject_id,
            kind=data.kind.value,
            details=dict(data.details),
        )
        self.session.add(feedback)
        await self.session.flush()
        return feedback

    async def append_collaboration_event(
        self,
        principal: Principal,
        content_id: UUID,
        data: CollaborationEventCreate,
    ) -> ContentCollaborationEvent:
        content = await self._content(principal.workspace_id, content_id)
        if data.content_version_id is not None:
            await self.get_version(principal, content_id, data.content_version_id)
        existing = await self.session.scalar(
            select(ContentCollaborationEvent).where(
                ContentCollaborationEvent.workspace_id == principal.workspace_id,
                ContentCollaborationEvent.actor_id == principal.subject_id,
                ContentCollaborationEvent.client_operation_id == data.client_operation_id,
            )
        )
        if existing is not None:
            return existing
        event = ContentCollaborationEvent(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            content_id=content.id,
            content_version_id=data.content_version_id,
            actor_id=principal.subject_id,
            client_operation_id=data.client_operation_id,
            event_kind=data.event_kind.value,
            block_key=data.block_key,
            text_range=data.text_range,
            payload=dict(data.payload),
        )
        self.session.add(event)
        await self.session.flush()
        return event

    async def create_template(
        self, principal: Principal, data: TemplateCreate
    ) -> ContentTemplate:
        if data.scope is TemplateScope.OFFICIAL:
            raise AppError(
                code="OFFICIAL_TEMPLATE_ADMIN_REQUIRED",
                message="공식 템플릿은 운영자 경계에서만 만들 수 있습니다.",
                status_code=403,
            )
        template = ContentTemplate(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            scope=data.scope.value,
            owner_id=principal.subject_id if data.scope is TemplateScope.PERSONAL else None,
            name=data.name,
            description=data.description,
            content_type=data.content_type.value,
            industry=data.industry,
            created_by=principal.subject_id,
        )
        self.session.add(template)
        await self.session.flush()
        return template

    async def create_template_version(
        self,
        principal: Principal,
        template_id: UUID,
        data: TemplateVersionCreate,
    ) -> TemplateVersion:
        template = await self.session.scalar(
            select(ContentTemplate)
            .where(
                ContentTemplate.workspace_id == principal.workspace_id,
                ContentTemplate.id == template_id,
                ContentTemplate.retired_at.is_(None),
            )
            .with_for_update()
        )
        if template is None:
            raise _not_found("TEMPLATE")
        if (
            template.scope == TemplateScope.PERSONAL.value
            and template.owner_id != principal.subject_id
        ):
            raise AppError(
                code="TEMPLATE_PERMISSION_DENIED",
                message="개인 템플릿 소유자만 새 버전을 만들 수 있습니다.",
                status_code=403,
            )
        version_number = int(
            await self.session.scalar(
                select(func.coalesce(func.max(TemplateVersion.version), 0)).where(
                    TemplateVersion.workspace_id == principal.workspace_id,
                    TemplateVersion.template_id == template.id,
                )
            )
            or 0
        ) + 1
        policy_hash = canonical_json_hash(data.policy_snapshot)
        payload = data.model_dump(mode="json", exclude={"publish"})
        version = TemplateVersion(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            template_id=template.id,
            prompt_version_id=data.prompt_version_id,
            version=version_number,
            status=(VersionStatus.PUBLISHED if data.publish else VersionStatus.DRAFT).value,
            input_schema=dict(data.input_schema),
            prompt_blocks=list(data.prompt_blocks),
            structure_blocks=list(data.structure_blocks),
            quality_rules=list(data.quality_rules),
            channel_config=dict(data.channel_config),
            policy_snapshot=dict(data.policy_snapshot),
            policy_hash=policy_hash,
            content_hash=canonical_json_hash(payload),
            created_by=principal.subject_id,
        )
        self.session.add(version)
        if data.publish:
            template.current_version_id = version.id
        await self.session.flush()
        return version

    async def export_content(
        self, principal: Principal, content_id: UUID, *, format: str
    ) -> tuple[str, str]:
        content = await self._content(principal.workspace_id, content_id, include_deleted=True)
        if content.current_version_id is None:
            raise _not_found("CONTENT_VERSION")
        version = await self.get_version(principal, content_id, content.current_version_id)
        if format == "json":
            import json

            return json.dumps(version.document, ensure_ascii=False, indent=2), "application/json"
        if format == "txt":
            return version.plain_text, "text/plain; charset=utf-8"
        if format == "html":
            body = "\n".join(
                f"<p>{html.escape(str(block.get('plain_text', '')))}</p>"
                for block in version.document
            )
            return f"<article><h1>{html.escape(version.title)}</h1>{body}</article>", "text/html"
        markdown = "\n\n".join(
            str(block.get("plain_text", "")) for block in version.document
        )
        return f"# {version.title}\n\n{markdown}", "text/markdown"

    async def claim_next_step(
        self, *, workspace_id: UUID, job_id: UUID
    ) -> GenerationJobStep | None:
        """Worker boundary: atomically claim the next independently retryable stage."""

        await self._scope(workspace_id)
        job = await self._job(workspace_id, job_id, for_update=True)
        state = JobState(job.state)
        if state is JobState.CANCEL_REQUESTED:
            ensure_job_transition(state, JobState.CANCELLED)
            job.state = JobState.CANCELLED.value
            job.finished_at = datetime.now(UTC)
            await self._cancel_pending_steps(workspace_id, job.id)
            return None
        if state in TERMINAL_JOB_STATES:
            return None
        step = await self.session.scalar(
            select(GenerationJobStep)
            .where(
                GenerationJobStep.workspace_id == workspace_id,
                GenerationJobStep.job_id == job_id,
                GenerationJobStep.state.in_(
                    {StepState.PENDING.value, StepState.RETRYING.value}
                ),
            )
            .order_by(GenerationJobStep.ordinal, GenerationJobStep.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if step is None:
            return None
        target = _step_job_state(step.step_kind)
        if target != state:
            ensure_job_transition(state, target)
            job.state = target.value
        step.state = StepState.RUNNING.value
        step.attempt += 1
        step.started_at = datetime.now(UTC)
        if job.started_at is None:
            job.started_at = step.started_at
        await self.session.flush()
        return step

    async def complete_step(
        self,
        *,
        workspace_id: UUID,
        job_id: UUID,
        step_id: UUID,
        result: Mapping[str, Any],
        output_ref: str | None,
    ) -> GenerationJobStep:
        step = await self._step(workspace_id, job_id, step_id, for_update=True)
        if step.state == StepState.SUCCEEDED.value:
            return step
        if step.state != StepState.RUNNING.value:
            raise AppError(
                code="GENERATION_STEP_NOT_RUNNING",
                message="실행 중인 단계만 완료할 수 있습니다.",
                status_code=409,
            )
        step.state = StepState.SUCCEEDED.value
        step.result = dict(result)
        step.output_ref = output_ref
        step.output_hash = canonical_json_hash(result)
        step.finished_at = datetime.now(UTC)
        await self.session.flush()
        return step

    async def fail_step(
        self,
        *,
        workspace_id: UUID,
        job_id: UUID,
        step_id: UUID,
        error_code: str,
        error_detail: str,
        retryable: bool,
    ) -> GenerationJob:
        job = await self._job(workspace_id, job_id, for_update=True)
        step = await self._step(workspace_id, job_id, step_id, for_update=True)
        step.state = StepState.FAILED.value
        step.error_code = error_code
        step.error_detail = error_detail
        step.finished_at = datetime.now(UTC)
        requested = JobState.RETRYABLE_FAILED if retryable else JobState.FINAL_FAILED
        current = JobState(job.state)
        if requested in ALLOWED_JOB_TRANSITIONS[current]:
            ensure_job_transition(current, requested)
            target = requested
        elif not retryable and JobState.RETRYABLE_FAILED in ALLOWED_JOB_TRANSITIONS[current]:
            ensure_job_transition(current, JobState.RETRYABLE_FAILED)
            ensure_job_transition(JobState.RETRYABLE_FAILED, JobState.FINAL_FAILED)
            target = JobState.FINAL_FAILED
        elif JobState.QUALITY_BLOCKED in ALLOWED_JOB_TRANSITIONS[current]:
            target = JobState.QUALITY_BLOCKED
            ensure_job_transition(current, target)
        else:
            # Preserve the authoritative parent state when its graph has no failure edge.
            # The failed step and error fields remain durable for an operator decision.
            target = current
        job.state = target.value
        job.error_code = error_code
        job.error_detail = error_detail
        if target is JobState.FINAL_FAILED:
            job.finished_at = datetime.now(UTC)
        await self.session.flush()
        return job

    async def finalize_for_review(
        self,
        *,
        workspace_id: UUID,
        job_id: UUID,
        title: str,
        blocks: list[ContentBlockInput],
        actor_id: UUID,
    ) -> ContentVersion:
        """Persist the generated result and stop at the independent approval boundary."""

        job = await self._job(workspace_id, job_id, for_update=True)
        content = await self._content(workspace_id, job.content_id, for_update=True)
        snapshot_hash = await self.session.scalar(
            select(GenerationInputSnapshot.snapshot_hash).where(
                GenerationInputSnapshot.workspace_id == workspace_id,
                GenerationInputSnapshot.id == job.input_snapshot_id,
            )
        )
        principal = Principal(
            subject_id=actor_id,
            workspace_id=workspace_id,
            session_id=None,
            permissions=frozenset(),
            authentication_method="worker",
        )
        version = await self._append_version(
            principal,
            content,
            title=title,
            blocks=blocks,
            change_kind=ContentChangeKind.GENERATED,
            change_note="generation result",
            source_snapshot_hash=snapshot_hash,
            generation_job_id=job.id,
            generation_snapshot_id=job.input_snapshot_id,
        )
        content.current_version_id = version.id
        content.title = title
        content.state = JobState.WAITING_REVIEW.value
        content.updated_by = actor_id
        current = JobState(job.state)
        if current is not JobState.WAITING_REVIEW:
            ensure_job_transition(current, JobState.WAITING_REVIEW)
            job.state = JobState.WAITING_REVIEW.value
        job.result = {**(job.result or {}), "content_version_id": str(version.id)}
        await self.session.flush()
        return version

    async def _command(
        self,
        principal: Principal,
        job_id: UUID,
        *,
        kind: CommandKind,
        idempotency_key: str,
        reason: str | None,
    ) -> GenerationJob:
        await self._scope(principal.workspace_id)
        request_hash = canonical_json_hash({"kind": kind.value, "reason": reason})
        existing = await self.session.scalar(
            select(GenerationJobCommand).where(
                GenerationJobCommand.workspace_id == principal.workspace_id,
                GenerationJobCommand.job_id == job_id,
                GenerationJobCommand.actor_id == principal.subject_id,
                GenerationJobCommand.command_kind == kind.value,
                GenerationJobCommand.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            if existing.request_hash != request_hash:
                raise _idempotency_conflict("Idempotency-Key")
            return await self._job(principal.workspace_id, job_id)
        job = await self._job(principal.workspace_id, job_id, for_update=True)
        current = JobState(job.state)
        if kind is CommandKind.CANCEL:
            if current in TERMINAL_JOB_STATES:
                raise AppError(
                    code="JOB_ALREADY_TERMINAL",
                    message="완료된 작업은 취소할 수 없습니다.",
                    status_code=409,
                )
            if current is JobState.CANCEL_REQUESTED:
                return job
            target = (
                JobState.CANCELLED
                if current is JobState.CREATED
                else JobState.CANCEL_REQUESTED
            )
            ensure_job_transition(current, target)
            job.cancel_requested_by = principal.subject_id
            job.cancel_requested_at = datetime.now(UTC)
        else:
            target = JobState.QUEUED
            ensure_job_transition(current, target)
            if job.max_attempts is not None and job.attempt >= job.max_attempts:
                raise AppError(
                    code="JOB_RETRY_LIMIT_REACHED",
                    message="고정된 재시도 정책의 최대 횟수에 도달했습니다.",
                    status_code=409,
                )
            job.attempt += 1
            job.error_code = None
            job.error_detail = None
            failed_steps = list(
                await self.session.scalars(
                    select(GenerationJobStep).where(
                        GenerationJobStep.workspace_id == principal.workspace_id,
                        GenerationJobStep.job_id == job.id,
                        GenerationJobStep.state.in_(
                            {StepState.FAILED.value, StepState.CANCELLED.value}
                        ),
                    )
                )
            )
            for step in failed_steps:
                step.state = StepState.RETRYING.value
                step.error_code = None
                step.error_detail = None
                step.finished_at = None
        job.state = target.value
        command = GenerationJobCommand(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            job_id=job.id,
            actor_id=principal.subject_id,
            command_kind=kind.value,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            from_state=current.value,
            to_state=target.value,
            reason=reason,
        )
        self.session.add(command)
        await self.session.flush()
        await self._record(
            principal,
            action=f"generation.job.{kind.value.lower()}",
            target_type="generation_job",
            target_id=job.id,
            event_type=f"generation.job.{kind.value.lower()}_requested",
            payload={"job_id": str(job.id), "state": job.state},
        )
        return job

    async def _job_content(
        self, principal: Principal, data: ContentJobCreate, resolved: Any
    ) -> ContentItem:
        if data.existing_content_id is not None:
            content = await self._content(
                principal.workspace_id,
                data.existing_content_id,
                for_update=True,
            )
            if content.content_type != data.content_type.value:
                raise AppError(
                    code="CONTENT_TYPE_MISMATCH",
                    message="기존 콘텐츠와 같은 유형으로만 선택 재생성할 수 있습니다.",
                    status_code=409,
                )
            return content
        brand = resolved.snapshot.brand_snapshot or {}
        return ContentItem(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            brief_id=resolved.brief.id,
            brand_id=UUID(str(brand["id"])) if brand.get("id") else None,
            content_type=data.content_type.value,
            channel=resolved.brief_version.channel,
            language=resolved.brief_version.language,
            title=resolved.brief_version.title,
            state=JobState.CREATED.value,
            metadata_json={"brief_version_id": str(resolved.brief_version.id)},
            created_by=principal.subject_id,
            updated_by=principal.subject_id,
            lock_version=1,
        )

    @staticmethod
    def _new_steps(
        *,
        workspace_id: UUID,
        job_id: UUID,
        snapshot_hash: str,
        outline: Iterable[Mapping[str, Any]],
        max_attempts: int | None,
    ) -> list[GenerationJobStep]:
        rows: list[GenerationJobStep] = []
        for planned in plan_generation_steps(outline):
            step_key = planned.kind.value
            if planned.section_key is not None:
                step_key = f"{step_key}:{planned.section_key}"
            input_hash = canonical_json_hash(
                {"snapshot_hash": snapshot_hash, "step_key": step_key}
            )
            rows.append(
                GenerationJobStep(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    job_id=job_id,
                    step_key=step_key,
                    step_kind=planned.kind.value,
                    section_key=planned.section_key,
                    ordinal=planned.ordinal,
                    state=StepState.PENDING.value,
                    input_snapshot_hash=input_hash,
                    attempt=0,
                    max_attempts=max_attempts,
                )
            )
        return rows

    async def _append_version(
        self,
        principal: Principal,
        content: ContentItem,
        *,
        title: str,
        blocks: list[ContentBlockInput],
        change_kind: ContentChangeKind,
        change_note: str | None,
        source_snapshot_hash: str | None,
        restored_from_version_id: UUID | None = None,
        generation_job_id: UUID | None = None,
        generation_snapshot_id: UUID | None = None,
    ) -> ContentVersion:
        version_number = int(
            await self.session.scalar(
                select(func.coalesce(func.max(ContentVersion.version_number), 0)).where(
                    ContentVersion.workspace_id == principal.workspace_id,
                    ContentVersion.content_id == content.id,
                )
            )
            or 0
        ) + 1
        document = [item.model_dump(mode="json") for item in blocks]
        plain_text = "\n\n".join(item.plain_text for item in blocks if item.plain_text)
        version = ContentVersion(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            content_id=content.id,
            parent_version_id=content.current_version_id,
            restored_from_version_id=restored_from_version_id,
            generation_job_id=generation_job_id,
            generation_snapshot_id=generation_snapshot_id,
            version_number=version_number,
            title=title,
            document=document,
            plain_text=plain_text,
            content_hash=content_document_hash(title, document),
            source_snapshot_hash=source_snapshot_hash,
            change_kind=change_kind.value,
            change_note=change_note,
            created_by=principal.subject_id,
        )
        self.session.add(version)
        self.session.add_all(
            ContentBlock(
                id=uuid4(),
                workspace_id=principal.workspace_id,
                content_version_id=version.id,
                block_key=item.block_key,
                block_type=item.block_type,
                position=index,
                payload=dict(item.payload),
                plain_text=item.plain_text,
                locked_facts=list(item.locked_facts),
                source_anchors=list(item.source_anchors),
            )
            for index, item in enumerate(blocks)
        )
        await self.session.flush()
        return version

    async def _assert_current_version(
        self,
        workspace_id: UUID,
        content: ContentItem,
        expected_id: UUID | None,
        expected_hash: str | None,
    ) -> ContentVersion | None:
        if content.current_version_id != expected_id:
            raise _lock_conflict_id(expected_id, content.current_version_id)
        if content.current_version_id is None:
            return None
        current = await self.session.scalar(
            select(ContentVersion).where(
                ContentVersion.workspace_id == workspace_id,
                ContentVersion.id == content.current_version_id,
            )
        )
        if current is None:
            raise _not_found("CONTENT_VERSION")
        if expected_hash is not None and current.content_hash != expected_hash:
            raise AppError(
                code="CONTENT_VERSION_HASH_CONFLICT",
                message="편집 기준 버전의 무결성 해시가 변경되었습니다.",
                status_code=409,
            )
        return current

    async def _job(
        self, workspace_id: UUID, job_id: UUID, *, for_update: bool = False
    ) -> GenerationJob:
        statement = select(GenerationJob).where(
            GenerationJob.workspace_id == workspace_id,
            GenerationJob.id == job_id,
        )
        if for_update:
            statement = statement.with_for_update()
        row = await self.session.scalar(statement)
        if row is None:
            raise _not_found("GENERATION_JOB")
        return row

    async def _step(
        self,
        workspace_id: UUID,
        job_id: UUID,
        step_id: UUID,
        *,
        for_update: bool,
    ) -> GenerationJobStep:
        statement = select(GenerationJobStep).where(
            GenerationJobStep.workspace_id == workspace_id,
            GenerationJobStep.job_id == job_id,
            GenerationJobStep.id == step_id,
        )
        if for_update:
            statement = statement.with_for_update()
        row = await self.session.scalar(statement)
        if row is None:
            raise _not_found("GENERATION_STEP")
        return row

    async def _content(
        self,
        workspace_id: UUID,
        content_id: UUID,
        *,
        for_update: bool = False,
        include_deleted: bool = False,
    ) -> ContentItem:
        statement = select(ContentItem).where(
            ContentItem.workspace_id == workspace_id,
            ContentItem.id == content_id,
        )
        if not include_deleted:
            statement = statement.where(ContentItem.deleted_at.is_(None))
        if for_update:
            statement = statement.with_for_update()
        row = await self.session.scalar(statement)
        if row is None:
            raise _not_found("CONTENT")
        return row

    async def _cancel_pending_steps(self, workspace_id: UUID, job_id: UUID) -> None:
        rows = list(
            await self.session.scalars(
                select(GenerationJobStep).where(
                    GenerationJobStep.workspace_id == workspace_id,
                    GenerationJobStep.job_id == job_id,
                    GenerationJobStep.state.in_(
                        {StepState.PENDING.value, StepState.RETRYING.value}
                    ),
                )
            )
        )
        for row in rows:
            row.state = StepState.CANCELLED.value

    async def _version_event(
        self,
        principal: Principal,
        content: ContentItem,
        version: ContentVersion,
        event_type: str,
    ) -> None:
        event = ContentCollaborationEvent(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            content_id=content.id,
            content_version_id=version.id,
            actor_id=principal.subject_id,
            client_operation_id=f"server:{event_type}:{version.id}",
            event_kind=(
                CollaborationEventKind.VERSION_RESTORED.value
                if event_type.endswith("restored")
                else CollaborationEventKind.VERSION_CREATED.value
            ),
            payload={"version_number": version.version_number, "hash": version.content_hash},
        )
        self.session.add(event)
        await self._record(
            principal,
            action=event_type,
            target_type="content_version",
            target_id=version.id,
            event_type=event_type,
            payload={
                "content_id": str(content.id),
                "content_version_id": str(version.id),
                "content_hash": version.content_hash,
            },
        )

    async def _record(
        self,
        principal: Principal,
        *,
        action: str,
        target_type: str,
        target_id: UUID,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        await append_audit_log(
            self.session,
            workspace_id=principal.workspace_id,
            actor_id=principal.subject_id,
            action=action,
            target_type=target_type,
            target_id=str(target_id),
            details=payload,
        )
        await add_outbox_event(
            self.session,
            workspace_id=principal.workspace_id,
            aggregate_type=target_type,
            aggregate_id=str(target_id),
            event_type=event_type,
            schema_version=OUTBOX_SCHEMA_VERSION,
            payload=payload,
        )

    async def _scope(self, workspace_id: UUID) -> None:
        await apply_workspace_scope(self.session, workspace_id)


def _assert_locked_facts_preserved(
    current_document: Iterable[Mapping[str, Any]],
    new_blocks: Iterable[ContentBlockInput],
) -> None:
    expected: set[str] = set()
    for block in current_document:
        for fact in block.get("locked_facts", []):
            expected.add(canonical_json_hash(fact))
    actual = {
        canonical_json_hash(fact)
        for block in new_blocks
        for fact in block.locked_facts
    }
    if not expected.issubset(actual):
        raise AppError(
            code="LOCKED_FACT_CHANGED",
            message="AI 편집은 잠긴 사실·고유명사·수치를 제거하거나 변경할 수 없습니다.",
            status_code=409,
        )


def _step_job_state(step_kind: str) -> JobState:
    mapping = {
        "VALIDATE_INPUT": JobState.VALIDATING,
        "RESEARCH": JobState.RESEARCHING,
        "PLAN_OUTLINE": JobState.PLANNING,
        "GENERATE_SECTION": JobState.GENERATING,
        "VERIFY_CLAIMS": JobState.VERIFYING,
        "VERIFY_POLICY": JobState.VERIFYING,
        "OPTIMIZE": JobState.OPTIMIZING,
        "PREPARE_REVIEW": JobState.OPTIMIZING,
    }
    try:
        return mapping[step_kind]
    except KeyError as exc:
        raise AppError(
            code="GENERATION_STEP_KIND_INVALID",
            message="알 수 없는 생성 단계입니다.",
            status_code=500,
        ) from exc


def _not_found(resource: str) -> AppError:
    return AppError(
        code=f"{resource}_NOT_FOUND",
        message="요청한 리소스를 찾을 수 없습니다.",
        status_code=404,
    )


def _idempotency_conflict(path: str) -> AppError:
    return AppError(
        code="IDEMPOTENCY_KEY_REUSED",
        message="같은 멱등 키를 다른 요청에 재사용할 수 없습니다.",
        status_code=409,
        fields=[{"path": path, "reason": "request hash mismatch"}],
    )


def _lock_conflict(expected: int, actual: int) -> AppError:
    return AppError(
        code="OPTIMISTIC_LOCK_CONFLICT",
        message="다른 요청이 먼저 콘텐츠를 변경했습니다.",
        status_code=409,
        fields=[{"path": "expected_lock_version", "reason": f"{expected}!={actual}"}],
    )


def _lock_conflict_id(expected: UUID | None, actual: UUID | None) -> AppError:
    return AppError(
        code="CONTENT_VERSION_CONFLICT",
        message="기준 콘텐츠 버전이 최신 버전과 다릅니다.",
        status_code=409,
        fields=[
            {
                "path": "expected_current_version_id",
                "reason": f"expected={expected}, actual={actual}",
            }
        ],
    )
