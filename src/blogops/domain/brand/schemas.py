"""Validated public contracts for brand, persona and product catalog APIs."""

import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal, Self
from urllib.parse import urlparse
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from blogops.domain.brand.models import (
    CatalogStatus,
    JourneyStage,
    KnowledgeLevel,
    ProductLinkKind,
    ProductSource,
    SearchIntent,
)

_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def _http_url(value: str | None) -> str | None:
    if value is None:
        return None
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("must be an absolute http or https URL")
    return value


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        raise ValueError("must include a timezone")
    return value


class DomainSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True, str_strip_whitespace=True)


class VoiceConfig(DomainSchema):
    formality: float = Field(default=0.5, ge=0, le=1)
    friendliness: float = Field(default=0.5, ge=0, le=1)
    expertise: float = Field(default=0.5, ge=0, le=1)
    enthusiasm: float = Field(default=0.5, ge=0, le=1)
    perspective: Literal["FIRST_PERSON", "BRAND", "EXPERT", "NEUTRAL"] = "BRAND"
    tone_labels: list[str] = Field(default_factory=list, max_length=20)
    sentence_endings: list[str] = Field(default_factory=list, max_length=30)


class PreferredExpression(DomainSchema):
    phrase: str = Field(min_length=1, max_length=500)
    purpose: Literal["GREETING", "CTA", "ENDING", "TERM", "OTHER"] = "OTHER"
    contexts: list[str] = Field(default_factory=list, max_length=30)


class RequiredPhrase(DomainSchema):
    phrase: str = Field(min_length=1, max_length=2000)
    purpose: Literal["DISCLAIMER", "ADVERTISING", "CONTACT", "OTHER"] = "OTHER"
    content_types: list[str] = Field(default_factory=list, max_length=50)
    insertion: Literal["HEADER", "BODY", "FOOTER"] = "FOOTER"


class BannedRule(DomainSchema):
    kind: Literal["WORD", "REGEX", "SEMANTIC", "CLAIM"]
    value: str = Field(min_length=1, max_length=2000)
    severity: Literal["WARN", "BLOCK"] = "BLOCK"
    reason: str | None = Field(default=None, max_length=500)
    contexts: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("value")
    @classmethod
    def validate_regex(cls, value: str, info: Any) -> str:
        if info.data.get("kind") == "REGEX":
            try:
                re.compile(value)
            except re.error as exc:
                raise ValueError("must be a valid regular expression") from exc
        return value


class StyleDictionaryEntry(DomainSchema):
    canonical: str = Field(min_length=1, max_length=240)
    variants: list[str] = Field(default_factory=list, max_length=50)
    case_sensitive: bool = False
    spacing_sensitive: bool = True


class CompetitorPolicy(DomainSchema):
    comparison_allowed: bool = False
    allowed_competitors: list[str] = Field(default_factory=list, max_length=100)
    blocked_competitors: list[str] = Field(default_factory=list, max_length=100)
    required_disclosure: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def competitors_are_unambiguous(self) -> Self:
        overlap = set(self.allowed_competitors).intersection(self.blocked_competitors)
        if overlap:
            raise ValueError("a competitor cannot be both allowed and blocked")
        return self


class VisualConfig(DomainSchema):
    logo_asset_id: UUID | None = None
    primary_colors: list[str] = Field(default_factory=list, max_length=12)
    secondary_colors: list[str] = Field(default_factory=list, max_length=12)
    fonts: list[str] = Field(default_factory=list, max_length=20)
    image_style_tags: list[str] = Field(default_factory=list, max_length=30)
    watermark: dict[str, Any] = Field(default_factory=dict)

    @field_validator("primary_colors", "secondary_colors")
    @classmethod
    def colors_are_hex(cls, values: list[str]) -> list[str]:
        if any(not _HEX_COLOR.fullmatch(value) for value in values):
            raise ValueError("colors must use #RRGGBB notation")
        return values


class StyleSampleFeatures(DomainSchema):
    source_reference: str = Field(min_length=1, max_length=2048)
    rights_confirmed: Literal[True]
    language: str = Field(default="ko-KR", min_length=2, max_length=16)
    extracted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    features: dict[str, Any] = Field(default_factory=dict)

    @field_validator("extracted_at")
    @classmethod
    def extracted_at_has_timezone(cls, value: datetime) -> datetime:
        return _aware(value)  # type: ignore[return-value]


