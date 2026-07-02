"""SQLAlchemy engine and session factory with pgvector registration.

Provides the shared database connection used by repositories, Alembic, and
the API layer. pgvector types are registered on engine connect.
"""

from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

_settings = get_settings()

engine = create_engine(
    _settings.database_url,
    pool_pre_ping=True,
)


@event.listens_for(engine, "connect")
def _register_pgvector(dbapi_connection: object, _: object) -> None:
    """Register pgvector adapters on each new DBAPI connection."""
    from pgvector.psycopg2 import register_vector

    register_vector(dbapi_connection)


SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def get_db_session() -> Generator[Session, None, None]:
    """Yield a transactional SQLAlchemy session (FastAPI dependency in Phase 7)."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
