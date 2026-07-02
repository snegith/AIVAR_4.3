"""Factory for selecting the configured LLM provider implementation."""

from __future__ import annotations

import os

from app.config import get_settings
from app.llm.anthropic_provider import AnthropicLLMProvider
from app.llm.provider import LLMProvider
from app.llm.stub_provider import StubLLMProvider


def get_llm_provider(*, dry_run: bool | None = None) -> LLMProvider:
    """Return stub provider for dry-run/CI; otherwise Anthropic when keyed."""
    settings = get_settings()
    use_stub = dry_run if dry_run is not None else os.getenv("LLM_DRY_RUN", "").lower() in {
        "1",
        "true",
        "yes",
    }
    if use_stub or not settings.anthropic_api_key:
        return StubLLMProvider(model_name="stub-local")
    return AnthropicLLMProvider()
