"""slowapi rate limiter scaffold for per-user_id ingestion limits.

Full endpoint wiring happens in Phase 7; this module centralizes limiter
configuration so every component reads the same env-driven thresholds.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import get_settings


def _user_id_key(request: object) -> str:
    """Extract user_id from JSON body for per-user rate limiting."""
    # Phase 7 will attach this to POST /v1/events; for now fall back to IP.
    try:
        from starlette.requests import Request

        if isinstance(request, Request):
            # Body may not be parsed yet at key extraction time in all paths;
            # header fallback supports health-check traffic without a body.
            user_id = request.headers.get("X-User-Id")
            if user_id:
                return user_id
    except Exception:
        pass
    return get_remote_address(request)  # type: ignore[arg-type]


_settings = get_settings()

limiter = Limiter(
    key_func=_user_id_key,
    default_limits=[_settings.rate_limit_string],
    storage_uri="memory://",
)
