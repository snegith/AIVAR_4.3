"""Repository layer for detector_db persistence and windowed reads.

Encapsulates windowed interaction queries, per-user advisory locking, and
optimistic-lock upserts on user_risk_profiles.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, desc, func, select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import (
    AlertRow,
    DetectedPatternRow,
    InteractionRow,
    SessionRow,
    UserRiskProfileRow,
)
from app.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class InteractionWindow:
    """Interactions and sessions in the rolling detection window for one user."""

    interactions: list[InteractionRow]
    sessions: list[SessionRow]
    window_start: datetime
    window_end: datetime


def acquire_user_advisory_lock(db: Session, user_id: str) -> None:
    """Serialize per-user scoring with a transaction-scoped advisory lock."""
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:user_id))"),
        {"user_id": user_id},
    )
    logger.debug(
        "advisory_lock_acquired",
        extra={"component": "repository", "event": "advisory_lock", "user_id": user_id},
    )


def _session_cutoff(
    db: Session, user_id: str, window_sessions: int
) -> datetime | None:
    """Earliest started_at among the last N sessions (more recent = tighter window)."""
    stmt: Select[tuple[datetime]] = (
        select(SessionRow.started_at)
        .where(SessionRow.user_id == user_id)
        .order_by(desc(SessionRow.started_at))
        .limit(window_sessions)
    )
    started_times = list(db.scalars(stmt).all())
    if not started_times:
        return None
    return min(started_times)


def get_interactions_window(
    db: Session,
    user_id: str,
    *,
    window_sessions: int | None = None,
    window_days: int | None = None,
    as_of: datetime | None = None,
) -> InteractionWindow:
    """Return interactions in the rolling window (last N sessions or D days, whichever is tighter).

    Uses the more restrictive cutoff: max(session_cutoff, day_cutoff) so the
    window never spans more than WINDOW_DAYS and never more than WINDOW_SESSIONS.
    """
    settings = get_settings()
    n_sessions = window_sessions if window_sessions is not None else settings.window_sessions
    n_days = window_days if window_days is not None else settings.window_days
    end = as_of or datetime.now(UTC)

    day_cutoff = end - timedelta(days=n_days)
    session_cutoff = _session_cutoff(db, user_id, n_sessions)

    if session_cutoff is None:
        cutoff = day_cutoff
    else:
        cutoff = max(session_cutoff, day_cutoff)

    interactions = list(
        db.scalars(
            select(InteractionRow)
            .where(
                InteractionRow.user_id == user_id,
                InteractionRow.ts >= cutoff,
                InteractionRow.ts <= end,
            )
            .order_by(InteractionRow.ts.asc())
        ).all()
    )

    session_ids = {row.session_id for row in interactions}
    sessions: list[SessionRow] = []
    if session_ids:
        sessions = list(
            db.scalars(
                select(SessionRow)
                .where(SessionRow.id.in_(session_ids))
                .order_by(SessionRow.started_at.asc())
            ).all()
        )

    window_start = cutoff
    if interactions:
        window_start = min(cutoff, interactions[0].ts)

    return InteractionWindow(
        interactions=interactions,
        sessions=sessions,
        window_start=window_start,
        window_end=end,
    )


def create_session(
    db: Session,
    *,
    user_id: str,
    started_at: datetime,
    session_metadata: dict[str, Any] | None = None,
) -> SessionRow:
    """Insert a new session row."""
    row = SessionRow(
        user_id=user_id,
        started_at=started_at,
        last_event_at=started_at,
        session_metadata=session_metadata,
    )
    db.add(row)
    db.flush()
    return row


def create_interaction(
    db: Session,
    *,
    session_id: uuid.UUID,
    user_id: str,
    ts: datetime,
    prompt: str,
    guardrail_outcome: str,
    embedding: list[float] | None = None,
    **kwargs: Any,
) -> InteractionRow:
    """Insert an interaction row, optionally with a 384-d embedding vector."""
    row = InteractionRow(
        session_id=session_id,
        user_id=user_id,
        ts=ts,
        prompt=prompt,
        guardrail_outcome=guardrail_outcome,
        embedding=embedding,
        **kwargs,
    )
    db.add(row)
    db.flush()
    return row


def get_interaction_by_id(db: Session, interaction_id: uuid.UUID) -> InteractionRow | None:
    """Fetch a single interaction by primary key."""
    return db.get(InteractionRow, interaction_id)


@dataclass(frozen=True)
class RiskProfileUpdate:
    """Fields to upsert on user_risk_profiles."""

    risk_score: Decimal
    signal_probing: Decimal | None = None
    signal_escalation: Decimal | None = None
    signal_enumeration: Decimal | None = None
    last_event_at: datetime | None = None
    last_scored_at: datetime | None = None
    session_count: int | None = None
    interaction_count: int | None = None
    status: str = "normal"


class OptimisticLockError(Exception):
    """Raised when user_risk_profiles version does not match on update."""


def upsert_risk_profile(
    db: Session,
    user_id: str,
    update: RiskProfileUpdate,
    *,
    expected_version: int | None = None,
    acquire_lock: bool = True,
) -> UserRiskProfileRow:
    """Upsert user_risk_profiles under advisory lock with optimistic version check.

  If expected_version is provided on an existing row, the update fails with
  OptimisticLockError when the stored version differs.
    """
    if acquire_lock:
        acquire_user_advisory_lock(db, user_id)

    existing = db.get(UserRiskProfileRow, user_id)
    if existing is None:
        row = UserRiskProfileRow(
            user_id=user_id,
            risk_score=update.risk_score,
            signal_probing=update.signal_probing,
            signal_escalation=update.signal_escalation,
            signal_enumeration=update.signal_enumeration,
            last_event_at=update.last_event_at,
            last_scored_at=update.last_scored_at,
            session_count=update.session_count,
            interaction_count=update.interaction_count,
            status=update.status,
            version=0,
        )
        db.add(row)
        db.flush()
        return row

    if expected_version is not None and existing.version != expected_version:
        raise OptimisticLockError(
            f"version mismatch for {user_id}: expected {expected_version}, got {existing.version}"
        )

    existing.risk_score = update.risk_score
    existing.signal_probing = update.signal_probing
    existing.signal_escalation = update.signal_escalation
    existing.signal_enumeration = update.signal_enumeration
    existing.last_event_at = update.last_event_at
    existing.last_scored_at = update.last_scored_at
    existing.session_count = update.session_count
    existing.interaction_count = update.interaction_count
    existing.status = update.status
    existing.version = existing.version + 1
    db.flush()
    return existing


def count_user_sessions(db: Session, user_id: str) -> int:
    """Return total session count for a user."""
    return int(
        db.scalar(
            select(func.count()).select_from(SessionRow).where(SessionRow.user_id == user_id)
        )
        or 0
    )


def count_user_interactions(db: Session, user_id: str) -> int:
    """Return total interaction count for a user."""
    return int(
        db.scalar(
            select(func.count())
            .select_from(InteractionRow)
            .where(InteractionRow.user_id == user_id)
        )
        or 0
    )


def get_risk_profile(db: Session, user_id: str) -> UserRiskProfileRow | None:
    """Fetch the current risk profile row for a user."""
    return db.get(UserRiskProfileRow, user_id)


def get_latest_user_event_at(db: Session, user_id: str) -> datetime | None:
    """Return the timestamp of the user's most recent interaction."""
    return db.scalar(
        select(func.max(InteractionRow.ts)).where(InteractionRow.user_id == user_id)
    )


