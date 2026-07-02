"""slowapi rate limiter scaffold for per-user_id ingestion limits.

Full endpoint wiring happens in Phase 7; this module centralizes limiter
configuration so every component reads the same env-driven thresholds.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import get_settings


def _user_id_key(request: object) -> str:
    """Extract user_id from request state for per-user rate limiting."""
    try:
        from starlette.requests import Request

        if isinstance(request, Request):
            user_id = getattr(request.state, "rate_limit_user_id", None)
            if isinstance(user_id, str) and user_id:
                return user_id
            header_user_id = request.headers.get("X-User-Id")
            if header_user_id:
                return header_user_id
    except Exception:
        pass
    return get_remote_address(request)  # type: ignore[arg-type]


_settings = get_settings()

limiter = Limiter(
    key_func=_user_id_key,
    default_limits=[_settings.rate_limit_string],
    storage_uri="memory://",
)
