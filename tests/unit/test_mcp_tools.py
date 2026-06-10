"""Tests for the MCP tool implementations in mongosemantic.mcp_server.tools.

The MCP wrappers in server.py are thin — they open a connection and call
through to these functions. The functions are pure (take a Database) and
covered here. Wrapper-specific behavior (transport, decorator schema) is
verified by the import-time smoke test below.
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import mongomock
import numpy as np
import pytest

from mongosemantic.db.client import Topology
from mongosemantic.mcp_server import tools as t
from mongosemantic.state import enqueue_embed
from mongosemantic.state.config_store import CollectionConfig, FieldSpec, save_config


def _db():
    return mongomock.MongoClient()["test"]


def _shadow_cfg(db):
    save_config(db, CollectionConfig(
        collection="articles", mode="shadow", shadow_collection="articles_embeddings",
        fields=[FieldSpec(path="body")], embedding_model="local-fast", embedding_dim=3,
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    ))


def _inline_cfg(db):
    save_config(db, CollectionConfig(
        collection="products", mode="inline", shadow_collection=None,
        fields=[FieldSpec(path="description")], embedding_model="local-fast", embedding_dim=3,
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    ))


# -- list_collections --------------------------------------------------------

def test_list_collections_includes_configured_and_unconfigured():
    db = _db()
    db["articles"].insert_one({"_id": "a", "body": "x"})
    db["other"].insert_one({"_id": "z"})
    _shadow_cfg(db)
    out = t.t_list_collections(db)
    rows = {r["name"]: r for r in out["collections"]}
    assert rows["articles"]["status"] == "configured"
    assert rows["articles"]["mode"] == "shadow"
    assert rows["other"]["status"] == "not_configured"


def test_list_collections_hides_internal_collections():
    db = _db()
    db["mongosemantic_jobs"].insert_one({"_id": 1})
    db["mongosemantic_state"].insert_one({"_id": 1})
    db["articles_embeddings"].insert_one({"_id": 1})
    db["real"].insert_one({"_id": 1})
    out = t.t_list_collections(db)
    names = {r["name"] for r in out["collections"]}
    assert names == {"real"}


# -- list_configured ---------------------------------------------------------

def test_list_configured_reflects_mode_and_chunking():
    db = _db()
    _shadow_cfg(db)
    _inline_cfg(db)
    out = t.t_list_configured(db)
    by_name = {c["collection"]: c for c in out["configured"]}
    assert by_name["articles"]["mode"] == "shadow"
    assert by_name["articles"]["chunked"] is False
    assert by_name["products"]["mode"] == "inline"
    assert by_name["products"]["shadow_collection"] is None


# -- inspect_collection ------------------------------------------------------

def test_inspect_collection_scores_fields():
    db = _db()
    db["articles"].insert_many([
        {"title": "x", "body": "Lorem ipsum dolor sit amet" * 12}
        for _ in range(10)
    ])
    out = t.t_inspect_collection(db, "articles", sample=10)
    paths = {f["path"] for f in out["fields"]}
    assert "title" in paths and "body" in paths
    # band is one of the four
    for f in out["fields"]:
        assert f["band"] in {"great", "good", "usable", "not_recommended"}


# -- get_sample_documents ----------------------------------------------------

def test_get_sample_documents_strips_msem_field():
    db = _db()
    db["products"].insert_one({
        "_id": "p1", "name": "shoes",
        "_msem": {"description": {"embedding": [0.1, 0.2, 0.3]}},
    })
    out = t.t_get_sample_documents(db, "products", limit=1)
    assert len(out["documents"]) == 1
    doc = out["documents"][0]
    assert "_msem" not in doc
    assert doc["name"] == "shoes"


# -- get_status --------------------------------------------------------------

def test_get_status_counts_inline_and_shadow_embeddings():
    db = _db()
    _shadow_cfg(db)
    _inline_cfg(db)
    db["articles_embeddings"].insert_many([{"x": i} for i in range(3)])
    db["products"].insert_many([
        {"_id": i, "_msem": {"description": {"embedding": [0.0]}}} for i in range(4)
    ])
    db["products"].insert_one({"_id": 99})  # no _msem — should not be counted
    enqueue_embed(db, "articles", "x", "body", None, "t", "h", "local-fast")
    out = t.t_get_status(db, Topology.STANDALONE)
    assert out["topology"] == "standalone"
    assert out["configured_count"] == 2
    assert out["total_embeddings"] == 3 + 4
    assert out["jobs"].get("pending") == 1


# -- semantic_search ---------------------------------------------------------

def test_semantic_search_uses_collection_config_model():
    db = _db()
    _shadow_cfg(db)
    fake_provider = MagicMock()
    fake_provider.embed = lambda q: np.array([1.0, 0.0, 0.0], dtype=np.float32)
    fake_rows = [{"source_id": "a", "source_collection": "articles", "field_path": "body",
                  "chunk_index": 0, "chunk_text": "hit", "score": 0.9}]
    with patch("mongosemantic.mcp_server.tools.get_provider", return_value=fake_provider), \
         patch("mongosemantic.mcp_server.tools._run_one", return_value=fake_rows) as run:
        out = t.t_semantic_search(db, Topology.STANDALONE, "q", "articles", limit=5)
        run.assert_called_once()
    assert out["rows"][0]["chunk_text"] == "hit"


def test_semantic_search_rejects_unconfigured_collection():
    db = _db()
    with pytest.raises(ValueError, match="not configured"):
        t.t_semantic_search(db, Topology.STANDALONE, "q", "missing")


def test_semantic_search_filter_and_rerank_plumb_through():
    db = _db()
    _shadow_cfg(db)
    fake_provider = MagicMock()
    fake_provider.embed = lambda q: np.array([1.0, 0.0, 0.0], dtype=np.float32)
    fetched = [
        {"source_id": "a", "source_collection": "articles", "field_path": "body",
         "chunk_index": 0, "chunk_text": "first", "score": 0.9},
        {"source_id": "b", "source_collection": "articles", "field_path": "body",
         "chunk_index": 0, "chunk_text": "second", "score": 0.8},
    ]
    # Cross-encoder disagrees with the vector ordering — its order must win.
    reranked = [
        dict(fetched[1], vector_score=0.8, score=0.95, reranked=True),
        dict(fetched[0], vector_score=0.9, score=0.40, reranked=True),
    ]
    fake_reranker = MagicMock()
    fake_reranker.rerank.return_value = reranked
    flt = {"year": {"$gte": 1960}}
    with patch("mongosemantic.mcp_server.tools.get_provider", return_value=fake_provider), \
         patch("mongosemantic.mcp_server.tools._run_one", return_value=fetched) as run, \
         patch("mongosemantic.mcp_server.tools.get_reranker", return_value=fake_reranker):
        out = t.t_semantic_search(db, Topology.STANDALONE, "q", "articles",
                                  limit=2, filter=flt, rerank=True)
    args, kwargs = run.call_args
    assert kwargs["source_filter"] == flt
    assert args[4] == 2 * 5  # limit * RERANK_CANDIDATE_MULTIPLIER
    fake_reranker.rerank.assert_called_once_with("q", fetched, 2)
    assert [r["source_id"] for r in out["rows"]] == ["b", "a"]
    assert out["rows"][0]["vector_score"] == 0.8
    assert out["rows"][0]["reranked"] is True


def test_semantic_search_rejects_invalid_filter():
    db = _db()
    _shadow_cfg(db)
    with pytest.raises(ValueError, match="invalid filter"):
        t.t_semantic_search(db, Topology.STANDALONE, "q", "articles",
                            filter={"$where": "1"})


def test_semantic_search_filter_runtime_rejection_raises_value_error():
    """A filter that validates client-side but is rejected by MongoDB at
    runtime is user input — surface it as ValueError, not OperationFailure."""
    from pymongo.errors import OperationFailure

    db = _db()
    _shadow_cfg(db)
    fake_provider = MagicMock()
    fake_provider.embed = lambda q: np.array([1.0, 0.0, 0.0], dtype=np.float32)
    with patch("mongosemantic.mcp_server.tools.get_provider", return_value=fake_provider), \
         patch("mongosemantic.mcp_server.tools._run_one",
               side_effect=OperationFailure("unknown operator $regexx")), \
         pytest.raises(ValueError, match="filter rejected by MongoDB"):
        t.t_semantic_search(db, Topology.STANDALONE, "q", "articles",
                            filter={"plot": {"$gte": 1}})


def test_semantic_search_operation_failure_without_filter_propagates():
    """No filter -> an OperationFailure is a genuine server error and must
    NOT be rebranded as a filter problem."""
    from pymongo.errors import OperationFailure

    db = _db()
    _shadow_cfg(db)
    fake_provider = MagicMock()
    fake_provider.embed = lambda q: np.array([1.0, 0.0, 0.0], dtype=np.float32)
    with patch("mongosemantic.mcp_server.tools.get_provider", return_value=fake_provider), \
         patch("mongosemantic.mcp_server.tools._run_one",
               side_effect=OperationFailure("server exploded")), \
         pytest.raises(OperationFailure, match="server exploded"):
        t.t_semantic_search(db, Topology.STANDALONE, "q", "articles")


def test_semantic_search_rerank_failure_degrades_with_notice():
    """Reranker loads but raises at runtime -> vector-ranked rows, truncated,
    with an explanatory notice (parity with the web route)."""
    db = _db()
    _shadow_cfg(db)
    fake_provider = MagicMock()
    fake_provider.embed = lambda q: np.array([1.0, 0.0, 0.0], dtype=np.float32)
    fetched = [
        {"source_id": str(i), "source_collection": "articles", "field_path": "body",
         "chunk_text": f"t{i}", "score": 1.0 - i / 10}
        for i in range(5)
    ]
    fake_reranker = MagicMock()
    fake_reranker.rerank.side_effect = RuntimeError("model exploded")
    with patch("mongosemantic.mcp_server.tools.get_provider", return_value=fake_provider), \
         patch("mongosemantic.mcp_server.tools._run_one", return_value=fetched), \
         patch("mongosemantic.mcp_server.tools.get_reranker", return_value=fake_reranker):
        out = t.t_semantic_search(db, Topology.STANDALONE, "q", "articles",
                                  limit=2, rerank=True)
    assert out["notice"] == "rerank failed: model exploded"
    assert len(out["rows"]) == 2
    assert [r["source_id"] for r in out["rows"]] == ["0", "1"]  # vector order


def test_semantic_search_rerank_limit_capped_at_1000():
    db = _db()
    _shadow_cfg(db)
    with pytest.raises(ValueError, match="rerank supports limit <= 1000"):
        t.t_semantic_search(db, Topology.STANDALONE, "q", "articles",
                            limit=1001, rerank=True)


def test_semantic_search_rerank_unavailable_adds_notice_and_truncates():
    db = _db()
    _shadow_cfg(db)
    fake_provider = MagicMock()
    fake_provider.embed = lambda q: np.array([1.0, 0.0, 0.0], dtype=np.float32)
    fetched = [
        {"source_id": str(i), "source_collection": "articles", "field_path": "body",
         "chunk_text": f"t{i}", "score": 1.0 - i / 10}
        for i in range(5)
    ]
    with patch("mongosemantic.mcp_server.tools.get_provider", return_value=fake_provider), \
         patch("mongosemantic.mcp_server.tools._run_one", return_value=fetched), \
         patch("mongosemantic.mcp_server.tools.get_reranker", return_value=None), \
         patch("mongosemantic.mcp_server.tools.rerank_reason", return_value="no model"):
        out = t.t_semantic_search(db, Topology.STANDALONE, "q", "articles",
                                  limit=2, rerank=True)
    assert out["notice"] == "rerank unavailable: no model"
    assert len(out["rows"]) == 2
    assert [r["source_id"] for r in out["rows"]] == ["0", "1"]


# -- search_all_collections --------------------------------------------------

def test_search_all_collections_merges_across_configs():
    db = _db()
    _shadow_cfg(db)
    _inline_cfg(db)
    fake_provider = MagicMock()
    fake_provider.embed = lambda q: np.array([1.0, 0.0, 0.0], dtype=np.float32)
    per = {
        "articles": [{"source_id": "a1", "source_collection": "articles",
                      "field_path": "body", "chunk_text": "t", "score": 0.9}],
        "products": [{"source_id": "p1", "source_collection": "products",
                      "field_path": "description", "chunk_text": "u", "score": 0.7}],
    }
    def fake_run(_db, _cfg, name, _q, _lim, _t):
        return per[name]
    with patch("mongosemantic.mcp_server.tools.get_provider", return_value=fake_provider), \
         patch("mongosemantic.mcp_server.tools._run_one", side_effect=fake_run):
        out = t.t_search_all_collections(db, Topology.STANDALONE, "q", limit=10)
    scores = [r["score"] for r in out["rows"]]
    assert scores == sorted(scores, reverse=True)
    assert {r["source_collection"] for r in out["rows"]} == {"articles", "products"}


def test_search_all_collections_reranks_after_merge():
    db = _db()
    _shadow_cfg(db)
    _inline_cfg(db)
    fake_provider = MagicMock()
    fake_provider.embed = lambda q: np.array([1.0, 0.0, 0.0], dtype=np.float32)
    per = {
        "articles": [{"source_id": "a1", "source_collection": "articles",
                      "field_path": "body", "chunk_text": "t", "score": 0.9}],
        "products": [{"source_id": "p1", "source_collection": "products",
                      "field_path": "description", "chunk_text": "u", "score": 0.7}],
    }

    def fake_run(_db, _cfg, name, _q, lim, _t):
        assert lim == 2 * 5  # per-collection over-fetch: limit * multiplier
        return list(per[name])

    def fake_rerank(query, rows, lim):
        out = [dict(r, vector_score=r["score"], score=1.0 - i * 0.1, reranked=True)
               for i, r in enumerate(reversed(rows))]
        return out[:lim]

    fake_reranker = MagicMock()
    fake_reranker.rerank = MagicMock(side_effect=fake_rerank)
    with patch("mongosemantic.mcp_server.tools.get_provider", return_value=fake_provider), \
         patch("mongosemantic.mcp_server.tools._run_one", side_effect=fake_run), \
         patch("mongosemantic.mcp_server.tools.get_reranker", return_value=fake_reranker):
        out = t.t_search_all_collections(db, Topology.STANDALONE, "q", limit=2, rerank=True)
    # one rerank call, over the merged cross-collection rows
    fake_reranker.rerank.assert_called_once()
    _q, passed_rows, passed_limit = fake_reranker.rerank.call_args[0]
    assert {r["source_collection"] for r in passed_rows} == {"articles", "products"}
    assert passed_limit == 2
    assert all(r["reranked"] is True for r in out["rows"])


# -- hybrid_search ---------------------------------------------------------

def test_hybrid_search_runs_hybrid_on_self_hosted_shadow():
    """Replica-set / standalone don't have $rankFusion, but shadow-mode hybrid
    now runs everywhere via client-side RRF ($text leg + vector leg)."""
    db = _db()
    _shadow_cfg(db)
    fake_provider = MagicMock()
    fake_provider.embed = lambda q: np.array([1.0, 0.0, 0.0], dtype=np.float32)
    fake_rows = [{"source_id": "a", "source_collection": "articles",
                  "field_path": "body", "chunk_text": "t", "score": 0.9}]
    with patch("mongosemantic.mcp_server.tools.get_provider", return_value=fake_provider), \
         patch("mongosemantic.mcp_server.tools.run_one_hybrid", return_value=fake_rows) as hyb:
        out = t.t_hybrid_search(db, Topology.REPLICA_SET, "q", "articles")
        hyb.assert_called_once()
    assert out["mode"] == "hybrid"
    assert out["notice"] is None


def test_hybrid_search_uses_hybrid_path_on_atlas_shadow():
    db = _db()
    _shadow_cfg(db)
    fake_provider = MagicMock()
    fake_provider.embed = lambda q: np.array([1.0, 0.0, 0.0], dtype=np.float32)
    fake_rows = [{"source_id": "a", "source_collection": "articles",
                  "field_path": "body", "chunk_text": "t", "score": 0.9}]
    with patch("mongosemantic.mcp_server.tools.get_provider", return_value=fake_provider), \
         patch("mongosemantic.mcp_server.tools.run_one_hybrid", return_value=fake_rows) as hyb:
        out = t.t_hybrid_search(db, Topology.ATLAS, "q", "articles")
        hyb.assert_called_once()
    assert out["mode"] == "hybrid"
    assert out["notice"] is None


def test_hybrid_search_falls_back_for_inline_mode():
    """Inline collections don't have a chunk_text column to index — hybrid
    can't run even on Atlas."""
    db = _db()
    _inline_cfg(db)
    fake_provider = MagicMock()
    fake_provider.embed = lambda q: np.array([1.0, 0.0, 0.0], dtype=np.float32)
    with patch("mongosemantic.mcp_server.tools.get_provider", return_value=fake_provider), \
         patch("mongosemantic.mcp_server.tools._run_one", return_value=[]):
        out = t.t_hybrid_search(db, Topology.ATLAS, "q", "products")
    assert out["mode"] == "semantic_fallback"
    assert out["notice"] == "hybrid requires shadow mode; returned pure semantic results"


