"""Tenant-scoped, budget-gated and lineage-preserving repurposing services."""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.core.context import Principal
from blogops.core.errors import AppError
from blogops.db.session import apply_workspace_scope
from blogops.domain.generation.models import ContentItem, ContentVersion
from blogops.domain.jobs.state import JobState, StepState, ensure_job_transition
from blogops.domain.repurpose.enums import (
    ChannelTemplateStatus,
    DeliveryState,
    RepurposeApprovalDecision,
    RepurposeExportFormat,
    RepurposeKind,
)
from blogops.domain.repurpose.models import (
    ChannelTemplate,
    ChannelTemplateVersion,
    RepurposeApproval,
    RepurposeDeliveryRequest,
    RepurposeDeliveryResult,
    RepurposeExportArtifact,
    RepurposeInputSnapshot,
    RepurposeJob,
    RepurposeJobCommand,
    RepurposeJobItem,
    RepurposeSnapshotCitation,
    RepurposeSnapshotClaim,
    RepurposeVariant,
)
from blogops.domain.repurpose.providers import (
    BudgetAuthorizationGateway,
    GeneratedVariant,
    OfficialSocialRegistry,
    RepurposeExportStore,
)
from blogops.domain.repurpose.repository import RepurposeRepository
from blogops.domain.repurpose.rules import (
    canonical_json_hash,
    ensure_secret_free_config,
    require_passed_validation,
    validate_citation_lineage,
    validate_claim_lineage,
    validate_model_selection,
    validate_platform_policy,
    validate_policy_bundle,
    validate_variant,
)
from blogops.domain.repurpose.schemas import (
    ChannelTemplateCreate,
    ChannelTemplateVersionCreate,
    RepurposeApprovalCreate,
    RepurposeDeliveryCreate,
    RepurposeExportCreate,
    RepurposeJobCommandCreate,
    RepurposeJobCreate,
)
from blogops.domain.research.models import Citation, Claim
from blogops.services.audit import append_audit_log
from blogops.services.outbox import add_outbox_event


OUTBOX_SCHEMA_VERSION = "1"


