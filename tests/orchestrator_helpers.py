"""Helpers to seed detector_db for orchestrator integration tests."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import SessionRow
from app.db.repositories import create_interaction
from app.detectors.base import DetectionWindow


def persist_detection_window(
    db: Session,
    window: DetectionWindow,
    *,
    user_id: str | None = None,
) -> None:
    """Insert sessions and interactions from a synthetic DetectionWindow."""
    session_by_id = {session.id: session for session in window.sessions}
    for session in window.sessions:
        db.add(
            SessionRow(
                id=session.id,
                user_id=user_id or session.user_id,
                started_at=session.started_at,
                last_event_at=session.started_at,
            )
        )
    db.flush()

    for interaction in window.interactions:
        session = session_by_id[interaction.session_id]
        create_interaction(
            db,
            session_id=session.id,
            user_id=user_id or interaction.user_id,
            ts=interaction.ts,
            prompt=interaction.prompt,
            guardrail_outcome=interaction.guardrail_outcome,
            embedding=interaction.embedding,
            template_signature=interaction.template_signature,
            numeric_tokens=interaction.numeric_tokens,
            capability_level=interaction.capability_level,
            langfuse_trace_id=interaction.langfuse_trace_id,
        )
    db.flush()
