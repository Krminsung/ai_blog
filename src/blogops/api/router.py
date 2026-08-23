"""Top-level API router."""

from fastapi import APIRouter

from blogops.api.v1.router import router as v1_router

api_router = APIRouter()
api_router.include_router(v1_router, prefix="/v1")
