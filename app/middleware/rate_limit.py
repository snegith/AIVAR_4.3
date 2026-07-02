"""HTTP middleware for per-user_id rate-limit key extraction."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

from starlette.requests import Request
from starlette.responses import Response


async def user_id_rate_limit_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Parse POST /v1/events JSON body once and stash user_id for slowapi."""
    if request.method == "POST" and request.url.path == "/v1/events":
        body = await request.body()

        async def receive() -> dict[str, object]:
            return {"type": "http.request", "body": body, "more_body": False}

        request._receive = receive  # type: ignore[attr-defined]
        try:
            payload = json.loads(body.decode("utf-8"))
            user_id = payload.get("user_id")
            if isinstance(user_id, str) and user_id:
                request.state.rate_limit_user_id = user_id
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass

    return await call_next(request)
