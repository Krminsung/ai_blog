"""Cross-domain snapshot and assignee validation boundaries for planning."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.core.errors import AppError
from blogops.domain.brand.models import (
    AudiencePersona,
    Brand,
    BrandVersion,
    CatalogStatus,
    Product,
    ProductLink,
    ProductPrice,
    ProductVariant,
    ProductVersion,
)
from blogops.domain.identity.enums import MembershipStatus, WorkspaceStatus
from blogops.domain.identity.models import Membership, Workspace
from blogops.domain.knowledge.enums import RightsStatus, SourceState
from blogops.domain.knowledge.models import KnowledgeSource, SourceVersion
from blogops.domain.planning.rules import canonical_json_hash
from blogops.domain.planning.schemas import ReferenceSelection


@dataclass(frozen=True, slots=True)
class WorkspacePolicySnapshot:
    generation_policy: dict[str, Any]
    generation_policy_hash: str
    approval_policy: dict[str, Any]
    approval_policy_hash: str
    timezone: str


@dataclass(frozen=True, slots=True)
class ResolvedPlanningReferences:
    snapshot: dict[str, Any]
    snapshot_hash: str
    brand_snapshot: dict[str, Any] | None
    audience_snapshot: dict[str, Any]
    keyword_snapshot: dict[str, Any]
    knowledge_source_snapshot: list[dict[str, Any]]


class PlanningReferenceResolver(Protocol):
    async def workspace_policy(self, workspace_id: UUID) -> WorkspacePolicySnapshot: ...

    async def resolve(
        self, workspace_id: UUID, selection: ReferenceSelection
    ) -> ResolvedPlanningReferences: ...


class ActiveMembershipResolver(Protocol):
    async def require_active(self, workspace_id: UUID, user_ids: set[UUID]) -> None: ...


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
                code="ASSIGNEE_NOT_ACTIVE_MEMBER",
                message="담당자는 현재 워크스페이스의 활성 멤버여야 합니다.",
                status_code=422,
                fields=[{"path": "assignees", "reason": str(user_id)} for user_id in missing],
            )


class SQLAlchemyPlanningReferenceResolver:
    """Captures exact cross-domain snapshots without database foreign keys.

    Keyword rows are queried through their stage-3 public table contract. The class itself is
    replaceable, so a future keyword service/API adapter can be injected without changing the
    planning model.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def workspace_policy(self, workspace_id: UUID) -> WorkspacePolicySnapshot:
        workspace = await self._session.scalar(
            select(Workspace).where(
                Workspace.id == workspace_id,
                Workspace.status == WorkspaceStatus.ACTIVE.value,
            )
        )
        if workspace is None:
            raise AppError(
                code="WORKSPACE_NOT_FOUND",
                message="워크스페이스를 찾을 수 없습니다.",
                status_code=404,
            )
        generation_policy = dict(workspace.generation_policy)
        approval_policy = dict(workspace.approval_policy)
        return WorkspacePolicySnapshot(
            generation_policy=generation_policy,
            generation_policy_hash=canonical_json_hash(generation_policy),
            approval_policy=approval_policy,
            approval_policy_hash=canonical_json_hash(approval_policy),
            timezone=workspace.timezone,
        )

    async def resolve(
        self, workspace_id: UUID, selection: ReferenceSelection
    ) -> ResolvedPlanningReferences:
        brand_snapshot = await self._brand(workspace_id, selection.brand_id)
        persona_snapshot = await self._persona(workspace_id, selection.persona_id)
        product_snapshots = [
            await self._product(workspace_id, product_id)
            for product_id in dict.fromkeys(selection.product_ids)
        ]
        knowledge_snapshots = [
            await self._knowledge_source(workspace_id, source_id)
            for source_id in dict.fromkeys(selection.knowledge_source_ids)
        ]
        primary_keyword = await self._keyword(
            workspace_id,
            selection.primary_keyword_id,
            fallback_text=selection.primary_keyword_text,
        )
        secondary_keywords = [
            await self._keyword(workspace_id, keyword_id, fallback_text=None)
            for keyword_id in dict.fromkeys(selection.secondary_keyword_ids)
        ]
        secondary_keywords.extend(
            {"id": None, "display_text": value, "normalized": value.casefold()}
            for value in selection.secondary_keyword_texts
        )
        cluster = await self._cluster(workspace_id, selection.keyword_cluster_id)
        keyword_snapshot = {
            "primary": primary_keyword,
            "secondary": secondary_keywords,
            "cluster": cluster,
        }
        snapshot = {
            "brand": brand_snapshot,
            "persona": persona_snapshot,
            "products": product_snapshots,
            "knowledge_sources": knowledge_snapshots,
            "keywords": keyword_snapshot,
        }
        return ResolvedPlanningReferences(
            snapshot=snapshot,
            snapshot_hash=canonical_json_hash(snapshot),
            brand_snapshot=brand_snapshot,
            audience_snapshot=persona_snapshot or {},
            keyword_snapshot=keyword_snapshot,
            knowledge_source_snapshot=knowledge_snapshots,
        )

    async def _brand(self, workspace_id: UUID, brand_id: UUID | None) -> dict[str, Any] | None:
        if brand_id is None:
            return None
        brand = await self._session.scalar(
            select(Brand).where(
                Brand.workspace_id == workspace_id,
                Brand.id == brand_id,
                Brand.status == CatalogStatus.ACTIVE.value,
            )
        )
        if brand is None:
            raise _reference_error("brand_id", brand_id)
        version = None
        if brand.current_version_id is not None:
            version = await self._session.scalar(
                select(BrandVersion).where(
                    BrandVersion.workspace_id == workspace_id,
                    BrandVersion.id == brand.current_version_id,
                )
            )
        return {
            "id": str(brand.id),
            "name": brand.name,
            "content_hash": brand.content_hash,
            "version_id": str(version.id) if version else None,
            "version_number": version.version_number if version else None,
            "version_hash": version.content_hash if version else None,
            "version_effective_from": (
                version.effective_from.isoformat() if version else None
            ),
            "version_created_at": version.created_at.isoformat() if version else None,
            "voice": version.voice if version else {},
            "preferred_expressions": version.preferred_expressions if version else [],
            "required_terms": version.required_terms if version else [],
            "required_phrases": version.required_phrases if version else [],
            "banned_terms": version.banned_terms if version else [],
            "banned_claims": version.banned_claims if version else [],
            "banned_rules": version.banned_rules if version else [],
            "style_dictionary": version.style_dictionary if version else [],
            "competitor_policy": version.competitor_policy if version else {},
        }

    async def _persona(
        self, workspace_id: UUID, persona_id: UUID | None
    ) -> dict[str, Any] | None:
        if persona_id is None:
            return None
        persona = await self._session.scalar(
            select(AudiencePersona).where(
                AudiencePersona.workspace_id == workspace_id,
                AudiencePersona.id == persona_id,
                AudiencePersona.status == CatalogStatus.ACTIVE.value,
            )
        )
        if persona is None:
            raise _reference_error("persona_id", persona_id)
        return {
            "id": str(persona.id),
            "brand_id": str(persona.brand_id) if persona.brand_id else None,
            "name": persona.name,
            "description": persona.description,
            "situations": persona.situations,
            "interests": persona.interests,
            "challenges": persona.challenges,
            "knowledge_level": persona.knowledge_level,
            "search_intents": persona.search_intents,
            "journey_stage": persona.journey_stage,
            "prohibited_targeting": persona.prohibited_targeting,
            "content_hash": persona.content_hash,
        }

    async def _product(self, workspace_id: UUID, product_id: UUID) -> dict[str, Any]:
        product = await self._session.scalar(
            select(Product).where(
                Product.workspace_id == workspace_id,
                Product.id == product_id,
                Product.status == CatalogStatus.ACTIVE.value,
            )
        )
        if product is None:
            raise _reference_error("product_ids", product_id)
        version = None
        if product.current_version_id is not None:
            version = await self._session.scalar(
                select(ProductVersion).where(
                    ProductVersion.workspace_id == workspace_id,
                    ProductVersion.id == product.current_version_id,
                )
            )
        variants: list[ProductVariant] = []
        prices: list[ProductPrice] = []
        links: list[ProductLink] = []
        if version is not None:
            variants = list(
                await self._session.scalars(
                    select(ProductVariant)
                    .where(
                        ProductVariant.workspace_id == workspace_id,
                        ProductVariant.product_id == product.id,
                        ProductVariant.product_version_id == version.id,
                        ProductVariant.is_available.is_(True),
                    )
                    .order_by(ProductVariant.sort_order, ProductVariant.id)
                )
            )
            prices = list(
                await self._session.scalars(
                    select(ProductPrice)
                    .where(
                        ProductPrice.workspace_id == workspace_id,
                        ProductPrice.product_id == product.id,
                        ProductPrice.product_version_id == version.id,
                        ProductPrice.valid_from <= func.now(),
                        (ProductPrice.valid_to.is_(None) | (ProductPrice.valid_to > func.now())),
                    )
                    .order_by(ProductPrice.variant_id, ProductPrice.valid_from.desc())
                )
            )
            links = list(
                await self._session.scalars(
                    select(ProductLink)
                    .where(
                        ProductLink.workspace_id == workspace_id,
                        ProductLink.product_id == product.id,
                        ProductLink.product_version_id == version.id,
                        (ProductLink.valid_from.is_(None) | (ProductLink.valid_from <= func.now())),
                        (ProductLink.valid_to.is_(None) | (ProductLink.valid_to > func.now())),
                    )
                    .order_by(ProductLink.sort_order, ProductLink.id)
                )
            )
        return {
            "id": str(product.id),
            "brand_id": str(product.brand_id),
            "sku": product.sku,
            "name": product.name,
            "content_hash": product.content_hash,
            "version_id": str(version.id) if version else None,
            "version_number": version.version_number if version else None,
            "version_hash": version.content_hash if version else None,
            "version_effective_from": (
                version.effective_from.isoformat() if version else None
            ),
            "version_created_at": version.created_at.isoformat() if version else None,
            "description": version.description if version else None,
            "product_url": version.product_url if version else None,
            "attributes": version.attributes if version else {},
            "approved_facts": version.approved_facts if version else [],
            "banned_claims": version.banned_claims if version else [],
            "comparison_attributes": version.comparison_attributes if version else {},
            "shipping_facts": version.shipping_facts if version else {},
            "variants": [
                {
                    "id": str(item.id),
                    "sku": item.sku,
                    "name": item.name,
                    "option_values": item.option_values,
                    "approved_facts": item.approved_facts,
                    "created_at": item.created_at.isoformat(),
                    "content_hash": item.content_hash,
                }
                for item in variants
            ],
            "prices": [
                {
                    "id": str(item.id),
                    "variant_id": str(item.variant_id) if item.variant_id else None,
                    "amount": str(item.amount),
                    "compare_at_amount": (
                        str(item.compare_at_amount) if item.compare_at_amount is not None else None
                    ),
                    "currency": item.currency,
                    "valid_from": item.valid_from.isoformat(),
                    "valid_to": item.valid_to.isoformat() if item.valid_to else None,
                    "synced_at": item.synced_at.isoformat(),
                    "content_hash": item.content_hash,
                }
                for item in prices
            ],
            "links": [
                {
                    "id": str(item.id),
                    "kind": item.kind,
                    "label": item.label,
                    "url": item.url,
                    "disclosure_required": item.disclosure_required,
                    "disclosure_text": item.disclosure_text,
                    "tracking": item.tracking,
                    "valid_from": item.valid_from.isoformat() if item.valid_from else None,
                    "valid_to": item.valid_to.isoformat() if item.valid_to else None,
                    "created_at": item.created_at.isoformat(),
                    "content_hash": item.content_hash,
                }
                for item in links
            ],
        }

    async def _knowledge_source(self, workspace_id: UUID, source_id: UUID) -> dict[str, Any]:
        source = await self._session.scalar(
            select(KnowledgeSource).where(
                KnowledgeSource.workspace_id == workspace_id,
                KnowledgeSource.id == source_id,
                KnowledgeSource.deleted_at.is_(None),
            )
        )
        if (
            source is None
            or source.state != SourceState.READY.value
            or source.rights_status == RightsStatus.PROHIBITED.value
            or source.current_version_id is None
        ):
            raise _reference_error("knowledge_source_ids", source_id)
        version = await self._session.scalar(
            select(SourceVersion).where(
                SourceVersion.workspace_id == workspace_id,
                SourceVersion.id == source.current_version_id,
            )
        )
        if version is None:
            raise _reference_error("knowledge_source_ids", source_id)
        return {
            "id": str(source.id),
            "name": source.name,
            "source_type": source.source_type,
            "rights_status": source.rights_status,
            "use_scope": source.use_scope,
            "quality_grade": source.quality_grade,
            "version_id": str(version.id),
            "version": version.version,
            "content_hash": version.content_hash,
            "retrieved_at": version.retrieved_at.isoformat(),
        }

    async def _keyword(
        self,
        workspace_id: UUID,
        keyword_id: UUID | None,
        *,
        fallback_text: str | None,
    ) -> dict[str, Any]:
        if keyword_id is None:
            assert fallback_text is not None
            return {
                "id": None,
                "display_text": fallback_text,
                "normalized": fallback_text.casefold(),
                "source": "USER_INPUT",
            }
        result = await self._session.execute(
            text(
                """
                SELECT id, workspace_id, display_text, normalized, language, region, intent,
                       intent_source, intent_confidence, intent_signals_json,
                       brand_alignment, risk_tags_json, is_excluded, exclusion_reason
                FROM keywords
                WHERE workspace_id = :workspace_id AND id = :keyword_id
                """
            ),
            {"workspace_id": str(workspace_id), "keyword_id": str(keyword_id)},
        )
        row = result.mappings().one_or_none()
        if row is None:
            raise _reference_error("keyword_id", keyword_id)
        snapshot = {
            key: str(value) if key in {"id", "workspace_id"} else value
            for key, value in row.items()
        }
        snapshot["evidence"] = await self._keyword_evidence(workspace_id, keyword_id)
        return snapshot

    async def _cluster(
        self, workspace_id: UUID, cluster_id: UUID | None
    ) -> dict[str, Any] | None:
        if cluster_id is None:
            return None
        result = await self._session.execute(
            text(
                """
                SELECT id, workspace_id, name, kind, method, version, primary_keyword_id,
                       intent, confidence, decision_state, signals_json
                FROM keyword_clusters
                WHERE workspace_id = :workspace_id AND id = :cluster_id
                """
            ),
            {"workspace_id": str(workspace_id), "cluster_id": str(cluster_id)},
        )
        row = result.mappings().one_or_none()
        if row is None:
            raise _reference_error("keyword_cluster_id", cluster_id)
        members_result = await self._session.execute(
            text(
                """
                SELECT m.keyword_id, m.similarity_score, m.is_primary, m.signals_json,
                       k.display_text, k.normalized, k.language, k.region, k.intent
                FROM keyword_cluster_members AS m
                JOIN keywords AS k
                  ON k.workspace_id = m.workspace_id AND k.id = m.keyword_id
                WHERE m.workspace_id = :workspace_id AND m.cluster_id = :cluster_id
                ORDER BY m.is_primary DESC, m.similarity_score DESC, m.keyword_id
                LIMIT 1000
                """
            ),
            {"workspace_id": str(workspace_id), "cluster_id": str(cluster_id)},
        )
        cluster = {
            key: str(value) if key in {"id", "workspace_id", "primary_keyword_id"} and value else value
            for key, value in row.items()
        }
        cluster["members"] = [
            {
                key: str(value) if key == "keyword_id" else value
                for key, value in member.items()
            }
            for member in members_result.mappings()
        ]
        return cluster

    async def _keyword_evidence(self, workspace_id: UUID, keyword_id: UUID) -> dict[str, Any]:
        metric_result = await self._session.execute(
            text(
                """
                SELECT id, job_id, provider_connection_id, provider, source_class,
                       source_label, value_kind, measured_at, retrieved_at, expires_at,
                       period_start, period_end, dimensions_json, dimensions_hash,
                       metrics_json, trend_points_json, demographics_json,
                       serp_samples_json, confidence, limitations_json, request_hash,
                       adapter_name, adapter_version, transform_version, raw_object_ref,
                       raw_response_hash, is_cached, is_stale
                FROM keyword_metrics
                WHERE workspace_id = :workspace_id AND keyword_id = :keyword_id
                  AND (expires_at IS NULL OR expires_at > now())
                ORDER BY measured_at DESC, id DESC
                LIMIT 1
                """
            ),
            {"workspace_id": str(workspace_id), "keyword_id": str(keyword_id)},
        )
        metric = metric_result.mappings().one_or_none()
        score_result = await self._session.execute(
            text(
                """
                SELECT id, metric_snapshot_id, profile_id, opportunity_score, components_json,
                       coverage, confidence, saturation_score, difficulty_lower,
                       difficulty_upper, difficulty_confidence, commerciality_score,
                       freshness_score, risk_score, score_version, scored_at
                FROM keyword_score_snapshots
                WHERE workspace_id = :workspace_id AND keyword_id = :keyword_id
                ORDER BY scored_at DESC, id DESC
                LIMIT 1
                """
            ),
            {"workspace_id": str(workspace_id), "keyword_id": str(keyword_id)},
        )
        score = score_result.mappings().one_or_none()
        if metric is None and score is None:
            return {"availability": "UNAVAILABLE", "reason": "NO_VALID_SNAPSHOT"}
        return {
            "availability": "AVAILABLE",
            "metric": _json_safe_mapping(metric) if metric is not None else None,
            "score": _json_safe_mapping(score) if score is not None else None,
        }


def _reference_error(field: str, resource_id: UUID) -> AppError:
    return AppError(
        code="PLANNING_REFERENCE_INVALID",
        message="같은 워크스페이스의 활성 참조만 사용할 수 있습니다.",
        status_code=422,
        fields=[{"path": field, "reason": str(resource_id)}],
    )


def _json_safe_mapping(row: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, UUID):
            result[key] = str(value)
        elif hasattr(value, "isoformat"):
            result[key] = value.isoformat()
        elif type(value).__module__ == "decimal":
            result[key] = str(value)
        else:
            result[key] = value
    return result
