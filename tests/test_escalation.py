"""Isolation tests for the escalation detector."""

from app.detectors import escalation
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
