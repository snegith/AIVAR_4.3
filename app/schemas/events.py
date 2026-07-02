"""Pydantic schemas for event ingestion endpoints."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class EventCreateRequest(BaseModel):
    """POST /v1/events request body."""

    user_id: str = Field(min_length=1)
    session_id: str | None = None
    prompt: str = Field(min_length=1)
    client_meta: dict[str, Any] | None = None


class EventCreateResponse(BaseModel):
    """POST /v1/events 202 response."""

    interaction_id: UUID
    session_id: UUID
    guardrail_outcome: str
    response_preview: str
    risk_score: float
    status: str
    langfuse_trace_id: str | None = None
