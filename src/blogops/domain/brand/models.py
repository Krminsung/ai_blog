"""Tenant-owned brand, audience and product catalog persistence models.

Mutable catalog roots use SQLAlchemy's version counter for optimistic locking.  Brand and
product versions (including their child rows) are append-only snapshots so a content job can
always resolve the exact facts that were available when it was created.
"""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from blogops.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class CatalogStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class KnowledgeLevel(StrEnum):
    BEGINNER = "BEGINNER"
    GENERAL = "GENERAL"
    EXPERT = "EXPERT"


class SearchIntent(StrEnum):
    INFORMATIONAL = "INFORMATIONAL"
    COMPARISON = "COMPARISON"
    PURCHASE = "PURCHASE"
    LOCAL = "LOCAL"
    NAVIGATIONAL = "NAVIGATIONAL"


class JourneyStage(StrEnum):
    AWARENESS = "AWARENESS"
    CONSIDERATION = "CONSIDERATION"
    PURCHASE = "PURCHASE"
    RETENTION = "RETENTION"


class ProductSource(StrEnum):
    MANUAL = "MANUAL"
    CSV = "CSV"
    API = "API"
    SHOPIFY = "SHOPIFY"
    CAFE24 = "CAFE24"


class ProductLinkKind(StrEnum):
    OFFICIAL = "OFFICIAL"
    AFFILIATE = "AFFILIATE"
    TRACKED = "TRACKED"


class Brand(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "brands"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="brand_workspace_id"),
        UniqueConstraint("workspace_id", "name", name="brand_workspace_name"),
        CheckConstraint("status IN ('ACTIVE', 'INACTIVE')", name="brand_status"),
        CheckConstraint("lock_version > 0", name="brand_lock_version_positive"),
        Index("ix_brands_workspace_status", "workspace_id", "status"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    industry: Mapped[str | None] = mapped_column(String(120))
    website_url: Mapped[str | None] = mapped_column(String(2048))
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=CatalogStatus.ACTIVE.value
    )
    current_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "brand_versions.id",
            name="fk_brands_current_version_id_brand_versions",
            ondelete="SET NULL",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        index=True,
    )
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    __mapper_args__ = {"version_id_col": lock_version}


