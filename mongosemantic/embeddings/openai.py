from __future__ import annotations

import os
from collections.abc import Sequence

import numpy as np

from mongosemantic.embeddings.provider import EmbeddingProvider
from mongosemantic.exceptions import ProviderError

_MODEL_MAP: dict[str, tuple[str, int]] = {
    "openai-small": ("text-embedding-3-small", 1536),
    "openai-large": ("text-embedding-3-large", 3072),
}

def _make_client():
    try:
        from openai import OpenAI
    except ImportError as e:
        raise ProviderError(
            "openai provider requires `pip install mongosemantic[openai]`"
        ) from e
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ProviderError("OPENAI_API_KEY is not set")
    return OpenAI(api_key=api_key)

class OpenAIProvider(EmbeddingProvider):
    def __init__(self, key: str) -> None:
        if key not in _MODEL_MAP:
            raise ValueError(f"Unknown openai key: {key}")
        self._openai_model, self.dim = _MODEL_MAP[key]
        self.model_name = key
        self._client = _make_client()

    def embed_batch(self, texts: Sequence[str]) -> np.ndarray:
        texts = list(texts)
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        try:
            resp = self._client.embeddings.create(model=self._openai_model, input=texts)
        except Exception as e:
            raise ProviderError(f"OpenAI embedding call failed: {e}") from e
        raw = np.array([item.embedding for item in resp.data], dtype=np.float32)
        return self._validate(raw, len(texts))
