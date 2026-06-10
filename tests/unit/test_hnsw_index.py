"""Tests for HnswIndexManager: build, persist, load, query.

Uses mongomock for the shadow collection. Generates synthetic L2-normalized
vectors so cosine scores stay in [-1, 1] and the top-k assertion is
meaningful.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import mongomock
import numpy as np
import pytest

from mongosemantic.search.hnsw_index import HnswIndexManager
from mongosemantic.state.config_store import CollectionConfig, FieldSpec


def _norm(v: np.ndarray) -> list[float]:
    n = np.linalg.norm(v)
    return (v / n if n else v).astype(np.float32).tolist()


def _cfg(collection: str = "wines", field_path: str = "description",
         model: str = "local-fast", dim: int = 4) -> CollectionConfig:
    return CollectionConfig(
        collection=collection,
        mode="shadow",
        shadow_collection=f"{collection}_embeddings",
        fields=[FieldSpec(path=field_path)],
        embedding_model=model,
        embedding_dim=dim,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def _seed_shadow(db, collection: str, field: str, model: str,
                 vectors: list[list[float]], source_id_prefix: str = "doc") -> None:
    """Insert one shadow row per vector with the parallel source doc."""
    shadow = db[f"{collection}_embeddings"]
    source = db[collection]
    for i, vec in enumerate(vectors):
        sid = f"{source_id_prefix}-{i}"
        shadow.insert_one({
            "source_id": sid,
            "source_collection": collection,
            "field_path": field,
            "chunk_index": 0,
            "chunk_text": f"text for {sid}",
            "embedding": vec,
            "embedding_model": model,
            "embedding_dim": len(vec),
        })
        source.insert_one({"_id": sid, "title": f"title {sid}"})


def test_build_then_query_returns_top_k_sorted(tmp_path: Path):
    """The nearest vector to the query should come back first; scores
    should be descending; result rows should be hydrated with source docs."""
    db = mongomock.MongoClient()["d"]
    cfg = _cfg(dim=4)
    # 5 vectors; the 3rd is identical to our query, so it should score 1.0.
    query = _norm(np.array([1, 0, 0, 0]))
    vectors = [
        _norm(np.array([0, 1, 0, 0])),
        _norm(np.array([0, 0, 1, 0])),
        query,                                # exact match
        _norm(np.array([0.5, 0.5, 0, 0])),    # decent angle
        _norm(np.array([-1, 0, 0, 0])),       # opposite
    ]
    _seed_shadow(db, "wines", "description", "local-fast", vectors)
    mgr = HnswIndexManager(cache_dir=tmp_path)
    built = mgr.build(db, cfg, "description")
    assert built == 5
    rows = mgr.query(db, cfg, "description", query, limit=3)
    assert rows is not None
    assert len(rows) == 3
    # Best hit is the exact match.
    assert rows[0]["source_id"] == "doc-2"
    assert rows[0]["score"] == pytest.approx(1.0, rel=1e-4)
    # Scores monotone decreasing.
    scores = [r["score"] for r in rows]
    assert scores == sorted(scores, reverse=True)
    # Source doc was hydrated from the parallel collection.
    assert rows[0]["source_doc"]["title"] == "title doc-2"


def test_query_returns_none_when_no_index_built(tmp_path: Path):
    """A missing index must return None so the caller falls back to brute
    force — not raise, not return an empty list."""
    db = mongomock.MongoClient()["d"]
    cfg = _cfg()
    mgr = HnswIndexManager(cache_dir=tmp_path)
    assert mgr.query(db, cfg, "description", [1.0, 0, 0, 0], limit=5) is None


def test_query_returns_none_for_inline_mode(tmp_path: Path):
    """Inline mode is intentionally unsupported in MVP — return None so
    the brute-force inline path keeps serving."""
    cfg = CollectionConfig(
        collection="x", mode="inline", shadow_collection=None,
        fields=[FieldSpec(path="body")], embedding_model="local-fast",
        embedding_dim=4,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db = mongomock.MongoClient()["d"]
    mgr = HnswIndexManager(cache_dir=tmp_path)
    assert mgr.query(db, cfg, "body", [1.0, 0, 0, 0], limit=5) is None


def test_save_then_load_from_disk_serves_queries(tmp_path: Path):
    """Build in one manager, throw it away, instantiate a fresh manager
    pointing at the same cache dir, and queries should still work — the
    on-disk index gets lazy-loaded on first query."""
    db = mongomock.MongoClient()["d"]
    cfg = _cfg(dim=3)
    vectors = [
        _norm(np.array([1, 0, 0])),
        _norm(np.array([0, 1, 0])),
        _norm(np.array([0, 0, 1])),
    ]
    _seed_shadow(db, "wines", "description", "local-fast", vectors)
    mgr1 = HnswIndexManager(cache_dir=tmp_path)
    mgr1.build(db, cfg, "description")

    # Fresh manager, same cache dir — no build() call.
    mgr2 = HnswIndexManager(cache_dir=tmp_path)
    rows = mgr2.query(db, cfg, "description", [1.0, 0.0, 0.0], limit=1)
    assert rows is not None
    assert len(rows) == 1
    assert rows[0]["source_id"] == "doc-0"
    assert rows[0]["score"] == pytest.approx(1.0, rel=1e-4)


def test_build_refuses_dim_mismatch(tmp_path: Path):
    """If shadow vectors don't match cfg.embedding_dim we must raise loudly
    instead of building a broken index."""
    db = mongomock.MongoClient()["d"]
    cfg = _cfg(dim=4)  # cfg says 4
    _seed_shadow(db, "wines", "description", "local-fast",
                 [[0.1, 0.2, 0.3]])  # actually 3
    mgr = HnswIndexManager(cache_dir=tmp_path)
    with pytest.raises(ValueError, match="dim"):
        mgr.build(db, cfg, "description")


def test_build_with_no_rows_returns_zero(tmp_path: Path):
    db = mongomock.MongoClient()["d"]
    cfg = _cfg()
    mgr = HnswIndexManager(cache_dir=tmp_path)
    assert mgr.build(db, cfg, "description") == 0


def test_stale_tracking(tmp_path: Path):
    """should_rebuild waits for both the MIN_REBUILD_INTERVAL and the
    staleness ratio. Newly-built indexes must not rebuild immediately."""
    db = mongomock.MongoClient()["d"]
    cfg = _cfg(dim=3)
    _seed_shadow(db, "wines", "description", "local-fast",
                 [_norm(np.array([1, 0, 0]))] * 10)
    mgr = HnswIndexManager(cache_dir=tmp_path)
    mgr.build(db, cfg, "description")
    key = ("wines", "description", "local-fast")
    # Just built — no rebuild even if marked stale.
    mgr.mark_stale(key, n=10)
    assert mgr.should_rebuild(key) is False


def test_query_with_allowed_ids_filters_results(tmp_path: Path):
    """query(allowed_ids=subset) must return only rows whose source_id is in
    the subset, and must return at least one row when the subset is non-empty
    and the index contains matching vectors."""
    db = mongomock.MongoClient()["d"]
    cfg = _cfg(dim=4)
    # 4 vectors in 4-d space; each is a basis vector for easy scoring.
    vectors = [
        _norm(np.array([1, 0, 0, 0])),   # doc-0
        _norm(np.array([0, 1, 0, 0])),   # doc-1
        _norm(np.array([0, 0, 1, 0])),   # doc-2
        _norm(np.array([0, 0, 0, 1])),   # doc-3
    ]
    _seed_shadow(db, "wines", "description", "local-fast", vectors)
    mgr = HnswIndexManager(cache_dir=tmp_path)
    mgr.build(db, cfg, "description")

    # Allow only doc-0 and doc-1; query toward doc-0.
    allowed = ["doc-0", "doc-1"]
    query = _norm(np.array([1, 0, 0, 0]))
    rows = mgr.query(db, cfg, "description", query, limit=4,
                     allowed_ids=allowed)
    assert rows is not None
    assert len(rows) >= 1
    returned_ids = {r["source_id"] for r in rows}
    assert returned_ids.issubset(set(allowed)), (
        f"Expected only ids in {allowed}, got {returned_ids}"
    )


def test_query_with_empty_allowed_ids_returns_empty(tmp_path: Path):
    """allowed_ids=[] means no document is permitted; must return [] (not None)
    so the caller knows HNSW answered and there is nothing to show."""
    db = mongomock.MongoClient()["d"]
    cfg = _cfg(dim=4)
    vectors = [
        _norm(np.array([1, 0, 0, 0])),
        _norm(np.array([0, 1, 0, 0])),
    ]
    _seed_shadow(db, "wines", "description", "local-fast", vectors)
    mgr = HnswIndexManager(cache_dir=tmp_path)
    mgr.build(db, cfg, "description")

    rows = mgr.query(db, cfg, "description", [1.0, 0, 0, 0], limit=5,
                     allowed_ids=[])
    assert rows == [], f"Expected [], got {rows!r}"


def test_query_without_allowed_ids_unchanged(tmp_path: Path):
    """Omitting allowed_ids (default None) must not change existing behavior:
    all indexed docs are eligible and at least top-k are returned."""
    db = mongomock.MongoClient()["d"]
    cfg = _cfg(dim=4)
    vectors = [
        _norm(np.array([1, 0, 0, 0])),   # doc-0 — exact match
        _norm(np.array([0, 1, 0, 0])),   # doc-1
        _norm(np.array([0, 0, 1, 0])),   # doc-2
    ]
    _seed_shadow(db, "wines", "description", "local-fast", vectors)
    mgr = HnswIndexManager(cache_dir=tmp_path)
    mgr.build(db, cfg, "description")

    query = _norm(np.array([1, 0, 0, 0]))
    rows = mgr.query(db, cfg, "description", query, limit=3)
    assert rows is not None
    assert len(rows) == 3
    # Nearest to query=[1,0,0,0] is doc-0.
    assert rows[0]["source_id"] == "doc-0"
    assert rows[0]["score"] == pytest.approx(1.0, rel=1e-4)
