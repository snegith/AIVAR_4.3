"""fastembed-based embedding service for 384-dim prompt vectors.

Loads BAAI/bge-small-en-v1.5 once at startup and encodes prompts deterministically
for probing/enumeration cosine similarity without per-call API cost.
"""

from __future__ import annotations

import numpy as np
from fastembed import TextEmbedding

from app.config import get_settings
from app.logging import get_logger

logger = get_logger(__name__)

EMBEDDING_DIMENSION = 384


def cosine_similarity(left: list[float], right: list[float]) -> float:
    """Return cosine similarity between two embedding vectors."""
    a = np.asarray(left, dtype=np.float64)
    b = np.asarray(right, dtype=np.float64)
    denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denominator == 0.0:
        return 0.0
    return float(np.dot(a, b) / denominator)


class EmbeddingService:
    """ONNX embedding encoder backed by fastembed."""

    def __init__(self, model_name: str | None = None) -> None:
        settings = get_settings()
        self._model_name = model_name or settings.embedding_model
        logger.info(
            "embedding_model_loading",
            extra={"component": "embeddings", "event": "model_load", "model": self._model_name},
        )
        self._model = TextEmbedding(model_name=self._model_name)
        logger.info(
            "embedding_model_ready",
            extra={"component": "embeddings", "event": "model_ready", "model": self._model_name},
        )

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> int:
        return EMBEDDING_DIMENSION

    def encode(self, text: str) -> list[float]:
        """Encode a single prompt into a 384-dimensional unit vector."""
        vectors = list(self._model.embed([text]))
        if not vectors:
            raise RuntimeError("fastembed returned no vectors for input text")
        embedding = vectors[0]
        return embedding.tolist() if hasattr(embedding, "tolist") else list(embedding)

    def encode_batch(self, texts: list[str]) -> list[list[float]]:
        """Encode multiple prompts in one ONNX batch."""
        if not texts:
            return []
        return [
            vector.tolist() if hasattr(vector, "tolist") else list(vector)
            for vector in self._model.embed(texts)
        ]
