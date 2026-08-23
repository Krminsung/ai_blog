"""Tenant-safe async application service for brand catalog operations."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from blogops.core.errors import AppError
from blogops.db.session import apply_workspace_scope
from blogops.domain.brand.models import (
    AudiencePersona,
    Brand,
    BrandVersion,
    CatalogStatus,
    Product,
    ProductAsset,
    ProductLink,
    ProductPrice,
    ProductVariant,
    ProductVersion,
)
from blogops.domain.brand.schemas import (
    AudiencePersonaCreate,
    AudiencePersonaRead,
    AudiencePersonaUpdate,
    BrandCreate,
    BrandRead,
    BrandUpdate,
    BrandVersionCreate,
    BrandVersionRead,
    DeactivateRequest,
    ProductAssetRead,
    ProductCreate,
    ProductLinkRead,
    ProductPriceCreate,
    ProductPriceRead,
    ProductRead,
    ProductUpdate,
    ProductVariantRead,
    ProductVersionCreate,
    ProductVersionRead,
)
from blogops.services.audit import append_audit_log
from blogops.services.outbox import add_outbox_event

_OUTBOX_SCHEMA_VERSION = "1.0"
_PRICE_STALE_AFTER = timedelta(hours=24)


def canonical_hash(payload: Any) -> str:
    """Return a stable digest for JSON-compatible snapshot data."""
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _not_found(resource: str) -> AppError:
    return AppError(
        code=f"{resource.upper()}_NOT_FOUND",
        message="요청한 리소스를 찾을 수 없습니다.",
        status_code=404,
    )


def _inactive(resource: str) -> AppError:
    return AppError(
        code=f"{resource.upper()}_INACTIVE",
        message="비활성 리소스에는 새 변경을 추가할 수 없습니다.",
        status_code=409,
    )


def _version_conflict(resource: str, expected: int, actual: int) -> AppError:
    return AppError(
        code="OPTIMISTIC_LOCK_CONFLICT",
        message="다른 요청이 먼저 리소스를 변경했습니다. 최신 값을 다시 조회해 주세요.",
        status_code=409,
        fields=[
            {"path": "expected_lock_version", "reason": f"expected {expected}, actual {actual}"},
            {"path": "resource", "reason": resource},
        ],
    )


def _assert_lock(resource: str, expected: int, actual: int) -> None:
    if expected != actual:
        raise _version_conflict(resource, expected, actual)


def _brand_root_payload(brand: Brand) -> dict[str, Any]:
    return {
        "id": str(brand.id),
        "workspace_id": str(brand.workspace_id),
        "name": brand.name,
        "description": brand.description,
        "industry": brand.industry,
        "website_url": brand.website_url,
        "status": brand.status,
        "current_version_id": str(brand.current_version_id) if brand.current_version_id else None,
        "deactivated_at": brand.deactivated_at.isoformat() if brand.deactivated_at else None,
    }


def _persona_payload(persona: AudiencePersona) -> dict[str, Any]:
    return {
        "id": str(persona.id),
        "workspace_id": str(persona.workspace_id),
        "brand_id": str(persona.brand_id) if persona.brand_id else None,
        "name": persona.name,
        "description": persona.description,
        "age_range": persona.age_range,
        "situations": persona.situations,
        "interests": persona.interests,
        "challenges": persona.challenges,
        "knowledge_level": persona.knowledge_level,
        "search_intents": persona.search_intents,
        "journey_stage": persona.journey_stage,
        "prohibited_targeting": persona.prohibited_targeting,
        "status": persona.status,
        "deactivated_at": persona.deactivated_at.isoformat() if persona.deactivated_at else None,
    }


def _product_root_payload(product: Product) -> dict[str, Any]:
    return {
        "id": str(product.id),
        "workspace_id": str(product.workspace_id),
        "brand_id": str(product.brand_id),
        "sku": product.sku,
        "name": product.name,
        "source": product.source,
        "external_id": product.external_id,
        "status": product.status,
        "current_version_id": (
            str(product.current_version_id) if product.current_version_id else None
        ),
        "last_synced_at": product.last_synced_at.isoformat() if product.last_synced_at else None,
        "deactivated_at": product.deactivated_at.isoformat() if product.deactivated_at else None,
    }


async def _flush_mutation(session: AsyncSession, resource: str) -> None:
    try:
        await session.flush()
    except StaleDataError as exc:
        raise AppError(
            code="OPTIMISTIC_LOCK_CONFLICT",
            message="다른 요청이 먼저 리소스를 변경했습니다. 최신 값을 다시 조회해 주세요.",
            status_code=409,
            fields=[{"path": "resource", "reason": resource}],
        ) from exc
    except IntegrityError as exc:
        raise AppError(
            code="CATALOG_CONFLICT",
            message="같은 식별자 또는 스냅샷이 이미 존재합니다.",
            status_code=409,
            fields=[{"path": "resource", "reason": resource}],
        ) from exc


class BrandCatalogService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _scope(self, workspace_id: UUID) -> None:
        await apply_workspace_scope(self._session, workspace_id)

    async def _record_change(
        self,
        *,
        workspace_id: UUID,
        actor_id: UUID,
        action: str,
        aggregate_type: str,
        aggregate_id: UUID,
        details: dict[str, Any],
        event_payload: dict[str, Any],
    ) -> None:
        await append_audit_log(
            self._session,
            workspace_id=workspace_id,
            actor_id=actor_id,
            action=action,
            target_type=aggregate_type,
            target_id=str(aggregate_id),
            details=details,
        )
        await add_outbox_event(
            self._session,
            workspace_id=workspace_id,
            aggregate_type=aggregate_type,
            aggregate_id=str(aggregate_id),
            event_type=action,
            schema_version=_OUTBOX_SCHEMA_VERSION,
            payload={
                "workspace_id": str(workspace_id),
                "actor_id": str(actor_id),
                "aggregate_id": str(aggregate_id),
                **event_payload,
            },
        )

    async def _brand(
        self, workspace_id: UUID, brand_id: UUID, *, require_active: bool = False
    ) -> Brand:
        brand = await self._session.scalar(
            select(Brand).where(Brand.workspace_id == workspace_id, Brand.id == brand_id)
        )
        if brand is None:
            raise _not_found("brand")
        if require_active and brand.status != CatalogStatus.ACTIVE.value:
            raise _inactive("brand")
        return brand

    async def _brand_response(self, brand: Brand, *, include_version: bool = True) -> BrandRead:
        current: BrandVersionRead | None = None
        if include_version and brand.current_version_id is not None:
            version = await self._session.scalar(
                select(BrandVersion).where(
                    BrandVersion.workspace_id == brand.workspace_id,
                    BrandVersion.brand_id == brand.id,
                    BrandVersion.id == brand.current_version_id,
                )
            )
            if version is not None:
                current = BrandVersionRead.model_validate(version)
        return BrandRead.model_validate(brand).model_copy(update={"current_version": current})

    async def create_brand(
        self, workspace_id: UUID, actor_id: UUID, data: BrandCreate
    ) -> BrandRead:
        await self._scope(workspace_id)
        duplicate = await self._session.scalar(
            select(Brand.id).where(Brand.workspace_id == workspace_id, Brand.name == data.name)
        )
        if duplicate is not None:
            raise AppError(
                code="BRAND_NAME_EXISTS",
                message="워크스페이스에 같은 이름의 브랜드가 이미 있습니다.",
                status_code=409,
                fields=[{"path": "name", "reason": "duplicate"}],
            )

        brand_id = uuid4()
        version_id = uuid4()
        brand = Brand(
            id=brand_id,
            workspace_id=workspace_id,
            name=data.name,
            description=data.description,
            industry=data.industry,
            website_url=data.website_url,
            status=CatalogStatus.ACTIVE.value,
            current_version_id=version_id,
            lock_version=1,
            content_hash="",
        )
        brand.content_hash = canonical_hash(_brand_root_payload(brand))
        version_payload = data.initial_version.model_dump(mode="json")
        version = BrandVersion(
            id=version_id,
            workspace_id=workspace_id,
            brand_id=brand_id,
            version_number=1,
            voice=version_payload["voice"],
            preferred_expressions=version_payload["preferred_expressions"],
            required_terms=version_payload["required_terms"],
            required_phrases=version_payload["required_phrases"],
            banned_terms=version_payload["banned_terms"],
            banned_claims=version_payload["banned_claims"],
            banned_rules=version_payload["banned_rules"],
            style_dictionary=version_payload["style_dictionary"],
            competitor_policy=version_payload["competitor_policy"],
            visual_config=version_payload["visual_config"],
            style_samples=version_payload["style_samples"],
            sample_style_features=version_payload["sample_style_features"],
            effective_from=data.initial_version.effective_from,
            created_by=actor_id,
            lock_version=1,
            content_hash=canonical_hash(version_payload),
        )
        # The current-version FK is deferred.  Flush the root first so the reverse
        # brand_id FK never depends on ORM relationship ordering.
        self._session.add(brand)
        await _flush_mutation(self._session, "brand")
        self._session.add(version)
        await _flush_mutation(self._session, "brand_version")
        await self._record_change(
            workspace_id=workspace_id,
            actor_id=actor_id,
            action="brand.created",
            aggregate_type="brand",
            aggregate_id=brand.id,
            details={
                "content_hash": brand.content_hash,
                "brand_version_id": str(version.id),
                "brand_version_hash": version.content_hash,
            },
            event_payload={
                "version_id": str(version.id),
                "version_number": version.version_number,
                "content_hash": version.content_hash,
            },
        )
        return await self._brand_response(brand)

    async def list_brands(
        self,
        workspace_id: UUID,
        *,
        include_inactive: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[BrandRead]:
        await self._scope(workspace_id)
        statement = select(Brand).where(Brand.workspace_id == workspace_id)
        if not include_inactive:
            statement = statement.where(Brand.status == CatalogStatus.ACTIVE.value)
        brands = list(
            await self._session.scalars(
                statement.order_by(Brand.created_at, Brand.id).limit(limit).offset(offset)
            )
        )
        return [await self._brand_response(brand, include_version=False) for brand in brands]

    async def get_brand(self, workspace_id: UUID, brand_id: UUID) -> BrandRead:
        await self._scope(workspace_id)
        return await self._brand_response(await self._brand(workspace_id, brand_id))

    async def update_brand(
        self, workspace_id: UUID, actor_id: UUID, brand_id: UUID, data: BrandUpdate
    ) -> BrandRead:
        await self._scope(workspace_id)
        brand = await self._brand(workspace_id, brand_id)
        _assert_lock("brand", data.expected_lock_version, brand.lock_version)
        if "name" in data.model_fields_set and data.name != brand.name:
            duplicate = await self._session.scalar(
                select(Brand.id).where(
                    Brand.workspace_id == workspace_id,
                    Brand.name == data.name,
                    Brand.id != brand_id,
                )
            )
            if duplicate is not None:
                raise AppError(
                    code="BRAND_NAME_EXISTS",
                    message="워크스페이스에 같은 이름의 브랜드가 이미 있습니다.",
                    status_code=409,
                    fields=[{"path": "name", "reason": "duplicate"}],
                )
        before_hash = brand.content_hash
        for field in ("name", "description", "industry", "website_url"):
            if field in data.model_fields_set:
                setattr(brand, field, getattr(data, field))
        brand.content_hash = canonical_hash(_brand_root_payload(brand))
        await _flush_mutation(self._session, "brand")
        await self._record_change(
            workspace_id=workspace_id,
            actor_id=actor_id,
            action="brand.updated",
            aggregate_type="brand",
            aggregate_id=brand.id,
            details={"before_hash": before_hash, "after_hash": brand.content_hash},
            event_payload={"content_hash": brand.content_hash, "lock_version": brand.lock_version},
        )
        return await self._brand_response(brand)

    async def create_brand_version(
        self,
        workspace_id: UUID,
        actor_id: UUID,
        brand_id: UUID,
        data: BrandVersionCreate,
    ) -> BrandVersionRead:
        await self._scope(workspace_id)
        brand = await self._brand(workspace_id, brand_id, require_active=True)
        payload = data.model_dump(mode="json")
        content_hash = canonical_hash(payload)
        duplicate = await self._session.scalar(
            select(BrandVersion.id).where(
                BrandVersion.workspace_id == workspace_id,
                BrandVersion.brand_id == brand_id,
                BrandVersion.content_hash == content_hash,
            )
        )
        if duplicate is not None:
            raise AppError(
                code="BRAND_VERSION_EXISTS",
                message="동일한 브랜드 버전이 이미 존재합니다.",
                status_code=409,
                fields=[{"path": "content_hash", "reason": content_hash}],
            )
        latest = await self._session.scalar(
            select(func.max(BrandVersion.version_number)).where(
                BrandVersion.workspace_id == workspace_id,
                BrandVersion.brand_id == brand_id,
            )
        )
        version = BrandVersion(
            id=uuid4(),
            workspace_id=workspace_id,
            brand_id=brand_id,
            version_number=(latest or 0) + 1,
            voice=payload["voice"],
            preferred_expressions=payload["preferred_expressions"],
            required_terms=payload["required_terms"],
            required_phrases=payload["required_phrases"],
            banned_terms=payload["banned_terms"],
            banned_claims=payload["banned_claims"],
            banned_rules=payload["banned_rules"],
            style_dictionary=payload["style_dictionary"],
            competitor_policy=payload["competitor_policy"],
            visual_config=payload["visual_config"],
            style_samples=payload["style_samples"],
            sample_style_features=payload["sample_style_features"],
            effective_from=data.effective_from,
            created_by=actor_id,
            lock_version=1,
            content_hash=content_hash,
        )
        before_hash = brand.content_hash
        brand.current_version_id = version.id
        brand.content_hash = canonical_hash(_brand_root_payload(brand))
        self._session.add(version)
        await _flush_mutation(self._session, "brand_version")
        await self._record_change(
            workspace_id=workspace_id,
            actor_id=actor_id,
            action="brand.version_created",
            aggregate_type="brand",
            aggregate_id=brand.id,
            details={
                "before_hash": before_hash,
                "after_hash": brand.content_hash,
                "version_id": str(version.id),
                "version_number": version.version_number,
                "version_hash": version.content_hash,
            },
            event_payload={
                "version_id": str(version.id),
                "version_number": version.version_number,
                "content_hash": version.content_hash,
            },
        )
        return BrandVersionRead.model_validate(version)

    async def list_brand_versions(
        self, workspace_id: UUID, brand_id: UUID, *, limit: int = 100, offset: int = 0
    ) -> list[BrandVersionRead]:
        await self._scope(workspace_id)
        await self._brand(workspace_id, brand_id)
        versions = list(
            await self._session.scalars(
                select(BrandVersion)
                .where(
                    BrandVersion.workspace_id == workspace_id,
                    BrandVersion.brand_id == brand_id,
                )
                .order_by(BrandVersion.version_number.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        return [BrandVersionRead.model_validate(version) for version in versions]

    async def get_brand_version(
        self, workspace_id: UUID, brand_id: UUID, version_number: int
    ) -> BrandVersionRead:
        await self._scope(workspace_id)
        await self._brand(workspace_id, brand_id)
        version = await self._session.scalar(
            select(BrandVersion).where(
                BrandVersion.workspace_id == workspace_id,
                BrandVersion.brand_id == brand_id,
                BrandVersion.version_number == version_number,
            )
        )
        if version is None:
            raise _not_found("brand_version")
        return BrandVersionRead.model_validate(version)

    async def deactivate_brand(
        self,
        workspace_id: UUID,
        actor_id: UUID,
        brand_id: UUID,
        data: DeactivateRequest,
    ) -> BrandRead:
        await self._scope(workspace_id)
        brand = await self._brand(workspace_id, brand_id)
        _assert_lock("brand", data.expected_lock_version, brand.lock_version)
        if brand.status == CatalogStatus.INACTIVE.value:
            return await self._brand_response(brand)
        before_hash = brand.content_hash
        brand.status = CatalogStatus.INACTIVE.value
        brand.deactivated_at = datetime.now(UTC)
        brand.content_hash = canonical_hash(_brand_root_payload(brand))
        await _flush_mutation(self._session, "brand")
        await self._record_change(
            workspace_id=workspace_id,
            actor_id=actor_id,
            action="brand.deactivated",
            aggregate_type="brand",
            aggregate_id=brand.id,
            details={
                "reason": data.reason,
                "before_hash": before_hash,
                "after_hash": brand.content_hash,
            },
            event_payload={"content_hash": brand.content_hash, "reason": data.reason},
        )
        return await self._brand_response(brand)

    async def _persona(self, workspace_id: UUID, persona_id: UUID) -> AudiencePersona:
        persona = await self._session.scalar(
            select(AudiencePersona).where(
                AudiencePersona.workspace_id == workspace_id,
                AudiencePersona.id == persona_id,
            )
        )
        if persona is None:
            raise _not_found("persona")
        return persona

    async def create_persona(
        self, workspace_id: UUID, actor_id: UUID, data: AudiencePersonaCreate
    ) -> AudiencePersonaRead:
        await self._scope(workspace_id)
        if data.brand_id is not None:
            await self._brand(workspace_id, data.brand_id, require_active=True)
        persona = AudiencePersona(
            id=uuid4(),
            workspace_id=workspace_id,
            brand_id=data.brand_id,
            name=data.name,
            description=data.description,
            age_range=data.age_range.model_dump(mode="json") if data.age_range else None,
            situations=data.situations,
            interests=data.interests,
            challenges=data.challenges,
            knowledge_level=data.knowledge_level.value,
            search_intents=[intent.value for intent in data.search_intents],
            journey_stage=data.journey_stage.value,
            prohibited_targeting=[
                rule.model_dump(mode="json") for rule in data.prohibited_targeting
            ],
            status=CatalogStatus.ACTIVE.value,
            lock_version=1,
            content_hash="",
        )
        persona.content_hash = canonical_hash(_persona_payload(persona))
        self._session.add(persona)
        await _flush_mutation(self._session, "persona")
        await self._record_change(
            workspace_id=workspace_id,
            actor_id=actor_id,
            action="persona.created",
            aggregate_type="audience_persona",
            aggregate_id=persona.id,
            details={"content_hash": persona.content_hash},
            event_payload={"content_hash": persona.content_hash},
        )
        return AudiencePersonaRead.model_validate(persona)

    async def list_personas(
        self,
        workspace_id: UUID,
        *,
        brand_id: UUID | None = None,
        include_inactive: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AudiencePersonaRead]:
        await self._scope(workspace_id)
        statement = select(AudiencePersona).where(AudiencePersona.workspace_id == workspace_id)
        if brand_id is not None:
            statement = statement.where(AudiencePersona.brand_id == brand_id)
        if not include_inactive:
            statement = statement.where(AudiencePersona.status == CatalogStatus.ACTIVE.value)
        personas = list(
            await self._session.scalars(
                statement.order_by(AudiencePersona.created_at, AudiencePersona.id)
                .limit(limit)
                .offset(offset)
            )
        )
        return [AudiencePersonaRead.model_validate(persona) for persona in personas]

    async def get_persona(
        self, workspace_id: UUID, persona_id: UUID
    ) -> AudiencePersonaRead:
        await self._scope(workspace_id)
        return AudiencePersonaRead.model_validate(await self._persona(workspace_id, persona_id))

    async def update_persona(
        self,
        workspace_id: UUID,
        actor_id: UUID,
        persona_id: UUID,
        data: AudiencePersonaUpdate,
    ) -> AudiencePersonaRead:
        await self._scope(workspace_id)
        persona = await self._persona(workspace_id, persona_id)
        _assert_lock("persona", data.expected_lock_version, persona.lock_version)
        if "brand_id" in data.model_fields_set and data.brand_id is not None:
            await self._brand(workspace_id, data.brand_id, require_active=True)
        before_hash = persona.content_hash
        for field in ("brand_id", "name", "description", "situations", "interests", "challenges"):
            if field in data.model_fields_set:
                setattr(persona, field, getattr(data, field))
        if "age_range" in data.model_fields_set:
            persona.age_range = (
                data.age_range.model_dump(mode="json") if data.age_range is not None else None
            )
        if "knowledge_level" in data.model_fields_set and data.knowledge_level is not None:
            persona.knowledge_level = data.knowledge_level.value
        if "search_intents" in data.model_fields_set and data.search_intents is not None:
            persona.search_intents = [intent.value for intent in data.search_intents]
        if "journey_stage" in data.model_fields_set and data.journey_stage is not None:
            persona.journey_stage = data.journey_stage.value
        if (
            "prohibited_targeting" in data.model_fields_set
            and data.prohibited_targeting is not None
        ):
            persona.prohibited_targeting = [
                rule.model_dump(mode="json") for rule in data.prohibited_targeting
            ]
        persona.content_hash = canonical_hash(_persona_payload(persona))
        await _flush_mutation(self._session, "persona")
        await self._record_change(
            workspace_id=workspace_id,
            actor_id=actor_id,
            action="persona.updated",
            aggregate_type="audience_persona",
            aggregate_id=persona.id,
            details={"before_hash": before_hash, "after_hash": persona.content_hash},
            event_payload={
                "content_hash": persona.content_hash,
                "lock_version": persona.lock_version,
            },
        )
        return AudiencePersonaRead.model_validate(persona)

    async def deactivate_persona(
        self,
        workspace_id: UUID,
        actor_id: UUID,
        persona_id: UUID,
        data: DeactivateRequest,
    ) -> AudiencePersonaRead:
        await self._scope(workspace_id)
        persona = await self._persona(workspace_id, persona_id)
        _assert_lock("persona", data.expected_lock_version, persona.lock_version)
        if persona.status == CatalogStatus.INACTIVE.value:
            return AudiencePersonaRead.model_validate(persona)
        before_hash = persona.content_hash
        persona.status = CatalogStatus.INACTIVE.value
        persona.deactivated_at = datetime.now(UTC)
        persona.content_hash = canonical_hash(_persona_payload(persona))
        await _flush_mutation(self._session, "persona")
        await self._record_change(
            workspace_id=workspace_id,
            actor_id=actor_id,
            action="persona.deactivated",
            aggregate_type="audience_persona",
            aggregate_id=persona.id,
            details={
                "reason": data.reason,
                "before_hash": before_hash,
                "after_hash": persona.content_hash,
            },
            event_payload={"content_hash": persona.content_hash, "reason": data.reason},
        )
        return AudiencePersonaRead.model_validate(persona)

    async def _product(
        self, workspace_id: UUID, product_id: UUID, *, require_active: bool = False
    ) -> Product:
        product = await self._session.scalar(
            select(Product).where(Product.workspace_id == workspace_id, Product.id == product_id)
        )
        if product is None:
            raise _not_found("product")
        if require_active and product.status != CatalogStatus.ACTIVE.value:
            raise _inactive("product")
        if require_active:
            await self._brand(workspace_id, product.brand_id, require_active=True)
        return product

    async def _new_product_snapshot(
        self,
        *,
        workspace_id: UUID,
        actor_id: UUID,
        product_id: UUID,
        version_number: int,
        data: ProductVersionCreate,
        version_id: UUID | None = None,
    ) -> ProductVersion:
        payload = data.model_dump(mode="json")
        version = ProductVersion(
            id=version_id or uuid4(),
            workspace_id=workspace_id,
            product_id=product_id,
            version_number=version_number,
            description=data.description,
            product_url=data.product_url,
            attributes=payload["attributes"],
            approved_facts=payload["approved_facts"],
            banned_claims=payload["banned_claims"],
            comparison_attributes=payload["comparison_attributes"],
            shipping_facts=payload["shipping_facts"],
            effective_from=data.effective_from,
            created_by=actor_id,
            lock_version=1,
            content_hash=canonical_hash(payload),
        )
        self._session.add(version)
        await _flush_mutation(self._session, "product_version")

        variant_rows: list[tuple[ProductVariant, Any]] = []
        for position, variant_data in enumerate(data.variants):
            variant_payload = variant_data.model_dump(mode="json", exclude={"prices"})
            variant = ProductVariant(
                id=uuid4(),
                workspace_id=workspace_id,
                product_id=product_id,
                product_version_id=version.id,
                sku=variant_data.sku,
                name=variant_data.name,
                option_values=variant_payload["option_values"],
                approved_facts=variant_payload["approved_facts"],
                is_available=variant_data.is_available,
                sort_order=position,
                lock_version=1,
                content_hash=canonical_hash(
                    {"position": position, "snapshot": variant_payload}
                ),
            )
            self._session.add(variant)
            variant_rows.append((variant, variant_data))

        if variant_rows:
            await _flush_mutation(self._session, "product_variant")

        for price_data in data.prices:
            self._add_price(
                workspace_id=workspace_id,
                product_id=product_id,
                version_id=version.id,
                variant_id=None,
                data=price_data,
            )
        for variant, variant_data in variant_rows:
            for price_data in variant_data.prices:
                self._add_price(
                    workspace_id=workspace_id,
                    product_id=product_id,
                    version_id=version.id,
                    variant_id=variant.id,
                    data=price_data,
                )

        for position, asset_data in enumerate(data.assets):
            asset_payload = asset_data.model_dump(mode="json")
            self._session.add(
                ProductAsset(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    product_id=product_id,
                    product_version_id=version.id,
                    media_asset_id=asset_data.media_asset_id,
                    uri=asset_data.uri,
                    usage_scope=asset_data.usage_scope,
                    license_name=asset_data.license_name,
                    license_reference=asset_data.license_reference,
                    alt_text=asset_data.alt_text,
                    is_official=asset_data.is_official,
                    valid_from=asset_data.valid_from,
                    valid_to=asset_data.valid_to,
                    sort_order=position,
                    lock_version=1,
                    content_hash=canonical_hash(
                        {"position": position, "snapshot": asset_payload}
                    ),
                )
            )
        for position, link_data in enumerate(data.links):
            link_payload = link_data.model_dump(mode="json")
            self._session.add(
                ProductLink(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    product_id=product_id,
                    product_version_id=version.id,
                    kind=link_data.kind.value,
                    label=link_data.label,
                    url=link_data.url,
                    disclosure_required=link_data.disclosure_required,
                    disclosure_text=link_data.disclosure_text,
                    tracking=link_payload["tracking"],
                    valid_from=link_data.valid_from,
                    valid_to=link_data.valid_to,
                    sort_order=position,
                    lock_version=1,
                    content_hash=canonical_hash(
                        {"position": position, "snapshot": link_payload}
                    ),
                )
            )
        await _flush_mutation(self._session, "product_snapshot_children")
        return version

    def _add_price(
        self,
        *,
        workspace_id: UUID,
        product_id: UUID,
        version_id: UUID,
        variant_id: UUID | None,
        data: ProductPriceCreate,
    ) -> None:
        payload = data.model_dump(mode="json")
        self._session.add(
            ProductPrice(
                id=uuid4(),
                workspace_id=workspace_id,
                product_id=product_id,
                product_version_id=version_id,
                variant_id=variant_id,
                amount=data.amount,
                compare_at_amount=data.compare_at_amount,
                currency=data.currency,
                valid_from=data.valid_from,
                valid_to=data.valid_to,
                synced_at=data.synced_at,
                lock_version=1,
                content_hash=canonical_hash(
                    {"variant_id": str(variant_id) if variant_id else None, "snapshot": payload}
                ),
            )
        )

    async def create_product(
        self, workspace_id: UUID, actor_id: UUID, data: ProductCreate
    ) -> ProductRead:
        await self._scope(workspace_id)
        await self._brand(workspace_id, data.brand_id, require_active=True)
        duplicate = await self._session.scalar(
            select(Product.id).where(
                Product.workspace_id == workspace_id,
                Product.brand_id == data.brand_id,
                Product.sku == data.sku,
            )
        )
        if duplicate is not None:
            raise AppError(
                code="PRODUCT_SKU_EXISTS",
                message="브랜드에 같은 SKU의 상품이 이미 있습니다.",
                status_code=409,
                fields=[{"path": "sku", "reason": "duplicate"}],
            )
        product_id = uuid4()
        version_id = uuid4()
        product = Product(
            id=product_id,
            workspace_id=workspace_id,
            brand_id=data.brand_id,
            sku=data.sku,
            name=data.name,
            source=data.source.value,
            external_id=data.external_id,
            status=CatalogStatus.ACTIVE.value,
            current_version_id=version_id,
            last_synced_at=data.last_synced_at,
            lock_version=1,
            content_hash="",
        )
        product.content_hash = canonical_hash(_product_root_payload(product))
        self._session.add(product)
        await _flush_mutation(self._session, "product")
        version = await self._new_product_snapshot(
            workspace_id=workspace_id,
            actor_id=actor_id,
            product_id=product_id,
            version_number=1,
            data=data.initial_version,
            version_id=version_id,
        )
        await self._record_change(
            workspace_id=workspace_id,
            actor_id=actor_id,
            action="product.created",
            aggregate_type="product",
            aggregate_id=product.id,
            details={
                "content_hash": product.content_hash,
                "version_id": str(version.id),
                "version_hash": version.content_hash,
            },
            event_payload={
                "brand_id": str(product.brand_id),
                "sku": product.sku,
                "version_id": str(version.id),
                "version_number": version.version_number,
                "content_hash": version.content_hash,
            },
        )
        return await self._product_response(product)

    async def import_products(
        self, workspace_id: UUID, actor_id: UUID, items: list[ProductCreate]
    ) -> list[ProductRead]:
        """Import a bounded batch atomically in the caller's session transaction."""
        products: list[ProductRead] = []
        for item in items:
            products.append(await self.create_product(workspace_id, actor_id, item))
        return products

    async def list_products(
        self,
        workspace_id: UUID,
        *,
        brand_id: UUID | None = None,
        include_inactive: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ProductRead]:
        await self._scope(workspace_id)
        statement = select(Product).where(Product.workspace_id == workspace_id)
        if brand_id is not None:
            statement = statement.where(Product.brand_id == brand_id)
        if not include_inactive:
            statement = statement.where(Product.status == CatalogStatus.ACTIVE.value)
        products = list(
            await self._session.scalars(
                statement.order_by(Product.created_at, Product.id).limit(limit).offset(offset)
            )
        )
        return [ProductRead.model_validate(product) for product in products]

    async def get_product(self, workspace_id: UUID, product_id: UUID) -> ProductRead:
        await self._scope(workspace_id)
        return await self._product_response(await self._product(workspace_id, product_id))

    async def update_product(
        self,
        workspace_id: UUID,
        actor_id: UUID,
        product_id: UUID,
        data: ProductUpdate,
    ) -> ProductRead:
        await self._scope(workspace_id)
        product = await self._product(workspace_id, product_id)
        _assert_lock("product", data.expected_lock_version, product.lock_version)
        if "sku" in data.model_fields_set and data.sku != product.sku:
            duplicate = await self._session.scalar(
                select(Product.id).where(
                    Product.workspace_id == workspace_id,
                    Product.brand_id == product.brand_id,
                    Product.sku == data.sku,
                    Product.id != product_id,
                )
            )
            if duplicate is not None:
                raise AppError(
                    code="PRODUCT_SKU_EXISTS",
                    message="브랜드에 같은 SKU의 상품이 이미 있습니다.",
                    status_code=409,
                    fields=[{"path": "sku", "reason": "duplicate"}],
                )
        before_hash = product.content_hash
        for field in ("sku", "name", "external_id", "last_synced_at"):
            if field in data.model_fields_set:
                setattr(product, field, getattr(data, field))
        product.content_hash = canonical_hash(_product_root_payload(product))
        await _flush_mutation(self._session, "product")
        await self._record_change(
            workspace_id=workspace_id,
            actor_id=actor_id,
            action="product.updated",
            aggregate_type="product",
            aggregate_id=product.id,
            details={"before_hash": before_hash, "after_hash": product.content_hash},
            event_payload={
                "content_hash": product.content_hash,
                "lock_version": product.lock_version,
            },
        )
        return await self._product_response(product)

    async def create_product_version(
        self,
        workspace_id: UUID,
        actor_id: UUID,
        product_id: UUID,
        data: ProductVersionCreate,
    ) -> ProductVersionRead:
        await self._scope(workspace_id)
        product = await self._product(workspace_id, product_id, require_active=True)
        payload_hash = canonical_hash(data.model_dump(mode="json"))
        duplicate = await self._session.scalar(
            select(ProductVersion.id).where(
                ProductVersion.workspace_id == workspace_id,
                ProductVersion.product_id == product_id,
                ProductVersion.content_hash == payload_hash,
            )
        )
        if duplicate is not None:
            raise AppError(
                code="PRODUCT_VERSION_EXISTS",
                message="동일한 상품 사실 스냅샷이 이미 존재합니다.",
                status_code=409,
                fields=[{"path": "content_hash", "reason": payload_hash}],
            )
        latest = await self._session.scalar(
            select(func.max(ProductVersion.version_number)).where(
                ProductVersion.workspace_id == workspace_id,
                ProductVersion.product_id == product_id,
            )
        )
        version = await self._new_product_snapshot(
            workspace_id=workspace_id,
            actor_id=actor_id,
            product_id=product_id,
            version_number=(latest or 0) + 1,
            data=data,
        )
        before_hash = product.content_hash
        product.current_version_id = version.id
        product.content_hash = canonical_hash(_product_root_payload(product))
        await _flush_mutation(self._session, "product_version")
        await self._record_change(
            workspace_id=workspace_id,
            actor_id=actor_id,
            action="product.version_created",
            aggregate_type="product",
            aggregate_id=product.id,
            details={
                "before_hash": before_hash,
                "after_hash": product.content_hash,
                "version_id": str(version.id),
                "version_number": version.version_number,
                "version_hash": version.content_hash,
            },
            event_payload={
                "version_id": str(version.id),
                "version_number": version.version_number,
                "content_hash": version.content_hash,
            },
        )
        return await self._product_version_response(version)

    async def list_product_versions(
        self, workspace_id: UUID, product_id: UUID, *, limit: int = 100, offset: int = 0
    ) -> list[ProductVersionRead]:
        await self._scope(workspace_id)
        await self._product(workspace_id, product_id)
        versions = list(
            await self._session.scalars(
                select(ProductVersion)
                .where(
                    ProductVersion.workspace_id == workspace_id,
                    ProductVersion.product_id == product_id,
                )
                .order_by(ProductVersion.version_number.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        return [await self._product_version_response(version) for version in versions]

    async def get_product_version(
        self, workspace_id: UUID, product_id: UUID, version_number: int
    ) -> ProductVersionRead:
        await self._scope(workspace_id)
        await self._product(workspace_id, product_id)
        version = await self._session.scalar(
            select(ProductVersion).where(
                ProductVersion.workspace_id == workspace_id,
                ProductVersion.product_id == product_id,
                ProductVersion.version_number == version_number,
            )
        )
        if version is None:
            raise _not_found("product_version")
        return await self._product_version_response(version)

    async def deactivate_product(
        self,
        workspace_id: UUID,
        actor_id: UUID,
        product_id: UUID,
        data: DeactivateRequest,
    ) -> ProductRead:
        await self._scope(workspace_id)
        product = await self._product(workspace_id, product_id)
        _assert_lock("product", data.expected_lock_version, product.lock_version)
        if product.status == CatalogStatus.INACTIVE.value:
            return await self._product_response(product)
        before_hash = product.content_hash
        product.status = CatalogStatus.INACTIVE.value
        product.deactivated_at = datetime.now(UTC)
        product.content_hash = canonical_hash(_product_root_payload(product))
        await _flush_mutation(self._session, "product")
        await self._record_change(
            workspace_id=workspace_id,
            actor_id=actor_id,
            action="product.deactivated",
            aggregate_type="product",
            aggregate_id=product.id,
            details={
                "reason": data.reason,
                "before_hash": before_hash,
                "after_hash": product.content_hash,
            },
            event_payload={"content_hash": product.content_hash, "reason": data.reason},
        )
        return await self._product_response(product)

    async def _product_response(self, product: Product) -> ProductRead:
        current: ProductVersionRead | None = None
        if product.current_version_id is not None:
            version = await self._session.scalar(
                select(ProductVersion).where(
                    ProductVersion.workspace_id == product.workspace_id,
                    ProductVersion.product_id == product.id,
                    ProductVersion.id == product.current_version_id,
                )
            )
            if version is not None:
                current = await self._product_version_response(version)
        return ProductRead.model_validate(product).model_copy(update={"current_version": current})

    async def _product_version_response(self, version: ProductVersion) -> ProductVersionRead:
        variants = list(
            await self._session.scalars(
                select(ProductVariant)
                .where(
                    ProductVariant.workspace_id == version.workspace_id,
                    ProductVariant.product_id == version.product_id,
                    ProductVariant.product_version_id == version.id,
                )
                .order_by(ProductVariant.sort_order, ProductVariant.id)
            )
        )
        prices = list(
            await self._session.scalars(
                select(ProductPrice)
                .where(
                    ProductPrice.workspace_id == version.workspace_id,
                    ProductPrice.product_id == version.product_id,
                    ProductPrice.product_version_id == version.id,
                )
                .order_by(ProductPrice.valid_from, ProductPrice.id)
            )
        )
        assets = list(
            await self._session.scalars(
                select(ProductAsset)
                .where(
                    ProductAsset.workspace_id == version.workspace_id,
                    ProductAsset.product_id == version.product_id,
                    ProductAsset.product_version_id == version.id,
                )
                .order_by(ProductAsset.sort_order, ProductAsset.id)
            )
        )
        links = list(
            await self._session.scalars(
                select(ProductLink)
                .where(
                    ProductLink.workspace_id == version.workspace_id,
                    ProductLink.product_id == version.product_id,
                    ProductLink.product_version_id == version.id,
                )
                .order_by(ProductLink.sort_order, ProductLink.id)
            )
        )
        price_reads = [self._price_response(price) for price in prices]
        prices_by_variant: dict[UUID, list[ProductPriceRead]] = {}
        root_prices: list[ProductPriceRead] = []
        for price in price_reads:
            if price.variant_id is None:
                root_prices.append(price)
            else:
                prices_by_variant.setdefault(price.variant_id, []).append(price)
        variant_reads = [
            ProductVariantRead(
                id=variant.id,
                workspace_id=variant.workspace_id,
                product_id=variant.product_id,
                product_version_id=variant.product_version_id,
                sku=variant.sku,
                name=variant.name,
                option_values=variant.option_values,
                approved_facts=variant.approved_facts,
                is_available=variant.is_available,
                prices=prices_by_variant.get(variant.id, []),
                created_at=variant.created_at,
                lock_version=variant.lock_version,
                content_hash=variant.content_hash,
            )
            for variant in variants
        ]
        asset_reads = [ProductAssetRead.model_validate(asset) for asset in assets]
        link_reads = [ProductLinkRead.model_validate(link) for link in links]
        return ProductVersionRead.model_validate(version).model_copy(
            update={
                "prices": root_prices,
                "variants": variant_reads,
                "assets": asset_reads,
                "links": link_reads,
            }
        )

    @staticmethod
    def _price_response(price: ProductPrice) -> ProductPriceRead:
        now = datetime.now(UTC)
        freshness: Literal["CURRENT", "STALE", "NOT_YET_VALID", "EXPIRED"]
        if now < price.valid_from:
            freshness = "NOT_YET_VALID"
            is_current = False
        elif price.valid_to is not None and now >= price.valid_to:
            freshness = "EXPIRED"
            is_current = False
        elif now - price.synced_at > _PRICE_STALE_AFTER:
            freshness = "STALE"
            is_current = True
        else:
            freshness = "CURRENT"
            is_current = True
        return ProductPriceRead(
            id=price.id,
            workspace_id=price.workspace_id,
            product_id=price.product_id,
            product_version_id=price.product_version_id,
            variant_id=price.variant_id,
            amount=price.amount,
            compare_at_amount=price.compare_at_amount,
            currency=price.currency,
            valid_from=price.valid_from,
            valid_to=price.valid_to,
            synced_at=price.synced_at,
            is_current=is_current,
            freshness=freshness,
            created_at=price.created_at,
            lock_version=price.lock_version,
            content_hash=price.content_hash,
        )
