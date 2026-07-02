"""Runtime-tunable configuration overrides for admin endpoints."""

from __future__ import annotations

from threading import Lock
from typing import Any

from app.config import Settings, get_settings

_PUBLIC_CONFIG_FIELDS = (
    "window_sessions",
    "window_days",
    "inactivity_reset_seconds",
    "alert_threshold",
    "watch_threshold",
    "risk_half_life_seconds",
    "risk_alpha",
    "weight_probing",
    "weight_escalation",
    "weight_enumeration",
    "probing_dbscan_eps",
    "probing_min_cluster_size",
    "probing_min_block_rate",
    "escalation_min_rho",
    "escalation_min_sessions",
    "enumeration_min_group_size",
    "enumeration_min_dominance",
)


class RuntimeConfigStore:
    """Thread-safe in-memory overrides merged over env defaults."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._overrides: dict[str, Any] = {}

    def public_snapshot(self) -> dict[str, Any]:
        """Return effective read-only config for GET /v1/config."""
        settings = self.effective_settings()
        return {field: getattr(settings, field) for field in _PUBLIC_CONFIG_FIELDS}

    def effective_settings(self) -> Settings:
        """Build Settings with runtime overrides applied."""
        base = get_settings()
        if not self._overrides:
            return base
        return base.model_copy(update=self._overrides)

    def update(self, updates: dict[str, Any]) -> dict[str, Any]:
        """Apply admin updates and return the new public snapshot."""
        unknown = sorted(set(updates) - set(_PUBLIC_CONFIG_FIELDS))
        if unknown:
            raise ValueError(f"Unsupported config keys: {', '.join(unknown)}")
        with self._lock:
            self._overrides.update(updates)
            return self.public_snapshot()

    def reset(self) -> None:
        """Clear all runtime overrides."""
        with self._lock:
            self._overrides.clear()


runtime_config_store = RuntimeConfigStore()