class BrandVersionCreate(DomainSchema):
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    preferred_expressions: list[PreferredExpression] = Field(default_factory=list, max_length=200)
    required_terms: list[str] = Field(default_factory=list, max_length=500)
    required_phrases: list[RequiredPhrase] = Field(default_factory=list, max_length=200)
    banned_terms: list[str] = Field(default_factory=list, max_length=500)
    banned_claims: list[BannedRule] = Field(default_factory=list, max_length=500)
    banned_rules: list[BannedRule] = Field(default_factory=list, max_length=500)
    style_dictionary: list[StyleDictionaryEntry] = Field(default_factory=list, max_length=1000)
    competitor_policy: CompetitorPolicy = Field(default_factory=CompetitorPolicy)
    visual_config: VisualConfig = Field(default_factory=VisualConfig)
    style_samples: list[StyleSampleFeatures] = Field(default_factory=list, max_length=100)
    sample_style_features: dict[str, Any] = Field(default_factory=dict)
    effective_from: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def term_sets_are_unambiguous(self) -> Self:
        overlap = set(self.required_terms).intersection(self.banned_terms)
        if overlap:
            raise ValueError("a term cannot be both required and banned")
        canonical_terms = [entry.canonical for entry in self.style_dictionary]
        if len(canonical_terms) != len(set(canonical_terms)):
            raise ValueError("style dictionary canonical terms must be unique")
        if self.sample_style_features and not self.style_samples:
            raise ValueError(
                "sample_style_features require at least one rights-confirmed style sample"
            )
        return self

    @field_validator("required_terms", "banned_terms")
    @classmethod
    def terms_are_non_empty_and_unique(cls, values: list[str]) -> list[str]:
        if any(not value or len(value) > 240 for value in values):
            raise ValueError("terms must contain between 1 and 240 characters")
        if len(values) != len(set(values)):
            raise ValueError("terms must be unique")
        return values

    @field_validator("effective_from")
    @classmethod
    def effective_from_has_timezone(cls, value: datetime) -> datetime:
        return _aware(value)  # type: ignore[return-value]


class BrandCreate(DomainSchema):
    name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=10000)
    industry: str | None = Field(default=None, max_length=120)
    website_url: str | None = Field(default=None, max_length=2048)
    initial_version: BrandVersionCreate = Field(default_factory=BrandVersionCreate)

    _validate_website = field_validator("website_url")(_http_url)


class BrandUpdate(DomainSchema):
    expected_lock_version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=10000)
    industry: str | None = Field(default=None, max_length=120)
    website_url: str | None = Field(default=None, max_length=2048)

    _validate_website = field_validator("website_url")(_http_url)

    @model_validator(mode="after")
    def includes_change(self) -> Self:
        changed = self.model_fields_set.difference({"expected_lock_version"})
        if not changed:
            raise ValueError("at least one brand field must be supplied")
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("name cannot be null")
        return self


class DeactivateRequest(DomainSchema):
    expected_lock_version: int = Field(ge=1)
    reason: str | None = Field(default=None, max_length=1000)


class BrandVersionRead(BrandVersionCreate):
    id: UUID
    workspace_id: UUID
    brand_id: UUID
    version_number: int
    created_by: UUID
    created_at: datetime
    lock_version: int
    content_hash: str


class BrandRead(DomainSchema):
    id: UUID
    workspace_id: UUID
    name: str
    description: str | None
    industry: str | None
    website_url: str | None
    status: CatalogStatus
    current_version_id: UUID | None
    deactivated_at: datetime | None
    lock_version: int
    content_hash: str
    created_at: datetime
    updated_at: datetime
    current_version: BrandVersionRead | None = None


class AgeRange(DomainSchema):
    minimum: int = Field(ge=0, le=120)
    maximum: int = Field(ge=0, le=120)

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.minimum > self.maximum:
            raise ValueError("minimum age cannot exceed maximum age")
        return self


class ProhibitedTargetingRule(DomainSchema):
    category: Literal[
        "RACE_ETHNICITY",
        "RELIGION",
        "HEALTH",
        "SEXUAL_ORIENTATION",
        "POLITICS",
        "FINANCIAL_HARDSHIP",
        "OTHER",
    ]
    reason: str = Field(min_length=1, max_length=500)
    action: Literal["BLOCK", "REQUIRE_REVIEW"] = "BLOCK"


class AudiencePersonaCreate(DomainSchema):
    brand_id: UUID | None = None
    name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=10000)
    age_range: AgeRange | None = None
    situations: list[str] = Field(default_factory=list, max_length=100)
    interests: list[str] = Field(default_factory=list, max_length=100)
    challenges: list[str] = Field(default_factory=list, max_length=100)
    knowledge_level: KnowledgeLevel = KnowledgeLevel.GENERAL
    search_intents: list[SearchIntent] = Field(default_factory=list, max_length=5)
    journey_stage: JourneyStage = JourneyStage.AWARENESS
    prohibited_targeting: list[ProhibitedTargetingRule] = Field(
        default_factory=list, max_length=30
    )


