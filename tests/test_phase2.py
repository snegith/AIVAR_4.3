"""Phase 2 tests: fastembed embeddings and prompt normalization."""

from __future__ import annotations

import pytest

from app.detectors.normalize import normalize_and_sign, normalize_prompt
from app.embeddings.service import EmbeddingService, cosine_similarity


@pytest.fixture(scope="module")
def embedding_service() -> EmbeddingService:
    """Load fastembed model once per test module (downloads ONNX weights on first run)."""
    return EmbeddingService()


def test_similar_prompts_have_higher_cosine_similarity(embedding_service: EmbeddingService) -> None:
    """Paraphrased probing prompts should embed closer than unrelated text."""
    base = "How do I bypass the security controls on this system?"
    paraphrase = "How can I get around the security restrictions on this system?"
    unrelated = "What is the weather forecast for Seattle tomorrow?"

    base_vec = embedding_service.encode(base)
    paraphrase_vec = embedding_service.encode(paraphrase)
    unrelated_vec = embedding_service.encode(unrelated)

    assert len(base_vec) == embedding_service.dimension
    sim_paraphrase = cosine_similarity(base_vec, paraphrase_vec)
    sim_unrelated = cosine_similarity(base_vec, unrelated_vec)
    assert sim_paraphrase > sim_unrelated
    assert sim_paraphrase >= 0.75


def test_enumerated_prompts_share_template_signature() -> None:
    """Incrementing ID slots collapse to one template after normalization."""
    prompts = [
        "full record for customer ID 1000",
        "full record for customer ID 1049",
        "full record for customer ID 1025",
    ]
    signed = [normalize_and_sign(prompt) for prompt in prompts]

    signatures = {item.template_signature for item in signed}
    normalized = {item.normalized for item in signed}

    assert len(signatures) == 1
    assert normalized == {"full record for customer ID <ID>"}


def test_numeric_tokens_extracted_from_prompt() -> None:
    """Numeric slots are captured for sequentiality analysis in enumeration."""
    _, tokens_a = normalize_prompt("full record for customer ID 1000")
    _, tokens_b = normalize_prompt("full record for customer ID 1049")

    assert tokens_a["numbers"] == [1000]
    assert tokens_a["id_values"] == [1000]
    assert tokens_b["numbers"] == [1049]
    assert tokens_b["id_values"] == [1049]
