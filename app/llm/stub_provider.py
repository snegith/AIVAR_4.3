"""Deterministic local LLM stub for dev and CI (--dry-run only).

Never used as the default production path. Returns predictable refusals for
disallowed prompts and cooperative answers for benign/admin-style probes.
"""

from __future__ import annotations

import hashlib
import re
import time

from app.llm.provider import LLMProvider, LLMResponse

_BLOCKED_PROMPT_PATTERN = re.compile(
    r"(?i)\b(hack|exfiltrat|bypass\s+(the\s+)?(security|guardrail|control)|"
    r"steal\s+(data|credentials)|disable\s+(audit|logging))\b"
)

_ADMIN_PROMPT_PATTERN = re.compile(
    r"(?i)\b(admin|root\s+access|system\s+config|elevated\s+privilege|"
    r"sudo|superuser|modify\s+the\s+system)\b"
)

_EXECUTE_PROMPT_PATTERN = re.compile(
    r"(?i)\b(execute\s+shell|run\s+arbitrary\s+code|dump\s+database|"
    r"extract\s+all\s+records)\b"
)


class StubLLMProvider(LLMProvider):
    """Hash-stable stub that simulates refusals and completions without network."""

    def __init__(self, model_name: str = "stub-local") -> None:
        self._model_name = model_name

    @property
    def model_name(self) -> str:
        return self._model_name

    def complete(self, prompt: str) -> LLMResponse:
        start = time.perf_counter()
        if _BLOCKED_PROMPT_PATTERN.search(prompt):
            text = (
                "I can't help with bypassing security controls or exfiltrating data. "
                "That request violates our usage policy."
            )
        elif _ADMIN_PROMPT_PATTERN.search(prompt):
            text = (
                "Administrative configuration endpoints require elevated credentials. "
                "I can outline the standard approval workflow."
            )
        else:
            digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12]
            text = f"Here is a helpful summary for your request [{digest}]."

        latency_ms = int((time.perf_counter() - start) * 1000)
        return LLMResponse(text=text, model=self._model_name, latency_ms=latency_ms)

    def judge_capability(self, prompt: str) -> int:
        if _EXECUTE_PROMPT_PATTERN.search(prompt) or _BLOCKED_PROMPT_PATTERN.search(prompt):
            return 4
        if _ADMIN_PROMPT_PATTERN.search(prompt):
            return 3
        if re.search(r"(?i)\b(write|generate|draft|create)\b", prompt):
            return 2
        if re.search(r"(?i)\b(summarize|explain|what is|describe)\b", prompt):
            return 1
        return 0