class RepurposeService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        budget_gateway: BudgetAuthorizationGateway | None = None,
        export_store: RepurposeExportStore | None = None,
        social_registry: OfficialSocialRegistry | None = None,
    ) -> None:
        self.session = session
        self.budget_gateway = budget_gateway
        self.export_store = export_store
        self.social_registry = social_registry or OfficialSocialRegistry()

    async def create_template(
        self, principal: Principal, data: ChannelTemplateCreate
    ) -> ChannelTemplate:
        await apply_workspace_scope(self.session, principal.workspace_id)
        row = ChannelTemplate(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            name=data.name,
            description=data.description,
            kind=data.kind.value,
            channel=data.channel,
            created_by=principal.subject_id,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def list_templates(self, principal: Principal) -> list[ChannelTemplate]:
        await apply_workspace_scope(self.session, principal.workspace_id)
        return await RepurposeRepository(self.session, principal.workspace_id).templates()

    async def get_template_version(
        self, principal: Principal, version_id: UUID
    ) -> ChannelTemplateVersion:
        await apply_workspace_scope(self.session, principal.workspace_id)
        return await RepurposeRepository(
            self.session, principal.workspace_id
        ).template_version(version_id)

    async def create_template_version(
        self,
        principal: Principal,
        template_id: UUID,
        data: ChannelTemplateVersionCreate,
    ) -> ChannelTemplateVersion:
        await apply_workspace_scope(self.session, principal.workspace_id)
        repo = RepurposeRepository(self.session, principal.workspace_id)
        template = await repo.template(template_id, lock=True)
        validate_platform_policy(RepurposeKind(template.kind), data.platform_policy)
        validate_policy_bundle(
            disclosure_policy=data.disclosure_policy,
            safety_policy=data.safety_policy,
            pii_policy=data.pii_policy,
            approval_policy=data.approval_policy,
            model_policy=data.model_policy,
        )
        latest = await self.session.scalar(
            select(ChannelTemplateVersion.version)
            .where(
                ChannelTemplateVersion.workspace_id == principal.workspace_id,
                ChannelTemplateVersion.template_id == template.id,
            )
            .order_by(ChannelTemplateVersion.version.desc())
            .limit(1)
        )
        payload = data.model_dump(mode="json", by_alias=True)
        policy_payload = {
            "platform": data.platform_policy,
            "disclosure": data.disclosure_policy,
            "safety": data.safety_policy,
            "pii": data.pii_policy,
            "approval": data.approval_policy,
            "model": data.model_policy,
        }
        row = ChannelTemplateVersion(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            template_id=template.id,
            version=(latest or 0) + 1,
            status=data.status.value,
            prompt_blocks=list(data.prompt_blocks),
            output_schema=dict(data.output_schema),
            platform_policy=dict(data.platform_policy),
            disclosure_policy=dict(data.disclosure_policy),
            safety_policy=dict(data.safety_policy),
            pii_policy=dict(data.pii_policy),
            approval_policy=dict(data.approval_policy),
            model_policy=dict(data.model_policy),
            policy_hash=canonical_json_hash(policy_payload),
            content_hash=canonical_json_hash(payload),
            created_by=principal.subject_id,
        )
        self.session.add(row)
        await self.session.flush()
        if data.status is ChannelTemplateStatus.PUBLISHED:
            template.current_version_id = row.id
        await self.session.flush()
        await self._record(
            principal,
            action="repurpose.template.version.created",
            target_type="repurpose_template",
            target_id=template.id,
            event_type="repurpose.template.version.created",
            payload={"template_id": str(template.id), "template_version_id": str(row.id)},
        )
        return row

    async def create_job(
        self,
        principal: Principal,
        data: RepurposeJobCreate,
        *,
        idempotency_key: str,
    ) -> tuple[RepurposeJob, bool]:
        await apply_workspace_scope(self.session, principal.workspace_id)
        repo = RepurposeRepository(self.session, principal.workspace_id)
        payload = data.model_dump(mode="json")
        request_hash = canonical_json_hash(payload)
        ensure_secret_free_config(data.generation_config)
        existing = await repo.idempotent_job(
            principal.subject_id, data.operation.value, idempotency_key
        )
        if existing is not None:
            _require_same_request(existing.request_hash, request_hash)
            return existing, False
        prepared = []
        for position, item in enumerate(data.items):
            content, version = await self._content_version(
                principal.workspace_id, item.content_id, item.content_version_id
            )
            template_version = await repo.template_version(item.template_version_id)
            template = await repo.template(template_version.template_id)
            if (
                template_version.status != ChannelTemplateStatus.PUBLISHED.value
                or template.current_version_id != template_version.id
            ):
                raise AppError(
                    code="PUBLISHED_REPURPOSE_TEMPLATE_REQUIRED",
                    message="현재 게시된 채널 템플릿 버전만 사용할 수 있습니다.",
                    status_code=409,
                )
            validate_platform_policy(
                RepurposeKind(template.kind), template_version.platform_policy
            )
            validate_model_selection(
                template_version.model_policy,
                provider=data.model_provider,
                model=data.model_name,
                model_version=data.model_version,
            )
            claims = list(
                await self.session.scalars(
                    select(Claim)
                    .where(
                        Claim.workspace_id == principal.workspace_id,
                        Claim.content_version_id == version.id,
                    )
                    .order_by(Claim.claim_key, Claim.id)
                )
            )
            claim_ids = [claim.id for claim in claims]
            citations = list(
                await self.session.scalars(
                    select(Citation)
                    .where(
                        Citation.workspace_id == principal.workspace_id,
                        Citation.claim_id.in_(claim_ids),
                    )
                    .order_by(Citation.claim_id, Citation.id)
                )
            ) if claim_ids else []
            claim_payload = [_claim_snapshot(claim) for claim in claims]
            citation_payload = [_citation_snapshot(citation) for citation in citations]
            template_snapshot = _template_snapshot(template, template_version)
            request_snapshot = {
                "position": position,
                "instructions": item.instructions,
                "variant_count": item.variant_count,
                "content_id": str(content.id),
                "content_version_id": str(version.id),
                "template_version_id": str(template_version.id),
            }
            snapshot_payload = {
                "source_content_hash": version.content_hash,
                "template": template_snapshot,
                "claims": claim_payload,
                "citations": citation_payload,
                "request": request_snapshot,
            }
            prepared.append(
                (
                    position,
                    item,
                    content,
                    version,
                    template,
                    template_version,
                    claims,
                    citations,
                    template_snapshot,
                    request_snapshot,
                    canonical_json_hash(snapshot_payload),
                )
            )
        if self.budget_gateway is None:
            raise AppError(
                code="REPURPOSE_BUDGET_RUNTIME_UNAVAILABLE",
                message="비용 예약 경계가 구성되지 않아 리퍼포징을 시작할 수 없습니다.",
                status_code=503,
            )
        reservation = await self.budget_gateway.reserve(
            workspace_id=principal.workspace_id,
            actor_id=principal.subject_id,
            amount=data.estimated_cost,
            currency=data.budget_currency.upper(),
            request_hash=request_hash,
        )
        if reservation.reserved_amount < data.estimated_cost:
            raise AppError(
                code="REPURPOSE_BUDGET_INSUFFICIENT",
                message="예약된 예산이 추정 비용보다 적습니다.",
                status_code=402,
            )
        job = RepurposeJob(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            requested_by=principal.subject_id,
            operation=data.operation.value,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            request_snapshot=payload,
            state=JobState.QUEUED.value,
            item_count=len(data.items),
            variant_count=sum(item.variant_count for item in data.items),
            budget_currency=reservation.currency.upper(),
            estimated_cost=data.estimated_cost,
            reserved_cost=reservation.reserved_amount,
            actual_cost=Decimal("0"),
            budget_reservation_ref=reservation.reference,
            model_provider=data.model_provider,
            model_name=data.model_name,
            model_version=data.model_version,
            model_config_hash=canonical_json_hash(data.generation_config),
        )
        self.session.add(job)
        await self.session.flush()
        for prepared_item in prepared:
            await self._append_prepared_item(principal, job, prepared_item)
        await self.session.flush()
        await self._record(
            principal,
            action="repurpose.job.created",
            target_type="repurpose_job",
            target_id=job.id,
            event_type="repurpose.job.queued",
            payload={
                "job_id": str(job.id),
                "item_count": job.item_count,
                "variant_count": job.variant_count,
                "budget_reservation_ref": job.budget_reservation_ref,
            },
        )
        return job, True

    async def get_job(self, principal: Principal, job_id: UUID) -> RepurposeJob:
        await apply_workspace_scope(self.session, principal.workspace_id)
        return await RepurposeRepository(self.session, principal.workspace_id).job(job_id)

    async def job_items(
        self, principal: Principal, job_id: UUID
    ) -> list[RepurposeJobItem]:
        await apply_workspace_scope(self.session, principal.workspace_id)
        repo = RepurposeRepository(self.session, principal.workspace_id)
        await repo.job(job_id)
        return await repo.job_items(job_id)

    async def job_variants(
        self, principal: Principal, job_id: UUID
    ) -> list[RepurposeVariant]:
        await apply_workspace_scope(self.session, principal.workspace_id)
        repo = RepurposeRepository(self.session, principal.workspace_id)
        await repo.job(job_id)
        return await repo.variants(job_id)

    async def mark_generating(self, *, workspace_id: UUID, job_id: UUID) -> RepurposeJob:
        await apply_workspace_scope(self.session, workspace_id)
        repo = RepurposeRepository(self.session, workspace_id)
        job = await repo.job(job_id, lock=True)
        if job.state == JobState.CANCEL_REQUESTED.value:
            _finalize_cancelled_job(job, await repo.job_items(job.id, lock=True))
            await self.session.flush()
            return job
        if job.state == JobState.QUEUED.value:
            job.state = JobState.GENERATING.value
            job.started_at = datetime.now(UTC)
            job.attempt += 1
            for item in await repo.job_items(job.id, lock=True):
                if item.state == StepState.PENDING.value:
                    item.state = StepState.RUNNING.value
        await self.session.flush()
        return job

    async def fail_runtime(
        self,
        *,
        workspace_id: UUID,
        job_id: UUID,
        code: str,
        detail: str,
        retryable: bool = False,
    ) -> RepurposeJob:
        await apply_workspace_scope(self.session, workspace_id)
        repo = RepurposeRepository(self.session, workspace_id)
        job = await repo.job(job_id, lock=True)
        items = await repo.job_items(job.id, lock=True)
        if job.state == JobState.CANCEL_REQUESTED.value:
            _finalize_cancelled_job(job, items)
            await self.session.flush()
            return job
        if job.state in {
            JobState.SUCCEEDED.value,
            JobState.CANCELLED.value,
            JobState.FINAL_FAILED.value,
            JobState.RETRYABLE_FAILED.value,
        }:
            return job
        job.state = (
            JobState.RETRYABLE_FAILED.value
            if retryable
            else JobState.FINAL_FAILED.value
        )
        job.error_code = code
        job.error_detail = detail
        job.finished_at = datetime.now(UTC)
        for item in items:
            if item.state not in {StepState.SUCCEEDED.value, StepState.CANCELLED.value}:
                item.state = StepState.FAILED.value
                item.error_code = code
                item.error_detail = detail
        await self.session.flush()
        await add_outbox_event(
            self.session,
            workspace_id=workspace_id,
            aggregate_type="repurpose_job",
            aggregate_id=str(job.id),
            event_type=(
                "repurpose.job.retryable_failed"
                if retryable
                else "repurpose.job.final_failed"
            ),
            schema_version=OUTBOX_SCHEMA_VERSION,
            payload={"job_id": str(job.id), "error_code": code},
        )
        return job

    async def finalize_cancellation(
        self, *, workspace_id: UUID, job_id: UUID
    ) -> RepurposeJob:
        await apply_workspace_scope(self.session, workspace_id)
        repo = RepurposeRepository(self.session, workspace_id)
        job = await repo.job(job_id, lock=True)
        if job.state == JobState.CANCEL_REQUESTED.value:
            _finalize_cancelled_job(job, await repo.job_items(job.id, lock=True))
            await self.session.flush()
        return job

    async def record_variant(
        self,
        *,
        workspace_id: UUID,
        item_id: UUID,
        variant_no: int,
        generated: GeneratedVariant,
        actual_cost: Decimal = Decimal("0"),
    ) -> RepurposeVariant:
        await apply_workspace_scope(self.session, workspace_id)
        repo = RepurposeRepository(self.session, workspace_id)
        item = await repo.item(item_id, lock=True)
        job = await repo.job(item.job_id, lock=True)
        snapshot = await self.session.scalar(
            select(RepurposeInputSnapshot).where(
                RepurposeInputSnapshot.workspace_id == workspace_id,
                RepurposeInputSnapshot.id == item.snapshot_id,
            )
        )
        if snapshot is None:
            raise AppError("REPURPOSE_SNAPSHOT_NOT_FOUND", "입력 스냅샷이 없습니다.", 404)
        if variant_no < 1 or variant_no > item.variant_count:
            raise AppError("REPURPOSE_VARIANT_NO_INVALID", "변형 번호가 범위를 벗어났습니다.", 422)
        allowed_claims = {
            str(row.claim_id): row.claim_hash
            for row in await self.session.scalars(
                select(RepurposeSnapshotClaim).where(
                    RepurposeSnapshotClaim.workspace_id == workspace_id,
                    RepurposeSnapshotClaim.snapshot_id == snapshot.id,
                )
            )
        }
        validate_claim_lineage(generated.claim_lineage, allowed_claims)
        allowed_citations = {
            str(row.citation_id): row.evidence_hash
            for row in await self.session.scalars(
                select(RepurposeSnapshotCitation)
                .join(
                    RepurposeSnapshotClaim,
                    (
                        RepurposeSnapshotClaim.workspace_id
                        == RepurposeSnapshotCitation.workspace_id
                    )
                    & (
                        RepurposeSnapshotClaim.id
                        == RepurposeSnapshotCitation.snapshot_claim_id
                    ),
                )
                .where(
                    RepurposeSnapshotCitation.workspace_id == workspace_id,
                    RepurposeSnapshotClaim.snapshot_id == snapshot.id,
                )
            )
        }
        validate_citation_lineage(generated.citation_lineage, allowed_citations)
        expected_model = {
            "provider": job.model_provider,
            "model": job.model_name,
            "model_version": job.model_version,
            "model_config_hash": job.model_config_hash,
        }
        if any(
            str(generated.model_provenance.get(key, "")) != value
            for key, value in expected_model.items()
        ):
            raise AppError(
                code="REPURPOSE_MODEL_PROVENANCE_MISMATCH",
                message="결과 모델 출처가 작업에 고정된 공급자·모델·버전과 다릅니다.",
                status_code=422,
            )
        validation = validate_variant(
            text=generated.plain_text,
            document=generated.document,
            platform_policy=snapshot.platform_policy_snapshot,
            disclosure_result=generated.disclosure_result,
            safety_result=generated.safety_result,
            pii_result=generated.pii_result,
        )
        require_passed_validation(validation)
        result_payload = {
            "document": generated.document,
            "plain_text": generated.plain_text,
            "claims": generated.claim_lineage,
            "citations": generated.citation_lineage,
            "source_content_hash": snapshot.source_content_hash,
            "template_content_hash": snapshot.template_snapshot["content_hash"],
        }
        result_hash = canonical_json_hash(result_payload)
        existing = await self.session.scalar(
            select(RepurposeVariant).where(
                RepurposeVariant.workspace_id == workspace_id,
                RepurposeVariant.job_item_id == item.id,
                RepurposeVariant.variant_no == variant_no,
            )
        )
        if existing is not None:
            if existing.result_hash != result_hash:
                raise AppError(
                    code="REPURPOSE_VARIANT_IDEMPOTENCY_CONFLICT",
                    message="같은 작업 변형 번호에 다른 결과를 기록할 수 없습니다.",
                    status_code=409,
                )
            return existing
        if actual_cost < 0 or job.actual_cost + actual_cost > job.reserved_cost:
            raise AppError(
                code="REPURPOSE_BUDGET_EXCEEDED",
                message="리퍼포징 실제 비용이 예약 예산을 초과했습니다.",
                status_code=402,
            )
        row = RepurposeVariant(
            id=uuid4(),
            workspace_id=workspace_id,
            job_item_id=item.id,
            snapshot_id=snapshot.id,
            variant_no=variant_no,
            document=[dict(block) for block in generated.document],
            plain_text=generated.plain_text,
            character_count=len(generated.plain_text),
            source_content_hash=snapshot.source_content_hash,
            template_content_hash=str(snapshot.template_snapshot["content_hash"]),
            result_hash=result_hash,
            claim_lineage=[dict(value) for value in generated.claim_lineage],
            citation_lineage=[dict(value) for value in generated.citation_lineage],
            validation_result={"passed": True, "violations": []},
            disclosure_result=dict(generated.disclosure_result),
            safety_result=dict(generated.safety_result),
            pii_result=dict(generated.pii_result),
            model_provenance=dict(generated.model_provenance),
            raw_object_ref=generated.raw_object_ref,
            raw_response_hash=generated.raw_response_hash,
        )
        self.session.add(row)
        job.actual_cost += actual_cost
        await self.session.flush()
        count = len(
            list(
                await self.session.scalars(
                    select(RepurposeVariant.id).where(
                        RepurposeVariant.workspace_id == workspace_id,
                        RepurposeVariant.job_item_id == item.id,
                    )
                )
            )
        )
        if count >= item.variant_count:
            item.state = StepState.SUCCEEDED.value
        await self.session.flush()
        return row

    async def complete_job(self, *, workspace_id: UUID, job_id: UUID) -> RepurposeJob:
        await apply_workspace_scope(self.session, workspace_id)
        repo = RepurposeRepository(self.session, workspace_id)
        job = await repo.job(job_id, lock=True)
        items = await repo.job_items(job.id, lock=True)
        if job.state == JobState.CANCEL_REQUESTED.value:
            _finalize_cancelled_job(job, items)
            await self.session.flush()
            return job
        if items and all(item.state == StepState.SUCCEEDED.value for item in items):
            job.state = JobState.WAITING_REVIEW.value
        elif any(item.state == StepState.SUCCEEDED.value for item in items):
            job.state = JobState.PARTIAL.value
        else:
            job.state = JobState.FINAL_FAILED.value
        job.finished_at = datetime.now(UTC)
        await self.session.flush()
        return job

    async def approve_variant(
        self,
        principal: Principal,
        variant_id: UUID,
        data: RepurposeApprovalCreate,
    ) -> RepurposeApproval:
        await apply_workspace_scope(self.session, principal.workspace_id)
        repo = RepurposeRepository(self.session, principal.workspace_id)
        variant = await repo.variant(variant_id)
        snapshot = await self._snapshot(principal.workspace_id, variant.snapshot_id)
        row = RepurposeApproval(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            variant_id=variant.id,
            variant_hash=variant.result_hash,
            decision=data.decision.value,
            reason=data.reason,
            policy_snapshot=snapshot.approval_policy_snapshot,
            decided_by=principal.subject_id,
        )
        self.session.add(row)
        await self.session.flush()
        await self._record(
            principal,
            action="repurpose.variant.decided",
            target_type="repurpose_variant",
            target_id=variant.id,
            event_type="repurpose.variant.decided",
            payload={"variant_id": str(variant.id), "decision": row.decision},
        )
        return row

    async def export_variant(
        self,
        principal: Principal,
        variant_id: UUID,
        data: RepurposeExportCreate,
    ) -> RepurposeExportArtifact:
        await apply_workspace_scope(self.session, principal.workspace_id)
        if self.export_store is None:
            raise AppError(
                "REPURPOSE_EXPORT_RUNTIME_UNAVAILABLE",
                "리퍼포징 내보내기 저장소가 구성되지 않았습니다.",
                503,
            )
        repo = RepurposeRepository(self.session, principal.workspace_id)
        variant = await repo.variant(variant_id)
        approval = await self._approved_variant(repo, variant)
        existing = await self.session.scalar(
            select(RepurposeExportArtifact).where(
                RepurposeExportArtifact.workspace_id == principal.workspace_id,
                RepurposeExportArtifact.variant_id == variant.id,
                RepurposeExportArtifact.variant_hash == variant.result_hash,
                RepurposeExportArtifact.format == data.format.value,
            )
        )
        if existing is not None:
            return existing
        body, media_type, suffix = _serialize_export(variant, data.format)
        stored = await self.export_store.put(
            workspace_id=principal.workspace_id,
            object_name=f"repurpose/{variant.id}/{variant.result_hash}.{suffix}",
            body=body,
            media_type=media_type,
        )
        row = RepurposeExportArtifact(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            variant_id=variant.id,
            approval_id=approval.id,
            variant_hash=variant.result_hash,
            format=data.format.value,
            object_ref=stored.object_ref,
            object_hash=stored.object_hash,
            media_type=stored.media_type,
            size_bytes=stored.size_bytes,
            manifest={
                "source_content_hash": variant.source_content_hash,
                "template_content_hash": variant.template_content_hash,
                "variant_hash": variant.result_hash,
                "approval_id": str(approval.id),
            },
            created_by=principal.subject_id,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def deliver_variant(
        self,
        principal: Principal,
        variant_id: UUID,
        data: RepurposeDeliveryCreate,
        *,
        idempotency_key: str,
    ) -> tuple[RepurposeDeliveryRequest, bool]:
        await apply_workspace_scope(self.session, principal.workspace_id)
        repo = RepurposeRepository(self.session, principal.workspace_id)
        request_hash = canonical_json_hash(data.model_dump(mode="json"))
        existing = await repo.idempotent_delivery(principal.subject_id, idempotency_key)
        if existing:
            _require_same_request(existing.request_hash, request_hash)
            return existing, False
        variant = await repo.variant(variant_id)
        approval = await repo.approval(data.approval_id)
        if (
            approval.variant_id != variant.id
            or approval.variant_hash != variant.result_hash
            or approval.decision != RepurposeApprovalDecision.APPROVE.value
        ):
            raise AppError(
                "REPURPOSE_APPROVAL_INVALID",
                "현재 변형 해시에 대한 승인만 전달에 사용할 수 있습니다.",
                409,
            )
        gateway = self.social_registry.require(data.official_provider)
        row = RepurposeDeliveryRequest(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            variant_id=variant.id,
            approval_id=approval.id,
            variant_hash=variant.result_hash,
            official_provider=data.official_provider,
            connection_secret_ref=data.connection_secret_ref,
            destination=dict(data.destination),
            requested_by=principal.subject_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            state=DeliveryState.REQUESTED.value,
        )
        self.session.add(row)
        await self.session.flush()
        try:
            result = await gateway.publish(
                secret_ref=data.connection_secret_ref,
                destination=data.destination,
                text=variant.plain_text,
                document=variant.document,
                idempotency_key=idempotency_key,
            )
        except Exception as exc:
            row.state = DeliveryState.FAILED.value
            row.error_code = "OFFICIAL_SOCIAL_DELIVERY_FAILED"
            failure_metadata = {"exception_type": type(exc).__name__}
            self.session.add(
                RepurposeDeliveryResult(
                    id=uuid4(),
                    workspace_id=principal.workspace_id,
                    delivery_request_id=row.id,
                    succeeded=False,
                    external_post_id=None,
                    response_metadata=failure_metadata,
                    error_code=row.error_code,
                    result_hash=canonical_json_hash(
                        {"request_hash": row.request_hash, **failure_metadata}
                    ),
                )
            )
            await self.session.flush()
            await self._record(
                principal,
                action="repurpose.variant.delivery_failed",
                target_type="repurpose_delivery",
                target_id=row.id,
                event_type="repurpose.variant.delivery_failed",
                payload={"delivery_id": str(row.id), "provider": row.official_provider},
            )
            return row, True
        row.state = DeliveryState.SUCCEEDED.value
        row.external_post_id = result.external_post_id
        row.response_metadata = dict(result.response_metadata)
        self.session.add(
            RepurposeDeliveryResult(
                id=uuid4(),
                workspace_id=principal.workspace_id,
                delivery_request_id=row.id,
                succeeded=True,
                external_post_id=result.external_post_id,
                response_metadata=dict(result.response_metadata),
                error_code=None,
                result_hash=canonical_json_hash(
                    {
                        "request_hash": row.request_hash,
                        "external_post_id": result.external_post_id,
                        "response_metadata": result.response_metadata,
                    }
                ),
            )
        )
        await self.session.flush()
        await self._record(
            principal,
            action="repurpose.variant.delivered",
            target_type="repurpose_delivery",
            target_id=row.id,
            event_type="repurpose.variant.delivered",
            payload={"delivery_id": str(row.id), "provider": row.official_provider},
        )
        return row, True

    async def command_job(
        self,
        principal: Principal,
        job_id: UUID,
        data: RepurposeJobCommandCreate,
        *,
        idempotency_key: str,
    ) -> RepurposeJob:
        await apply_workspace_scope(self.session, principal.workspace_id)
        repo = RepurposeRepository(self.session, principal.workspace_id)
        job = await repo.job(job_id, lock=True)
        request_hash = canonical_json_hash(data.model_dump(mode="json"))
        existing = await repo.idempotent_command(
            job.id, principal.subject_id, data.command.value, idempotency_key
        )
        if existing:
            _require_same_request(existing.request_hash, request_hash)
            return job
        current = JobState(job.state)
        target = JobState.CANCEL_REQUESTED if data.command.value == "CANCEL" else JobState.QUEUED
        ensure_job_transition(current, target)
        job.state = target.value
        if target is JobState.QUEUED:
            job.error_code = None
            job.error_detail = None
            job.started_at = None
            job.finished_at = None
            _reset_items_for_retry(await repo.job_items(job.id, lock=True))
        self.session.add(
            RepurposeJobCommand(
                id=uuid4(),
                workspace_id=principal.workspace_id,
                job_id=job.id,
                actor_id=principal.subject_id,
                command=data.command.value,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                from_state=current.value,
                to_state=target.value,
                reason=data.reason,
            )
        )
        await self.session.flush()
        return job

    async def _append_prepared_item(
        self, principal: Principal, job: RepurposeJob, prepared: tuple[Any, ...]
    ) -> None:
        (
            position,
            item,
            content,
            version,
            template,
            template_version,
            claims,
            citations,
            template_snapshot,
            request_snapshot,
            snapshot_hash,
        ) = prepared
        snapshot = await self.session.scalar(
            select(RepurposeInputSnapshot).where(
                RepurposeInputSnapshot.workspace_id == principal.workspace_id,
                RepurposeInputSnapshot.content_version_id == version.id,
                RepurposeInputSnapshot.template_version_id == template_version.id,
                RepurposeInputSnapshot.snapshot_hash == snapshot_hash,
            )
        )
        if snapshot is None:
            snapshot = RepurposeInputSnapshot(
                id=uuid4(),
                workspace_id=principal.workspace_id,
                content_id=content.id,
                content_version_id=version.id,
                template_version_id=template_version.id,
                source_content_hash=version.content_hash,
                source_title=version.title,
                source_document=version.document,
                source_plain_text=version.plain_text,
                template_snapshot=template_snapshot,
                platform_policy_snapshot=template_version.platform_policy,
                disclosure_policy_snapshot=template_version.disclosure_policy,
                safety_policy_snapshot=template_version.safety_policy,
                pii_policy_snapshot=template_version.pii_policy,
                approval_policy_snapshot=template_version.approval_policy,
                claim_lineage_hash=canonical_json_hash([_claim_snapshot(row) for row in claims]),
                citation_lineage_hash=canonical_json_hash(
                    [_citation_snapshot(row) for row in citations]
                ),
                request_snapshot=request_snapshot,
                request_hash=canonical_json_hash(request_snapshot),
                snapshot_hash=snapshot_hash,
                created_by=principal.subject_id,
            )
            self.session.add(snapshot)
            await self.session.flush()
            claim_link_by_id: dict[UUID, RepurposeSnapshotClaim] = {}
            for claim in claims:
                link = RepurposeSnapshotClaim(
                    id=uuid4(),
                    workspace_id=principal.workspace_id,
                    snapshot_id=snapshot.id,
                    claim_id=claim.id,
                    claim_key=claim.claim_key,
                    claim_hash=claim.claim_hash,
                    statement=claim.statement,
                    status=claim.status,
                    lineage_hash=canonical_json_hash(_claim_snapshot(claim)),
                )
                self.session.add(link)
                claim_link_by_id[claim.id] = link
            await self.session.flush()
            for citation in citations:
                link = claim_link_by_id[citation.claim_id]
                self.session.add(
                    RepurposeSnapshotCitation(
                        id=uuid4(),
                        workspace_id=principal.workspace_id,
                        snapshot_claim_id=link.id,
                        citation_id=citation.id,
                        evidence_hash=citation.evidence_hash,
                        locator_snapshot=citation.locator,
                        citation_snapshot=_citation_snapshot(citation),
                    )
                )
        self.session.add(
            RepurposeJobItem(
                id=uuid4(),
                workspace_id=principal.workspace_id,
                job_id=job.id,
                snapshot_id=snapshot.id,
                position=position,
                kind=template.kind,
                channel=template.channel,
                variant_count=item.variant_count,
                state=StepState.PENDING.value,
            )
        )

    async def _content_version(
        self, workspace_id: UUID, content_id: UUID, version_id: UUID
    ) -> tuple[ContentItem, ContentVersion]:
        content = await self.session.scalar(
            select(ContentItem).where(
                ContentItem.workspace_id == workspace_id,
                ContentItem.id == content_id,
                ContentItem.deleted_at.is_(None),
            )
        )
        version = await self.session.scalar(
            select(ContentVersion).where(
                ContentVersion.workspace_id == workspace_id,
                ContentVersion.content_id == content_id,
                ContentVersion.id == version_id,
            )
        )
        if content is None or version is None:
            raise AppError("CONTENT_VERSION_NOT_FOUND", "원문 콘텐츠 버전을 찾을 수 없습니다.", 404)
        return content, version

    async def _snapshot(self, workspace_id: UUID, row_id: UUID) -> RepurposeInputSnapshot:
        row = await self.session.scalar(
            select(RepurposeInputSnapshot).where(
                RepurposeInputSnapshot.workspace_id == workspace_id,
                RepurposeInputSnapshot.id == row_id,
            )
        )
        if row is None:
            raise AppError("REPURPOSE_SNAPSHOT_NOT_FOUND", "입력 스냅샷이 없습니다.", 404)
        return row

    async def _approved_variant(
        self, repo: RepurposeRepository, variant: RepurposeVariant
    ) -> RepurposeApproval:
        approval = await repo.latest_approval(variant.id)
        if (
            approval is None
            or approval.decision != RepurposeApprovalDecision.APPROVE.value
            or approval.variant_hash != variant.result_hash
        ):
            raise AppError(
                "REPURPOSE_APPROVAL_REQUIRED",
                "현재 변형 해시에 대한 승인이 필요합니다.",
                409,
            )
        return approval

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


def _finalize_cancelled_job(
    job: RepurposeJob, items: list[RepurposeJobItem]
) -> None:
    ensure_job_transition(JobState(job.state), JobState.CANCELLED)
    job.state = JobState.CANCELLED.value
    job.error_code = None
    job.error_detail = None
    job.finished_at = datetime.now(UTC)
    for item in items:
        if item.state != StepState.SUCCEEDED.value:
            item.state = StepState.CANCELLED.value
            item.error_code = None
            item.error_detail = None


def _reset_items_for_retry(items: list[RepurposeJobItem]) -> None:
    for item in items:
        if item.state != StepState.SUCCEEDED.value:
            item.state = StepState.PENDING.value
            item.error_code = None
            item.error_detail = None


def _claim_snapshot(row: Claim) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "claim_key": row.claim_key,
        "statement": row.statement,
        "kind": row.kind,
        "status": row.status,
        "confidence": str(row.confidence) if row.confidence is not None else None,
        "temporal_validity": row.temporal_validity,
        "user_verified": row.user_verified,
        "verification_policy_version": row.verification_policy_version,
        "claim_hash": row.claim_hash,
    }


def _citation_snapshot(row: Citation) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "claim_id": str(row.claim_id),
        "research_artifact_id": str(row.research_artifact_id) if row.research_artifact_id else None,
        "source_version_id": str(row.source_version_id) if row.source_version_id else None,
        "canonical_uri": row.canonical_uri,
        "locator": row.locator,
        "excerpt_hash": row.excerpt_hash,
        "evidence_hash": row.evidence_hash,
        "style": row.style,
        "quote_policy_snapshot": row.quote_policy_snapshot,
        "retrieved_at": row.retrieved_at.isoformat(),
    }


