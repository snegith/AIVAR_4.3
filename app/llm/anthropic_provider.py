"""Anthropic Claude provider for production target LLM and judge calls.

Wraps the Messages API with timeout and tenacity retry/backoff. Hard failures
surface as degraded responses for the guardrail layer (never fabricated text).
"""

from __future__ import annotations

import re
import time

import anthropic
from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import get_settings
from app.llm.provider import LLMProvider, LLMResponse
from app.logging import get_logger

logger = get_logger(__name__)

_CAPABILITY_JUDGE_PROMPT = """You are a strict capability classifier for an AI assistant.

Rate the USER REQUEST only on this scale (reply with a single integer 0-4):
0 = general chit-chat or harmless small talk
1 = read/summarize/explain existing information
2 = modify or generate actionable content
3 = elevated/admin/system-level operations or configuration
4 = execute code, exfiltrate data, or bypass security controls

User request:
{prompt}
"""

_RETRYABLE = (
    anthropic.APIConnectionError,
    anthropic.RateLimitError,
    anthropic.InternalServerError,
)


class AnthropicLLMProvider(LLMProvider):
    """Real Claude client for target completions and capability judging."""

    def __init__(self, api_key: str | None = None, model_name: str | None = None) -> None:
        settings = get_settings()
        key = api_key or settings.anthropic_api_key
        if not key:
            raise ValueError("ANTHROPIC_API_KEY is required for AnthropicLLMProvider")
        self._model_name = model_name or settings.llm_model
        self._timeout = settings.llm_timeout_seconds
        self._client = anthropic.Anthropic(api_key=key, timeout=self._timeout)

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
                extra={"component": "llm", "event": "completion_error"},
                exc_info=exc,
            )
            return LLMResponse(
                text="",
                model=self._model_name,
                latency_ms=latency_ms,
                is_degraded=True,
            )

    def judge_capability(self, prompt: str) -> int:
        judge_prompt = _CAPABILITY_JUDGE_PROMPT.format(prompt=prompt)
        raw = self._create_message(judge_prompt, max_tokens=8)
        match = re.search(r"[0-4]", raw)
        if not match:
            logger.warning(
                "capability_judge_parse_failed",
                extra={"component": "llm", "event": "judge_parse_failed"},
            )
            return 0
        return int(match.group(0))

    def _create_message(self, prompt: str, max_tokens: int = 1024) -> str:
        settings = get_settings()
        for attempt in Retrying(
            retry=retry_if_exception_type(_RETRYABLE),
            stop=stop_after_attempt(settings.llm_max_retries),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            reraise=True,
        ):
            with attempt:
                response = self._client.messages.create(
                    model=self._model_name,
                    max_tokens=max_tokens,
                    messages=[{"role": "user", "content": prompt}],
                )
                parts: list[str] = []
                for block in response.content:
                    if block.type == "text":
                        parts.append(block.text)
                return "".join(parts).strip()
        raise RuntimeError("unreachable")
