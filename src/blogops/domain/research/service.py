"""Tenant-scoped research planning and append-only evidence ledger services."""

from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.core.context import Principal
from blogops.core.errors import AppError
from blogops.db.session import apply_workspace_scope
from blogops.domain.generation.models import ContentItem, ContentVersion
from blogops.domain.generation.rules import canonical_json_hash
from blogops.domain.jobs.state import JobState, ensure_job_transition
from blogops.domain.knowledge.models import SourceVersion
from blogops.domain.planning.enums import BriefStatus
from blogops.domain.planning.models import BriefVersion, ContentBrief
from blogops.domain.research.enums import (
    ResearchArtifactKind,
    SourceQualityGrade,
    SourceSelection,
)
from blogops.domain.research.models import (
    Citation,
    Claim,
    ClaimDecision,
    ResearchArtifact,
    ResearchRun,
)
from blogops.domain.research.rules import (
    assess_claim_evidence,
    enforce_quote_policy,
    research_export_rows,
    source_set_hash,
)
from blogops.domain.research.schemas import (
    ClaimCreate,
    ClaimDecisionCreate,
    ResearchArtifactCreate,
    ResearchRunCreate,
)
from blogops.services.audit import append_audit_log
from blogops.services.outbox import add_outbox_event


OUTBOX_SCHEMA_VERSION = "1"


