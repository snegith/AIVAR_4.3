"""Alert feed endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.repositories import get_alert_by_id, list_alerts, update_alert_status
from app.dependencies import get_db
from app.exceptions import NotFoundError
from app.schemas.alerts import AlertItem, AlertListResponse, AlertStatusUpdateRequest

router = APIRouter(prefix="/v1/alerts", tags=["alerts"])


def _to_alert_item(row: object) -> AlertItem:
    from app.db.models import AlertRow

    assert isinstance(row, AlertRow)
    return AlertItem(
        id=row.id,
        user_id=row.user_id,
        created_at=row.created_at,
        risk_score_at_alert=float(row.risk_score_at_alert)
        if row.risk_score_at_alert is not None
        else None,
        threshold=float(row.threshold) if row.threshold is not None else None,
        dominant_pattern=row.dominant_pattern,
        pattern_breakdown=row.pattern_breakdown,
        summary=row.summary,
        contributing_pattern_ids=row.contributing_pattern_ids,
        status=row.status,
    )


@router.get("", response_model=AlertListResponse)
def list_alert_feed(
    db: Annotated[Session, Depends(get_db)],
    status: str | None = None,
    user_id: str | None = None,
    since: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AlertListResponse:
    """Return filtered alerts."""
    rows, total = list_alerts(
        db,
        status=status,
        user_id=user_id,
        since=since,
        limit=limit,
        offset=offset,
    )
    return AlertListResponse(items=[_to_alert_item(row) for row in rows], total=total)


@router.get("/{alert_id}", response_model=AlertItem)
def get_alert(
    alert_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
) -> AlertItem:
    """Return one alert with full pattern breakdown."""
    row = get_alert_by_id(db, alert_id)
    if row is None:
        raise NotFoundError(f"Unknown alert_id: {alert_id}")
    return _to_alert_item(row)


@router.patch("/{alert_id}", response_model=AlertItem)
def patch_alert_status(
    alert_id: uuid.UUID,
    body: AlertStatusUpdateRequest,
    db: Annotated[Session, Depends(get_db)],
) -> AlertItem:
    """Acknowledge or resolve an alert."""
    row = update_alert_status(db, alert_id, body.status)
    if row is None:
        raise NotFoundError(f"Unknown alert_id: {alert_id}")
    db.commit()
    return _to_alert_item(row)
