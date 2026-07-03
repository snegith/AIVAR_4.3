"""Systematic enumeration detector via template dominance and numeric regularity."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.detectors.base import (
    DetectionWindow,
    DetectorConfig,
    DetectorResult,
    WindowInteraction,
    gated_signal,
    is_arithmetic_progression,
    mean_pairwise_cosine_similarity,
    normalized_entropy,
)
from app.logging import get_logger

logger = get_logger(__name__)


def _extract_slot_values(numeric_tokens: dict[str, Any] | None) -> list[int]:
    if not numeric_tokens:
        return []
    if numeric_tokens.get("id_values"):
        return [int(v) for v in numeric_tokens["id_values"]]
    if numeric_tokens.get("numbers"):
        return [int(v) for v in numeric_tokens["numbers"]]
    return []


def detect(window: DetectionWindow, cfg: DetectorConfig | None = None) -> DetectorResult:
    """Detect template-dominant enumeration sweeps in a rolling window."""
    config = cfg or DetectorConfig.from_settings()
    window_total = len(window.interactions)
    if window_total == 0:
        return DetectorResult(signal=0.0, fired=False, evidence={"reason": "empty_window"})

    groups: dict[str, list[WindowInteraction]] = defaultdict(list)
    for row in window.interactions:
        if row.template_signature:
            groups[row.template_signature].append(row)

    if not groups:
        return DetectorResult(signal=0.0, fired=False, evidence={"group_count": 0})

    template_signature, group_rows = max(groups.items(), key=lambda item: len(item[1]))
    group_size = len(group_rows)
    dominance = group_size / window_total

    slot_values = [_extract_slot_values(row.numeric_tokens) for row in group_rows]
    flat_values = [value for values in slot_values for value in values]
    if not flat_values:
        coverage = 0.0
        regularity = 0.0
        arithmetic = False
    else:
        distinct = len(set(flat_values))
        coverage = distinct / (max(flat_values) - min(flat_values) + 1)
        ordered = sorted(flat_values)
        diffs = [ordered[i + 1] - ordered[i] for i in range(len(ordered) - 1)]
        regularity = 1.0 - normalized_entropy(diffs) if diffs else 1.0
        arithmetic = is_arithmetic_progression(flat_values)

    vectors = [row.embedding for row in group_rows if row.embedding is not None]
    mean_sim = mean_pairwise_cosine_similarity(vectors) if vectors else 0.0

    fired = (
        group_size >= config.enumeration_min_group_size
        and dominance >= config.enumeration_min_dominance
        and regularity >= config.enumeration_min_regularity
        and mean_sim >= config.enumeration_min_mean_sim
        and (coverage >= config.enumeration_min_coverage or arithmetic)
    )

    size_term = min(group_size / config.enumeration_group_saturation, 1.0)
    raw_signal = 0.4 * size_term + 0.3 * regularity + 0.3 * dominance
    signal = gated_signal(raw_signal, fired)

    evidence: dict[str, Any] = {
        "template_signature": template_signature,
        "group_size": group_size,
        "dominance": round(dominance, 4),
        "regularity": round(regularity, 4),
        "coverage": round(coverage, 4),
        "mean_sim_in_G": round(mean_sim, 4),
        "value_range": [min(flat_values), max(flat_values)] if flat_values else None,
        "arithmetic_progression": arithmetic,
        "contributing_interaction_ids": [str(row.id) for row in group_rows],
    }

    if fired:
        logger.info(
            "enumeration_detector_fired",
            extra={
                "component": "enumeration",
                "event": "fired",
                "group_size": group_size,
            },
        )

    return DetectorResult(signal=signal, fired=fired, evidence=evidence)
