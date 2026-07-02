"""Risk score accumulation with time decay, inactivity reset, and alert logic.

Combines the three detector sub-signals into a decayed EWMA risk score and
determines watch/alert status. Inactivity reset must run before detectors in
the orchestrator; this module exposes that early-return path via evaluate().
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.config import Settings, get_settings
from app.db.repositories import RiskProfileUpdate
from app.detectors.base import DetectorResult
from app.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class ScoreSignals:
    """Detector sub-signals in [0, 1] for one scoring cycle.

    Values must come from ``DetectorResult.signal`` after detector-side gating
    (``gated_signal``: raw * 0.3 when ``fired=False``). The scorer does not
    re-apply dampening — it must not zero non-fired signals.
    """

    probing: float
    escalation: float
    enumeration: float

    @classmethod
    def from_detector_results(
        cls,
        probing: DetectorResult,
        escalation: DetectorResult,
        enumeration: DetectorResult,
    ) -> ScoreSignals:
        """Build score inputs from detector outputs using pre-damped ``signal`` values."""
        return cls(
            probing=probing.signal,
            escalation=escalation.signal,
            enumeration=enumeration.signal,
        )


@dataclass(frozen=True)
class ProfileSnapshot:
    """Minimal prior risk profile state for scoring."""

    risk_score: float
    signal_probing: float
    signal_escalation: float
    signal_enumeration: float
    last_event_at: datetime | None
    last_scored_at: datetime | None
    status: str = "normal"
    version: int = 0

    @classmethod
    def from_row(cls, row: object) -> ProfileSnapshot:
        """Build a snapshot from a UserRiskProfileRow ORM instance."""
        from app.db.models import UserRiskProfileRow

        if not isinstance(row, UserRiskProfileRow):
            raise TypeError("expected UserRiskProfileRow")
        return cls(
            risk_score=float(row.risk_score),
            signal_probing=float(row.signal_probing or 0),
            signal_escalation=float(row.signal_escalation or 0),
            signal_enumeration=float(row.signal_enumeration or 0),
            last_event_at=row.last_event_at,
            last_scored_at=row.last_scored_at,
            status=row.status,
            version=row.version,
        )


@dataclass(frozen=True)
class AlertDraft:
    """Alert payload produced when risk crosses the alert threshold."""

    risk_score_at_alert: float
    threshold: float
    dominant_pattern: str
    pattern_breakdown: dict[str, Any]
    summary: str


@dataclass(frozen=True)
class ScoringOutcome:
    """Result of one scoring evaluation cycle."""

    risk_score: float
    signal_probing: float
    signal_escalation: float
    signal_enumeration: float
    last_scored_at: datetime
    status: str
    inactivity_reset: bool
    skipped_detection: bool
    alert: AlertDraft | None
    instantaneous_score: float | None = None


class RiskScorer:
    """Decay-weighted risk accumulator with inactivity reset and alert gating."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    @property
    def settings(self) -> Settings:
        return self._settings

    def decay_factor(self, dt_seconds: float) -> float:
        """Continuous-time decay multiplier for elapsed seconds since last score."""
        if dt_seconds <= 0:
            return 1.0
        half_life = self._settings.risk_half_life_seconds
        decay_lambda = math.log(2) / half_life
        return math.exp(-decay_lambda * dt_seconds)

    def compute_instantaneous_score(self, signals: ScoreSignals) -> float:
        """Weighted composite S_inst in [0, 100]."""
        s = self._settings
        weighted = (
            s.weight_probing * signals.probing
            + s.weight_escalation * signals.escalation
            + s.weight_enumeration * signals.enumeration
        )
        return 100.0 * weighted

    def accumulate(self, previous_score: float, instantaneous_score: float, dt_seconds: float) -> float:
        """Apply decay then EWMA gain, capped at 100."""
        decayed = previous_score * self.decay_factor(dt_seconds)
        updated = decayed + self._settings.risk_alpha * instantaneous_score
        return min(100.0, updated)

    def is_inactivity_reset_needed(
        self,
        last_event_at: datetime | None,
        now: datetime,
    ) -> bool:
        """True when the user has been inactive longer than the configured window."""
        if last_event_at is None:
            return False
        inactive_seconds = (now - last_event_at).total_seconds()
        return inactive_seconds > self._settings.inactivity_reset_seconds

    def determine_status(self, risk_score: float) -> str:
        """Map composite score to normal / watch / alerted."""
        if risk_score >= self._settings.alert_threshold:
            return "alerted"
        if risk_score >= self._settings.watch_threshold:
            return "watch"
        return "normal"

    def dominant_pattern(self, signals: ScoreSignals) -> str:
        """Return the technique with the highest sub-signal."""
        ranked = [
            ("probing", signals.probing),
            ("escalation", signals.escalation),
            ("enumeration", signals.enumeration),
        ]
        return max(ranked, key=lambda item: item[1])[0]

    def build_pattern_breakdown(
        self,
        signals: ScoreSignals,
        instantaneous_score: float,
    ) -> dict[str, Any]:
        """Structured sub-signal summary for alerts and threat cards."""
        s = self._settings
        return {
            "probing": {"signal": signals.probing, "weight": s.weight_probing},
            "escalation": {"signal": signals.escalation, "weight": s.weight_escalation},
            "enumeration": {"signal": signals.enumeration, "weight": s.weight_enumeration},
            "instantaneous_score": instantaneous_score,
        }

    def build_alert_summary(self, dominant: str, risk_score: float) -> str:
        """Human-readable alert headline."""
        headlines = {
            "probing": "Repeated boundary probing detected across sessions",
            "escalation": "Privilege escalation pattern detected across sessions",
            "enumeration": "Systematic enumeration pattern detected across sessions",
        }
        headline = headlines.get(dominant)
        if headline:
            return headline
        return f"Risk score {risk_score:.0f} crossed alert threshold"

    def should_create_alert(self, previous_score: float | None, new_score: float) -> bool:
        """Alert only when risk newly crosses the configured threshold."""
        threshold = self._settings.alert_threshold
        if new_score < threshold:
            return False
        if previous_score is None:
            return True
        return previous_score < threshold

    def evaluate(
        self,
        profile: ProfileSnapshot | None,
        signals: ScoreSignals | None,
        now: datetime | None = None,
    ) -> ScoringOutcome:
        """Score one cycle, or early-return on inactivity reset (detectors skipped).

        When inactivity reset triggers, detector signals are ignored even if
        provided — this preserves the zeroed profile on repeated recompute.
        """
        scored_at = now or datetime.now(UTC)

        if profile is not None and self.is_inactivity_reset_needed(profile.last_event_at, scored_at):
            logger.info(
                "inactivity_reset",
                extra={
                    "component": "risk_scorer",
                    "event": "inactivity_reset",
                    "user_id": getattr(profile, "user_id", None),
                },
            )
            return ScoringOutcome(
                risk_score=0.0,
                signal_probing=0.0,
                signal_escalation=0.0,
                signal_enumeration=0.0,
                last_scored_at=scored_at,
                status="normal",
                inactivity_reset=True,
                skipped_detection=True,
                alert=None,
                instantaneous_score=None,
            )

        if signals is None:
            signals = ScoreSignals(probing=0.0, escalation=0.0, enumeration=0.0)

        previous_score = profile.risk_score if profile is not None else 0.0
        if profile is not None and profile.last_scored_at is not None:
            dt_seconds = (scored_at - profile.last_scored_at).total_seconds()
        else:
            dt_seconds = 0.0

        s_inst = self.compute_instantaneous_score(signals)
        new_score = self.accumulate(previous_score, s_inst, dt_seconds)
        status = self.determine_status(new_score)

        alert: AlertDraft | None = None
        if self.should_create_alert(
            previous_score if profile is not None else None,
            new_score,
        ):
            dominant = self.dominant_pattern(signals)
            alert = AlertDraft(
                risk_score_at_alert=new_score,
                threshold=self._settings.alert_threshold,
                dominant_pattern=dominant,
                pattern_breakdown=self.build_pattern_breakdown(signals, s_inst),
                summary=self.build_alert_summary(dominant, new_score),
            )
            logger.info(
                "alert_threshold_crossed",
                extra={
                    "component": "risk_scorer",
                    "event": "alert_created",
                    "dominant_pattern": dominant,
                    "risk_score": new_score,
                },
            )

        return ScoringOutcome(
            risk_score=new_score,
            signal_probing=signals.probing,
            signal_escalation=signals.escalation,
            signal_enumeration=signals.enumeration,
            last_scored_at=scored_at,
            status=status,
            inactivity_reset=False,
            skipped_detection=False,
            alert=alert,
            instantaneous_score=s_inst,
        )

    def to_profile_update(
        self,
        outcome: ScoringOutcome,
        *,
        last_event_at: datetime | None,
        session_count: int | None = None,
        interaction_count: int | None = None,
    ) -> RiskProfileUpdate:
        """Convert a scoring outcome into a repository upsert payload."""
        return RiskProfileUpdate(
            risk_score=Decimal(str(round(outcome.risk_score, 2))),
            signal_probing=Decimal(str(round(outcome.signal_probing, 4))),
            signal_escalation=Decimal(str(round(outcome.signal_escalation, 4))),
            signal_enumeration=Decimal(str(round(outcome.signal_enumeration, 4))),
            last_event_at=last_event_at,
            last_scored_at=outcome.last_scored_at,
            session_count=session_count,
            interaction_count=interaction_count,
            status=outcome.status,
        )