class ResearchService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_run(
        self,
        principal: Principal,
        data: ResearchRunCreate,
        *,
        idempotency_key: str,
    ) -> tuple[ResearchRun, bool]:
        await apply_workspace_scope(self.session, principal.workspace_id)
        request_hash = canonical_json_hash(data.model_dump(mode="json"))
        existing = await self.session.scalar(
            select(ResearchRun).where(
                ResearchRun.workspace_id == principal.workspace_id,
                ResearchRun.requested_by == principal.subject_id,
                ResearchRun.operation == data.operation,
                ResearchRun.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            if existing.request_hash != request_hash:
                raise AppError(
                    code="IDEMPOTENCY_KEY_REUSED",
                    message="같은 멱등 키를 다른 연구 요청에 재사용할 수 없습니다.",
                    status_code=409,
                )
            return existing, False
        brief_version = await self.session.scalar(
            select(BriefVersion).where(
                BriefVersion.workspace_id == principal.workspace_id,
                BriefVersion.id == data.brief_version_id,
            )
        )
        brief = None
        if brief_version is not None:
            brief = await self.session.scalar(
                select(ContentBrief).where(
                    ContentBrief.workspace_id == principal.workspace_id,
                    ContentBrief.id == brief_version.brief_id,
                )
            )
        if (
            brief_version is None
            or brief is None
            or brief.status != BriefStatus.APPROVED.value
            or brief.current_version_id != brief_version.id
        ):
            raise AppError(
                code="APPROVED_BRIEF_VERSION_REQUIRED",
                message="현재 승인된 브리프 버전만 연구 계획에 사용할 수 있습니다.",
                status_code=409,
            )
        if data.content_id is not None:
            await self._content(principal.workspace_id, data.content_id)
        allowed = frozenset(
            str(item) for item in data.search_policy.get("allowed_providers", [])
        )
        invalid = [key for key in data.provider_keys if key not in allowed]
        if not allowed or invalid:
            raise AppError(
                code="RESEARCH_PROVIDER_NOT_ALLOWED",
                message="연구 공급자는 고정된 허용 정책에 명시되어야 합니다.",
                status_code=422,
                fields=[{"path": "provider_keys", "reason": item} for item in invalid],
            )
        plan = {
            "questions": data.questions,
            "required_facts": data.required_facts,
            "queries": data.queries,
            "brief_version_id": str(data.brief_version_id),
        }
        run = ResearchRun(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            generation_job_id=data.generation_job_id,
            content_id=data.content_id,
            brief_version_id=data.brief_version_id,
            requested_by=principal.subject_id,
            operation=data.operation,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            state=JobState.QUEUED.value,
            plan_snapshot=plan,
            plan_hash=canonical_json_hash(plan),
            search_policy_snapshot=dict(data.search_policy),
            search_policy_hash=canonical_json_hash(data.search_policy),
            source_policy_snapshot=dict(data.source_policy),
            source_policy_hash=canonical_json_hash(data.source_policy),
            provider_keys=list(dict.fromkeys(data.provider_keys)),
        )
        self.session.add(run)
        await self.session.flush()
        await self._record(
            principal,
            action="research.run.created",
            target_type="research_run",
            target_id=run.id,
            event_type="research.run.queued",
            payload={"research_run_id": str(run.id), "state": run.state},
        )
        return run, True

    async def get_run(self, principal: Principal, run_id: UUID) -> ResearchRun:
        return await self._run(principal.workspace_id, run_id)

    async def list_content_runs(
        self, principal: Principal, content_id: UUID
    ) -> list[ResearchRun]:
        await self._content(principal.workspace_id, content_id)
        return list(
            await self.session.scalars(
                select(ResearchRun)
                .where(
                    ResearchRun.workspace_id == principal.workspace_id,
                    ResearchRun.content_id == content_id,
                )
                .order_by(ResearchRun.created_at.desc(), ResearchRun.id)
            )
        )

    async def add_artifact(
        self,
        principal: Principal,
        run_id: UUID,
        data: ResearchArtifactCreate,
    ) -> ResearchArtifact:
        run = await self._run(principal.workspace_id, run_id)
        if data.source_version_id is not None:
            source_version = await self.session.scalar(
                select(SourceVersion.id).where(
                    SourceVersion.workspace_id == principal.workspace_id,
                    SourceVersion.id == data.source_version_id,
                )
            )
            if source_version is None:
                raise _not_found("SOURCE_VERSION")
        excerpt_hash = canonical_json_hash(data.excerpt or "") if data.excerpt else None
        word_count = len((data.excerpt or "").split())
        try:
            enforce_quote_policy(word_count, data.quote_policy_snapshot)
        except ValueError as exc:
            raise AppError(
                code="QUOTE_POLICY_EXCEEDED",
                message="인용 한도를 초과했습니다. 요약으로 전환해야 합니다.",
                status_code=422,
            ) from exc
        if data.artifact_kind is ResearchArtifactKind.USER_FACT:
            if data.rights_status != "OWNED":
                raise AppError(
                    code="USER_FACT_OWNERSHIP_REQUIRED",
                    message="사용자 사실은 사용자 소유 자료로 명확히 구분해야 합니다.",
                    status_code=422,
                )
        payload = data.model_dump(mode="json")
        artifact_hash = canonical_json_hash(payload)
        existing = await self.session.scalar(
            select(ResearchArtifact).where(
                ResearchArtifact.workspace_id == principal.workspace_id,
                ResearchArtifact.research_run_id == run.id,
                ResearchArtifact.artifact_hash == artifact_hash,
            )
        )
        if existing is not None:
            return existing
        artifact = ResearchArtifact(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            research_run_id=run.id,
            query_id=data.query_id,
            source_version_id=data.source_version_id,
            artifact_kind=data.artifact_kind.value,
            selection=data.selection.value,
            selection_reason=data.selection_reason,
            exclusion_reason=data.exclusion_reason,
            grade=data.grade.value,
            title=data.title,
            domain=data.domain,
            canonical_uri=data.canonical_uri,
            publisher=data.publisher,
            published_at=data.published_at,
            modified_at=data.modified_at,
            retrieved_at=data.retrieved_at,
            freshness_score=data.freshness_score,
            freshness_policy_snapshot=dict(data.freshness_policy_snapshot),
            rights_status=data.rights_status,
            use_scope=data.use_scope,
            quote_policy_snapshot=dict(data.quote_policy_snapshot),
            summary=data.summary,
            excerpt=data.excerpt,
            excerpt_hash=excerpt_hash,
            raw_object_ref=data.raw_object_ref,
            provider=data.provider,
            provider_version=data.provider_version,
            artifact_hash=artifact_hash,
            created_by=principal.subject_id,
        )
        self.session.add(artifact)
        if run.approved_source_set_hash is not None:
            run.approved_source_set_hash = None
            run.approved_by = None
            run.approved_at = None
        await self.session.flush()
        return artifact

    async def list_artifacts(
        self, principal: Principal, run_id: UUID, *, include_excluded: bool
    ) -> list[ResearchArtifact]:
        await self._run(principal.workspace_id, run_id)
        statement = select(ResearchArtifact).where(
            ResearchArtifact.workspace_id == principal.workspace_id,
            ResearchArtifact.research_run_id == run_id,
        )
        if not include_excluded:
            statement = statement.where(
                ResearchArtifact.selection != SourceSelection.EXCLUDED.value
            )
        return list(
            await self.session.scalars(
                statement.order_by(ResearchArtifact.grade, ResearchArtifact.retrieved_at.desc())
            )
        )

    async def record_claim(
        self,
        principal: Principal,
        content_id: UUID,
        content_version_id: UUID,
        data: ClaimCreate,
    ) -> tuple[Claim, list[Citation]]:
        version = await self.session.scalar(
            select(ContentVersion).where(
                ContentVersion.workspace_id == principal.workspace_id,
                ContentVersion.content_id == content_id,
                ContentVersion.id == content_version_id,
            )
        )
        if version is None:
            raise _not_found("CONTENT_VERSION")
        artifact_by_id: dict[UUID, ResearchArtifact] = {}
        grades: list[SourceQualityGrade] = []
        for citation_data in data.citations:
            if citation_data.research_artifact_id is not None:
                artifact = await self.session.scalar(
                    select(ResearchArtifact).where(
                        ResearchArtifact.workspace_id == principal.workspace_id,
                        ResearchArtifact.id == citation_data.research_artifact_id,
                    )
                )
                if artifact is None or artifact.selection == SourceSelection.EXCLUDED.value:
                    raise AppError(
                        code="CITATION_ARTIFACT_INVALID",
                        message="선택된 동일 워크스페이스 자료만 인용할 수 있습니다.",
                        status_code=422,
                    )
                artifact_by_id[artifact.id] = artifact
                grades.append(SourceQualityGrade(artifact.grade))
            try:
                enforce_quote_policy(
                    len((citation_data.excerpt or "").split()),
                    citation_data.quote_policy_snapshot,
                )
            except ValueError as exc:
                raise AppError(
                    code="QUOTE_POLICY_EXCEEDED",
                    message="인용 한도를 초과했습니다. 요약으로 전환해야 합니다.",
                    status_code=422,
                ) from exc
        assessment = assess_claim_evidence(
            data.kind,
            grades,
            user_verified=data.user_verified,
            has_conflict=data.has_conflict,
        )
        claim_payload = data.model_dump(mode="json", exclude={"citations"})
        claim = Claim(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            content_version_id=version.id,
            research_run_id=data.research_run_id,
            claim_key=data.claim_key,
            block_key=data.block_key,
            text_range=data.text_range,
            statement=data.statement,
            kind=data.kind.value,
            status=assessment.status.value,
            confidence=data.confidence,
            temporal_validity=dict(data.temporal_validity),
            user_verified=data.user_verified,
            verification_policy_version=data.verification_policy_version,
            claim_hash=canonical_json_hash(claim_payload),
            created_by=principal.subject_id,
        )
        citations: list[Citation] = []
        for citation_data in data.citations:
            excerpt_hash = canonical_json_hash(citation_data.excerpt or "")
            evidence_payload = citation_data.model_dump(mode="json")
            citations.append(
                Citation(
                    id=uuid4(),
                    workspace_id=principal.workspace_id,
                    claim_id=claim.id,
                    research_artifact_id=citation_data.research_artifact_id,
                    source_version_id=citation_data.source_version_id,
                    canonical_uri=citation_data.canonical_uri,
                    locator=dict(citation_data.locator),
                    excerpt=citation_data.excerpt,
                    excerpt_hash=excerpt_hash,
                    evidence_hash=canonical_json_hash(evidence_payload),
                    style=citation_data.style.value,
                    quote_word_count=len((citation_data.excerpt or "").split()),
                    quote_policy_snapshot=dict(citation_data.quote_policy_snapshot),
                    publisher=citation_data.publisher,
                    published_at=citation_data.published_at,
                    modified_at=citation_data.modified_at,
                    retrieved_at=citation_data.retrieved_at,
                    created_by=principal.subject_id,
                )
            )
        self.session.add(claim)
        self.session.add_all(citations)
        await self.session.flush()
        await append_audit_log(
            self.session,
            workspace_id=principal.workspace_id,
            actor_id=principal.subject_id,
            action="research.claim.recorded",
            target_type="claim",
            target_id=str(claim.id),
            details={"status": claim.status, "content_version_id": str(version.id)},
        )
        return claim, citations

    async def list_claims(
        self,
        principal: Principal,
        content_id: UUID,
        *,
        content_version_id: UUID | None,
    ) -> list[tuple[Claim, list[Citation]]]:
        content = await self._content(principal.workspace_id, content_id)
        version_id = content_version_id or content.current_version_id
        if version_id is None:
            return []
        claims = list(
            await self.session.scalars(
                select(Claim)
                .where(
                    Claim.workspace_id == principal.workspace_id,
                    Claim.content_version_id == version_id,
                )
                .order_by(Claim.created_at, Claim.id)
            )
        )
        result: list[tuple[Claim, list[Citation]]] = []
        for claim in claims:
            citations = list(
                await self.session.scalars(
                    select(Citation)
                    .where(
                        Citation.workspace_id == principal.workspace_id,
                        Citation.claim_id == claim.id,
                    )
                    .order_by(Citation.created_at, Citation.id)
                )
            )
            result.append((claim, citations))
        return result

    async def decide_claim(
        self,
        principal: Principal,
        claim_id: UUID,
        data: ClaimDecisionCreate,
    ) -> ClaimDecision:
        claim = await self.session.scalar(
            select(Claim).where(
                Claim.workspace_id == principal.workspace_id,
                Claim.id == claim_id,
            )
        )
        if claim is None:
            raise _not_found("CLAIM")
        decision = ClaimDecision(
            id=uuid4(),
            workspace_id=principal.workspace_id,
            claim_id=claim.id,
            replacement_claim_id=data.replacement_claim_id,
            decision=data.decision.value,
            reason=data.reason,
            evidence_snapshot=dict(data.evidence_snapshot),
            decided_by=principal.subject_id,
        )
        self.session.add(decision)
        await self.session.flush()
        return decision

    async def approve_source_set(
        self, principal: Principal, run_id: UUID
    ) -> ResearchRun:
        run = await self._run(principal.workspace_id, run_id, for_update=True)
        artifacts = await self.list_artifacts(principal, run.id, include_excluded=False)
        if not artifacts:
            raise AppError(
                code="RESEARCH_SOURCE_SET_EMPTY",
                message="승인할 자료가 없습니다.",
                status_code=409,
            )
        snapshots = [
            {"id": str(item.id), "artifact_hash": item.artifact_hash, "grade": item.grade}
            for item in artifacts
        ]
        run.approved_source_set_hash = source_set_hash(snapshots)
        run.approved_by = principal.subject_id
        run.approved_at = datetime.now(UTC)
        await self.session.flush()
        await append_audit_log(
            self.session,
            workspace_id=principal.workspace_id,
            actor_id=principal.subject_id,
            action="research.source_set.approved",
            target_type="research_run",
            target_id=str(run.id),
            details={"source_set_hash": run.approved_source_set_hash},
        )
        return run

    async def export_claim_ledger(
        self,
        principal: Principal,
        content_id: UUID,
        *,
        content_version_id: UUID | None,
        format: str,
    ) -> tuple[str, str]:
        ledger = await self.list_claims(
            principal,
            content_id,
            content_version_id=content_version_id,
        )
        claims = [
            {
                "id": str(claim.id),
                "statement": claim.statement,
                "kind": claim.kind,
                "status": claim.status,
                "claim_hash": claim.claim_hash,
            }
            for claim, _ in ledger
        ]
        citations_by_claim = {
            str(claim.id): [
                {
                    "uri": citation.canonical_uri,
                    "locator": citation.locator,
                    "excerpt_hash": citation.excerpt_hash,
                    "retrieved_at": citation.retrieved_at.isoformat(),
                }
                for citation in citations
            ]
            for claim, citations in ledger
        }
        rows = research_export_rows(claims, citations_by_claim)
        if format == "json":
            return json.dumps(rows, ensure_ascii=False, indent=2), "application/json"
        if format == "csv":
            output = io.StringIO()
            writer = csv.DictWriter(
                output,
                fieldnames=("claim_id", "claim", "kind", "status", "claim_hash", "citations"),
            )
            writer.writeheader()
            for row in rows:
                writer.writerow(
                    {
                        **row,
                        "citations": json.dumps(row["citations"], ensure_ascii=False),
                    }
                )
            return output.getvalue(), "text/csv; charset=utf-8"
        parts = []
        for row in rows:
            parts.append(f"## {row['claim']}\n\n- 상태: {row['status']}\n- 해시: `{row['claim_hash']}`")
            for citation in row["citations"]:
                parts.append(f"  - {citation.get('uri') or 'internal source'}")
        return "\n\n".join(parts), "text/markdown; charset=utf-8"

    async def mark_researching(self, *, workspace_id: UUID, run_id: UUID) -> ResearchRun:
        """Worker-owned transition; provider execution happens outside request handling."""

        await apply_workspace_scope(self.session, workspace_id)
        run = await self._run(workspace_id, run_id, for_update=True)
        current = JobState(run.state)
        if current is JobState.QUEUED:
            ensure_job_transition(current, JobState.VALIDATING)
            run.state = JobState.VALIDATING.value
            current = JobState.VALIDATING
        ensure_job_transition(current, JobState.RESEARCHING)
        run.state = JobState.RESEARCHING.value
        run.started_at = datetime.now(UTC)
        await self.session.flush()
        return run

    async def fail_run(
        self,
        *,
        workspace_id: UUID,
        run_id: UUID,
        error_code: str,
        error_detail: str,
        retryable: bool,
    ) -> ResearchRun:
        await apply_workspace_scope(self.session, workspace_id)
        run = await self._run(workspace_id, run_id, for_update=True)
        current = JobState(run.state)
        if current is JobState.QUEUED:
            ensure_job_transition(current, JobState.VALIDATING)
            run.state = JobState.VALIDATING.value
            current = JobState.VALIDATING
        if current is JobState.VALIDATING and retryable:
            ensure_job_transition(current, JobState.RESEARCHING)
            run.state = JobState.RESEARCHING.value
            current = JobState.RESEARCHING
        target = JobState.RETRYABLE_FAILED if retryable else JobState.FINAL_FAILED
        ensure_job_transition(current, target)
        run.state = target.value
        run.error_code = error_code
        run.error_detail = error_detail
        run.finished_at = datetime.now(UTC)
        await self.session.flush()
        return run

    async def _run(
        self, workspace_id: UUID, run_id: UUID, *, for_update: bool = False
    ) -> ResearchRun:
        statement = select(ResearchRun).where(
            ResearchRun.workspace_id == workspace_id,
            ResearchRun.id == run_id,
        )
        if for_update:
            statement = statement.with_for_update()
        row = await self.session.scalar(statement)
        if row is None:
            raise _not_found("RESEARCH_RUN")
        return row

    async def _content(self, workspace_id: UUID, content_id: UUID) -> ContentItem:
        row = await self.session.scalar(
            select(ContentItem).where(
                ContentItem.workspace_id == workspace_id,
                ContentItem.id == content_id,
                ContentItem.deleted_at.is_(None),
            )
        )
        if row is None:
            raise _not_found("CONTENT")
        return row

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


def _not_found(resource: str) -> AppError:
    return AppError(
        code=f"{resource}_NOT_FOUND",
        message="요청한 리소스를 찾을 수 없습니다.",
        status_code=404,
    )
