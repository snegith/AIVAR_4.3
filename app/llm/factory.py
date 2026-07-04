"""Factory for selecting the configured LLM provider implementation."""

from __future__ import annotations

import os

from app.config import get_settings
from app.llm.groq_provider import GroqLLMProvider
from app.llm.provider import LLMProvider
from app.llm.stub_provider import StubLLMProvider


def get_llm_provider(*, dry_run: bool | None = None) -> LLMProvider:
    """Return the configured LLM backend.

    Priority when ``LLM_PROVIDER=auto`` (default):
      1. Stub when ``LLM_DRY_RUN`` is set (CI / simulate --dry-run).
      2. Groq when ``GROQ_API_KEY`` is present (free-tier friendly).
      3. Stub fallback when no real provider key is configured.

    Explicit ``LLM_PROVIDER=groq|stub`` overrides auto selection.
    """
    settings = get_settings()
    use_stub = dry_run if dry_run is not None else os.getenv("LLM_DRY_RUN", "").lower() in {
        "1",
        "true",
        "yes",
    }
    if use_stub:
        return StubLLMProvider(model_name="stub-local")

    provider = settings.llm_provider.strip().lower()

    if provider == "stub":
        return StubLLMProvider(model_name="stub-local")

    if provider == "groq":
        if settings.groq_api_key:
            return GroqLLMProvider()
        return StubLLMProvider(model_name="stub-local")

    # auto: prefer Groq (free tier), then stub
    if settings.groq_api_key:
        return GroqLLMProvider()
    return StubLLMProvider(model_name="stub-local")
