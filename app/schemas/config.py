"""Pydantic schemas for config endpoints."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class ConfigResponse(BaseModel):
    """GET /v1/config response."""

    model_config = ConfigDict(extra="allow")

    thresholds: dict[str, Any]
    weights: dict[str, Any]
    windows: dict[str, Any]
    detectors: dict[str, Any]


class AdminConfigUpdateRequest(BaseModel):
    """PUT /v1/admin/config request body."""

    model_config = ConfigDict(extra="allow")


class SetLastEventAtRequest(BaseModel):
    """POST /v1/admin/users/{user_id}/set_last_event_at body."""

    ts: str
