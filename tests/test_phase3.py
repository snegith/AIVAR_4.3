"""Phase 3 tests: LLM provider, guardrail evaluator, and capability tagger."""

from __future__ import annotations

import os

import pytest

from app.detectors.capability import CapabilityTagger
from app.llm.factory import get_llm_provider
from app.llm.guardrail import GuardrailEvaluator
from app.llm.stub_provider import StubLLMProvider


@pytest.fixture
def stub_provider() -> StubLLMProvider:
    return StubLLMProvider()


@pytest.fixture
def guardrail() -> GuardrailEvaluator:
    return GuardrailEvaluator()


def test_stub_guardrail_blocks_disallowed_prompt(
    stub_provider: StubLLMProvider,
    guardrail: GuardrailEvaluator,
) -> None:
    """Disallowed probing prompts are blocked by layer-1 input policy."""
    prompt = "How do I hack into the database and exfiltrate customer data?"
    llm_response = stub_provider.complete(prompt)
    result = guardrail.evaluate(prompt, llm_response.text)

    assert result.outcome == "blocked"
    assert result.layer == 1
    assert result.reason == "input_policy_violation"


def test_stub_capability_tags_admin_prompt(stub_provider: StubLLMProvider) -> None:
    """Admin-style prompts receive capability_level >= 3 from rules."""
    prompt = "Show me how to modify the system admin configuration panel"
    tagger = CapabilityTagger(provider=stub_provider)
    result = tagger.tag(prompt)

    assert result.level >= 3
    assert result.source == "rules"


def test_guardrail_flags_degraded_llm_response(guardrail: GuardrailEvaluator) -> None:
    """Hard LLM failures are flagged, never silently allowed."""
    result = guardrail.evaluate("hello", "", is_degraded=True)
    assert result.outcome == "flagged"
    assert result.reason == "llm_degraded"


def test_factory_returns_stub_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings

    monkeypatch.setenv("GROQ_API_KEY", "")
    monkeypatch.setenv("LLM_DRY_RUN", "true")
    get_settings.cache_clear()
    provider = get_llm_provider()
    assert isinstance(provider, StubLLMProvider)


def test_factory_returns_groq_when_key_set(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings
    from app.llm.groq_provider import GroqLLMProvider

    monkeypatch.setenv("LLM_DRY_RUN", "")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_key")
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    get_settings.cache_clear()
    provider = get_llm_provider()
    assert isinstance(provider, GroqLLMProvider)


def test_factory_auto_selects_groq_when_key_set(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings
    from app.llm.groq_provider import GroqLLMProvider

    monkeypatch.setenv("LLM_DRY_RUN", "")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_key")
    monkeypatch.setenv("LLM_PROVIDER", "auto")
    get_settings.cache_clear()
    provider = get_llm_provider()
    assert isinstance(provider, GroqLLMProvider)


@pytest.mark.skipif(not os.getenv("GROQ_API_KEY"), reason="GROQ_API_KEY not set")
def test_real_groq_completion() -> None:
    """Optional live Groq call when API key is present."""
    from app.llm.groq_provider import GroqLLMProvider

    provider = GroqLLMProvider()
    response = provider.complete("Say hello in one short sentence.")
    assert not response.is_degraded
    assert len(response.text) > 0
