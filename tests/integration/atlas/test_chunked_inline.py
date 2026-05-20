"""Tier 5 — chunked indexing + inline mode on Atlas (single-field, M0-safe).

Both phases re-apply with different configurations, so this tier does its own
teardown + apply. Each phase only processes a handful of worker batches to
keep total runtime tolerable (~5–10 min per phase).
"""
from __future__ import annotations

import pytest
from typer.testing import CliRunner

from mongosemantic.cli import app
from mongosemantic.embeddings.provider import get_provider
from mongosemantic.worker.runner import process_batch
from tests.integration.atlas.conftest import wait_for_no_mongosemantic_search_indexes


@pytest.mark.atlas
def test_chunked_indexing_produces_multiple_chunks_per_doc(
    atlas_client,
    atlas_dataset_loaded,
    env_pointing_at_atlas,
    atlas_db_name,
    atlas_collection_name,
):
    """Chunked shadow apply on `fullplot` (longer text) — any non-trivial
    source doc should produce >1 chunk in the shadow."""
    runner = CliRunner()
    db = atlas_client[atlas_db_name]

    # Teardown anything from prior tiers + wait for Atlas to actually
    # free the FTS-index slots (drop is async vs the cap accounting).
    runner.invoke(app, ["teardown", "--collection", atlas_collection_name, "--yes"])
    wait_for_no_mongosemantic_search_indexes(
        db, [atlas_collection_name, f"{atlas_collection_name}_embeddings"]
    )

    r = runner.invoke(app, [
        "apply", "--collection", atlas_collection_name,
        "--field", "fullplot",
        "--mode", "shadow",
        "--chunked",
        "--chunk-size", "60",
        "--chunk-overlap", "10",
    ])
    assert r.exit_code == 0, r.output

    r = runner.invoke(app, ["index", "--collection", atlas_collection_name])
    assert r.exit_code == 0, r.output

    # ~640 docs is enough to see chunking land for the long-plot ones.
    provider = get_provider("local-fast")
    for _ in range(10):
        process_batch(db, provider, "atlas-tier5-chunked", 64)

    shadow = db[f"{atlas_collection_name}_embeddings"]
    multi_chunk = list(shadow.aggregate([
        {"$group": {"_id": "$source_id", "n": {"$sum": 1}}},
        {"$match": {"n": {"$gt": 1}}},
        {"$limit": 1},
    ]))
    assert multi_chunk, (
        "expected at least one source doc to chunk into >1 embeddings on shadow"
    )


@pytest.mark.atlas
def test_inline_mode_writes_under_msem(
    atlas_client,
    atlas_dataset_loaded,
    env_pointing_at_atlas,
    atlas_db_name,
    atlas_collection_name,
):
    """Inline mode writes the embedding directly into the source doc under
    `_msem.{field}` instead of into a shadow collection."""
    runner = CliRunner()
    db = atlas_client[atlas_db_name]

    # Teardown chunked phase + frees indexes.
    runner.invoke(app, ["teardown", "--collection", atlas_collection_name, "--yes"])

    r = runner.invoke(app, [
        "apply", "--collection", atlas_collection_name,
        "--field", "plot",
        "--mode", "inline",
    ])
    assert r.exit_code == 0, r.output

    r = runner.invoke(app, ["index", "--collection", atlas_collection_name])
    assert r.exit_code == 0, r.output

    provider = get_provider("local-fast")
    for _ in range(8):
        process_batch(db, provider, "atlas-tier5-inline", 64)

    # Inline writes into the source collection under _msem.{field}.
    coll = db[atlas_collection_name]
    sample = coll.find_one({"_msem.plot": {"$exists": True}})
    assert sample is not None, (
        "no source doc has _msem.plot after inline indexing — inline write path "
        "may not be hooked up correctly for Atlas"
    )
    # The embedding may live under .embedding or .vector depending on schema.
    msem = sample["_msem"]["plot"]
    vec = msem.get("embedding") or msem.get("vector")
    assert isinstance(vec, list) and len(vec) >= 384, (
        f"expected an embedding vector under _msem.plot, got {msem!r}"
    )