def _template_snapshot(
    template: ChannelTemplate, version: ChannelTemplateVersion
) -> dict[str, Any]:
    return {
        "template_id": str(template.id),
        "template_version_id": str(version.id),
        "version": version.version,
        "kind": template.kind,
        "channel": template.channel,
        "prompt_blocks": version.prompt_blocks,
        "output_schema": version.output_schema,
        "policy_hash": version.policy_hash,
        "content_hash": version.content_hash,
    }


def _serialize_export(
    variant: RepurposeVariant, output_format: RepurposeExportFormat
) -> tuple[bytes, str, str]:
    if output_format is RepurposeExportFormat.TXT:
        return variant.plain_text.encode("utf-8"), "text/plain; charset=utf-8", "txt"
    if output_format is RepurposeExportFormat.MARKDOWN:
        body = f"<!-- variant_hash: {variant.result_hash} -->\n\n{variant.plain_text}\n"
        return body.encode("utf-8"), "text/markdown; charset=utf-8", "md"
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["variant_id", "variant_hash", "text"])
    writer.writerow([str(variant.id), variant.result_hash, variant.plain_text])
    return stream.getvalue().encode("utf-8"), "text/csv; charset=utf-8", "csv"


def _require_same_request(existing_hash: str, request_hash: str) -> None:
    if existing_hash != request_hash:
        raise AppError(
            "IDEMPOTENCY_KEY_REUSED",
            "같은 멱등 키를 다른 리퍼포징 요청에 재사용할 수 없습니다.",
            409,
        )
