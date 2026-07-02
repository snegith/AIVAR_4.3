"""Pydantic schemas for user risk profile endpoints."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class RiskSignals(BaseModel):
    """Latest detector sub-signals."""

    probing: float
    escalation: float
    enumeration: float


class RiskProfileResponse(BaseModel):
    """GET /v1/users/{user_id}/risk response."""

    user_id: str
    risk_score: float
    status: str
    signals: RiskSignals
    session_count: int
    interaction_count: int
    last_event_at: datetime | None
    last_scored_at: datetime | None
