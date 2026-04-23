from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

import numpy as np

from mongosemantic.config import MODEL_DIMS
from mongosemantic.exceptions import DimMismatchError


def l2_normalize(v: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(v, axis=-1, keepdims=True)
    # Avoid divide-by-zero on empty vectors
    safe = np.where(norm == 0, 1, norm)
    return v / safe


class EmbeddingProvider(ABC):
    model_name: str = ""
    dim: int = 0

    @abstractmethod
    def embed_batch(self, texts: Sequence[str]) -> np.ndarray:
        """Return array of shape (len(texts), dim). Must be L2-normalized."""

    def embed(self, text: str) -> np.ndarray:
        return self.embed_batch([text])[0]

    def _validate(self, matrix: np.ndarray, n_expected: int) -> np.ndarray:
        if matrix.shape != (n_expected, self.dim):
            raise DimMismatchError(
                f"Provider {self.model_name} returned {matrix.shape}, "
                f"expected ({n_expected}, {self.dim})"
            )
        return l2_normalize(matrix.astype(np.float32))


def get_provider(model_key: str) -> EmbeddingProvider:
    from mongosemantic.embeddings.local import LocalProvider
    from mongosemantic.embeddings.ollama import OllamaProvider
    from mongosemantic.embeddings.openai import OpenAIProvider

    if model_key in ("local-fast", "local-better"):
        return LocalProvider(model_key)
    if model_key in ("openai-small", "openai-large"):
        return OpenAIProvider(model_key)
    if model_key == "ollama-nomic":
        return OllamaProvider(model_key)
    raise ValueError(f"Unknown model key: {model_key}. Known: {list(MODEL_DIMS)}")