class AudiencePersonaUpdate(DomainSchema):
    expected_lock_version: int = Field(ge=1)
    brand_id: UUID | None = None
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=10000)
    age_range: AgeRange | None = None
    situations: list[str] | None = Field(default=None, max_length=100)
    interests: list[str] | None = Field(default=None, max_length=100)
    challenges: list[str] | None = Field(default=None, max_length=100)
    knowledge_level: KnowledgeLevel | None = None
    search_intents: list[SearchIntent] | None = Field(default=None, max_length=5)
    journey_stage: JourneyStage | None = None
    prohibited_targeting: list[ProhibitedTargetingRule] | None = Field(
        default=None, max_length=30
    )

    @model_validator(mode="after")
    def includes_change(self) -> Self:
        changed = self.model_fields_set.difference({"expected_lock_version"})
        if not changed:
            raise ValueError("at least one persona field must be supplied")
        non_nullable = {
            "name",
            "situations",
            "interests",
            "challenges",
            "knowledge_level",
            "search_intents",
            "journey_stage",
            "prohibited_targeting",
        }
        if any(
            field in self.model_fields_set and getattr(self, field) is None
            for field in non_nullable
        ):
            raise ValueError("non-null persona fields cannot be null")
        return self


class AudiencePersonaRead(DomainSchema):
    id: UUID
    workspace_id: UUID
    brand_id: UUID | None
    name: str
    description: str | None
    age_range: AgeRange | None
    situations: list[str]
    interests: list[str]
    challenges: list[str]
    knowledge_level: KnowledgeLevel
    search_intents: list[SearchIntent]
    journey_stage: JourneyStage
    prohibited_targeting: list[ProhibitedTargetingRule]
    status: CatalogStatus
    deactivated_at: datetime | None
    lock_version: int
    content_hash: str
    created_at: datetime
    updated_at: datetime


class ProductFact(DomainSchema):
    key: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9._:-]+$")
    value: Any
    fact_type: Literal["MATERIAL", "BENEFIT", "SHIPPING", "WARRANTY", "OTHER"] = "OTHER"
    source_reference: str | None = Field(default=None, max_length=2048)
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    locked: Literal[True] = True

    @field_validator("valid_from", "valid_to")
    @classmethod
    def validity_has_timezone(cls, value: datetime | None) -> datetime | None:
        return _aware(value)

    @model_validator(mode="after")
    def validity_is_ordered(self) -> Self:
        if self.valid_from and self.valid_to and self.valid_to <= self.valid_from:
            raise ValueError("valid_to must be later than valid_from")
        return self


class ProductClaimRule(DomainSchema):
    kind: Literal["WORD", "REGEX", "SEMANTIC", "CLAIM"] = "CLAIM"
    value: str = Field(min_length=1, max_length=2000)
    severity: Literal["WARN", "BLOCK"] = "BLOCK"
    reason: str | None = Field(default=None, max_length=500)

    @field_validator("value")
    @classmethod
    def validate_regex(cls, value: str, info: Any) -> str:
        if info.data.get("kind") == "REGEX":
            try:
                re.compile(value)
            except re.error as exc:
                raise ValueError("must be a valid regular expression") from exc
        return value


class ProductPriceCreate(DomainSchema):
    amount: Decimal = Field(ge=0, max_digits=19, decimal_places=4)
    compare_at_amount: Decimal | None = Field(
        default=None, ge=0, max_digits=19, decimal_places=4
    )
    currency: str = Field(default="KRW", min_length=3, max_length=3, pattern=r"^[A-Za-z]{3}$")
    valid_from: datetime = Field(default_factory=lambda: datetime.now(UTC))
    valid_to: datetime | None = None
    synced_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("currency")
    @classmethod
    def uppercase_currency(cls, value: str) -> str:
        return value.upper()

    @field_validator("valid_from", "valid_to", "synced_at")
    @classmethod
    def times_have_timezone(cls, value: datetime | None) -> datetime | None:
        return _aware(value)

    @model_validator(mode="after")
    def price_is_consistent(self) -> Self:
        if self.valid_to and self.valid_to <= self.valid_from:
            raise ValueError("valid_to must be later than valid_from")
        if self.compare_at_amount is not None and self.compare_at_amount < self.amount:
            raise ValueError("compare_at_amount cannot be lower than amount")
        return self


