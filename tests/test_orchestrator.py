"""Integration tests for the detection orchestrator."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.config import Settings
from app.db.models import AlertRow, DetectedPatternRow, UserRiskProfileRow
from app.detection.orchestrator import DetectionOrchestrator
from app.scoring.risk_scorer import RiskScorer
from tests.detector_helpers import make_probing_attack_window
from tests.orchestrator_helpers import persist_detection_window


def test_orchestrator_persists_risk_patterns_and_alert(db_session) -> None:
    """Probing attack data should produce risk profile, pattern, and alert rows."""
    user_id = "orchestrator-prober"
    persist_detection_window(db_session, make_probing_attack_window(blocked_count=20), user_id=user_id)
    orchestrator = DetectionOrchestrator(Settings(risk_alpha=0.6, alert_threshold=70.0))
    now = datetime.now(UTC)

    result = None
    for cycle in range(8):
        result = orchestrator.run(
            db_session,
            user_id,
            as_of=now + timedelta(hours=cycle),
        )
        if result.alert_id is not None:
            break

    assert result is not None
    assert result.risk_score >= 70.0
    assert result.status == "alerted"
    assert result.alert_id is not None

    profile = db_session.get(UserRiskProfileRow, user_id)
    assert profile is not None
    assert float(profile.risk_score) >= 70.0
    assert profile.status == "alerted"
    assert float(profile.signal_probing or 0) > 0

    patterns = list(
        db_session.scalars(
            select(DetectedPatternRow).where(DetectedPatternRow.user_id == user_id)
        ).all()
    )
    assert any(row.pattern_type == "probing" for row in patterns)

    alert = db_session.get(AlertRow, result.alert_id)
    assert alert is not None
    assert alert.dominant_pattern == "probing"
    assert alert.contributing_pattern_ids is not None
    assert len(alert.contributing_pattern_ids) >= 1


def test_orchestrator_inactivity_reset_skips_detectors(db_session) -> None:
    """Stale last_event_at must zero profile without writing new patterns."""
    user_id = "orchestrator-inactive"
    persist_detection_window(db_session, make_probing_attack_window(blocked_count=20), user_id=user_id)
    now = datetime.now(UTC)
    stale = now - timedelta(days=8)

    profile = UserRiskProfileRow(
        user_id=user_id,
        risk_score=82.5,
        signal_probing=0.85,
        signal_escalation=0.7,
        signal_enumeration=0.6,
        last_event_at=stale,
        last_scored_at=now - timedelta(hours=1),
        status="alerted",
        version=0,
    )
    db_session.add(profile)
    db_session.flush()

    pattern_count_before = db_session.scalar(
        select(func.count()).select_from(DetectedPatternRow).where(
            DetectedPatternRow.user_id == user_id
        )
    )

    result = DetectionOrchestrator(Settings(inactivity_reset_seconds=604800)).run(
        db_session,
        user_id,
        as_of=now,
    )

    assert result.inactivity_reset is True
    assert result.skipped_detection is True
    assert result.risk_score == 0.0
    assert result.pattern_ids == ()

    refreshed = db_session.get(UserRiskProfileRow, user_id)
    assert refreshed is not None
    assert float(refreshed.risk_score) == 0.0
    assert float(refreshed.signal_probing or 0) == 0.0
    assert refreshed.status == "normal"

    pattern_count_after = db_session.scalar(
        select(func.count()).select_from(DetectedPatternRow).where(
            DetectedPatternRow.user_id == user_id
        )
    )
    assert pattern_count_after == pattern_count_before


def test_orchestrator_uses_damped_detector_signals(db_session) -> None:
    """Orchestrator must pass DetectorResult.signal (damped), not zero on not fired."""
    user_id = "orchestrator-damped"
    persist_detection_window(db_session, make_probing_attack_window(blocked_count=20), user_id=user_id)
    orchestrator = DetectionOrchestrator(Settings(risk_alpha=0.6))

    result = orchestrator.run(db_session, user_id, as_of=datetime.now(UTC))
    profile = db_session.get(UserRiskProfileRow, user_id)
    assert profile is not None
    assert float(profile.signal_probing) > 0.0
    assert result.risk_score > 0.0

    scorer = RiskScorer(Settings(risk_alpha=0.6))
    assert result.risk_score == pytest.approx(
        scorer.accumulate(0.0, 100.0 * 0.35 * float(profile.signal_probing or 0), 0.0),
        rel=0.01,
    )
