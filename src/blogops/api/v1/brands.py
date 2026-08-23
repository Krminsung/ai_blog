"""Brand, audience persona and product catalog HTTP routes.

This router intentionally has no version prefix.  The central v1 registry mounts it under
``/v1`` while the relative resource paths remain ``/brands``, ``/personas`` and ``/products``.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from blogops.core.context import Principal
from blogops.core.permissions import Permission, require_permissions
from blogops.db.session import get_tenant_session
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
    ProductCreate,
    ProductImportRead,
    ProductImportRequest,
    ProductRead,
    ProductUpdate,
    ProductVersionCreate,
    ProductVersionRead,
)
from blogops.domain.brand.service import BrandCatalogService

router = APIRouter()

BrandReader = Annotated[
    Principal,
    Depends(require_permissions(Permission.BRAND_READ)),
]
BrandWriter = Annotated[
    Principal,
    Depends(require_permissions(Permission.BRAND_WRITE)),
]
Session = Annotated[AsyncSession, Depends(get_tenant_session)]


def get_catalog_service(session: Session) -> BrandCatalogService:
    return BrandCatalogService(session)


CatalogService = Annotated[BrandCatalogService, Depends(get_catalog_service)]


@router.post(
    "/brands",
    response_model=BrandRead,
    status_code=status.HTTP_201_CREATED,
    tags=["brands"],
)
async def create_brand(
    payload: BrandCreate, principal: BrandWriter, service: CatalogService
) -> BrandRead:
    return await service.create_brand(principal.workspace_id, principal.subject_id, payload)


@router.get("/brands", response_model=list[BrandRead], tags=["brands"])
async def list_brands(
    principal: BrandReader,
    service: CatalogService,
    include_inactive: bool = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[BrandRead]:
    return await service.list_brands(
        principal.workspace_id,
        include_inactive=include_inactive,
        limit=limit,
        offset=offset,
    )


@router.get("/brands/{brand_id}", response_model=BrandRead, tags=["brands"])
async def get_brand(
    brand_id: UUID, principal: BrandReader, service: CatalogService
) -> BrandRead:
    return await service.get_brand(principal.workspace_id, brand_id)


@router.patch("/brands/{brand_id}", response_model=BrandRead, tags=["brands"])
async def update_brand(
    brand_id: UUID,
    payload: BrandUpdate,
    principal: BrandWriter,
    service: CatalogService,
) -> BrandRead:
    return await service.update_brand(
        principal.workspace_id, principal.subject_id, brand_id, payload
    )


@router.post(
    "/brands/{brand_id}/versions",
    response_model=BrandVersionRead,
    status_code=status.HTTP_201_CREATED,
    tags=["brands"],
)
async def create_brand_version(
    brand_id: UUID,
    payload: BrandVersionCreate,
    principal: BrandWriter,
    service: CatalogService,
) -> BrandVersionRead:
    return await service.create_brand_version(
        principal.workspace_id, principal.subject_id, brand_id, payload
    )


@router.get(
    "/brands/{brand_id}/versions",
    response_model=list[BrandVersionRead],
    tags=["brands"],
)
async def list_brand_versions(
    brand_id: UUID,
    principal: BrandReader,
    service: CatalogService,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[BrandVersionRead]:
    return await service.list_brand_versions(
        principal.workspace_id, brand_id, limit=limit, offset=offset
    )


@router.get(
    "/brands/{brand_id}/versions/{version_number}",
    response_model=BrandVersionRead,
    tags=["brands"],
)
async def get_brand_version(
    brand_id: UUID,
    version_number: Annotated[int, Path(ge=1)],
    principal: BrandReader,
    service: CatalogService,
) -> BrandVersionRead:
    return await service.get_brand_version(
        principal.workspace_id, brand_id, version_number
    )


@router.post("/brands/{brand_id}/deactivate", response_model=BrandRead, tags=["brands"])
async def deactivate_brand(
    brand_id: UUID,
    payload: DeactivateRequest,
    principal: BrandWriter,
    service: CatalogService,
) -> BrandRead:
    return await service.deactivate_brand(
        principal.workspace_id, principal.subject_id, brand_id, payload
    )


@router.post(
    "/personas",
    response_model=AudiencePersonaRead,
    status_code=status.HTTP_201_CREATED,
    tags=["personas"],
)
async def create_persona(
    payload: AudiencePersonaCreate,
    principal: BrandWriter,
    service: CatalogService,
) -> AudiencePersonaRead:
    return await service.create_persona(principal.workspace_id, principal.subject_id, payload)


@router.get("/personas", response_model=list[AudiencePersonaRead], tags=["personas"])
async def list_personas(
    principal: BrandReader,
    service: CatalogService,
    brand_id: UUID | None = None,
    include_inactive: bool = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[AudiencePersonaRead]:
    return await service.list_personas(
        principal.workspace_id,
        brand_id=brand_id,
        include_inactive=include_inactive,
        limit=limit,
        offset=offset,
    )


@router.get("/personas/{persona_id}", response_model=AudiencePersonaRead, tags=["personas"])
async def get_persona(
    persona_id: UUID, principal: BrandReader, service: CatalogService
) -> AudiencePersonaRead:
    return await service.get_persona(principal.workspace_id, persona_id)


@router.patch("/personas/{persona_id}", response_model=AudiencePersonaRead, tags=["personas"])
async def update_persona(
    persona_id: UUID,
    payload: AudiencePersonaUpdate,
    principal: BrandWriter,
    service: CatalogService,
) -> AudiencePersonaRead:
    return await service.update_persona(
        principal.workspace_id, principal.subject_id, persona_id, payload
    )


@router.post(
    "/personas/{persona_id}/deactivate",
    response_model=AudiencePersonaRead,
    tags=["personas"],
)
async def deactivate_persona(
    persona_id: UUID,
    payload: DeactivateRequest,
    principal: BrandWriter,
    service: CatalogService,
) -> AudiencePersonaRead:
    return await service.deactivate_persona(
        principal.workspace_id, principal.subject_id, persona_id, payload
    )


# Keep the static import path above /products/{product_id} so it cannot be consumed as a UUID.
@router.post(
    "/products/import",
    response_model=ProductImportRead,
    status_code=status.HTTP_201_CREATED,
    tags=["products"],
)
async def import_products(
    payload: ProductImportRequest,
    principal: BrandWriter,
    service: CatalogService,
) -> ProductImportRead:
    products = await service.import_products(
        principal.workspace_id, principal.subject_id, payload.items
    )
    return ProductImportRead(imported_count=len(products), products=products)


@router.post(
    "/products",
    response_model=ProductRead,
    status_code=status.HTTP_201_CREATED,
    tags=["products"],
)
async def create_product(
    payload: ProductCreate, principal: BrandWriter, service: CatalogService
) -> ProductRead:
    return await service.create_product(principal.workspace_id, principal.subject_id, payload)


@router.get("/products", response_model=list[ProductRead], tags=["products"])
async def list_products(
    principal: BrandReader,
    service: CatalogService,
    brand_id: UUID | None = None,
    include_inactive: bool = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ProductRead]:
    return await service.list_products(
        principal.workspace_id,
        brand_id=brand_id,
        include_inactive=include_inactive,
        limit=limit,
        offset=offset,
    )


@router.get("/products/{product_id}", response_model=ProductRead, tags=["products"])
async def get_product(
    product_id: UUID, principal: BrandReader, service: CatalogService
) -> ProductRead:
    return await service.get_product(principal.workspace_id, product_id)


@router.patch("/products/{product_id}", response_model=ProductRead, tags=["products"])
async def update_product(
    product_id: UUID,
    payload: ProductUpdate,
    principal: BrandWriter,
    service: CatalogService,
) -> ProductRead:
    return await service.update_product(
        principal.workspace_id, principal.subject_id, product_id, payload
    )


@router.post(
    "/products/{product_id}/versions",
    response_model=ProductVersionRead,
    status_code=status.HTTP_201_CREATED,
    tags=["products"],
)
async def create_product_version(
    product_id: UUID,
    payload: ProductVersionCreate,
    principal: BrandWriter,
    service: CatalogService,
) -> ProductVersionRead:
    return await service.create_product_version(
        principal.workspace_id, principal.subject_id, product_id, payload
    )


@router.get(
    "/products/{product_id}/versions",
    response_model=list[ProductVersionRead],
    tags=["products"],
)
async def list_product_versions(
    product_id: UUID,
    principal: BrandReader,
    service: CatalogService,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ProductVersionRead]:
    return await service.list_product_versions(
        principal.workspace_id, product_id, limit=limit, offset=offset
    )


@router.get(
    "/products/{product_id}/versions/{version_number}",
    response_model=ProductVersionRead,
    tags=["products"],
)
async def get_product_version(
    product_id: UUID,
    version_number: Annotated[int, Path(ge=1)],
    principal: BrandReader,
    service: CatalogService,
) -> ProductVersionRead:
    return await service.get_product_version(
        principal.workspace_id, product_id, version_number
    )


@router.post(
    "/products/{product_id}/deactivate",
    response_model=ProductRead,
    tags=["products"],
)
async def deactivate_product(
    product_id: UUID,
    payload: DeactivateRequest,
    principal: BrandWriter,
    service: CatalogService,
) -> ProductRead:
    return await service.deactivate_product(
        principal.workspace_id, principal.subject_id, product_id, payload
    )
