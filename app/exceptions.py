"""Structured API error types and FastAPI exception handlers."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.db.repositories import OptimisticLockError
from app.logging import get_logger

logger = get_logger(__name__)


class AppError(Exception):
    """Base application error mapped to a structured JSON response."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        status_code: int,
        detail: Any = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.detail = detail


class NotFoundError(AppError):
    """Resource not found."""

    def __init__(self, message: str, *, detail: Any = None) -> None:
        super().__init__(code="not_found", message=message, status_code=404, detail=detail)


class UnauthorizedError(AppError):
    """Missing or invalid admin credentials."""

    def __init__(self, message: str = "Invalid admin key") -> None:
        super().__init__(code="unauthorized", message=message, status_code=401)


class LLMProviderError(AppError):
    """Upstream LLM failure."""

    def __init__(self, message: str, *, detail: Any = None) -> None:
        super().__init__(code="llm_provider_error", message=message, status_code=502, detail=detail)


class DatabaseUnavailableError(AppError):
    """Database connectivity or transaction failure."""

    def __init__(self, message: str = "Database unavailable", *, detail: Any = None) -> None:
        super().__init__(
            code="database_unavailable",
            message=message,
            status_code=503,
            detail=detail,
        )


def error_payload(code: str, message: str, detail: Any = None) -> dict[str, Any]:
    """Standard error envelope for all API responses."""
    payload: dict[str, Any] = {"code": code, "message": message}
    if detail is not None:
        payload["detail"] = detail
    return {"error": payload}


def register_exception_handlers(app: FastAPI) -> None:
    """Attach structured JSON handlers to the FastAPI app."""

    @app.exception_handler(AppError)
    async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload(exc.code, exc.message, exc.detail),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content=error_payload("validation_error", "Invalid request body", exc.errors()),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        if isinstance(exc.detail, dict) and "error" in exc.detail:
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload("http_error", str(exc.detail)),
        )

    @app.exception_handler(OptimisticLockError)
    async def optimistic_lock_handler(_: Request, exc: OptimisticLockError) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content=error_payload("conflict", str(exc)),
        )

    @app.exception_handler(SQLAlchemyError)
    async def database_error_handler(_: Request, exc: SQLAlchemyError) -> JSONResponse:
        logger.exception(
            "database_error",
            extra={"component": "api", "event": "database_error"},
        )
        return JSONResponse(
            status_code=503,
            content=error_payload("database_unavailable", "Database unavailable", str(exc)),
        )
