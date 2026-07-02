"""Detected patterns and threat card endpoints."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.repositories import get_risk_profile, list_detected_patterns
from app.dependencies import get_db
from app.schemas.patterns import PatternItem, PatternsResponse, ThreatCard

router = APIRouter(prefix="/v1/users", tags=["patterns"])

PatternType = Literal["probing", "escalation", "enumeration"]


@router.get("/{user_id}/patterns", response_model=PatternsResponse)
def get_user_patterns(
    user_id: str,
    db: Annotated[Session, Depends(get_db)],
    type: Annotated[PatternType | None, Query(alias="type")] = None,
) -> PatternsResponse:
    """Return detected patterns and a threat card summary for a user."""
    profile = get_risk_profile(db, user_id)
    risk_score = float(profile.risk_score) if profile is not None else 0.0

    rows = list_detected_patterns(db, user_id, pattern_type=type)
    patterns = [
        PatternItem(
            pattern_type=row.pattern_type,
            signal_strength=float(row.signal_strength) if row.signal_strength is not None else None,
            window_start=row.window_start,
            window_end=row.window_end,
            evidence=row.evidence,
            contributing_interaction_ids=row.contributing_interaction_ids,
        )
        for row in rows
    ]

    dominant = None
    if patterns:
        dominant = max(patterns, key=lambda item: item.signal_strength or 0.0).pattern_type

    summary = "No cross-session adversarial patterns detected."
    if dominant == "probing":
        summary = "Repeated boundary probing detected across sessions"
    elif dominant == "escalation":
        summary = "Privilege escalation pattern detected across sessions"
    elif dominant == "enumeration":
        summary = "Systematic enumeration pattern detected across sessions"

    return PatternsResponse(
        user_id=user_id,
        threat_card=ThreatCard(
            dominant_technique=dominant,
            risk_score=risk_score,
            summary=summary,
        ),
        patterns=patterns,
    )