def test_hybrid_search_passes_filter_and_overfetches_for_rerank():
    db = _db()
    _shadow_cfg(db)
    fake_provider = MagicMock()
    fake_provider.embed = lambda q: np.array([1.0, 0.0, 0.0], dtype=np.float32)
    fetched = [{"source_id": "a", "source_collection": "articles",
                "field_path": "body", "chunk_text": "t", "score": 0.9}]
    fake_reranker = MagicMock()
    fake_reranker.rerank.return_value = [
        dict(fetched[0], vector_score=0.9, score=0.8, reranked=True)
    ]
    flt = {"year": 1999}
    with patch("mongosemantic.mcp_server.tools.get_provider", return_value=fake_provider), \
         patch("mongosemantic.mcp_server.tools.run_one_hybrid", return_value=fetched) as hyb, \
         patch("mongosemantic.mcp_server.tools.get_reranker", return_value=fake_reranker):
        out = t.t_hybrid_search(db, Topology.STANDALONE, "q", "articles",
                                limit=3, filter=flt, rerank=True)
    args, kwargs = hyb.call_args
    assert kwargs["source_filter"] == flt
    assert kwargs["hnsw"] is None
    assert args[5] == 3 * 5  # limit * RERANK_CANDIDATE_MULTIPLIER
    assert out["mode"] == "hybrid"
    assert out["notice"] is None
    assert out["rows"][0]["reranked"] is True
    assert out["rows"][0]["vector_score"] == 0.9


