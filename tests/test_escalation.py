"""Isolation tests for the escalation detector."""

import uuid
from datetime import UTC, datetime, timedelta

from app.detectors import escalation
from app.detectors.base import DetectionWindow, WindowInteraction, WindowSession
from tests.detector_helpers import make_escalation_attack_window, make_escalation_benign_window


def test_escalation_fires_at_ten_sessions() -> None:
    window = make_escalation_attack_window(session_count=10)
    result = escalation.detect(window)
    assert result.fired is True
    assert result.evidence["rho"] >= 0.6
    assert result.evidence["level_range"] >= 2


def test_escalation_silent_on_benign_window() -> None:
    window = make_escalation_benign_window()
    result = escalation.detect(window)
    assert result.fired is False
    assert result.signal < 0.45


def test_escalation_silent_when_capability_levels_are_constant() -> None:
    """Flat capability series must not call spearmanr (no ConstantInputWarning)."""
    now = datetime.now(UTC)
    interactions: list[WindowInteraction] = []
    sessions: list[WindowSession] = []
    for idx in range(6):
        session_id = uuid.uuid4()
        started = now - timedelta(days=6 - idx)
        sessions.append(WindowSession(id=session_id, user_id="flat", started_at=started))
        interactions.append(
            WindowInteraction(
                id=uuid.uuid4(),
                session_id=session_id,
                user_id="flat",
                ts=started,
                prompt=f"benign flat question {idx}",
                guardrail_outcome="allowed",
                capability_level=0,
            )
        )
    window = DetectionWindow(
        interactions=interactions,
        sessions=sessions,
        window_start=now - timedelta(days=7),
        window_end=now,
    )
    result = escalation.detect(window)
    assert result.fired is False
    assert result.signal == 0.0
    assert result.evidence.get("constant_capability") is True
