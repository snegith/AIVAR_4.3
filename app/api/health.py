"""Operational health and readiness endpoints."""

from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.dependencies import get_db
from app.embeddings.service import EmbeddingService

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    """Liveness probe used by Docker healthcheck."""
    return {"status": "ok"}


@router.get("/ready")
def ready(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> JSONResponse:
    """Readiness probe: database, embedding model, optional Langfuse."""
    settings = get_settings()
    checks: dict[str, str] = {}

    try:
        db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {exc}"

    embedding_service: EmbeddingService | None = getattr(
        request.app.state, "embedding_service", None
    )
    if embedding_service is not None:
        checks["embedding_model"] = "ok"
    else:
        checks["embedding_model"] = "not_loaded"

    if settings.langfuse_enabled:
        try:
            response = httpx.get(f"{settings.langfuse_host.rstrip('/')}/api/public/health", timeout=2.0)
            checks["langfuse"] = "ok" if response.status_code < 500 else f"error: {response.status_code}"
        except Exception as exc:
            checks["langfuse"] = f"error: {exc}"
    else:
        checks["langfuse"] = "disabled"

    failed = [name for name, value in checks.items() if value != "ok" and value != "disabled"]
    status_code = status.HTTP_200_OK if not failed else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(status_code=status_code, content={"status": "ready" if not failed else "degraded", "checks": checks})
