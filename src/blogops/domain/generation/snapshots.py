"""Resolver for exact, immutable generation inputs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterable, Mapping
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.core.errors import AppError
from blogops.domain.generation.enums import ModelCatalogStatus, VersionStatus
from blogops.domain.generation.models import (
    ContentTemplate,
    GenerationInputSnapshot,
    GenerationSnapshotKeywordMetric,
    GenerationSnapshotSource,
    ModelCatalogEntry,
    ModelPricingVersion,
    PromptVersion,
    TemplateVersion,
)
from blogops.domain.generation.rules import CONTENT_TYPE_CONTRACTS, canonical_json_hash
from blogops.domain.generation.schemas import ContentJobCreate
from blogops.domain.keywords.models import KeywordMetricSnapshot
from blogops.domain.knowledge.enums import RightsStatus, SourceState
from blogops.domain.knowledge.models import KnowledgeSource, SourceVersion
from blogops.domain.planning.enums import BriefStatus
from blogops.domain.planning.models import BriefVersion, ContentBrief


@dataclass(frozen=True, slots=True)
class ResolvedGenerationInputs:
    snapshot: GenerationInputSnapshot
    source_links: tuple[GenerationSnapshotSource, ...]
    metric_links: tuple[GenerationSnapshotKeywordMetric, ...]
    brief: ContentBrief
    brief_version: BriefVersion
    template: ContentTemplate
    template_version: TemplateVersion
    prompt_version: PromptVersion
    model_entry: ModelCatalogEntry
    pricing_version: ModelPricingVersion


class SQLAlchemyGenerationSnapshotResolver:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def resolve(
        self,
        *,
        workspace_id: UUID,
        actor_id: UUID,
        request_hash: str,
        data: ContentJobCreate,
    ) -> ResolvedGenerationInputs:
        brief_version = await self._one(
            BriefVersion,
            workspace_id,
            data.brief_version_id,
            path="brief_version_id",
        )
        brief = await self._session.scalar(
            select(ContentBrief).where(
                ContentBrief.workspace_id == workspace_id,
                ContentBrief.id == brief_version.brief_id,
            )
        )
        if (
            brief is None
            or brief.status != BriefStatus.APPROVED.value
            or brief.current_version_id != brief_version.id
        ):
            raise AppError(
                code="APPROVED_BRIEF_VERSION_REQUIRED",
                message="현재 승인된 브리프 버전만 생성 입력으로 사용할 수 있습니다.",
                status_code=409,
                fields=[{"path": "brief_version_id", "reason": str(data.brief_version_id)}],
            )

        template_version = await self._one(
            TemplateVersion,
            workspace_id,
            data.template_version_id,
            path="template_version_id",
        )
        template = await self._one(
            ContentTemplate,
            workspace_id,
            template_version.template_id,
            path="template_version_id",
        )
        prompt_version = await self._one(
            PromptVersion,
            workspace_id,
            data.prompt_version_id,
            path="prompt_version_id",
        )
        if (
            template_version.status != VersionStatus.PUBLISHED.value
            or prompt_version.status != VersionStatus.PUBLISHED.value
            or template_version.prompt_version_id != prompt_version.id
            or template.content_type != data.content_type.value
        ):
            raise AppError(
                code="PUBLISHED_TEMPLATE_PROMPT_REQUIRED",
                message="콘텐츠 유형과 일치하는 발행된 템플릿·프롬프트 버전이 필요합니다.",
                status_code=422,
            )

        model_entry = await self._one(
            ModelCatalogEntry,
            workspace_id,
            data.model_entry_id,
            path="model_entry_id",
        )
        pricing_version = await self._one(
            ModelPricingVersion,
            workspace_id,
            data.pricing_version_id,
            path="pricing_version_id",
        )
        if (
            model_entry.status != ModelCatalogStatus.ACTIVE.value
            or pricing_version.model_entry_id != model_entry.id
            or data.quality.value not in model_entry.quality_grades
        ):
            raise AppError(
                code="MODEL_ROUTE_INVALID",
                message="활성 모델·가격 버전과 요청 품질 등급이 일치하지 않습니다.",
                status_code=422,
            )
        self._assert_model_policy(brief_version.generation_policy_snapshot, model_entry)

        source_snapshots, source_rows = await self._sources(
            workspace_id,
            brief_version,
            data.source_version_ids,
        )
        metric_snapshots, metric_rows = await self._metrics(
            workspace_id,
            brief_version,
            data.keyword_metric_snapshot_ids,
        )
        brief_snapshot = _brief_snapshot(brief_version)
        references = dict(brief_version.reference_snapshot)
        template_snapshot = _template_snapshot(template, template_version)
        prompt_snapshot = _prompt_snapshot(prompt_version)
        model_snapshot = _model_snapshot(model_entry)
        pricing_snapshot = _pricing_snapshot(pricing_version)
        safety_policy = dict(brief_version.generation_policy_snapshot.get("safety", {}))
        contract_snapshot = asdict(CONTENT_TYPE_CONTRACTS[data.content_type])
        payload = {
            "brief": brief_snapshot,
            "brand": references.get("brand"),
            "products": references.get("products", []),
            "persona": references.get("persona"),
            "source_versions": source_snapshots,
            "keyword_metrics": metric_snapshots,
            "template": template_snapshot,
            "prompt": prompt_snapshot,
            "model": model_snapshot,
            "pricing": pricing_snapshot,
            "content_type": data.content_type.value,
            "type_input": data.type_input,
            "variables": data.variables,
            "generation_policy": brief_version.generation_policy_snapshot,
            "approval_policy": brief_version.approval_policy_snapshot,
            "safety_policy": safety_policy,
            "contract": contract_snapshot,
            "request": data.model_dump(mode="json"),
            "request_hash": request_hash,
        }
        snapshot_id = uuid4()
        snapshot = GenerationInputSnapshot(
            id=snapshot_id,
            workspace_id=workspace_id,
            brief_id=brief.id,
            brief_version_id=brief_version.id,
            template_version_id=template_version.id,
            prompt_version_id=prompt_version.id,
            model_entry_id=model_entry.id,
            pricing_version_id=pricing_version.id,
            content_type=data.content_type.value,
            brief_snapshot=brief_snapshot,
            brand_snapshot=references.get("brand"),
            product_snapshots=list(references.get("products", [])),
            persona_snapshot=references.get("persona"),
            source_version_snapshots=source_snapshots,
            keyword_metric_snapshots=metric_snapshots,
            template_snapshot=template_snapshot,
            prompt_snapshot=prompt_snapshot,
            model_snapshot=model_snapshot,
            pricing_snapshot=pricing_snapshot,
            type_input_snapshot=dict(data.type_input),
            variables_snapshot=dict(data.variables),
            generation_policy_snapshot=dict(brief_version.generation_policy_snapshot),
            generation_policy_hash=brief_version.generation_policy_hash,
            approval_policy_snapshot=dict(brief_version.approval_policy_snapshot),
            approval_policy_hash=brief_version.approval_policy_hash,
            safety_policy_snapshot=safety_policy,
            safety_policy_hash=canonical_json_hash(safety_policy),
            contract_snapshot=contract_snapshot,
            request_snapshot=data.model_dump(mode="json"),
            request_hash=request_hash,
            snapshot_hash=canonical_json_hash(payload),
            created_by=actor_id,
        )
        source_links = tuple(
            GenerationSnapshotSource(
                id=uuid4(),
                workspace_id=workspace_id,
                snapshot_id=snapshot_id,
                source_id=row.source_id,
                source_version_id=row.id,
                content_hash=row.content_hash,
                snapshot=item,
            )
            for item, row in zip(source_snapshots, source_rows, strict=True)
        )
        metric_links = tuple(
            GenerationSnapshotKeywordMetric(
                id=uuid4(),
                workspace_id=workspace_id,
                snapshot_id=snapshot_id,
                keyword_id=row.keyword_id,
                metric_snapshot_id=row.id,
                request_hash=row.request_hash,
                snapshot=item,
            )
            for item, row in zip(metric_snapshots, metric_rows, strict=True)
        )
        return ResolvedGenerationInputs(
            snapshot=snapshot,
            source_links=source_links,
            metric_links=metric_links,
            brief=brief,
            brief_version=brief_version,
            template=template,
            template_version=template_version,
            prompt_version=prompt_version,
            model_entry=model_entry,
            pricing_version=pricing_version,
        )

    async def _sources(
        self,
        workspace_id: UUID,
        brief_version: BriefVersion,
        requested_ids: Iterable[UUID],
    ) -> tuple[list[dict[str, Any]], list[SourceVersion]]:
        approved_refs = {
            UUID(str(item["version_id"])): item
            for item in brief_version.knowledge_source_snapshot
            if item.get("version_id")
        }
        requested = set(requested_ids)
        unapproved = requested.difference(approved_refs)
        if unapproved:
            raise AppError(
                code="SOURCE_NOT_IN_APPROVED_BRIEF",
                message="승인된 브리프에 고정되지 않은 자료 버전은 사용할 수 없습니다.",
                status_code=409,
                fields=[
                    {"path": "source_version_ids", "reason": str(item)}
                    for item in sorted(unapproved, key=str)
                ],
            )
        ids = sorted(approved_refs, key=str)
        snapshots: list[dict[str, Any]] = []
        rows: list[SourceVersion] = []
        for source_version_id in ids:
            version = await self._one(
                SourceVersion,
                workspace_id,
                source_version_id,
                path="source_version_ids",
            )
            source = await self._one(
                KnowledgeSource,
                workspace_id,
                version.source_id,
                path="source_version_ids",
            )
            if (
                source.deleted_at is not None
                or source.state != SourceState.READY.value
                or source.rights_status
                in {RightsStatus.PROHIBITED.value, RightsStatus.UNCONFIRMED.value}
            ):
                raise AppError(
                    code="SOURCE_NO_LONGER_ALLOWED",
                    message="자료의 권리 또는 활성 상태가 변경되어 다시 승인해야 합니다.",
                    status_code=409,
                    fields=[{"path": "source_version_ids", "reason": str(version.id)}],
                )
            approved = approved_refs[source_version_id]
            if approved.get("content_hash") != version.content_hash:
                raise AppError(
                    code="SOURCE_SNAPSHOT_HASH_MISMATCH",
                    message="승인된 자료 해시가 저장된 버전과 일치하지 않습니다.",
                    status_code=409,
                )
            snapshots.append(
                {
                    "id": str(source.id),
                    "version_id": str(version.id),
                    "version": version.version,
                    "content_hash": version.content_hash,
                    "retrieved_at": version.retrieved_at.isoformat(),
                    "rights_status": source.rights_status,
                    "use_scope": source.use_scope,
                    "quality_grade": source.quality_grade,
                    "metadata": _json_safe(version.metadata_json),
                }
            )
            rows.append(version)
        return snapshots, rows

    async def _metrics(
        self,
        workspace_id: UUID,
        brief_version: BriefVersion,
        requested_ids: Iterable[UUID],
    ) -> tuple[list[dict[str, Any]], list[KeywordMetricSnapshot]]:
        approved_ids = _collect_metric_ids(brief_version.keyword_snapshot)
        requested = set(requested_ids)
        unapproved = requested.difference(approved_ids)
        if unapproved:
            raise AppError(
                code="KEYWORD_METRIC_NOT_IN_APPROVED_BRIEF",
                message="승인된 브리프에 고정되지 않은 키워드 지표는 사용할 수 없습니다.",
                status_code=409,
                fields=[
                    {"path": "keyword_metric_snapshot_ids", "reason": str(item)}
                    for item in sorted(unapproved, key=str)
                ],
            )
        rows: list[KeywordMetricSnapshot] = []
        snapshots: list[dict[str, Any]] = []
        for metric_id in sorted(approved_ids, key=str):
            row = await self._one(
                KeywordMetricSnapshot,
                workspace_id,
                metric_id,
                path="keyword_metric_snapshot_ids",
            )
            rows.append(row)
            snapshots.append(_metric_snapshot(row))
        return snapshots, rows

    async def _one(
        self,
        model: type[Any],
        workspace_id: UUID,
        resource_id: UUID,
        *,
        path: str,
    ) -> Any:
        row = await self._session.scalar(
            select(model).where(model.workspace_id == workspace_id, model.id == resource_id)
        )
        if row is None:
            raise AppError(
                code="GENERATION_REFERENCE_NOT_FOUND",
                message="같은 워크스페이스의 정확한 입력 버전을 찾을 수 없습니다.",
                status_code=404,
                fields=[{"path": path, "reason": str(resource_id)}],
            )
        return row

    @staticmethod
    def _assert_model_policy(
        generation_policy: Mapping[str, Any], model: ModelCatalogEntry
    ) -> None:
        model_policy = generation_policy.get("model", {})
        allowed = frozenset(str(item) for item in model_policy.get("allowed_providers", []))
        denied = frozenset(str(item) for item in model_policy.get("denied_providers", []))
        if not allowed or model.provider not in allowed or model.provider in denied:
            raise AppError(
                code="MODEL_PROVIDER_NOT_ALLOWED",
                message="브리프에 고정된 모델 정책이 이 공급자를 허용하지 않습니다.",
                status_code=422,
                fields=[{"path": "model_entry_id", "reason": model.provider}],
            )
        allowed_regions = frozenset(
            str(item) for item in model_policy.get("allowed_regions", [])
        )
        if allowed_regions and model.region not in allowed_regions:
            raise AppError(
                code="MODEL_REGION_NOT_ALLOWED",
                message="고정된 데이터 리전 정책과 모델 리전이 일치하지 않습니다.",
                status_code=422,
            )


def _brief_snapshot(version: BriefVersion) -> dict[str, Any]:
    return {
        "id": str(version.id),
        "brief_id": str(version.brief_id),
        "version_number": version.version_number,
        "snapshot_hash": version.snapshot_hash,
        "title": version.title,
        "objective": version.objective,
        "search_intent": version.search_intent,
        "journey_stage": version.journey_stage,
        "questions": version.questions,
        "required_facts": version.required_facts,
        "banned_claims": version.banned_claims,
        "outline": version.outline,
        "cta_plan": version.cta_plan,
        "internal_link_plan": version.internal_link_plan,
        "image_plan": version.image_plan,
        "approval_stages": version.approval_stages,
        "channel": version.channel,
        "language": version.language,
        "tone": version.tone,
        "target_length_min": version.target_length_min,
        "target_length_max": version.target_length_max,
        "disclosures": version.disclosures,
        "reference_snapshot_hash": version.reference_snapshot_hash,
        "created_at": version.created_at.isoformat(),
    }


def _template_snapshot(root: ContentTemplate, version: TemplateVersion) -> dict[str, Any]:
    return {
        "id": str(root.id),
        "version_id": str(version.id),
        "version": version.version,
        "scope": root.scope,
        "content_type": root.content_type,
        "input_schema": version.input_schema,
        "prompt_blocks": version.prompt_blocks,
        "structure_blocks": version.structure_blocks,
        "quality_rules": version.quality_rules,
        "channel_config": version.channel_config,
        "policy_hash": version.policy_hash,
        "content_hash": version.content_hash,
    }


def _prompt_snapshot(version: PromptVersion) -> dict[str, Any]:
    return {
        "id": str(version.id),
        "prompt_id": str(version.prompt_id),
        "version": version.version,
        "system_blocks": version.system_blocks,
        "task_blocks": version.task_blocks,
        "output_schema": version.output_schema,
        "safety_policy_version": version.safety_policy_version,
        "content_hash": version.content_hash,
    }


def _model_snapshot(model: ModelCatalogEntry) -> dict[str, Any]:
    return {
        "id": str(model.id),
        "provider": model.provider,
        "model": model.model,
        "model_version": model.model_version,
        "region": model.region,
        "quality_grades": model.quality_grades,
        "capabilities": model.capabilities,
        "context_limit": model.context_limit,
        "parameter_policy": model.parameter_policy,
        "data_policy": model.data_policy,
        "customer_managed_key": model.customer_managed_key,
    }


def _pricing_snapshot(pricing: ModelPricingVersion) -> dict[str, Any]:
    return {
        "id": str(pricing.id),
        "model_entry_id": str(pricing.model_entry_id),
        "currency": pricing.currency,
        "rates": pricing.rates,
        "effective_at": pricing.effective_at.isoformat(),
        "expires_at": pricing.expires_at.isoformat() if pricing.expires_at else None,
        "content_hash": pricing.content_hash,
    }


def _metric_snapshot(row: KeywordMetricSnapshot) -> dict[str, Any]:
    fields = (
        "id",
        "keyword_id",
        "job_id",
        "provider_connection_id",
        "provider",
        "source_class",
        "source_label",
        "value_kind",
        "measured_at",
        "retrieved_at",
        "expires_at",
        "period_start",
        "period_end",
        "dimensions_json",
        "dimensions_hash",
        "metrics_json",
        "trend_points_json",
        "demographics_json",
        "serp_samples_json",
        "confidence",
        "limitations_json",
        "request_hash",
        "adapter_name",
        "adapter_version",
        "transform_version",
        "raw_object_ref",
        "raw_response_hash",
        "is_cached",
        "is_stale",
    )
    return {field: _json_safe(getattr(row, field)) for field in fields}


def _collect_metric_ids(value: Any) -> set[UUID]:
    found: set[UUID] = set()
    if isinstance(value, Mapping):
        metric = value.get("metric")
        if isinstance(metric, Mapping) and metric.get("id"):
            found.add(UUID(str(metric["id"])))
        for key, item in value.items():
            if key == "metric_snapshot_id" and item:
                found.add(UUID(str(item)))
            else:
                found.update(_collect_metric_ids(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_collect_metric_ids(item))
    return found


def _json_safe(value: Any) -> Any:
    if isinstance(value, (UUID, datetime, date, Decimal)):
        return str(value) if not isinstance(value, (datetime, date)) else value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value
