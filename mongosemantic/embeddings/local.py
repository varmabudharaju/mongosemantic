from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from mongosemantic.embeddings.provider import EmbeddingProvider

_MODEL_MAP: dict[str, tuple[str, int]] = {
    "local-fast": ("sentence-transformers/all-MiniLM-L6-v2", 384),
    "local-better": ("sentence-transformers/all-mpnet-base-v2", 768),
}


class LocalProvider(EmbeddingProvider):
    def __init__(self, key: str) -> None:
        if key not in _MODEL_MAP:
            raise ValueError(f"Unknown local model key: {key}")
        hf_name, dim = _MODEL_MAP[key]
        self.model_name = key
        self.dim = dim
        # Lazy import so unit tests that don't use the provider are fast
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(hf_name)

    def embed_batch(self, texts: Sequence[str]) -> np.ndarray:
        texts = list(texts)
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        raw = self._model.encode(
            texts, batch_size=32, convert_to_numpy=True, show_progress_bar=False
        )
        return self._validate(raw, len(texts))
