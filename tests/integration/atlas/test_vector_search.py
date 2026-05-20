"""Tier 2 — $vectorSearch on Atlas (single-field for M0 compatibility).

Note on single-field choice: Atlas M0/M2/M5 cap search indexes at 3 per
cluster. A shadow-mode multi-field apply needs 2 indexes per field
(vectorSearch + search for hybrid), so 2-field apply hits the cap at 4 > 3
and partially fails (now caught loudly by the fix in v0.7.3). For the
verification suite we use a single field — covers the $vectorSearch path
end-to-end while remaining runnable on the free tier. The multi-field
*merge* logic is unit-tested in tests/unit/test_search_pipelines.py.
"""
from __future__ import annotations

import pytest
from typer.testing import CliRunner

from mongosemantic.cli import app
from mongosemantic.db.indexes import vector_index_name
from mongosemantic.embeddings.provider import get_provider
from mongosemantic.state import load_config
from mongosemantic.worker.runner import process_batch
from tests.integration.atlas.conftest import wait_for_search_index_queryable


@pytest.mark.atlas
def test_vector_search_single_field(
    atlas_client,
    atlas_dataset_loaded,
    env_pointing_at_atlas,
    atlas_db_name,
    atlas_collection_name,
):
    runner = CliRunner()
    db = atlas_client[atlas_db_name]

    # Clean slate from any prior tier.
    runner.invoke(app, ["teardown", "--collection", atlas_collection_name, "--yes"])

    # Single-field shadow apply: 2 indexes total (vector + BM25 for `title`),
    # fits M0's 3-index cap with one slot to spare.
    r = runner.invoke(app, [
        "apply", "--collection", atlas_collection_name,
        "--field", "title",
        "--mode", "shadow",
    ])
    assert r.exit_code == 0, r.output

    r = runner.invoke(app, ["index", "--collection", atlas_collection_name])
    assert r.exit_code == 0, r.output

    # Process a representative batch — full corpus would burn budget on every run.
    # ~1,280 docs is enough for the vector index to have a useful neighborhood.
    provider = get_provider("local-fast")
    for _ in range(20):
        process_batch(db, provider, "atlas-tier2", 64)

    # Wait for the vector index on the embeddings collection to become queryable.
    shadow = db[f"{atlas_collection_name}_embeddings"]
    cfg = load_config(db, atlas_collection_name)
    idx_title = (cfg.vector_index_names or {}).get("title") or vector_index_name(
        atlas_collection_name, "title"
    )
    wait_for_search_index_queryable(shadow, idx_title, timeout=180)

    # Search and assert Atlas-side ranking shape.
    r = runner.invoke(app, [
        "search", "heist gone wrong",
        "--collection", atlas_collection_name,
        "--limit", "5",
    ])
    assert r.exit_code == 0, r.output
    # Atlas-side $vectorSearch produces cosine-ish scores in [0, 1].
    # Brute-force fallback would show dot-product scores well above 1.
    assert "0." in r.output, (
        f"expected fractional similarity scores, got:\n{r.output}"
    )
