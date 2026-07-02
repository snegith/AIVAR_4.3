"""Isolation tests for the probing detector."""

from app.detectors import probing
from tests.detector_helpers import make_probing_attack_window, make_probing_benign_window


def test_probing_fires_on_paraphrased_blocked_cluster() -> None:
    window = make_probing_attack_window(blocked_count=20, target_sim=0.82)
    result = probing.detect(window)
    assert result.fired is True
    assert result.signal > 0.5
    assert result.evidence["cluster_size"] >= 5
    assert result.evidence["mean_sim"] >= 0.75


def test_probing_silent_on_benign_window() -> None:
    window = make_probing_benign_window()
    result = probing.detect(window)
    assert result.fired is False
    assert result.signal < 0.45
