"""HTTP session driver for paced cross-session simulation."""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from sim.personas import PersonaPlan


@dataclass(frozen=True)
class PersonaRunStats:
    """Runtime stats collected while driving a persona."""

    persona: str
    user_id: str
    sessions_sent: int
    requests_sent: int
    errors: int


def run_persona(
    client: httpx.Client,
    plan: PersonaPlan,
    *,
    request_delay_ms: int,
) -> PersonaRunStats:
    """Drive one persona plan against POST /v1/events."""
    session_id: str | None = None
    requests_sent = 0
    errors = 0

    for burst in plan.sessions:
        for prompt in burst:
            payload = {
                "user_id": plan.user_id,
                "session_id": session_id,
                "prompt": prompt,
            }
            response = client.post("/v1/events", json=payload)
            requests_sent += 1
            if response.status_code != 202:
                errors += 1
            else:
                session_id = response.json().get("session_id")
            if request_delay_ms > 0:
                time.sleep(request_delay_ms / 1000.0)
        session_id = None

    return PersonaRunStats(
        persona=plan.name,
        user_id=plan.user_id,
        sessions_sent=len(plan.sessions),
        requests_sent=requests_sent,
        errors=errors,
    )
