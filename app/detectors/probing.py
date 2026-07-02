"""Guardrail boundary probing detector via blocked-prompt DBSCAN clustering."""

from __future__ import annotations

from typing import Any

import numpy as np
from sklearn.cluster import DBSCAN

from app.detectors.base import (
    DetectionWindow,
    DetectorConfig,
    DetectorResult,
    clamp,
    gated_signal,
    mean_pairwise_cosine_similarity,
)
from app.logging import get_logger

logger = get_logger(__name__)


def detect(window: DetectionWindow, cfg: DetectorConfig | None = None) -> DetectorResult:
    """Detect gradual boundary probing in a rolling interaction window."""
    config = cfg or DetectorConfig.from_settings()
    total = len(window.interactions)
    if total == 0:
        return DetectorResult(signal=0.0, fired=False, evidence={"reason": "empty_window"})

    blocked = [
        row
        for row in window.interactions
        if row.guardrail_outcome == "blocked" and row.embedding is not None
    ]
    block_rate = len(blocked) / total

    if len(blocked) < config.probing_dbscan_min_samples:
        signal = gated_signal(0.3 * block_rate, fired=False)
        return DetectorResult(
            signal=signal,
            fired=False,
            evidence={"block_rate": block_rate, "blocked_count": len(blocked)},
        )

    embeddings = np.asarray([row.embedding for row in blocked], dtype=np.float64)
    clustering = DBSCAN(
        eps=config.probing_dbscan_eps,
        min_samples=config.probing_dbscan_min_samples,
        metric="cosine",
    ).fit(embeddings)
    labels = clustering.labels_

    cluster_members: dict[int, list[int]] = {}
    for idx, label in enumerate(labels):
        if label == -1:
            continue
        cluster_members.setdefault(int(label), []).append(idx)

    if not cluster_members:
        signal = gated_signal(0.3 * block_rate, fired=False)
        return DetectorResult(
            signal=signal,
            fired=False,
            evidence={"block_rate": block_rate, "cluster_size": 0},
        )

    largest_label = max(cluster_members, key=lambda label: len(cluster_members[label]))
    member_indices = cluster_members[largest_label]
    cluster_rows = [blocked[i] for i in member_indices]
    cluster_vectors = [row.embedding for row in cluster_rows if row.embedding is not None]
    cluster_size = len(cluster_vectors)
    mean_sim = mean_pairwise_cosine_similarity(cluster_vectors)

    fired = (
        cluster_size >= config.probing_min_cluster_size
        and config.probing_min_mean_sim <= mean_sim <= config.probing_max_mean_sim
        and block_rate >= config.probing_min_block_rate
    )

    size_term = min(cluster_size / config.probing_cluster_saturation, 1.0)
    sim_term = clamp(
        (mean_sim - config.probing_min_mean_sim)
        / (config.probing_sim_term_high - config.probing_min_mean_sim)
    )
    raw_signal = 0.5 * size_term + 0.2 * sim_term + 0.3 * block_rate
    signal = gated_signal(raw_signal, fired)

    evidence: dict[str, Any] = {
        "cluster_size": cluster_size,
        "mean_sim": round(mean_sim, 4),
        "block_rate": round(block_rate, 4),
        "sample_prompts": [row.prompt for row in cluster_rows[:5]],
        "contributing_interaction_ids": [str(row.id) for row in cluster_rows],
    }

    if fired:
        logger.info(
            "probing_detector_fired",
            extra={"component": "probing", "event": "fired", "cluster_size": cluster_size},
        )

    return DetectorResult(signal=signal, fired=fired, evidence=evidence)
