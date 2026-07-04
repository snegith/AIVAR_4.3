"""LLMProvider interface for target completions and capability judging.

Abstracts Groq (production) and a deterministic stub (dev/CI only) so ingestion
and detectors can swap backends without changing call sites.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class LLMResponse:
    """Result of a target-LLM completion call."""

    text: str
    model: str
    latency_ms: int
    is_degraded: bool = False


class LLMProvider(ABC):
    """Backend for adversarial target responses and capability judging."""

    @abstractmethod
    def complete(self, prompt: str) -> LLMResponse:
        """Generate a target-LLM response for the user prompt."""

    @abstractmethod
    def judge_capability(self, prompt: str) -> int:
        """Return capability level 0..4 for ambiguous prompts (LLM judge)."""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Model identifier stored on interaction rows."""
