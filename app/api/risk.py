"""User risk profile read endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.repositories import get_risk_profile
from app.dependencies import get_db
from app.exceptions import NotFoundError
from app.schemas.risk import RiskProfileResponse, RiskSignals

router = APIRouter(prefix="/v1/users", tags=["risk"])


@router.get("/{user_id}/risk", response_model=RiskProfileResponse)
def get_user_risk(
    user_id: str,
    db: Annotated[Session, Depends(get_db)],
) -> RiskProfileResponse:
    """Return the persisted risk profile for a user."""
    profile = get_risk_profile(db, user_id)
    if profile is None:
        raise NotFoundError(f"Unknown user_id: {user_id}")

    return RiskProfileResponse(
        user_id=user_id,
        risk_score=float(profile.risk_score),
        status=profile.status,
        signals=RiskSignals(
            probing=float(profile.signal_probing or 0),
            escalation=float(profile.signal_escalation or 0),
            enumeration=float(profile.signal_enumeration or 0),
        ),
        session_count=int(profile.session_count or 0),
        interaction_count=int(profile.interaction_count or 0),
        last_event_at=profile.last_event_at,
        last_scored_at=profile.last_scored_at,
    )