class ProductVariantCreate(DomainSchema):
    sku: str = Field(min_length=1, max_length=160)
    name: str = Field(min_length=1, max_length=240)
    option_values: dict[str, Any] = Field(default_factory=dict)
    approved_facts: list[ProductFact] = Field(default_factory=list, max_length=500)
    is_available: bool = True
    prices: list[ProductPriceCreate] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def fact_keys_are_unique(self) -> Self:
        keys = [fact.key for fact in self.approved_facts]
        if len(keys) != len(set(keys)):
            raise ValueError("approved fact keys must be unique within a variant")
        return self


class ProductAssetCreate(DomainSchema):
    media_asset_id: UUID | None = None
    uri: str = Field(max_length=2048)
    usage_scope: str = Field(default="CONTENT", min_length=1, max_length=120)
    license_name: str = Field(min_length=1, max_length=160)
    license_reference: str | None = Field(default=None, max_length=2048)
    alt_text: str | None = Field(default=None, max_length=500)
    is_official: bool = True
    valid_from: datetime | None = None
    valid_to: datetime | None = None

    _validate_uri = field_validator("uri")(_http_url)
    _validate_license_reference = field_validator("license_reference")(_http_url)

    @field_validator("valid_from", "valid_to")
    @classmethod
    def times_have_timezone(cls, value: datetime | None) -> datetime | None:
        return _aware(value)

    @model_validator(mode="after")
    def validity_is_ordered(self) -> Self:
        if self.valid_from and self.valid_to and self.valid_to <= self.valid_from:
            raise ValueError("valid_to must be later than valid_from")
        return self


class ProductLinkCreate(DomainSchema):
    kind: ProductLinkKind
    label: str = Field(min_length=1, max_length=240)
    url: str = Field(max_length=2048)
    disclosure_required: bool = False
    disclosure_text: str | None = Field(default=None, max_length=500)
    tracking: dict[str, Any] = Field(default_factory=dict)
    valid_from: datetime | None = None
    valid_to: datetime | None = None

    _validate_url = field_validator("url")(_http_url)

    @field_validator("valid_from", "valid_to")
    @classmethod
    def times_have_timezone(cls, value: datetime | None) -> datetime | None:
        return _aware(value)

    @model_validator(mode="after")
    def link_is_consistent(self) -> Self:
        if self.valid_from and self.valid_to and self.valid_to <= self.valid_from:
            raise ValueError("valid_to must be later than valid_from")
        if self.kind == ProductLinkKind.AFFILIATE and not self.disclosure_required:
            raise ValueError("affiliate links must require disclosure")
        if self.disclosure_required and not self.disclosure_text:
            raise ValueError("disclosure_text is required when disclosure_required is true")
        return self


def _assert_non_overlapping(prices: list[ProductPriceCreate]) -> None:
    by_currency: dict[str, list[ProductPriceCreate]] = {}
    for price in prices:
        by_currency.setdefault(price.currency, []).append(price)
    for currency_prices in by_currency.values():
        ordered = sorted(currency_prices, key=lambda item: item.valid_from)
        for previous, current in zip(ordered, ordered[1:], strict=False):
            if previous.valid_to is None or current.valid_from < previous.valid_to:
                raise ValueError("price validity windows cannot overlap for the same currency")


class ProductVersionCreate(DomainSchema):
    description: str | None = Field(default=None, max_length=20000)
    product_url: str | None = Field(default=None, max_length=2048)
    attributes: dict[str, Any] = Field(default_factory=dict)
    approved_facts: list[ProductFact] = Field(default_factory=list, max_length=1000)
    banned_claims: list[ProductClaimRule] = Field(default_factory=list, max_length=500)
    comparison_attributes: dict[str, Any] = Field(default_factory=dict)
    shipping_facts: dict[str, Any] = Field(default_factory=dict)
    prices: list[ProductPriceCreate] = Field(default_factory=list, max_length=100)
    variants: list[ProductVariantCreate] = Field(default_factory=list, max_length=500)
    assets: list[ProductAssetCreate] = Field(default_factory=list, max_length=500)
    links: list[ProductLinkCreate] = Field(default_factory=list, max_length=200)
    effective_from: datetime = Field(default_factory=lambda: datetime.now(UTC))

    _validate_product_url = field_validator("product_url")(_http_url)

    @field_validator("effective_from")
    @classmethod
    def effective_from_has_timezone(cls, value: datetime) -> datetime:
        return _aware(value)  # type: ignore[return-value]

    @model_validator(mode="after")
    def catalog_snapshot_is_unambiguous(self) -> Self:
        fact_keys = [fact.key for fact in self.approved_facts]
        if len(fact_keys) != len(set(fact_keys)):
            raise ValueError("approved fact keys must be unique within a product version")
        variant_skus = [variant.sku for variant in self.variants]
        if len(variant_skus) != len(set(variant_skus)):
            raise ValueError("variant SKUs must be unique within a product version")
        _assert_non_overlapping(self.prices)
        for variant in self.variants:
            _assert_non_overlapping(variant.prices)
        return self


