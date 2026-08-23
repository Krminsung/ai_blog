"""Version 1 route registry."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/meta", tags=["system"])
async def api_metadata() -> dict[str, str]:
    return {"api_version": "v1", "default_locale": "ko-KR", "storage_timezone": "UTC"}
