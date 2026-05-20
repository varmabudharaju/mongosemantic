"""Tier 3 — Atlas $search BM25 index creation and queryability.

Reuses the state established by Tier 2 (test_vector_search.py): shadow
multi-field apply on title + plot, with both vector + BM25 indexes already
created on the embeddings collection. No teardown — purely a read-side
verification.
"""
from __future__ import annotations

import pytest

from mongosemantic.search.hybrid import search_index_name
from mongosemantic.state import load_config

from tests.integration.atlas.conftest import wait_for_search_index_queryable


@pytest.mark.atlas
def test_bm25_index_present_and_queryable(
    atlas_client, atlas_dataset_loaded, atlas_db_name, atlas_collection_name,
):
    db = atlas_client[atlas_db_name]
    cfg = load_config(db, atlas_collection_name)
    assert cfg is not None, (
        "no mongosemantic config found — Tier 2 (test_vector_search.py) must "
        "run first to establish the multi-field shadow state."
    )

    shadow = db[f"{atlas_collection_name}_embeddings"]
    bm25_idx = search_index_name(atlas_collection_name, "title")
    info = wait_for_search_index_queryable(shadow, bm25_idx, timeout=180)
    assert info.get("queryable") is True

    # Query the indexed path `chunk_text` — mongosemantic's shadow layout
    # stores the indexed text under chunk_text regardless of source field
    # name (see db/indexes.py:search_index_definition).
    pipeline = [
        {"$search": {"index": bm25_idx, "text": {"query": "gangster", "path": "chunk_text"}}},
        {"$limit": 5},
        {"$project": {"_id": 0, "chunk_text": 1, "score": {"$meta": "searchScore"}}},
    ]
    hits = list(shadow.aggregate(pipeline))
    assert len(hits) > 0, "BM25 $search returned zero hits for 'gangster'"
    assert all(h["score"] > 0 for h in hits), f"non-positive BM25 score in {hits}"
