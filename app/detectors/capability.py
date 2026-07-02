"""Capability level tagger: rules-first classifier with optional LLM judge.

Levels 0..4 per escalation detector rubric. High-confidence rule hits are
returned immediately; ambiguous prompts defer to the LLM judge.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.config import get_settings
from app.llm.provider import LLMProvider
from app.logging import get_logger

logger = get_logger(__name__)

_LEVEL_PATTERNS: list[tuple[int, re.Pattern[str], float]] = [
    (
        4,
        re.compile(
            r"(?i)\b(execute\s+shell|run\s+arbitrary\s+code|exfiltrat|"
            r"bypass\s+(security|control)|dump\s+(all\s+)?records)\b"
        ),
        0.95,
    ),
    (
        3,
        re.compile(
            r"(?i)\b(admin|root\s+access|system\s+config|elevated\s+privilege|"
            r"sudo|superuser|modify\s+(the\s+)?system)\b"
        ),
        0.9,
    ),
    (
        2,
        re.compile(r"(?i)\b(write|generate|draft|create|compose)\b.*\b(email|code|script)\b"),
        0.85,
    ),
    (
        1,
        re.compile(r"(?i)\b(summarize|explain|what is|describe|list)\b"),
        0.85,
    ),
    (
        0,
        re.compile(r"(?i)\b(hello|hi|weather|thanks|thank you)\b"),
        0.9,
    ),
]


@dataclass(frozen=True)
class CapabilityResult:
    """Tagged capability level with provenance."""

    level: int
    confidence: float
    source: str


class CapabilityTagger:
    """Assign capability_level 0..4 using rules and an optional LLM judge."""

    def __init__(self, provider: LLMProvider | None = None) -> None:
        self._provider = provider
        self._threshold = get_settings().capability_judge_confidence_threshold

    def tag(self, prompt: str) -> CapabilityResult:
        """Return the capability level for a user prompt."""
        rule_result = self._classify_with_rules(prompt)
        if rule_result is not None and rule_result.confidence >= self._threshold:
            return rule_result

        if self._provider is None:
            if rule_result is not None:
                return rule_result
            return CapabilityResult(level=0, confidence=0.5, source="rules_default")

        level = self._provider.judge_capability(prompt)
        level = max(0, min(4, level))
        logger.info(
            "capability_judge_used",
            extra={"component": "capability", "event": "judge", "level": level},
        )
        return CapabilityResult(level=level, confidence=0.8, source="judge")

    def _classify_with_rules(self, prompt: str) -> CapabilityResult | None:
        best: CapabilityResult | None = None
        for level, pattern, confidence in _LEVEL_PATTERNS:
            if pattern.search(prompt):
                candidate = CapabilityResult(level=level, confidence=confidence, source="rules")
                if best is None or level > best.level:
                    best = candidate
        return best