def test_hybrid_search_rejects_invalid_filter():
    db = _db()
    _shadow_cfg(db)
    with pytest.raises(ValueError, match="invalid filter"):
        t.t_hybrid_search(db, Topology.STANDALONE, "q", "articles",
                          filter={"a": {"$expr": {"$gt": [1, 0]}}})


def test_hybrid_search_filter_runtime_rejection_raises_value_error():
    from pymongo.errors import OperationFailure

    db = _db()
    _shadow_cfg(db)
    fake_provider = MagicMock()
    fake_provider.embed = lambda q: np.array([1.0, 0.0, 0.0], dtype=np.float32)
    with patch("mongosemantic.mcp_server.tools.get_provider", return_value=fake_provider), \
         patch("mongosemantic.mcp_server.tools.run_one_hybrid",
               side_effect=OperationFailure("unknown operator $regexx")), \
         pytest.raises(ValueError, match="filter rejected by MongoDB"):
        t.t_hybrid_search(db, Topology.STANDALONE, "q", "articles",
                          filter={"plot": {"$gte": 1}})


def test_hybrid_search_operation_failure_without_filter_propagates():
    from pymongo.errors import OperationFailure

    db = _db()
    _shadow_cfg(db)
    fake_provider = MagicMock()
    fake_provider.embed = lambda q: np.array([1.0, 0.0, 0.0], dtype=np.float32)
    with patch("mongosemantic.mcp_server.tools.get_provider", return_value=fake_provider), \
         patch("mongosemantic.mcp_server.tools.run_one_hybrid",
               side_effect=OperationFailure("server exploded")), \
         pytest.raises(OperationFailure, match="server exploded"):
        t.t_hybrid_search(db, Topology.STANDALONE, "q", "articles")


