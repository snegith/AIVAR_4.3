"""Isolation tests for the enumeration detector."""

from app.detectors import enumeration
from tests.detector_helpers import make_enumeration_attack_window, make_enumeration_benign_window


def test_enumeration_fires_on_template_sweep() -> None:
    window = make_enumeration_attack_window(group_size=50)
    result = enumeration.detect(window)
    assert result.fired is True
    assert result.evidence["group_size"] == 50
    assert result.evidence["dominance"] >= 0.4
    assert result.evidence["regularity"] >= 0.7


def test_enumeration_silent_on_diverse_power_user() -> None:
    window = make_enumeration_benign_window()
    result = enumeration.detect(window)
    assert result.fired is False
    assert result.signal < 0.45
