"""Focused contracts for immutable brand and product catalog data."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from blogops.api.v1.brands import router
from blogops.domain.brand.models import (
    AudiencePersona,
    Brand,
    BrandVersion,
    Product,
    ProductAsset,
    ProductLink,
    ProductPrice,
    ProductVariant,
    ProductVersion,
)
from blogops.domain.brand.schemas import (
    BrandVersionCreate,
    ProductFact,
    ProductLinkCreate,
    ProductPriceCreate,
    ProductVersionCreate,
)
from blogops.domain.brand.service import canonical_hash


def test_every_tenant_catalog_table_has_scope_lock_and_hash() -> None:
    models = (
        Brand,
        BrandVersion,
        AudiencePersona,
        Product,
        ProductVersion,
        ProductVariant,
        ProductPrice,
        ProductAsset,
        ProductLink,
    )
    for model in models:
        assert {"workspace_id", "lock_version", "content_hash"}.issubset(
            model.__table__.columns.keys()
        )


def test_snapshot_hash_is_canonical() -> None:
    assert canonical_hash({"나": [2, 1], "a": {"x": True}}) == canonical_hash(
        {"a": {"x": True}, "나": [2, 1]}
    )


def test_brand_term_cannot_be_required_and_banned() -> None:
    with pytest.raises(ValidationError):
        BrandVersionCreate(required_terms=["최고"], banned_terms=["최고"])


def test_product_facts_are_locked() -> None:
    with pytest.raises(ValidationError):
        ProductFact.model_validate({"key": "material", "value": "cotton", "locked": False})


def test_affiliate_link_requires_disclosure() -> None:
    with pytest.raises(ValidationError):
        ProductLinkCreate.model_validate(
            {
                "kind": "AFFILIATE",
                "label": "구매하기",
                "url": "https://example.com/buy",
            }
        )


def test_price_windows_cannot_overlap() -> None:
    start = datetime(2026, 8, 23, tzinfo=UTC)
    with pytest.raises(ValidationError):
        ProductVersionCreate(
            prices=[
                ProductPriceCreate(
                    amount=Decimal("10000"),
                    valid_from=start,
                    valid_to=start + timedelta(days=2),
                    synced_at=start,
                ),
                ProductPriceCreate(
                    amount=Decimal("9000"),
                    valid_from=start + timedelta(days=1),
                    synced_at=start,
                ),
            ]
        )


def test_catalog_router_uses_relative_resource_paths() -> None:
    paths = {getattr(route, "path", "") for route in router.routes}
    assert {"/brands", "/products", "/personas", "/products/import"}.issubset(paths)
