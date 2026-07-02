"""FastAPI application factory, routers, middleware, and exception handlers."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.api import admin, alerts, events, health, patterns, risk
from app.config import get_settings
from app.embeddings.service import EmbeddingService
from app.exceptions import error_payload, register_exception_handlers
from app.logging import get_logger, setup_logging
from app.middleware.rate_limit import user_id_rate_limit_middleware
from app.ratelimit import limiter

logger = get_logger(__name__)


def _rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """Map slowapi rate limit errors to the structured error envelope."""
    return JSONResponse(
        status_code=429,
        content=error_payload("rate_limited", "Rate limit exceeded", {"retry_after": exc.detail}),
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup/shutdown hooks."""
    settings = get_settings()
    setup_logging(settings.log_level)
    logger.info(
        "application_start",
        extra={"component": "api", "event": "startup"},
    )
    app.state.embedding_service = EmbeddingService()
    yield
    app.state.embedding_service = None
    logger.info("application_stop", extra={"component": "api", "event": "shutdown"})


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    app = FastAPI(
        title="Adversarial Pattern Detector",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)
    register_exception_handlers(app)
    app.middleware("http")(user_id_rate_limit_middleware)

    app.include_router(health.router)
    app.include_router(events.router)
    app.include_router(risk.router)
    app.include_router(patterns.router)
    app.include_router(alerts.router)
    app.include_router(admin.router)

    return app


app = create_app()
