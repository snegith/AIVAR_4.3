"""Shared detector types, config, and math helpers for pure detect() functions."""

from __future__ import annotations

import math
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np

from app.config import Settings, get_settings


@dataclass(frozen=True)
class WindowInteraction:
    """Minimal interaction view passed into detectors (no ORM dependency)."""

    id: uuid.UUID
    session_id: uuid.UUID
    user_id: str
    ts: datetime
    prompt: str
    guardrail_outcome: str
    embedding: list[float] | None = None
    template_signature: str | None = None
    numeric_tokens: dict[str, Any] | None = None
    capability_level: int | None = None
    langfuse_trace_id: str | None = None


@dataclass(frozen=True)
class WindowSession:
    """Minimal session view for escalation session ordering."""

    id: uuid.UUID
    user_id: str
    started_at: datetime


@dataclass(frozen=True)
class DetectionWindow:
    """Rolling per-user window consumed by all detectors."""

    interactions: list[WindowInteraction]
    sessions: list[WindowSession]
    window_start: datetime
    window_end: datetime


@dataclass(frozen=True)
class DetectorResult:
    """Output of a single detector cycle."""

    signal: float
    fired: bool
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DetectorConfig:
    """Config-driven detector thresholds (from Settings)."""

    probing_dbscan_eps: float = 0.25
    probing_dbscan_min_samples: int = 4
    probing_min_cluster_size: int = 5
    probing_min_mean_sim: float = 0.75
    probing_max_mean_sim: float = 0.985
    probing_min_block_rate: float = 0.6
    probing_sim_term_high: float = 0.97
    probing_cluster_saturation: int = 20

    escalation_min_rho: float = 0.6
    escalation_min_level_range: int = 2
    escalation_min_nondec_frac: float = 0.7
    escalation_min_sessions: int = 5
    escalation_mk_min_sessions: int = 8

    enumeration_min_group_size: int = 20
    enumeration_min_dominance: float = 0.4
    enumeration_min_regularity: float = 0.7
    enumeration_min_mean_sim: float = 0.9
    enumeration_min_coverage: float = 0.6
    enumeration_group_saturation: int = 50

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> DetectorConfig:
        """Build detector config from application settings."""
        s = settings or get_settings()
        return cls(
            probing_dbscan_eps=s.probing_dbscan_eps,
            probing_dbscan_min_samples=s.probing_dbscan_min_samples,
            probing_min_cluster_size=s.probing_min_cluster_size,
            probing_min_mean_sim=s.probing_min_mean_sim,
            probing_max_mean_sim=s.probing_max_mean_sim,
            probing_min_block_rate=s.probing_min_block_rate,
            probing_sim_term_high=s.probing_sim_term_high,
            probing_cluster_saturation=s.probing_cluster_saturation,
            escalation_min_rho=s.escalation_min_rho,
            escalation_min_level_range=s.escalation_min_level_range,
            escalation_min_nondec_frac=s.escalation_min_nondec_frac,
            escalation_min_sessions=s.escalation_min_sessions,
            escalation_mk_min_sessions=s.escalation_mk_min_sessions,
            enumeration_min_group_size=s.enumeration_min_group_size,
            enumeration_min_dominance=s.enumeration_min_dominance,
            enumeration_min_regularity=s.enumeration_min_regularity,
            enumeration_min_mean_sim=s.enumeration_min_mean_sim,
            enumeration_min_coverage=s.enumeration_min_coverage,
            enumeration_group_saturation=s.enumeration_group_saturation,
        )


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    """Clamp a float to [low, high]."""
    return max(low, min(high, value))


def mean_pairwise_cosine_similarity(vectors: list[list[float]]) -> float:
    """Mean cosine similarity across all pairs in a vector set."""
    if len(vectors) < 2:
        return 1.0 if len(vectors) == 1 else 0.0
    matrix = np.asarray(vectors, dtype=np.float64)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    normed = matrix / norms
    sim_matrix = normed @ normed.T
    n = len(vectors)
    total = 0.0
    pairs = 0
    for i in range(n):
        for j in range(i + 1, n):
            total += float(sim_matrix[i, j])
            pairs += 1
    return total / pairs if pairs else 0.0


def normalized_entropy(values: list[int]) -> float:
    """Normalized Shannon entropy in [0, 1] for a list of integers."""
    if not values:
        return 0.0
    counts = Counter(values)
    total = len(values)
    entropy = -sum((count / total) * math.log2(count / total) for count in counts.values())
    max_entropy = math.log2(len(counts)) if len(counts) > 1 else 1.0
    if max_entropy == 0.0:
        return 0.0
    return entropy / max_entropy


def is_arithmetic_progression(values: list[int]) -> bool:
    """True when sorted values have identical first differences."""
    if len(values) < 2:
        return False
    ordered = sorted(values)
    step = ordered[1] - ordered[0]
    return all(ordered[i + 1] - ordered[i] == step for i in range(len(ordered) - 1))


def gated_signal(raw: float, fired: bool) -> float:
    """Apply the standard 0.3 dampener when a detector does not fire."""
    return raw if fired else raw * 0.3
