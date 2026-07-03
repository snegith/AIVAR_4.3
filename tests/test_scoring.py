"""Tests for risk scoring: EWMA decay, accumulation, alerts, and inactivity reset."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.config import Settings
from app.db.repositories import RiskProfileUpdate, upsert_risk_profile
from app.detectors import enumeration, escalation, probing
from app.detectors.base import DetectorResult, gated_signal
from app.scoring.risk_scorer import ProfileSnapshot, RiskScorer, ScoreSignals
from tests.detector_helpers import (
    make_enumeration_benign_window,
    make_escalation_benign_window,
    make_probing_benign_window,
)


def _scorer(**overrides: float | int) -> RiskScorer:
    return RiskScorer(Settings(**overrides))


def test_scorer_uses_damped_detector_signals_not_zeroed() -> None:
    """Non-fired detectors must contribute raw*0.3 via .signal, not be zeroed."""
    esc_result = escalation.detect(make_escalation_benign_window())
    enum_result = enumeration.detect(make_enumeration_benign_window())
    probe_result = probing.detect(make_probing_benign_window())

    assert esc_result.fired is False
    assert enum_result.fired is False
    assert esc_result.signal > 0.0
    assert enum_result.signal > 0.0

    signals = ScoreSignals.from_detector_results(probe_result, esc_result, enum_result)
    assert signals.escalation == pytest.approx(esc_result.signal)
    assert signals.enumeration == pytest.approx(enum_result.signal)
    assert signals.probing == pytest.approx(probe_result.signal)

    scorer = _scorer()
    damped_inst = scorer.compute_instantaneous_score(signals)
    zeroed_inst = scorer.compute_instantaneous_score(
        ScoreSignals(probing=0.0, escalation=0.0, enumeration=0.0)
    )
    assert damped_inst > zeroed_inst
    assert damped_inst == pytest.approx(
        100.0
        * (
            scorer.settings.weight_probing * probe_result.signal
            + scorer.settings.weight_escalation * esc_result.signal
            + scorer.settings.weight_enumeration * enum_result.signal
        )
    )


def test_gated_signal_dampening_matches_detector_contract() -> None:
    """Detectors apply *0.3 when not fired; scorer must consume that value as-is."""
    raw = 0.68
    damped = gated_signal(raw, fired=False)
    assert damped == pytest.approx(raw * 0.3)

    result = DetectorResult(signal=damped, fired=False, evidence={})
    signals = ScoreSignals.from_detector_results(
        DetectorResult(signal=0.0, fired=False, evidence={}),
        result,
        DetectorResult(signal=0.0, fired=False, evidence={}),
    )
    scorer = _scorer()
    s_inst = scorer.compute_instantaneous_score(signals)
    assert s_inst == pytest.approx(100.0 * scorer.settings.weight_escalation * damped)
    assert s_inst > 0.0


def test_instantaneous_score_weighted_combine() -> None:
    scorer = _scorer()
    signals = ScoreSignals(probing=1.0, escalation=0.0, enumeration=0.0)
    assert scorer.compute_instantaneous_score(signals) == pytest.approx(35.0)

    signals_all = ScoreSignals(probing=1.0, escalation=1.0, enumeration=1.0)
    assert scorer.compute_instantaneous_score(signals_all) == pytest.approx(100.0)


def test_decay_halflife_reduces_prior_score_by_half() -> None:
    scorer = _scorer(risk_half_life_seconds=86400.0, risk_alpha=0.6)
    new_score = scorer.accumulate(previous_score=80.0, instantaneous_score=0.0, dt_seconds=86400.0)
    assert new_score == pytest.approx(40.0)


def test_accumulation_over_cycles_reaches_alert_threshold() -> None:
    scorer = _scorer(risk_alpha=0.6)
    signals = ScoreSignals(probing=1.0, escalation=0.0, enumeration=0.0)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    profile: ProfileSnapshot | None = None

    for _ in range(6):
        outcome = scorer.evaluate(profile, signals, now)
        profile = ProfileSnapshot(
            risk_score=outcome.risk_score,
            signal_probing=outcome.signal_probing,
            signal_escalation=outcome.signal_escalation,
            signal_enumeration=outcome.signal_enumeration,
            last_event_at=now,
            last_scored_at=outcome.last_scored_at,
            status=outcome.status,
        )
        now += timedelta(hours=1)

    assert profile is not None
    assert profile.risk_score >= 70.0


def test_accumulation_capped_at_100() -> None:
    scorer = _scorer(risk_alpha=1.0)
    new_score = scorer.accumulate(previous_score=90.0, instantaneous_score=100.0, dt_seconds=0.0)
    assert new_score == 100.0


def test_status_watch_and_alert_thresholds() -> None:
    scorer = _scorer(alert_threshold=70.0, watch_threshold=45.0)
    assert scorer.determine_status(30.0) == "normal"
    assert scorer.determine_status(50.0) == "watch"
    assert scorer.determine_status(75.0) == "alerted"


def test_alert_created_when_crossing_threshold() -> None:
    scorer = _scorer(risk_alpha=0.6, alert_threshold=70.0)
    signals = ScoreSignals(probing=1.0, escalation=1.0, enumeration=1.0)
    now = datetime(2026, 2, 1, tzinfo=UTC)

    first = scorer.evaluate(None, signals, now)
    assert first.alert is None

    profile = ProfileSnapshot(
        risk_score=first.risk_score,
        signal_probing=first.signal_probing,
        signal_escalation=first.signal_escalation,
        signal_enumeration=first.signal_enumeration,
        last_event_at=now,
        last_scored_at=first.last_scored_at,
        status=first.status,
    )
    second = scorer.evaluate(profile, signals, now + timedelta(minutes=5))
    assert second.risk_score >= 70.0
    assert second.alert is not None
    assert second.alert.dominant_pattern == "probing"
    assert second.alert.pattern_breakdown["instantaneous_score"] == pytest.approx(100.0)


def test_alert_not_repeated_when_already_above_threshold() -> None:
    scorer = _scorer(alert_threshold=70.0)
    signals = ScoreSignals(probing=0.9, escalation=0.9, enumeration=0.9)
    now = datetime(2026, 3, 1, tzinfo=UTC)
    profile = ProfileSnapshot(
        risk_score=80.0,
        signal_probing=0.9,
        signal_escalation=0.9,
        signal_enumeration=0.9,
        last_event_at=now,
        last_scored_at=now - timedelta(hours=1),
        status="alerted",
    )
    outcome = scorer.evaluate(profile, signals, now)
    assert outcome.risk_score >= 70.0
    assert outcome.alert is None


def test_inactivity_reset_zeros_profile_and_skips_detection() -> None:
    scorer = _scorer(inactivity_reset_seconds=604800)
    now = datetime(2026, 4, 1, tzinfo=UTC)
    stale_event = now - timedelta(days=8)
    profile = ProfileSnapshot(
        risk_score=85.0,
        signal_probing=0.9,
        signal_escalation=0.8,
        signal_enumeration=0.7,
        last_event_at=stale_event,
        last_scored_at=now - timedelta(hours=2),
        status="alerted",
    )
    high_signals = ScoreSignals(probing=1.0, escalation=1.0, enumeration=1.0)

    outcome = scorer.evaluate(profile, high_signals, now)

    assert outcome.inactivity_reset is True
    assert outcome.skipped_detection is True
    assert outcome.risk_score == 0.0
    assert outcome.signal_probing == 0.0
    assert outcome.signal_escalation == 0.0
    assert outcome.signal_enumeration == 0.0
    assert outcome.status == "normal"
    assert outcome.alert is None


def test_second_recompute_after_inactivity_reset_stays_zero() -> None:
    """Repeated recompute without a new event must not re-apply detector signals."""
    scorer = _scorer(inactivity_reset_seconds=604800)
    now = datetime(2026, 4, 10, tzinfo=UTC)
    stale_event = now - timedelta(days=10)
    profile = ProfileSnapshot(
        risk_score=85.0,
        signal_probing=0.9,
        signal_escalation=0.8,
        signal_enumeration=0.7,
        last_event_at=stale_event,
        last_scored_at=now - timedelta(days=1),
        status="alerted",
    )
    high_signals = ScoreSignals(probing=1.0, escalation=1.0, enumeration=1.0)

    first = scorer.evaluate(profile, high_signals, now)
    assert first.risk_score == 0.0
    assert first.skipped_detection is True

    reset_profile = ProfileSnapshot(
        risk_score=first.risk_score,
        signal_probing=first.signal_probing,
        signal_escalation=first.signal_escalation,
        signal_enumeration=first.signal_enumeration,
        last_event_at=stale_event,
        last_scored_at=first.last_scored_at,
        status=first.status,
    )
    second = scorer.evaluate(reset_profile, high_signals, now + timedelta(minutes=1))

    assert second.risk_score == 0.0
    assert second.signal_probing == 0.0
    assert second.signal_escalation == 0.0
    assert second.signal_enumeration == 0.0
    assert second.skipped_detection is True
    assert second.inactivity_reset is True


def test_inactivity_reset_persisted_via_repository(db_session: object) -> None:
    """Integration: inactive profile upsert leaves risk_score and signals at zero."""
    from sqlalchemy.orm import Session

    assert isinstance(db_session, Session)
    scorer = RiskScorer(Settings(inactivity_reset_seconds=604800))
    user_id = "scoring-reset-user"
    now = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    stale_event = now - timedelta(days=8)

    upsert_risk_profile(
        db_session,
        user_id,
        RiskProfileUpdate(
            risk_score=Decimal("82.50"),
            signal_probing=Decimal("0.8500"),
            signal_escalation=Decimal("0.7000"),
            signal_enumeration=Decimal("0.6000"),
            last_event_at=stale_event,
            last_scored_at=now - timedelta(hours=3),
            status="alerted",
        ),
    )

    from app.db.models import UserRiskProfileRow

    row = db_session.get(UserRiskProfileRow, user_id)
    assert row is not None
    profile = ProfileSnapshot.from_row(row)
    outcome = scorer.evaluate(
        profile,
        ScoreSignals(probing=1.0, escalation=1.0, enumeration=1.0),
        now,
    )
    update = scorer.to_profile_update(outcome, last_event_at=stale_event)
    upsert_risk_profile(db_session, user_id, update)

    refreshed = db_session.get(UserRiskProfileRow, user_id)
    assert refreshed is not None
    assert float(refreshed.risk_score) == 0.0
    assert float(refreshed.signal_probing or 0) == 0.0
    assert float(refreshed.signal_escalation or 0) == 0.0
    assert float(refreshed.signal_enumeration or 0) == 0.0
    assert refreshed.status == "normal"

    second_outcome = scorer.evaluate(ProfileSnapshot.from_row(refreshed), ScoreSignals(1, 1, 1), now)
    assert second_outcome.risk_score == 0.0
    assert second_outcome.skipped_detection is True
