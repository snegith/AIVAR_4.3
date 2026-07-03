"""Privilege escalation detector using session-level capability trends."""

from __future__ import annotations

from typing import Any

import numpy as np
from scipy.stats import kendalltau, spearmanr

from app.detectors.base import DetectionWindow, DetectorConfig, DetectorResult, clamp, gated_signal
from app.logging import get_logger

logger = get_logger(__name__)


def detect(window: DetectionWindow, cfg: DetectorConfig | None = None) -> DetectorResult:
    """Detect monotonic capability escalation across sessions."""
    config = cfg or DetectorConfig.from_settings()
    session_by_id = {session.id: session for session in window.sessions}

    per_session_level: dict[Any, int] = {}
    per_session_ids: dict[Any, list[str]] = {}
    for row in window.interactions:
        if row.capability_level is None:
            continue
        current = per_session_level.get(row.session_id, 0)
        if row.capability_level >= current:
            per_session_level[row.session_id] = row.capability_level
            per_session_ids.setdefault(row.session_id, []).append(str(row.id))

    ordered_sessions = sorted(
        (
            (session_by_id[sid].started_at, sid, level)
            for sid, level in per_session_level.items()
            if sid in session_by_id
        ),
        key=lambda item: item[0],
    )

    if len(ordered_sessions) < config.escalation_min_sessions:
        return DetectorResult(
            signal=0.0,
            fired=False,
            evidence={"session_count": len(ordered_sessions)},
        )

    indices = list(range(len(ordered_sessions)))
    levels = [item[2] for item in ordered_sessions]
    level_range = max(levels) - min(levels)

    if len(set(levels)) == 1:
        return DetectorResult(
            signal=0.0,
            fired=False,
            evidence={
                "session_count": len(ordered_sessions),
                "level_range": 0,
                "constant_capability": True,
            },
        )

    rho_result = spearmanr(indices, levels)
    rho = float(rho_result.statistic) if rho_result.statistic is not None else 0.0
    slope = float(np.polyfit(indices, levels, 1)[0]) if len(levels) >= 2 else 0.0

    if len(levels) < 2:
        nondec_frac = 1.0
    else:
        nondecreasing = sum(1 for i in range(len(levels) - 1) if levels[i + 1] >= levels[i])
        nondec_frac = nondecreasing / (len(levels) - 1)

    fired = (
        rho >= config.escalation_min_rho
        and level_range >= config.escalation_min_level_range
        and nondec_frac >= config.escalation_min_nondec_frac
        and len(ordered_sessions) >= config.escalation_min_sessions
    )

    raw_signal = (
        0.5 * clamp(rho) + 0.3 * (level_range / 4.0) + 0.2 * nondec_frac
    )
    signal = gated_signal(raw_signal, fired)

    series = [
        {
            "session_id": str(item[1]),
            "started_at": item[0].isoformat(),
            "capability_level": item[2],
        }
        for item in ordered_sessions
    ]
    contributing_ids: list[str] = []
    for _, sid, _ in ordered_sessions:
        contributing_ids.extend(per_session_ids.get(sid, []))

    evidence: dict[str, Any] = {
        "session_series": series,
        "rho": round(rho, 4),
        "slope": round(slope, 4),
        "level_range": level_range,
        "nondec_frac": round(nondec_frac, 4),
        "contributing_interaction_ids": contributing_ids,
    }

    if len(ordered_sessions) >= config.escalation_mk_min_sessions:
        mk_tau, mk_p = kendalltau(indices, levels)
        evidence["mann_kendall_tau"] = round(float(mk_tau), 4) if mk_tau is not None else None
        evidence["mann_kendall_p"] = round(float(mk_p), 4) if mk_p is not None else None

    if fired:
        logger.info(
            "escalation_detector_fired",
            extra={"component": "escalation", "event": "fired", "rho": rho},
        )

    return DetectorResult(signal=signal, fired=fired, evidence=evidence)
