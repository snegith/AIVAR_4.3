"""Shared pytest fixtures for the adversarial pattern detector test suite."""

from __future__ import annotations

import os
from collections.abc import Generator

import pytest
from alembic.config import Config
from pgvector.psycopg2 import register_vector
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from app.config import Settings, get_settings
from app.llm.stub_provider import StubLLMProvider

DEFAULT_DATABASE_URL = "postgresql://detector:detector@localhost:5433/detector_db"


def _database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


def _postgres_reachable(url: str) -> bool:
    try:
        engine = create_engine(url, pool_pre_ping=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:
        return False


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Generator[None, None, None]:
    """Isolate settings cache between tests."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def settings() -> Settings:
    """Fresh settings instance for unit tests."""
    return Settings()


@pytest.fixture
def stub_llm() -> StubLLMProvider:
    """Deterministic stub LLM for guardrail and capability tests."""
    return StubLLMProvider()


@pytest.fixture(scope="session")
def db_engine() -> Generator[Engine, None, None]:
    """Session-scoped engine with migrations applied (requires local Postgres)."""
    url = _database_url()
    if not _postgres_reachable(url):
        pytest.skip(f"Postgres not reachable at {url}")

    os.environ["DATABASE_URL"] = url
    get_settings.cache_clear()

    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(alembic_cfg, "head")

    engine = create_engine(url, pool_pre_ping=True)

    @event.listens_for(engine, "connect")
    def _register_pgvector(dbapi_connection: object, _: object) -> None:
        register_vector(dbapi_connection)

    yield engine
    engine.dispose()


@pytest.fixture
def db_session(db_engine: Engine) -> Generator[Session, None, None]:
    """Transactional session rolled back after each test."""
    connection = db_engine.connect()
    transaction = connection.begin()
    session = sessionmaker(bind=connection, autocommit=False, autoflush=False)()
    yield session
    session.close()
    transaction.rollback()
    connection.close()
