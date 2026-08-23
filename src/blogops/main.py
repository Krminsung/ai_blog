"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import secrets

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response

from blogops.api.health import router as health_router
from blogops.api.router import api_router
from blogops.core.config import Settings, get_settings
from blogops.core.errors import install_error_handlers
from blogops.core.logging import configure_logging
from blogops.core.middleware import RequestContextMiddleware, SecurityHeadersMiddleware
from blogops.core.telemetry import configure_telemetry
from blogops.db.redis import close_redis
from blogops.db.session import get_database


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        await close_redis()
        if get_database.cache_info().currsize:
            await get_database().close()
            get_database.cache_clear()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url="/redoc" if settings.docs_enabled else None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
        lifespan=lifespan,
    )
    app.state.settings = settings

    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestContextMiddleware)
    if settings.allowed_host_list:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_host_list)
    if settings.cors_origin_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origin_list,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID"],
        )

    install_error_handlers(app)
    app.include_router(health_router)
    app.include_router(api_router)

    if settings.metrics_enabled:
        @app.get("/metrics", include_in_schema=False)
        async def metrics(request: Request) -> Response:
            expected = settings.metrics_auth_token
            if expected is not None:
                supplied = request.headers.get("Authorization", "")
                valid = f"Bearer {expected.get_secret_value()}"
                if not secrets.compare_digest(supplied, valid):
                    return Response(status_code=404)
            return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    configure_telemetry(app, settings)
    return app


app = create_app()
