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
    llm_max_retries: int = Field(default=3, alias="LLM_MAX_RETRIES")
    capability_judge_confidence_threshold: float = Field(
        default=0.75,
        alias="CAPABILITY_JUDGE_CONFIDENCE_THRESHOLD",
    )

    # Embeddings (fastembed ONNX bge-small)
    embedding_model: str = Field(
        default="BAAI/bge-small-en-v1.5",
        alias="EMBEDDING_MODEL",
    )

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

    # Probing detector
    probing_dbscan_eps: float = Field(default=0.25, alias="PROBING_DBSCAN_EPS")
    probing_dbscan_min_samples: int = Field(default=4, alias="PROBING_DBSCAN_MIN_SAMPLES")
    probing_min_cluster_size: int = Field(default=5, alias="PROBING_MIN_CLUSTER_SIZE")
    probing_min_mean_sim: float = Field(default=0.75, alias="PROBING_MIN_MEAN_SIM")
    probing_max_mean_sim: float = Field(default=0.985, alias="PROBING_MAX_MEAN_SIM")
    probing_min_block_rate: float = Field(default=0.6, alias="PROBING_MIN_BLOCK_RATE")
    probing_sim_term_high: float = Field(default=0.97, alias="PROBING_SIM_TERM_HIGH")
    probing_cluster_saturation: int = Field(default=20, alias="PROBING_CLUSTER_SATURATION")

    # Escalation detector
    escalation_min_rho: float = Field(default=0.6, alias="ESCALATION_MIN_RHO")
    escalation_min_level_range: int = Field(default=2, alias="ESCALATION_MIN_LEVEL_RANGE")
    escalation_min_nondec_frac: float = Field(default=0.7, alias="ESCALATION_MIN_NONDEC_FRAC")
    escalation_min_sessions: int = Field(default=5, alias="ESCALATION_MIN_SESSIONS")
    escalation_mk_min_sessions: int = Field(default=8, alias="ESCALATION_MK_MIN_SESSIONS")

    # Enumeration detector
    enumeration_min_group_size: int = Field(default=20, alias="ENUMERATION_MIN_GROUP_SIZE")
    enumeration_min_dominance: float = Field(default=0.4, alias="ENUMERATION_MIN_DOMINANCE")
    enumeration_min_regularity: float = Field(default=0.7, alias="ENUMERATION_MIN_REGULARITY")
    enumeration_min_mean_sim: float = Field(default=0.9, alias="ENUMERATION_MIN_MEAN_SIM")
    enumeration_min_coverage: float = Field(default=0.6, alias="ENUMERATION_MIN_COVERAGE")
    enumeration_group_saturation: int = Field(default=50, alias="ENUMERATION_GROUP_SATURATION")

    @property
    def rate_limit_string(self) -> str:
        """slowapi limit string: N requests per window."""
        minutes = self.rate_limit_window_seconds // 60
        return f"{self.rate_limit_requests}/{minutes}minute"


@lru_cache
def get_settings() -> Settings:
    """Return cached settings singleton."""
    return Settings()
