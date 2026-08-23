"""Version 1 route registry."""

from fastapi import APIRouter, Depends

from blogops.api.v1.brands import router as brands_router
from blogops.api.v1.content import router as content_router
from blogops.api.v1.identity import router as identity_router
from blogops.api.v1.jobs import router as jobs_router
from blogops.api.v1.keywords import router as keywords_router
from blogops.api.v1.knowledge import router as knowledge_router
from blogops.api.v1.planning import router as planning_router
from blogops.api.v1.quality import router as quality_router
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
router.include_router(jobs_router, dependencies=[Depends(get_current_principal)])


@router.get("/meta", tags=["system"])
async def api_metadata() -> dict[str, str]:
    return {"api_version": "v1", "default_locale": "ko-KR", "storage_timezone": "UTC"}
