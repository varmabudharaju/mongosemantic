from __future__ import annotations

import os
from collections.abc import Sequence

import httpx
import numpy as np

from mongosemantic.embeddings.provider import EmbeddingProvider
from mongosemantic.exceptions import ProviderError

_MODEL_MAP: dict[str, tuple[str, int]] = {
    "ollama-nomic": ("nomic-embed-text", 768),
}

class OllamaProvider(EmbeddingProvider):
    def __init__(self, key: str) -> None:
        if key not in _MODEL_MAP:
            raise ValueError(f"Unknown ollama key: {key}")
        self._ollama_model, self.dim = _MODEL_MAP[key]
        self.model_name = key
        self.host = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")

    def embed_batch(self, texts: Sequence[str]) -> np.ndarray:
        texts = list(texts)
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        try:
            resp = httpx.post(
                f"{self.host}/api/embed",
                json={"model": self._ollama_model, "input": texts},
                timeout=60.0,
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise ProviderError(f"Ollama call failed: {e}") from e
        vectors = resp.json().get("embeddings") or []
        raw = np.array(vectors, dtype=np.float32)
        return self._validate(raw, len(texts))
