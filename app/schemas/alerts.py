"""Pydantic schemas for alert feed endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class AlertItem(BaseModel):
    """Alert list/detail item."""

    id: UUID
    user_id: str
    created_at: datetime
    risk_score_at_alert: float | None
    threshold: float | None
    dominant_pattern: str | None
    pattern_breakdown: dict[str, Any] | None
    summary: str | None
    contributing_pattern_ids: list[UUID] | None
    status: str


class AlertListResponse(BaseModel):
    """GET /v1/alerts response."""

    items: list[AlertItem]
    total: int


class AlertStatusUpdateRequest(BaseModel):
    """PATCH /v1/alerts/{alert_id} request body."""

    status: str = Field(pattern="^(open|ack|resolved)$")
