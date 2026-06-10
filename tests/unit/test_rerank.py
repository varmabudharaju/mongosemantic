"""tests/unit/test_rerank.py"""
from unittest.mock import MagicMock, patch

import numpy as np

import mongosemantic.search.rerank as rr


def _rows():
    return [
        {"source_id": "a", "chunk_text": "weak match", "score": 0.9},
        {"source_id": "b", "chunk_text": "strong match", "score": 0.5},
        {"source_id": "c", "chunk_text": "medium match", "score": 0.7},
    ]


def _fake_model(logits):
    m = MagicMock()
    m.predict = lambda pairs: np.array(logits, dtype=np.float32)
    return m


def test_rerank_reorders_and_annotates():
    r = rr.Reranker.__new__(rr.Reranker)
    r.model_name = "fake"
    r._model = _fake_model([-2.0, 3.0, 0.5])  # b should win, then c, then a
    out = r.rerank("q", _rows(), limit=2)
    assert [row["source_id"] for row in out] == ["b", "c"]
    assert out[0]["reranked"] is True
    assert out[0]["vector_score"] == 0.5          # original score preserved
    assert 0.0 < out[0]["score"] < 1.0            # sigmoid(logit)
    assert out[0]["score"] > out[1]["score"]


def test_rerank_empty_rows():
    r = rr.Reranker.__new__(rr.Reranker)
    r.model_name = "fake"
    r._model = _fake_model([])
    assert r.rerank("q", [], limit=5) == []


def test_get_reranker_caches_failure():
    rr.reset_for_tests()
    with patch.object(rr, "_load_model", side_effect=RuntimeError("no model")):
        assert rr.get_reranker() is None
        assert rr.get_reranker() is None  # cached, _load_model not retried
        assert "no model" in rr.rerank_reason()
    rr.reset_for_tests()


def test_get_reranker_caches_instance():
    rr.reset_for_tests()
    with patch.object(rr, "_load_model", return_value=_fake_model([1.0])) as load:
        first = rr.get_reranker()
        second = rr.get_reranker()
        assert first is second
        assert load.call_count == 1
    rr.reset_for_tests()
