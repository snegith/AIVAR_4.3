"""Optional Langfuse v2 trace mirror for ingestion events.

Mirrors each persisted interaction as a Langfuse trace + generation. Failures are
logged and swallowed so ingestion and detection never depend on Langfuse.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from app.config import Settings, get_settings
from app.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class MirrorPayload:
    """Fields required to mirror one interaction to Langfuse."""

    interaction_id: uuid.UUID
    session_id: uuid.UUID
    user_id: str
    prompt: str
    response: str
    guardrail_outcome: str
    capability_level: int | None
    model: str | None
    is_degraded: bool


class LangfuseMirror:
    """Send interaction traces to a self-hosted Langfuse v2 instance."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._client: Any | None = None

    def is_configured(self) -> bool:
        """True when mirroring is enabled and API keys are present."""
        settings = self._settings
        return (
            settings.langfuse_enabled
            and bool(settings.langfuse_public_key)
            and bool(settings.langfuse_secret_key)
        )

    def _client_instance(self) -> Any:
        if self._client is not None:
            return self._client
        from langfuse import Langfuse

        self._client = Langfuse(
            public_key=self._settings.langfuse_public_key,
            secret_key=self._settings.langfuse_secret_key,
            host=self._settings.langfuse_sdk_host,
        )
        return self._client

    def mirror_interaction(self, payload: MirrorPayload) -> str | None:
        """Mirror one interaction; return trace id or None on skip/failure."""
        if not self.is_configured():
            return None

        try:
            client = self._client_instance()
            trace = client.trace(
                id=str(payload.interaction_id),
                name="adversarial-detector-interaction",
                user_id=payload.user_id,
                session_id=str(payload.session_id),
                input={"prompt": payload.prompt},
                metadata={
                    "interaction_id": str(payload.interaction_id),
                    "guardrail_outcome": payload.guardrail_outcome,
                    "capability_level": payload.capability_level,
                    "is_degraded": payload.is_degraded,
                },
            )
            generation = trace.generation(
                name="llm-complete",
                model=payload.model or "unknown",
                input=payload.prompt,
                metadata={"guardrail_outcome": payload.guardrail_outcome},
            )
            generation.end(output=payload.response)
            trace.update(output={"response_preview": payload.response[:160]})
            client.flush()
            trace_id = str(trace.id)
            logger.info(
                "langfuse_mirror_success",
                extra={
                    "component": "langfuse_mirror",
                    "event": "mirrored",
                    "interaction_id": str(payload.interaction_id),
                    "trace_id": trace_id,
                },
            )
            return trace_id
        except Exception:
            logger.exception(
                "langfuse_mirror_failed",
                extra={
                    "component": "langfuse_mirror",
                    "event": "mirror_error",
                    "interaction_id": str(payload.interaction_id),
                },
            )
            return None


def mirror_interaction_to_langfuse(
    payload: MirrorPayload,
    settings: Settings | None = None,
) -> str | None:
    """Convenience wrapper used by the events route."""
    return LangfuseMirror(settings).mirror_interaction(payload)
