"""Centralized application settings via Pydantic Settings.

All configurable values (thresholds, windows, secrets, rate limits) are loaded
from environment variables with documented defaults. No hardcoded secrets.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the adversarial pattern detector."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Application
    app_env: str = Field(default="development", alias="APP_ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # Database
    database_url: str = Field(
        default="postgresql://detector:detector@localhost:5433/detector_db",
        alias="DATABASE_URL",
    )

    # LLM
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    llm_model: str = Field(default="claude-3-5-haiku-latest", alias="LLM_MODEL")
    llm_timeout_seconds: int = Field(default=30, alias="LLM_TIMEOUT_SECONDS")

    # Admin
    admin_key: str = Field(default="change-me-in-production", alias="ADMIN_KEY")

    # Langfuse v2 mirror (optional)
    langfuse_enabled: bool = Field(default=False, alias="LANGFUSE_ENABLED")
    langfuse_host: str = Field(default="http://localhost:3000", alias="LANGFUSE_HOST")
    langfuse_public_key: str | None = Field(default=None, alias="LANGFUSE_PUBLIC_KEY")
    langfuse_secret_key: str | None = Field(default=None, alias="LANGFUSE_SECRET_KEY")

    # Detection windows
    window_sessions: int = Field(default=30, alias="WINDOW_SESSIONS")
    window_days: int = Field(default=7, alias="WINDOW_DAYS")
    inactivity_reset_seconds: int = Field(default=604800, alias="INACTIVITY_RESET_SECONDS")

    # Risk scoring
    alert_threshold: float = Field(default=70.0, alias="ALERT_THRESHOLD")
    watch_threshold: float = Field(default=45.0, alias="WATCH_THRESHOLD")
    risk_half_life_seconds: float = Field(default=86400.0, alias="RISK_HALF_LIFE_SECONDS")
    risk_alpha: float = Field(default=0.6, alias="RISK_ALPHA")
    weight_probing: float = Field(default=0.35, alias="WEIGHT_PROBING")
    weight_escalation: float = Field(default=0.35, alias="WEIGHT_ESCALATION")
    weight_enumeration: float = Field(default=0.30, alias="WEIGHT_ENUMERATION")

    # Rate limiting (POST /v1/events per user_id)
    rate_limit_requests: int = Field(default=100, alias="RATE_LIMIT_REQUESTS")
    rate_limit_window_seconds: int = Field(default=300, alias="RATE_LIMIT_WINDOW_SECONDS")
    rate_limit_burst: int = Field(default=20, alias="RATE_LIMIT_BURST")

    @property
    def rate_limit_string(self) -> str:
        """slowapi limit string: N requests per window."""
        minutes = self.rate_limit_window_seconds // 60
        return f"{self.rate_limit_requests}/{minutes}minute"


@lru_cache
def get_settings() -> Settings:
    """Return cached settings singleton."""
    return Settings()