class BrandVersion(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "brand_versions"
    __table_args__ = (
        UniqueConstraint("workspace_id", "brand_id", "version_number", name="brand_version_no"),
        UniqueConstraint("workspace_id", "brand_id", "content_hash", name="brand_version_hash"),
        CheckConstraint("version_number > 0", name="brand_version_number_positive"),
        CheckConstraint("lock_version > 0", name="brand_version_lock_positive"),
        Index(
            "ix_brand_versions_workspace_effective",
            "workspace_id",
            "brand_id",
            "effective_from",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    brand_id: Mapped[UUID] = mapped_column(
        ForeignKey("brands.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    voice: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    preferred_expressions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    required_terms: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    required_phrases: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    banned_terms: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    banned_claims: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    banned_rules: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    style_dictionary: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    competitor_policy: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    visual_config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    style_samples: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    sample_style_features: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    __mapper_args__ = {"version_id_col": lock_version}


class AudiencePersona(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "audience_personas"
    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE', 'INACTIVE')", name="persona_status"),
        CheckConstraint(
            "knowledge_level IN ('BEGINNER', 'GENERAL', 'EXPERT')",
            name="persona_knowledge_level",
        ),
        CheckConstraint(
            "journey_stage IN ('AWARENESS', 'CONSIDERATION', 'PURCHASE', 'RETENTION')",
            name="persona_journey_stage",
        ),
        CheckConstraint("lock_version > 0", name="persona_lock_version_positive"),
        Index("ix_audience_personas_workspace_brand", "workspace_id", "brand_id"),
        Index("ix_audience_personas_workspace_status", "workspace_id", "status"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    brand_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("brands.id", ondelete="RESTRICT"), index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    age_range: Mapped[dict[str, int] | None] = mapped_column(JSONB)
    situations: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    interests: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    challenges: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    knowledge_level: Mapped[str] = mapped_column(String(16), nullable=False)
    search_intents: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    journey_stage: Mapped[str] = mapped_column(String(24), nullable=False)
    prohibited_targeting: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=CatalogStatus.ACTIVE.value
    )
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    __mapper_args__ = {"version_id_col": lock_version}


class Product(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("workspace_id", "brand_id", "sku", name="product_brand_sku"),
        CheckConstraint("status IN ('ACTIVE', 'INACTIVE')", name="product_status"),
        CheckConstraint(
            "source IN ('MANUAL', 'CSV', 'API', 'SHOPIFY', 'CAFE24')",
            name="product_source",
        ),
        CheckConstraint("lock_version > 0", name="product_lock_version_positive"),
        Index("ix_products_workspace_status", "workspace_id", "status"),
        Index("ix_products_workspace_brand", "workspace_id", "brand_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    brand_id: Mapped[UUID] = mapped_column(
        ForeignKey("brands.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    sku: Mapped[str] = mapped_column(String(160), nullable=False)
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    source: Mapped[str] = mapped_column(String(24), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=CatalogStatus.ACTIVE.value
    )
    current_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "product_versions.id",
            name="fk_products_current_version_id_product_versions",
            ondelete="SET NULL",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        index=True,
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    __mapper_args__ = {"version_id_col": lock_version}


class ProductVersion(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "product_versions"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "product_id", "version_number", name="product_version_no"
        ),
        UniqueConstraint("workspace_id", "product_id", "content_hash", name="product_version_hash"),
        CheckConstraint("version_number > 0", name="product_version_number_positive"),
        CheckConstraint("lock_version > 0", name="product_version_lock_positive"),
        Index(
            "ix_product_versions_workspace_effective",
            "workspace_id",
            "product_id",
            "effective_from",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    product_id: Mapped[UUID] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    product_url: Mapped[str | None] = mapped_column(String(2048))
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    approved_facts: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    banned_claims: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    comparison_attributes: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    shipping_facts: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    __mapper_args__ = {"version_id_col": lock_version}


class ProductVariant(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "product_variants"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "product_version_id", "sku", name="product_variant_sku"
        ),
        CheckConstraint("sort_order >= 0", name="product_variant_sort_nonnegative"),
        CheckConstraint("lock_version > 0", name="product_variant_lock_positive"),
        Index("ix_product_variants_workspace_version", "workspace_id", "product_version_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    product_id: Mapped[UUID] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    product_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("product_versions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    sku: Mapped[str] = mapped_column(String(160), nullable=False)
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    option_values: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    approved_facts: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    is_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    __mapper_args__ = {"version_id_col": lock_version}


class ProductPrice(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "product_prices"
    __table_args__ = (
        CheckConstraint("amount >= 0", name="product_price_amount_nonnegative"),
        CheckConstraint(
            "compare_at_amount IS NULL OR compare_at_amount >= amount",
            name="product_price_comparison_valid",
        ),
        CheckConstraint(
            "valid_to IS NULL OR valid_to > valid_from", name="product_price_validity_ordered"
        ),
        CheckConstraint(
            "char_length(currency) = 3 AND currency = upper(currency)",
            name="product_price_currency",
        ),
        CheckConstraint("lock_version > 0", name="product_price_lock_positive"),
        Index(
            "ix_product_prices_workspace_validity",
            "workspace_id",
            "product_id",
            "valid_from",
            "valid_to",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    product_id: Mapped[UUID] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    product_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("product_versions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    variant_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("product_variants.id", ondelete="RESTRICT"), index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(19, 4), nullable=False)
    compare_at_amount: Mapped[Decimal | None] = mapped_column(Numeric(19, 4))
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    __mapper_args__ = {"version_id_col": lock_version}


class ProductAsset(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "product_assets"
    __table_args__ = (
        CheckConstraint("sort_order >= 0", name="product_asset_sort_nonnegative"),
        CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from",
            name="product_asset_validity_ordered",
        ),
        CheckConstraint("lock_version > 0", name="product_asset_lock_positive"),
        Index("ix_product_assets_workspace_version", "workspace_id", "product_version_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    product_id: Mapped[UUID] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    product_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("product_versions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    media_asset_id: Mapped[UUID | None] = mapped_column(index=True)
    uri: Mapped[str] = mapped_column(String(2048), nullable=False)
    usage_scope: Mapped[str] = mapped_column(String(120), nullable=False)
    license_name: Mapped[str] = mapped_column(String(160), nullable=False)
    license_reference: Mapped[str | None] = mapped_column(String(2048))
    alt_text: Mapped[str | None] = mapped_column(String(500))
    is_official: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    __mapper_args__ = {"version_id_col": lock_version}


class ProductLink(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "product_links"
    __table_args__ = (
        CheckConstraint("sort_order >= 0", name="product_link_sort_nonnegative"),
        CheckConstraint(
            "kind IN ('OFFICIAL', 'AFFILIATE', 'TRACKED')", name="product_link_kind"
        ),
        CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from",
            name="product_link_validity_ordered",
        ),
        CheckConstraint(
            "kind <> 'AFFILIATE' OR disclosure_required", name="affiliate_disclosure_required"
        ),
        CheckConstraint(
            "NOT disclosure_required OR disclosure_text IS NOT NULL",
            name="product_link_disclosure_text_required",
        ),
        CheckConstraint("lock_version > 0", name="product_link_lock_positive"),
        Index("ix_product_links_workspace_version", "workspace_id", "product_version_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    product_id: Mapped[UUID] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    product_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("product_versions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    label: Mapped[str] = mapped_column(String(240), nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    disclosure_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    disclosure_text: Mapped[str | None] = mapped_column(String(500))
    tracking: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    __mapper_args__ = {"version_id_col": lock_version}


def _reject_snapshot_mutation(_mapper: object, _connection: object, target: object) -> None:
    raise ValueError(f"{type(target).__name__} rows are immutable snapshots")


for _snapshot_model in (
    BrandVersion,
    ProductVersion,
    ProductVariant,
    ProductPrice,
    ProductAsset,
    ProductLink,
):
    event.listen(_snapshot_model, "before_update", _reject_snapshot_mutation)
    event.listen(_snapshot_model, "before_delete", _reject_snapshot_mutation)
