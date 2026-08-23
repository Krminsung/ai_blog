"""Process and dependency health endpoints."""

import asyncio
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from blogops import __version__
from blogops.core.config import get_settings
from blogops.db.redis import get_redis
from blogops.db.session import get_database

router = APIRouter(tags=["system"])


@router.get("/health/live")
async def live() -> dict[str, str]:
    settings = get_settings()
    return {
        "status": "ok",
        "service": settings.otel_service_name,
        "version": __version__,
    }


async def _database_health() -> None:
    await get_database().ping()


async def _redis_health() -> None:
    await get_redis().ping()


@router.get("/health/ready", response_model=None)
async def ready() -> dict[str, Any] | JSONResponse:
    timeout = get_settings().dependency_health_timeout_seconds
    checks = await asyncio.gather(
        asyncio.wait_for(_database_health(), timeout=timeout),
        asyncio.wait_for(_redis_health(), timeout=timeout),
        return_exceptions=True,
    )
    names = ("postgresql", "redis")
    details: dict[str, str] = {}
    for name, result in zip(names, checks, strict=True):
        details[name] = "ok" if not isinstance(result, Exception) else "unavailable"
    if all(value == "ok" for value in details.values()):
        return {"status": "ok", "checks": details}
    return JSONResponse(status_code=503, content={"status": "unavailable", "checks": details})
