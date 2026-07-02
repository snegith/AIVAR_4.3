"""Admin and configuration endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.repositories import get_risk_profile, reset_user_risk_profile, set_user_last_event_at
from app.dependencies import get_db, get_effective_settings, get_runtime_config, require_admin
from app.detection.orchestrator import DetectionOrchestrator
from app.exceptions import NotFoundError
from app.runtime_config import RuntimeConfigStore
from app.schemas.config import AdminConfigUpdateRequest, ConfigResponse, SetLastEventAtRequest
from app.schemas.risk import RiskProfileResponse, RiskSignals

router = APIRouter(prefix="/v1", tags=["admin"])


def _profile_response(user_id: str, db: Session) -> RiskProfileResponse:
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


def _build_config_response(store: RuntimeConfigStore) -> ConfigResponse:
    snapshot = store.public_snapshot()
    return ConfigResponse(
        thresholds={
            "alert_threshold": snapshot["alert_threshold"],
            "watch_threshold": snapshot["watch_threshold"],
            "risk_half_life_seconds": snapshot["risk_half_life_seconds"],
            "risk_alpha": snapshot["risk_alpha"],
        },
        weights={
            "weight_probing": snapshot["weight_probing"],
            "weight_escalation": snapshot["weight_escalation"],
            "weight_enumeration": snapshot["weight_enumeration"],
        },
        windows={
            "window_sessions": snapshot["window_sessions"],
            "window_days": snapshot["window_days"],
            "inactivity_reset_seconds": snapshot["inactivity_reset_seconds"],
        },
        detectors={
            "probing_dbscan_eps": snapshot["probing_dbscan_eps"],
            "probing_min_cluster_size": snapshot["probing_min_cluster_size"],
            "probing_min_block_rate": snapshot["probing_min_block_rate"],
            "escalation_min_rho": snapshot["escalation_min_rho"],
            "escalation_min_sessions": snapshot["escalation_min_sessions"],
            "enumeration_min_group_size": snapshot["enumeration_min_group_size"],
            "enumeration_min_dominance": snapshot["enumeration_min_dominance"],
        },
    )


@router.get("/config", response_model=ConfigResponse)
def get_config(
    config_store: Annotated[RuntimeConfigStore, Depends(get_runtime_config)],
) -> ConfigResponse:
    """Return effective thresholds, weights, and windows."""
    return _build_config_response(config_store)


@router.put("/admin/config", response_model=ConfigResponse, dependencies=[Depends(require_admin)])
def put_admin_config(
    body: AdminConfigUpdateRequest,
    config_store: Annotated[RuntimeConfigStore, Depends(get_runtime_config)],
) -> ConfigResponse:
    """Update runtime-tunable configuration overrides."""
    updates: dict[str, Any] = body.model_dump(exclude_none=True)
    try:
        config_store.update(updates)
    except ValueError as exc:
        from app.exceptions import AppError

        raise AppError(
            code="validation_error",
            message=str(exc),
            status_code=400,
        ) from exc
    return _build_config_response(config_store)


@router.post(
    "/admin/recompute/{user_id}",
    response_model=RiskProfileResponse,
    dependencies=[Depends(require_admin)],
)
def admin_recompute(
    user_id: str,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[object, Depends(get_effective_settings)],
) -> RiskProfileResponse:
    """Force a synchronous detection and scoring cycle."""
    from app.config import Settings

    assert isinstance(settings, Settings)
    DetectionOrchestrator(settings).run(db, user_id)
    db.commit()
    return _profile_response(user_id, db)


@router.post(
    "/admin/reset/{user_id}",
    response_model=RiskProfileResponse,
    dependencies=[Depends(require_admin)],
)
def admin_reset(
    user_id: str,
    db: Annotated[Session, Depends(get_db)],
) -> RiskProfileResponse:
    """Manually reset a user's risk profile."""
    reset_user_risk_profile(db, user_id)
    db.commit()
    return _profile_response(user_id, db)


@router.post(
    "/admin/users/{user_id}/set_last_event_at",
    response_model=RiskProfileResponse,
    dependencies=[Depends(require_admin)],
)
def admin_set_last_event_at(
    user_id: str,
    body: SetLastEventAtRequest,
    db: Annotated[Session, Depends(get_db)],
) -> RiskProfileResponse:
    """Backdate last_event_at for inactivity-reset simulation tests."""
    ts = datetime.fromisoformat(body.ts.replace("Z", "+00:00"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    set_user_last_event_at(db, user_id, ts)
    db.commit()
    return _profile_response(user_id, db)