def test_hybrid_search_rerank_limit_capped_at_1000():
    db = _db()
    _shadow_cfg(db)
    with pytest.raises(ValueError, match="rerank supports limit <= 1000"):
        t.t_hybrid_search(db, Topology.STANDALONE, "q", "articles",
                          limit=1001, rerank=True)


def test_search_all_collections_rerank_limit_capped_at_1000():
    db = _db()
    with pytest.raises(ValueError, match="rerank supports limit <= 1000"):
        t.t_search_all_collections(db, Topology.STANDALONE, "q",
                                   limit=1001, rerank=True)


# -- safe_aggregation --------------------------------------------------------

def test_safe_aggregation_runs_match_count():
    db = _db()
    db["c"].insert_many([{"x": 1}, {"x": 2}, {"x": 3}])
    out = t.t_safe_aggregation(db, "c",
        [{"$match": {"x": {"$gte": 2}}}, {"$count": "n"}])
    assert out["rows"] == [{"n": 2}]


def test_safe_aggregation_rejects_dangerous_stage():
    db = _db()
    with pytest.raises(ValueError, match="rejected"):
        t.t_safe_aggregation(db, "c", [{"$out": "leaked"}])


# -- get_schema_context ------------------------------------------------------

def test_get_schema_context_returns_examples_and_types():
    db = _db()
    db["articles"].insert_many([
        {"title": f"t{i}", "body": "Lorem ipsum dolor sit amet" * 5,
         "tags": ["a", "b"]}
        for i in range(5)
    ])
    out = t.t_get_schema_context(db, "articles", sample=5)
    by_path = {f["path"]: f for f in out["fields"]}
    assert "title" in by_path
    assert by_path["title"]["type"] == "string"
    assert by_path["title"]["example"] is not None
    assert "note" in out and "safe_aggregation" in out["note"]
