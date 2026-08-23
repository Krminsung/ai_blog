"""Version 1 route registry."""

from fastapi import APIRouter, Depends

from blogops.api.v1.admin import router as admin_router
from blogops.api.v1.analytics import router as analytics_router
from blogops.api.v1.b2b import router as b2b_router
from blogops.api.v1.billing import router as billing_router, usage_router
from blogops.api.v1.brands import router as brands_router
from blogops.api.v1.bulk import router as bulk_router
from blogops.api.v1.content import router as content_router
from blogops.api.v1.developer import router as developer_router
from blogops.api.v1.identity import router as identity_router
from blogops.api.v1.jobs import router as jobs_router
from blogops.api.v1.keywords import router as keywords_router
from blogops.api.v1.knowledge import router as knowledge_router
from blogops.api.v1.media import router as media_router
from blogops.api.v1.planning import router as planning_router
from blogops.api.v1.publishing import router as publishing_router
from blogops.api.v1.quality import router as quality_router
from blogops.api.v1.repurpose import router as repurpose_router
from blogops.api.v1.research import router as research_router
from blogops.domain.identity.dependencies import get_current_principal

router = APIRouter()
router.include_router(identity_router)
router.include_router(brands_router, dependencies=[Depends(get_current_principal)])
router.include_router(knowledge_router, dependencies=[Depends(get_current_principal)])
router.include_router(keywords_router, dependencies=[Depends(get_current_principal)])
router.include_router(planning_router, dependencies=[Depends(get_current_principal)])
router.include_router(content_router, dependencies=[Depends(get_current_principal)])
router.include_router(research_router, dependencies=[Depends(get_current_principal)])
router.include_router(quality_router, dependencies=[Depends(get_current_principal)])
router.include_router(media_router, dependencies=[Depends(get_current_principal)])
router.include_router(bulk_router, dependencies=[Depends(get_current_principal)])
router.include_router(publishing_router, dependencies=[Depends(get_current_principal)])
router.include_router(analytics_router, dependencies=[Depends(get_current_principal)])
router.include_router(repurpose_router, dependencies=[Depends(get_current_principal)])
router.include_router(billing_router, dependencies=[Depends(get_current_principal)])
router.include_router(usage_router, dependencies=[Depends(get_current_principal)])
router.include_router(developer_router, dependencies=[Depends(get_current_principal)])
router.include_router(b2b_router, dependencies=[Depends(get_current_principal)])
router.include_router(admin_router, dependencies=[Depends(get_current_principal)])
router.include_router(jobs_router, dependencies=[Depends(get_current_principal)])


@router.get("/meta", tags=["system"])
async def api_metadata() -> dict[str, str]:
    return {"api_version": "v1", "default_locale": "ko-KR", "storage_timezone": "UTC"}
