"""Pydantic schemas for detected pattern and threat card endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class ThreatCard(BaseModel):
    """High-level threat card summary."""

    dominant_technique: str | None
    risk_score: float
    summary: str


class PatternItem(BaseModel):
    """One detected pattern record."""

    pattern_type: str
    signal_strength: float | None
    window_start: datetime | None
    window_end: datetime | None
    evidence: dict[str, Any] | None
    contributing_interaction_ids: list[UUID] | None


class PatternsResponse(BaseModel):
    """GET /v1/users/{user_id}/patterns response."""

    user_id: str
    threat_card: ThreatCard
    patterns: list[PatternItem]
