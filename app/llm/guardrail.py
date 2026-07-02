"""Three-layer guardrail evaluator: input rules, sensitivity flags, output checks.

Layers:
  1. Hard input deny patterns -> blocked (policy violation)
  2. Soft input warn patterns -> flagged for review
  3. Output/refusal analysis on LLM response -> blocked when model refuses or
     echoes harmful intent; flagged when LLM call degraded
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from app.logging import get_logger

logger = get_logger(__name__)

GuardrailOutcome = Literal["allowed", "blocked", "flagged"]

_LAYER1_BLOCK_PATTERNS = (
    re.compile(r"(?i)\b(hack|exfiltrat|steal\s+(data|credentials))\b"),
    re.compile(r"(?i)\bbypass\b.*\b(security|guardrail|control|policy)\b"),
    re.compile(r"(?i)\b(disable|turn off)\b.*\b(audit|logging|monitoring)\b"),
)

_LAYER2_FLAG_PATTERNS = (
    re.compile(r"(?i)\b(admin|root|sudo|privileged)\b"),
    re.compile(r"(?i)\b(customer id|account number|ssn)\b"),
)

_REFUSAL_PATTERNS = (
    re.compile(r"(?i)\b(i can'?t|i cannot|i won'?t|i will not)\b"),
    re.compile(r"(?i)\b(violates|against)\b.*\b(policy|guidelines)\b"),
    re.compile(r"(?i)\b(not able to help|unable to assist)\b"),
)


@dataclass(frozen=True)
class GuardrailResult:
    """Outcome of guardrail evaluation for one interaction."""

    outcome: GuardrailOutcome
    reason: str | None
    layer: int | None


class GuardrailEvaluator:
    """Evaluate prompts and LLM responses through three guardrail layers."""

    def evaluate(
        self,
        prompt: str,
        response: str | None,
        *,
        is_degraded: bool = False,
    ) -> GuardrailResult:
        """Return allowed, blocked, or flagged with the deciding layer."""
        if is_degraded:
            logger.info(
                "guardrail_degraded",
                extra={"component": "guardrail", "event": "degraded_flag"},
            )
            return GuardrailResult(
                outcome="flagged",
                reason="llm_degraded",
                layer=3,
            )

        layer1 = self._evaluate_input_blocks(prompt)
        if layer1 is not None:
            return layer1

        layer2 = self._evaluate_input_flags(prompt)
        if layer2 is not None:
            return layer2

        layer3 = self._evaluate_output(prompt, response or "")
        if layer3 is not None:
            return layer3

        return GuardrailResult(outcome="allowed", reason=None, layer=None)

    def _evaluate_input_blocks(self, prompt: str) -> GuardrailResult | None:
        for pattern in _LAYER1_BLOCK_PATTERNS:
            if pattern.search(prompt):
                return GuardrailResult(
                    outcome="blocked",
                    reason="input_policy_violation",
                    layer=1,
                )
        return None

    def _evaluate_input_flags(self, prompt: str) -> GuardrailResult | None:
        for pattern in _LAYER2_FLAG_PATTERNS:
            if pattern.search(prompt):
                return GuardrailResult(
                    outcome="flagged",
                    reason="sensitive_input",
                    layer=2,
                )
        return None

    def _evaluate_output(self, prompt: str, response: str) -> GuardrailResult | None:
        if not response.strip():
            return None

        for pattern in _REFUSAL_PATTERNS:
            if pattern.search(response):
                return GuardrailResult(
                    outcome="blocked",
                    reason="model_refusal",
                    layer=3,
                )

        # Harmful request echoed without refusal in output
        for pattern in _LAYER1_BLOCK_PATTERNS:
            if pattern.search(prompt) and not pattern.search(response):
                return None
            if pattern.search(prompt) and pattern.search(response):
                return GuardrailResult(
                    outcome="blocked",
                    reason="harmful_output",
                    layer=3,
                )

        return None
