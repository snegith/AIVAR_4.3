"""Prompt normalization, template signatures, and numeric token extraction.

Masks digits, IDs, dates, and simple entities so enumeration prompts with
varying slots collapse to a shared template_signature for grouping.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

# ID-bearing phrases (customer ID 1234, id: 5678, etc.)
_ID_PATTERN = re.compile(
    r"(?i)\b((?:customer|account|order|user|record)\s+id|id)\s*[:#]?\s*(\d+)\b"
)

# ISO and US-style dates
_DATE_PATTERNS = (
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b"),
)

# Email addresses and multi-word proper names
_EMAIL_PATTERN = re.compile(r"\b[\w.+-]+@[\w.-]+\.\w+\b")
_ENTITY_PATTERN = re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b")

# Remaining standalone integers
_NUMBER_PATTERN = re.compile(r"\b\d+\b")


@dataclass(frozen=True)
class NormalizedPrompt:
    """Normalization output stored on interactions rows."""

    normalized: str
    template_signature: str
    numeric_tokens: dict[str, Any]


def extract_numeric_tokens(prompt: str) -> dict[str, Any]:
    """Extract integer tokens and ID-slot values from the raw prompt."""
    numbers = [int(match) for match in _NUMBER_PATTERN.findall(prompt)]
    id_values: list[int] = []
    for match in _ID_PATTERN.finditer(prompt):
        id_values.append(int(match.group(2)))
    return {
        "numbers": numbers,
        "id_values": id_values,
    }


def compute_template_signature(normalized_prompt: str) -> str:
    """Return sha1 hex digest of the normalized prompt template."""
    return hashlib.sha1(normalized_prompt.encode("utf-8")).hexdigest()


def normalize_prompt(prompt: str) -> tuple[str, dict[str, Any]]:
    """Mask variable slots and return (normalized_text, numeric_tokens)."""
    numeric_tokens = extract_numeric_tokens(prompt)
    text = prompt

    def _replace_id(match: re.Match[str]) -> str:
        label = match.group(1)
        return f"{label} <ID>"

    text = _ID_PATTERN.sub(_replace_id, text)
    for pattern in _DATE_PATTERNS:
        text = pattern.sub("<NUM>", text)
    text = _EMAIL_PATTERN.sub("<ENT>", text)
    text = _ENTITY_PATTERN.sub("<ENT>", text)
    text = _NUMBER_PATTERN.sub("<NUM>", text)

    return text, numeric_tokens


def normalize_and_sign(prompt: str) -> NormalizedPrompt:
    """Normalize a prompt and compute its template signature in one step."""
    normalized, numeric_tokens = normalize_prompt(prompt)
    return NormalizedPrompt(
        normalized=normalized,
        template_signature=compute_template_signature(normalized),
        numeric_tokens=numeric_tokens,
    )
