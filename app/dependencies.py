"""Shared FastAPI dependencies for DB sessions and services."""

from __future__ import annotations

from collections.abc import Generator
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.session import SessionLocal
from app.embeddings.service import EmbeddingService
from app.exceptions import UnauthorizedError
from app.llm.factory import get_llm_provider
from app.llm.provider import LLMProvider
from app.runtime_config import RuntimeConfigStore, runtime_config_store


def get_db() -> Generator[Session, None, None]:
    """Yield a request-scoped SQLAlchemy session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_embedding_service(request: Request) -> EmbeddingService:
    """Return the app-scoped embedding service loaded at startup."""
    service = getattr(request.app.state, "embedding_service", None)
    if service is None:
        raise RuntimeError("Embedding service is not initialized")
    return service


def get_llm(*, dry_run: bool | None = None) -> LLMProvider:
    """Return configured LLM provider (stub when dry-run or no API key)."""
    return get_llm_provider(dry_run=dry_run)


def get_runtime_config() -> RuntimeConfigStore:
    """Return the process-wide runtime config store."""
    return runtime_config_store


def get_effective_settings(
    config_store: Annotated[RuntimeConfigStore, Depends(get_runtime_config)],
) -> Settings:
    """Return settings merged with runtime admin overrides."""
    return config_store.effective_settings()


def require_admin(x_admin_key: Annotated[str | None, Header()] = None) -> None:
    """Validate the X-Admin-Key header against configured ADMIN_KEY."""
    settings = runtime_config_store.effective_settings()
    if not x_admin_key or x_admin_key != settings.admin_key:
        raise UnauthorizedError()
