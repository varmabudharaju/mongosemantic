"""Local cross-encoder reranking.

Two-stage retrieval: callers over-fetch limit * RERANK_CANDIDATE_MULTIPLIER
candidates, then rerank(query, rows, limit) re-scores each (query, chunk_text)
pair with a local cross-encoder and returns the top `limit`.

The model (~80 MB, CPU-fast) loads lazily exactly once per process; a failed
load is remembered so a broken install degrades to vector-only search instead
of retrying the import on every request. Mirrors ProviderRegistry semantics
(worker/runner.py) without the per-model keying - there is one rerank model.
"""

from __future__ import annotations

import logging
import math
import threading
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_RERANK_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"
RERANK_CANDIDATE_MULTIPLIER = 5

_lock = threading.Lock()
_instance: Reranker | None = None
_failed: str | None = None


def _load_model(model_name: str) -> Any:
    # Lazy import: unit tests and non-rerank paths never pay for it.
    from sentence_transformers import CrossEncoder

    return CrossEncoder(model_name)


class Reranker:
    def __init__(self, model_name: str = DEFAULT_RERANK_MODEL) -> None:
        self.model_name = model_name
        self._model = _load_model(model_name)

    def rerank(self, query: str, rows: list[dict], limit: int) -> list[dict]:
        if not rows:
            return []
        pairs = [(query, r.get("chunk_text") or "") for r in rows]
        logits = self._model.predict(pairs)
        out: list[dict] = []
        for r, logit in zip(rows, logits, strict=True):
            row = dict(r)
            row["vector_score"] = row.get("score")
            row["score"] = float(1.0 / (1.0 + math.exp(-float(logit))))
            row["reranked"] = True
            out.append(row)
        out.sort(key=lambda r: r["score"], reverse=True)
        return out[:limit]


def get_reranker() -> Reranker | None:
    global _instance, _failed
    if _instance is not None:
        return _instance
    if _failed is not None:
        return None
    with _lock:
        if _instance is not None:
            return _instance
        if _failed is not None:
            return None
        try:
            _instance = Reranker()
        except Exception as e:
            log.exception("failed to load rerank model")
            _failed = str(e)
            return None
        return _instance


def rerank_reason() -> str:
    """Why get_reranker() returned None (only meaningful after it has)."""
    return _failed or "rerank model not loaded"


def reset_for_tests() -> None:
    global _instance, _failed
    _instance = None
    _failed = None
