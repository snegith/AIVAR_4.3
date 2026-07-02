"""Phase 1 data-layer tests: migrations, embedding round-trip, windowed queries."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import inspect, text

from app.db.repositories import (
    OptimisticLockError,
    RiskProfileUpdate,
    create_interaction,
    create_session,
    get_interaction_by_id,
    get_interactions_window,
    upsert_risk_profile,
)


def test_migrations_apply_all_tables(db_session) -> None:
    """Alembic 0001 creates all five tables and enables pgvector."""
    inspector = inspect(db_session.bind)
    tables = set(inspector.get_table_names())
    assert tables >= {
        "sessions",
        "interactions",
        "user_risk_profiles",
        "detected_patterns",
        "alerts",
        "alembic_version",
    }
    ext = db_session.execute(
        text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
    ).scalar()
    assert ext == 1


def test_interaction_embedding_round_trip(db_session) -> None:
    """A 384-dim embedding persists and reads back with full dimensionality."""
    now = datetime.now(UTC)
    session = create_session(db_session, user_id="embed-user", started_at=now)
    embedding = [float(i) / 384.0 for i in range(384)]

    created = create_interaction(
        db_session,
        session_id=session.id,
        user_id="embed-user",
        ts=now,
        prompt="probe text",
        guardrail_outcome="blocked",
        embedding=embedding,
    )
    db_session.flush()

    loaded = get_interaction_by_id(db_session, created.id)
    assert loaded is not None
    assert loaded.embedding is not None
    assert len(loaded.embedding) == 384
    assert loaded.embedding[0] == pytest.approx(0.0)
    assert loaded.embedding[-1] == pytest.approx(383.0 / 384.0)


def test_get_interactions_window_respects_sessions_and_days(db_session) -> None:
    """Windowed query returns only interactions inside the tighter of N sessions or D days."""
    user_id = "window-user"
    now = datetime.now(UTC)

    # Five recent sessions (inside 7-day and 3-session windows)
    recent_sessions = []
    for offset in range(5):
        started = now - timedelta(days=offset)
        recent_sessions.append(
            create_session(db_session, user_id=user_id, started_at=started)
        )
        create_interaction(
            db_session,
            session_id=recent_sessions[-1].id,
            user_id=user_id,
            ts=started,
            prompt=f"recent-{offset}",
            guardrail_outcome="allowed",
        )

    # Old session outside 7-day window but would be in last 30 sessions
    old_started = now - timedelta(days=10)
    old_session = create_session(db_session, user_id=user_id, started_at=old_started)
    create_interaction(
        db_session,
        session_id=old_session.id,
        user_id=user_id,
        ts=old_started,
        prompt="old-outside-window",
        guardrail_outcome="blocked",
    )
    db_session.flush()

    window = get_interactions_window(
        db_session,
        user_id,
        window_sessions=3,
        window_days=7,
        as_of=now,
    )

    prompts = [row.prompt for row in window.interactions]
    assert "old-outside-window" not in prompts
    assert len(window.interactions) == 3
    assert prompts == ["recent-2", "recent-1", "recent-0"]


def test_upsert_risk_profile_advisory_lock_and_version(db_session) -> None:
    """upsert_risk_profile creates, updates, and enforces optimistic version."""
    now = datetime.now(UTC)
    update = RiskProfileUpdate(
        risk_score=Decimal("12.50"),
        signal_probing=Decimal("0.1000"),
        last_event_at=now,
        last_scored_at=now,
        status="watch",
    )

    created = upsert_risk_profile(db_session, "risk-user", update)
    db_session.flush()
    assert created.version == 0
    assert created.risk_score == Decimal("12.50")

    updated = upsert_risk_profile(
        db_session,
        "risk-user",
        RiskProfileUpdate(risk_score=Decimal("20.00"), status="alerted"),
        expected_version=0,
    )
    db_session.flush()
    assert updated.version == 1
    assert updated.risk_score == Decimal("20.00")

    with pytest.raises(OptimisticLockError):
        upsert_risk_profile(
            db_session,
            "risk-user",
            RiskProfileUpdate(risk_score=Decimal("99.00")),
            expected_version=0,
        )
