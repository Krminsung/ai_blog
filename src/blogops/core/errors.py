"""Stable public error contract for API and worker boundaries."""

from dataclasses import dataclass, field
from typing import Any

import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from blogops.core.context import request_id_context

logger = structlog.get_logger(__name__)


@dataclass(slots=True)
class AppError(Exception):
    code: str
    message: str
    status_code: int = 400
    fields: list[dict[str, str]] = field(default_factory=list)
    remediation: dict[str, Any] | None = None


def _response(error: AppError, request_id: str | None) -> JSONResponse:
    body: dict[str, Any] = {
        "error": {
            "code": error.code,
            "message": error.message,
            "request_id": request_id,
            "fields": error.fields,
        }
    }
    if error.remediation is not None:
        body["error"]["remediation"] = error.remediation
    return JSONResponse(status_code=error.status_code, content=body)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
        return _response(exc, request_id_context.get())

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        fields = [
            {
                "path": ".".join(str(part) for part in item["loc"]),
                "reason": item["type"],
            }
            for item in exc.errors()
        ]
        return _response(
            AppError(
                code="VALIDATION_FAILED",
                message="요청 값이 올바르지 않습니다.",
                status_code=422,
                fields=fields,
            ),
            request_id_context.get(),
        )

    @app.exception_handler(HTTPException)
    async def http_error_handler(_request: Request, exc: HTTPException) -> JSONResponse:
        return _response(
            AppError(
                code="HTTP_ERROR",
                message=str(exc.detail),
                status_code=exc.status_code,
            ),
            request_id_context.get(),
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "unhandled_request_error",
            method=request.method,
            path=request.url.path,
            exception_type=type(exc).__name__,
        )
        return _response(
            AppError(
                code="INTERNAL_ERROR",
                message="요청을 처리하는 중 오류가 발생했습니다.",
                status_code=500,
            ),
            request_id_context.get(),
        )
