"""Top-level API router."""

from fastapi import APIRouter

from blogops.api.v1.billing import payment_webhook_router
from blogops.api.v1.router import router as v1_router
from blogops.api.v1.security import deletion_webhook_router

api_router = APIRouter()
api_router.include_router(v1_router, prefix="/v1")
api_router.include_router(payment_webhook_router)
api_router.include_router(deletion_webhook_router)
