"""Event ingestion route: LLM, guardrail, embed, persist, schedule detection."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Request, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.repositories import (
    create_interaction,
    get_risk_profile,
    resolve_or_create_session,
)
from app.dependencies import get_db, get_embedding_service, get_llm
from app.detection.orchestrator import run_detection_for_user
from app.detectors.capability import CapabilityTagger
from app.detectors.normalize import normalize_and_sign
from app.embeddings.service import EmbeddingService
from app.exceptions import LLMProviderError
from app.integrations.langfuse_mirror import MirrorPayload, mirror_interaction_to_langfuse
from app.llm.guardrail import GuardrailEvaluator
from app.llm.provider import LLMProvider
from app.logging import get_logger
from app.ratelimit import limiter
from app.schemas.events import EventCreateRequest, EventCreateResponse

logger = get_logger(__name__)

router = APIRouter(prefix="/v1", tags=["events"])


def _event_rate_limit() -> str:
    return get_settings().rate_limit_string


def _risk_snapshot(db: Session, user_id: str) -> tuple[float, str]:
    profile = get_risk_profile(db, user_id)
    if profile is None:
        return 0.0, "normal"
    return float(profile.risk_score), profile.status


@router.post("/events", status_code=status.HTTP_202_ACCEPTED, response_model=EventCreateResponse)
@limiter.limit(_event_rate_limit)
async def create_event(
    request: Request,
    body: EventCreateRequest,
    background_tasks: BackgroundTasks,
    db: Annotated[Session, Depends(get_db)],
    embedding_service: Annotated[EmbeddingService, Depends(get_embedding_service)],
    llm: Annotated[LLMProvider, Depends(get_llm)],
) -> EventCreateResponse:
    """Ingest one user interaction and schedule background detection."""
    now = datetime.now(UTC)
    session_uuid = uuid.UUID(body.session_id) if body.session_id else None
    session = resolve_or_create_session(
        db,
        user_id=body.user_id,
        session_id=session_uuid,
        now=now,
        session_metadata=body.client_meta,
    )

    try:
        llm_response = llm.complete(body.prompt)
    except Exception as exc:
        logger.exception(
            "llm_complete_failed",
            extra={"component": "events", "event": "llm_error", "user_id": body.user_id},
        )
        raise LLMProviderError("LLM provider failed", detail=str(exc)) from exc

    guardrail = GuardrailEvaluator().evaluate(
        body.prompt,
        llm_response.text,
        is_degraded=llm_response.is_degraded,
    )
    capability = CapabilityTagger(llm).tag(body.prompt)
    normalized = normalize_and_sign(body.prompt)
    embedding = embedding_service.encode(body.prompt)

    interaction = create_interaction(
        db,
        session_id=session.id,
        user_id=body.user_id,
        ts=now,
        prompt=body.prompt,
        guardrail_outcome=guardrail.outcome,
        embedding=embedding,
        normalized_prompt=normalized.normalized,
        response=llm_response.text,
        guardrail_reason=guardrail.reason,
        capability_level=capability.level,
        template_signature=normalized.template_signature,
        numeric_tokens=normalized.numeric_tokens,
        latency_ms=llm_response.latency_ms,
        model=llm_response.model,
        is_degraded=llm_response.is_degraded,
    )
    session.last_event_at = now
    session.interaction_count = (session.interaction_count or 0) + 1
    db.flush()

    trace_id = mirror_interaction_to_langfuse(
        MirrorPayload(
            interaction_id=interaction.id,
            session_id=session.id,
            user_id=body.user_id,
            prompt=body.prompt,
            response=llm_response.text,
            guardrail_outcome=guardrail.outcome,
            capability_level=capability.level,
            model=llm_response.model,
            is_degraded=llm_response.is_degraded,
        )
    )
    if trace_id is not None:
        interaction.langfuse_trace_id = trace_id

    db.commit()

    background_tasks.add_task(run_detection_for_user, body.user_id)

    risk_score, risk_status = _risk_snapshot(db, body.user_id)
    preview = llm_response.text[:160]

    logger.info(
        "event_ingested",
        extra={
            "component": "events",
            "event": "ingested",
            "user_id": body.user_id,
            "interaction_id": str(interaction.id),
            "guardrail_outcome": guardrail.outcome,
        },
    )

    return EventCreateResponse(
        interaction_id=interaction.id,
        session_id=session.id,
        guardrail_outcome=guardrail.outcome,
        response_preview=preview,
        risk_score=risk_score,
        status=risk_status,
        langfuse_trace_id=trace_id,
    )