class ProductCreate(DomainSchema):
    brand_id: UUID
    sku: str = Field(min_length=1, max_length=160)
    name: str = Field(min_length=1, max_length=240)
    source: ProductSource = ProductSource.MANUAL
    external_id: str | None = Field(default=None, max_length=255)
    last_synced_at: datetime | None = None
    initial_version: ProductVersionCreate = Field(default_factory=ProductVersionCreate)

    @field_validator("last_synced_at")
    @classmethod
    def synced_at_has_timezone(cls, value: datetime | None) -> datetime | None:
        return _aware(value)


class ProductUpdate(DomainSchema):
    expected_lock_version: int = Field(ge=1)
    sku: str | None = Field(default=None, min_length=1, max_length=160)
    name: str | None = Field(default=None, min_length=1, max_length=240)
    external_id: str | None = Field(default=None, max_length=255)
    last_synced_at: datetime | None = None

    @field_validator("last_synced_at")
    @classmethod
    def synced_at_has_timezone(cls, value: datetime | None) -> datetime | None:
        return _aware(value)

    @model_validator(mode="after")
    def includes_change(self) -> Self:
        changed = self.model_fields_set.difference({"expected_lock_version"})
        if not changed:
            raise ValueError("at least one product field must be supplied")
        if "sku" in self.model_fields_set and self.sku is None:
            raise ValueError("sku cannot be null")
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("name cannot be null")
        return self


class ProductPriceRead(ProductPriceCreate):
    id: UUID
    workspace_id: UUID
    product_id: UUID
    product_version_id: UUID
    variant_id: UUID | None
    is_current: bool
    freshness: Literal["CURRENT", "STALE", "NOT_YET_VALID", "EXPIRED"]
    created_at: datetime
    lock_version: int
    content_hash: str


class ProductVariantRead(DomainSchema):
    id: UUID
    workspace_id: UUID
    product_id: UUID
    product_version_id: UUID
    sku: str
    name: str
    option_values: dict[str, Any]
    approved_facts: list[ProductFact]
    is_available: bool
    prices: list[ProductPriceRead] = Field(default_factory=list)
    created_at: datetime
    lock_version: int
    content_hash: str


class ProductAssetRead(ProductAssetCreate):
    id: UUID
    workspace_id: UUID
    product_id: UUID
    product_version_id: UUID
    created_at: datetime
    lock_version: int
    content_hash: str


class ProductLinkRead(ProductLinkCreate):
    id: UUID
    workspace_id: UUID
    product_id: UUID
    product_version_id: UUID
    created_at: datetime
    lock_version: int
    content_hash: str


class ProductVersionRead(DomainSchema):
    id: UUID
    workspace_id: UUID
    product_id: UUID
    version_number: int
    description: str | None
    product_url: str | None
    attributes: dict[str, Any]
    approved_facts: list[ProductFact]
    banned_claims: list[ProductClaimRule]
    comparison_attributes: dict[str, Any]
    shipping_facts: dict[str, Any]
    effective_from: datetime
    created_by: UUID
    created_at: datetime
    lock_version: int
    content_hash: str
    prices: list[ProductPriceRead] = Field(default_factory=list)
    variants: list[ProductVariantRead] = Field(default_factory=list)
    assets: list[ProductAssetRead] = Field(default_factory=list)
    links: list[ProductLinkRead] = Field(default_factory=list)


class ProductRead(DomainSchema):
    id: UUID
    workspace_id: UUID
    brand_id: UUID
    sku: str
    name: str
    source: ProductSource
    external_id: str | None
    status: CatalogStatus
    current_version_id: UUID | None
    last_synced_at: datetime | None
    deactivated_at: datetime | None
    lock_version: int
    content_hash: str
    created_at: datetime
    updated_at: datetime
    current_version: ProductVersionRead | None = None


class ProductImportRequest(DomainSchema):
    items: list[ProductCreate] = Field(min_length=1, max_length=500)


class ProductImportRead(DomainSchema):
    imported_count: int
    products: list[ProductRead]
