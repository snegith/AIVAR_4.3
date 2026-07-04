"""Groq LLM provider for zero-cost production inference on the free tier.

Uses Groq's OpenAI-compatible chat API for target completions and capability
judging. Rate-limit retries are essential on the free tier (~30 RPM).
"""

from __future__ import annotations

import re
import time

from groq import APIConnectionError, Groq, InternalServerError, RateLimitError
from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import get_settings
from app.llm.capability_prompt import CAPABILITY_JUDGE_PROMPT
from app.llm.provider import LLMProvider, LLMResponse
from app.logging import get_logger

logger = get_logger(__name__)

_RETRYABLE = (
    APIConnectionError,
    RateLimitError,
    InternalServerError,
)


class GroqLLMProvider(LLMProvider):
    """Groq client for target completions and capability judging."""

    def __init__(self, api_key: str | None = None, model_name: str | None = None) -> None:
        settings = get_settings()
        key = api_key or settings.groq_api_key
        if not key:
            raise ValueError("GROQ_API_KEY is required for GroqLLMProvider")
        self._model_name = model_name or settings.groq_model
        self._timeout = settings.llm_timeout_seconds
        self._client = Groq(api_key=key, timeout=self._timeout)

    @property
    def model_name(self) -> str:
        return self._model_name

    def complete(self, prompt: str) -> LLMResponse:
        start = time.perf_counter()
        try:
            text = self._create_message(prompt)
            latency_ms = int((time.perf_counter() - start) * 1000)
            return LLMResponse(text=text, model=self._model_name, latency_ms=latency_ms)
        except Exception as exc:
            latency_ms = int((time.perf_counter() - start) * 1000)
            logger.error(
                "llm_completion_failed",
                extra={"component": "llm", "event": "completion_error", "provider": "groq"},
                exc_info=exc,
            )
            return LLMResponse(
                text="",
                model=self._model_name,
                latency_ms=latency_ms,
                is_degraded=True,
            )

    def judge_capability(self, prompt: str) -> int:
        judge_prompt = CAPABILITY_JUDGE_PROMPT.format(prompt=prompt)
        raw = self._create_message(judge_prompt, max_tokens=8)
        match = re.search(r"[0-4]", raw)
        if not match:
            logger.warning(
                "capability_judge_parse_failed",
                extra={"component": "llm", "event": "judge_parse_failed", "provider": "groq"},
            )
            return 0
        return int(match.group(0))

    def _create_message(self, prompt: str, max_tokens: int = 1024) -> str:
        settings = get_settings()
        for attempt in Retrying(
            retry=retry_if_exception_type(_RETRYABLE),
            stop=stop_after_attempt(settings.llm_max_retries),
            wait=wait_exponential(multiplier=2, min=2, max=30),
            reraise=True,
        ):
            with attempt:
                response = self._client.chat.completions.create(
                    model=self._model_name,
                    max_tokens=max_tokens,
                    messages=[{"role": "user", "content": prompt}],
                )
                content = response.choices[0].message.content
                return (content or "").strip()
        raise RuntimeError("unreachable")
