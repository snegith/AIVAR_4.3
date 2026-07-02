"""Detection orchestrator: advisory lock, detectors, scorer, pattern and alert writes.

Runs as a BackgroundTask after event ingestion (Phase 7). Enforces inactivity
reset before detectors and persists user_risk_profiles, detected_patterns,
and alerts in one transactional cycle per user.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.models import AlertRow, DetectedPatternRow, InteractionRow, UserRiskProfileRow
from app.db.repositories import (
    InteractionWindow,
    acquire_user_advisory_lock,
    create_alert,
    create_detected_pattern,
    count_user_interactions,
    count_user_sessions,
    get_interactions_window,
    get_latest_user_event_at,
    get_risk_profile,
    upsert_risk_profile,
)
from app.db.session import SessionLocal
from app.detectors import enumeration, escalation, probing
from app.detectors.base import (
    DetectionWindow,
    DetectorResult,
    WindowInteraction,
    WindowSession,
)
from app.logging import get_logger
from app.scoring.risk_scorer import ProfileSnapshot, RiskScorer, ScoreSignals

logger = get_logger(__name__)

_PATTERN_TYPES = ("probing", "escalation", "enumeration")


@dataclass(frozen=True)
class OrchestrationResult:
    """Outcome of one detection+scoring cycle for a user."""

    user_id: str
    risk_score: float
    status: str
    skipped_detection: bool
    inactivity_reset: bool
    pattern_ids: tuple[uuid.UUID, ...]
    alert_id: uuid.UUID | None


def _parse_contributing_ids(evidence: dict[str, Any]) -> list[uuid.UUID]:
    """Parse contributing interaction UUIDs from detector evidence."""
    raw = evidence.get("contributing_interaction_ids") or []
    return [uuid.UUID(str(value)) for value in raw]


def _interaction_window_to_detection_window(window: InteractionWindow) -> DetectionWindow:
    """Convert repository window rows into detector-facing dataclasses."""
    interactions = [
        WindowInteraction(
            id=row.id,
            session_id=row.session_id,
            user_id=row.user_id,
            ts=row.ts,
            prompt=row.prompt,
            guardrail_outcome=row.guardrail_outcome,
            embedding=list(row.embedding) if row.embedding is not None else None,
            template_signature=row.template_signature,
            numeric_tokens=row.numeric_tokens,
            capability_level=row.capability_level,
            langfuse_trace_id=row.langfuse_trace_id,
        )
        for row in window.interactions
    ]
    sessions = [
        WindowSession(id=row.id, user_id=row.user_id, started_at=row.started_at)
        for row in window.sessions
    ]
    return DetectionWindow(
        interactions=interactions,
        sessions=sessions,
        window_start=window.window_start,
        window_end=window.window_end,
    )


def _enrich_evidence_with_trace_urls(
    db: Session,
    evidence: dict[str, Any],
    interaction_ids: list[uuid.UUID],
) -> dict[str, Any]:
    """Attach sample Langfuse trace URLs when interactions have trace ids."""
    settings = get_settings()
    if not interaction_ids:
        return evidence

    rows = list(
        db.scalars(select(InteractionRow).where(InteractionRow.id.in_(interaction_ids))).all()
    )
    trace_urls: list[str] = []
    for row in rows:
        if row.langfuse_trace_id and settings.langfuse_enabled:
            trace_urls.append(f"{settings.langfuse_host.rstrip('/')}/trace/{row.langfuse_trace_id}")

    if not trace_urls:
        return evidence

    enriched = dict(evidence)
    enriched["sample_trace_urls"] = trace_urls[:5]
    return enriched


class DetectionOrchestrator:
    """Coordinate detectors, scoring, and persistence for one user."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._scorer = RiskScorer(self._settings)

    def run(
        self,
        db: Session,
        user_id: str,
        *,
        as_of: datetime | None = None,
    ) -> OrchestrationResult:
        """Execute one detection cycle under a per-user advisory lock."""
        now = as_of or datetime.now(UTC)
        acquire_user_advisory_lock(db, user_id)

        existing = get_risk_profile(db, user_id)
        profile = ProfileSnapshot.from_row(existing) if existing is not None else None
        last_event_at = (
            existing.last_event_at
            if existing is not None and existing.last_event_at is not None
            else get_latest_user_event_at(db, user_id)
        )

        if profile is not None and self._scorer.is_inactivity_reset_needed(
            profile.last_event_at, now
        ):
            outcome = self._scorer.evaluate(profile, None, now)
            upsert_risk_profile(
                db,
                user_id,
                self._scorer.to_profile_update(
                    outcome,
                    last_event_at=last_event_at,
                    session_count=count_user_sessions(db, user_id),
                    interaction_count=count_user_interactions(db, user_id),
                ),
                acquire_lock=False,
            )
            logger.info(
                "detection_cycle_complete",
                extra={
                    "component": "orchestrator",
                    "event": "inactivity_reset",
                    "user_id": user_id,
                },
            )
            return OrchestrationResult(
                user_id=user_id,
                risk_score=outcome.risk_score,
                status=outcome.status,
                skipped_detection=True,
                inactivity_reset=True,
                pattern_ids=(),
                alert_id=None,
            )

        window = get_interactions_window(db, user_id, as_of=now)
        detection_window = _interaction_window_to_detection_window(window)

        probing_result = probing.detect(detection_window)
        escalation_result = escalation.detect(detection_window)
        enumeration_result = enumeration.detect(detection_window)
        signals = ScoreSignals.from_detector_results(
            probing_result, escalation_result, enumeration_result
        )
        outcome = self._scorer.evaluate(profile, signals, now)

        pattern_ids = self._persist_patterns(
            db,
            user_id=user_id,
            detected_at=now,
            window=window,
            results={
                "probing": probing_result,
                "escalation": escalation_result,
                "enumeration": enumeration_result,
            },
        )

        alert_id: uuid.UUID | None = None
        if outcome.alert is not None:
            alert_row = create_alert(
                db,
                user_id=user_id,
                created_at=now,
                risk_score_at_alert=Decimal(str(round(outcome.alert.risk_score_at_alert, 2))),
                threshold=Decimal(str(round(outcome.alert.threshold, 2))),
                dominant_pattern=outcome.alert.dominant_pattern,
                pattern_breakdown=outcome.alert.pattern_breakdown,
                summary=outcome.alert.summary,
                contributing_pattern_ids=list(pattern_ids) if pattern_ids else None,
            )
            alert_id = alert_row.id

        upsert_risk_profile(
            db,
            user_id,
            self._scorer.to_profile_update(
                outcome,
                last_event_at=last_event_at,
                session_count=count_user_sessions(db, user_id),
                interaction_count=count_user_interactions(db, user_id),
            ),
            acquire_lock=False,
        )

        logger.info(
            "detection_cycle_complete",
            extra={
                "component": "orchestrator",
                "event": "scored",
                "user_id": user_id,
                "risk_score": outcome.risk_score,
                "status": outcome.status,
            },
        )
        return OrchestrationResult(
            user_id=user_id,
            risk_score=outcome.risk_score,
            status=outcome.status,
            skipped_detection=outcome.skipped_detection,
            inactivity_reset=outcome.inactivity_reset,
            pattern_ids=tuple(pattern_ids),
            alert_id=alert_id,
        )

    def _persist_patterns(
        self,
        db: Session,
        *,
        user_id: str,
        detected_at: datetime,
        window: InteractionWindow,
        results: dict[str, DetectorResult],
    ) -> list[uuid.UUID]:
        """Write detected_patterns rows for detectors that fired this cycle."""
        pattern_ids: list[uuid.UUID] = []
        for pattern_type in _PATTERN_TYPES:
            result = results[pattern_type]
            if not result.fired:
                continue
            contributing_ids = _parse_contributing_ids(result.evidence)
            evidence = _enrich_evidence_with_trace_urls(db, result.evidence, contributing_ids)
            row = create_detected_pattern(
                db,
                user_id=user_id,
                pattern_type=pattern_type,
                detected_at=detected_at,
                signal_strength=Decimal(str(round(result.signal, 4))),
                window_start=window.window_start,
                window_end=window.window_end,
                evidence=evidence,
                contributing_interaction_ids=contributing_ids or None,
            )
            pattern_ids.append(row.id)
        return pattern_ids


def run_detection_for_user(user_id: str, *, as_of: datetime | None = None) -> None:
    """BackgroundTask entrypoint: own DB session, commit on success."""
    db = SessionLocal()
    try:
        DetectionOrchestrator().run(db, user_id, as_of=as_of)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception(
            "detection_cycle_failed",
            extra={"component": "orchestrator", "event": "error", "user_id": user_id},
        )
        raise
    finally:
        db.close()
