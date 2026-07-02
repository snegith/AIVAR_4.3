"""Synthetic window builders for detector unit tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import numpy as np

from app.detectors.base import DetectionWindow, WindowInteraction, WindowSession
from app.detectors.normalize import normalize_and_sign


def vector_with_target_similarity(
    base: np.ndarray,
    target_sim: float,
    seed: int,
) -> list[float]:
    """Build a unit vector with cosine similarity target_sim to base."""
    rng = np.random.default_rng(seed)
    orthogonal = rng.standard_normal(len(base))
    orthogonal = orthogonal - np.dot(orthogonal, base) * base
    orth_norm = np.linalg.norm(orthogonal)
    if orth_norm == 0:
        orthogonal = rng.standard_normal(len(base))
        orthogonal = orthogonal - np.dot(orthogonal, base) * base
        orth_norm = np.linalg.norm(orthogonal)
    orthogonal = orthogonal / orth_norm
    mixed = target_sim * base + np.sqrt(max(0.0, 1.0 - target_sim**2)) * orthogonal
    mixed = mixed / np.linalg.norm(mixed)
    return mixed.tolist()


def unit_vector(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    vec = rng.standard_normal(384)
    return vec / np.linalg.norm(vec)


def make_cluster_embeddings(
    base: np.ndarray,
    count: int,
    *,
    seed: int = 100,
) -> list[list[float]]:
    """Build embeddings in a tight cone: DBSCAN cluster with mean_sim ~0.78-0.97."""
    rng = np.random.default_rng(seed)
    orthogonal = rng.standard_normal(len(base))
    orthogonal = orthogonal - np.dot(orthogonal, base) * base
    orthogonal = orthogonal / np.linalg.norm(orthogonal)

    vectors: list[list[float]] = []
    for idx in range(count):
        theta = 0.45 + (idx / max(count - 1, 1)) * 0.6
        mixed = np.cos(theta) * base + np.sin(theta) * orthogonal
        vectors.append((mixed / np.linalg.norm(mixed)).tolist())
    return vectors


def make_probing_attack_window(
    *,
    blocked_count: int = 20,
    allowed_count: int = 0,
    target_sim: float = 0.82,
) -> DetectionWindow:
    """Synthetic boundary prober with paraphrased blocked prompts."""
    now = datetime.now(UTC)
    base = unit_vector(42)
    interactions: list[WindowInteraction] = []
    sessions: list[WindowSession] = []

    cluster_embeddings = make_cluster_embeddings(base, blocked_count)

    for idx in range(blocked_count):
        session_id = uuid.uuid4()
        sessions.append(
            WindowSession(id=session_id, user_id="prober", started_at=now - timedelta(hours=idx))
        )
        interactions.append(
            WindowInteraction(
                id=uuid.uuid4(),
                session_id=session_id,
                user_id="prober",
                ts=now - timedelta(hours=idx),
                prompt=f"blocked probe variant {idx}",
                guardrail_outcome="blocked",
                embedding=cluster_embeddings[idx],
            )
        )

    for idx in range(allowed_count):
        session_id = uuid.uuid4()
        sessions.append(
            WindowSession(
                id=session_id,
                user_id="prober",
                started_at=now - timedelta(hours=blocked_count + idx),
            )
        )
        interactions.append(
            WindowInteraction(
                id=uuid.uuid4(),
                session_id=session_id,
                user_id="prober",
                ts=now - timedelta(hours=blocked_count + idx),
                prompt=f"benign allowed {idx}",
                guardrail_outcome="allowed",
                embedding=vector_with_target_similarity(base, 0.2, seed=500 + idx),
            )
        )

    return DetectionWindow(
        interactions=interactions,
        sessions=sessions,
        window_start=now - timedelta(days=7),
        window_end=now,
    )


def make_probing_benign_window() -> DetectionWindow:
    """High-volume user with diverse allowed prompts and low block rate."""
    now = datetime.now(UTC)
    interactions: list[WindowInteraction] = []
    sessions: list[WindowSession] = []
    for idx in range(30):
        session_id = uuid.uuid4()
        sessions.append(
            WindowSession(id=session_id, user_id="normal", started_at=now - timedelta(hours=idx))
        )
        seed = 1000 + idx * 17
        interactions.append(
            WindowInteraction(
                id=uuid.uuid4(),
                session_id=session_id,
                user_id="normal",
                ts=now - timedelta(hours=idx),
                prompt=f"legitimate question topic {idx}",
                guardrail_outcome="allowed",
                embedding=unit_vector(seed).tolist(),
            )
        )
    return DetectionWindow(
        interactions=interactions,
        sessions=sessions,
        window_start=now - timedelta(days=7),
        window_end=now,
    )


def make_escalation_attack_window(session_count: int = 10) -> DetectionWindow:
    """Monotonic capability climb across sessions (Persona D)."""
    now = datetime.now(UTC)
    interactions: list[WindowInteraction] = []
    sessions: list[WindowSession] = []
    for idx in range(session_count):
        session_id = uuid.uuid4()
        level = min(4, idx // 2)
        started = now - timedelta(days=session_count - idx)
        sessions.append(WindowSession(id=session_id, user_id="escalator", started_at=started))
        interactions.append(
            WindowInteraction(
                id=uuid.uuid4(),
                session_id=session_id,
                user_id="escalator",
                ts=started,
                prompt=f"escalation step {idx}",
                guardrail_outcome="allowed",
                capability_level=level,
            )
        )
    return DetectionWindow(
        interactions=interactions,
        sessions=sessions,
        window_start=now - timedelta(days=session_count + 1),
        window_end=now,
    )


def make_escalation_benign_window() -> DetectionWindow:
    """Sessions without monotonic capability trend."""
    now = datetime.now(UTC)
    interactions: list[WindowInteraction] = []
    sessions: list[WindowSession] = []
    levels = [0, 1, 0, 1, 2, 1, 0, 2]
    for idx, level in enumerate(levels):
        session_id = uuid.uuid4()
        started = now - timedelta(days=len(levels) - idx)
        sessions.append(WindowSession(id=session_id, user_id="normal", started_at=started))
        interactions.append(
            WindowInteraction(
                id=uuid.uuid4(),
                session_id=session_id,
                user_id="normal",
                ts=started,
                prompt=f"normal question {idx}",
                guardrail_outcome="allowed",
                capability_level=level,
            )
        )
    return DetectionWindow(
        interactions=interactions,
        sessions=sessions,
        window_start=now - timedelta(days=10),
        window_end=now,
    )


def make_enumeration_attack_window(group_size: int = 50) -> DetectionWindow:
    """Template-dominant ID sweep (Persona B)."""
    now = datetime.now(UTC)
    base = unit_vector(77)
    interactions: list[WindowInteraction] = []
    sessions: list[WindowSession] = []
    for idx in range(group_size):
        session_id = uuid.uuid4()
        customer_id = 1000 + idx
        prompt = f"full record for customer ID {customer_id}"
        signed = normalize_and_sign(prompt)
        sessions.append(
            WindowSession(id=session_id, user_id="scraper", started_at=now - timedelta(hours=idx))
        )
        interactions.append(
            WindowInteraction(
                id=uuid.uuid4(),
                session_id=session_id,
                user_id="scraper",
                ts=now - timedelta(hours=idx),
                prompt=prompt,
                guardrail_outcome="allowed",
                template_signature=signed.template_signature,
                numeric_tokens=signed.numeric_tokens,
                embedding=vector_with_target_similarity(base, 0.95, seed=200 + idx),
            )
        )
    return DetectionWindow(
        interactions=interactions,
        sessions=sessions,
        window_start=now - timedelta(days=7),
        window_end=now,
    )


def make_enumeration_benign_window() -> DetectionWindow:
    """Diverse templates — low dominance power user."""
    now = datetime.now(UTC)
    interactions: list[WindowInteraction] = []
    sessions: list[WindowSession] = []
    for idx in range(40):
        session_id = uuid.uuid4()
        prompt = f"question about topic {idx} with unique context {idx * 13}"
        signed = normalize_and_sign(prompt)
        sessions.append(
            WindowSession(id=session_id, user_id="power", started_at=now - timedelta(hours=idx))
        )
        interactions.append(
            WindowInteraction(
                id=uuid.uuid4(),
                session_id=session_id,
                user_id="power",
                ts=now - timedelta(hours=idx),
                prompt=prompt,
                guardrail_outcome="allowed",
                template_signature=signed.template_signature,
                numeric_tokens=signed.numeric_tokens,
                embedding=unit_vector(3000 + idx).tolist(),
            )
        )
    return DetectionWindow(
        interactions=interactions,
        sessions=sessions,
        window_start=now - timedelta(days=7),
        window_end=now,
    )