def create_detected_pattern(
    db: Session,
    *,
    user_id: str,
    pattern_type: str,
    detected_at: datetime,
    signal_strength: Decimal | None,
    window_start: datetime | None,
    window_end: datetime | None,
    evidence: dict[str, Any] | None,
    contributing_interaction_ids: list[uuid.UUID] | None,
) -> DetectedPatternRow:
    """Insert a detected_patterns row for a fired detector cycle."""
    row = DetectedPatternRow(
        user_id=user_id,
        pattern_type=pattern_type,
        detected_at=detected_at,
        signal_strength=signal_strength,
        window_start=window_start,
        window_end=window_end,
        evidence=evidence,
        contributing_interaction_ids=contributing_interaction_ids,
    )
    db.add(row)
    db.flush()
    return row


def create_alert(
    db: Session,
    *,
    user_id: str,
    created_at: datetime,
    risk_score_at_alert: Decimal,
    threshold: Decimal,
    dominant_pattern: str,
    pattern_breakdown: dict[str, Any],
    summary: str,
    contributing_pattern_ids: list[uuid.UUID] | None,
) -> AlertRow:
    """Insert an alerts row when composite risk crosses the threshold."""
    row = AlertRow(
        user_id=user_id,
        created_at=created_at,
        risk_score_at_alert=risk_score_at_alert,
        threshold=threshold,
        dominant_pattern=dominant_pattern,
        pattern_breakdown=pattern_breakdown,
        summary=summary,
        contributing_pattern_ids=contributing_pattern_ids,
    )
    db.add(row)
    db.flush()
    return row
