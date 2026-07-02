"""FastAPI application factory and minimal bootstrap endpoints.

Phase 0 provides a runnable API shell; full /v1 routes arrive in Phase 7.
"""

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.config import get_settings
from app.logging import get_logger, setup_logging
from app.ratelimit import limiter

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup/shutdown hooks."""
    logger.info(
        "application_start",
        extra={"component": "api", "event": "startup"},
    )
    yield
    logger.info("application_stop", extra={"component": "api", "event": "shutdown"})


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    settings = get_settings()
    setup_logging(settings.log_level)

    app = FastAPI(
        title="Adversarial Pattern Detector",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    @app.get("/health")
    def health() -> dict[str, str]:
        """Liveness probe used by Docker healthcheck."""
        return {"status": "ok"}

    return app


app = create_app()
