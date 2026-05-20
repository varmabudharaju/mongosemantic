"""Tier 6 — Atlas migration with vector + search index carry-over.

Migrates a configured shadow collection from `local-fast` (384-d) to
`local-better` (768-d). The atomic-rename migration path should:
  - Re-embed the corpus under the new model
  - Atomically swap the new shadow into place
  - Carry over both Atlas index types onto the renamed collection
  - Leave an archive collection holding the old 384-d vectors

Test isolation: this tier sets up its own state (teardown + apply + index +
worker) so it can run independently of tiers 2–5.

Cost: ~30+ min on Atlas M0 — re-embedding with the 768-d model dominates.
"""
from __future__ import annotations

import re

import pytest
from tests.integration.atlas.conftest import (
    wait_for_no_mongosemantic_search_indexes,
    wait_for_search_index_queryable,
)
from typer.testing import CliRunner

from mongosemantic.cli import app
from mongosemantic.db.indexes import vector_index_name
from mongosemantic.embeddings.provider import get_provider
from mongosemantic.state import load_config
from mongosemantic.worker.runner import process_batch


def _top_hit_text(runner: CliRunner, collection: str, query: str) -> str | None:
    """Return the first result row's snippet from the search CLI's output,
    or None if no rows present."""
    r = runner.invoke(app, [
        "search", query,
        "--collection", collection,
        "--limit", "1",
    ])
    assert r.exit_code == 0, r.output
    # Result rows in the rich table are below the header. Grab a non-header
    # row's snippet (the last column).
    for line in r.output.splitlines():
        if "│" in line and "Score" not in line and "─" not in line:
            cells = [c.strip() for c in line.split("│") if c.strip()]
            if len(cells) >= 4:
                return cells[-1]
    return None


@pytest.mark.atlas
def test_migration_carries_over_indexes_and_top_hit(
    atlas_client,
    atlas_dataset_loaded,
    env_pointing_at_atlas,
    atlas_db_name,
    atlas_collection_name,
):
    runner = CliRunner()
    db = atlas_client[atlas_db_name]

    # Fresh state.
    runner.invoke(app, ["teardown", "--collection", atlas_collection_name, "--yes"])
    wait_for_no_mongosemantic_search_indexes(
        db, [atlas_collection_name, f"{atlas_collection_name}_embeddings"]
    )

    # Apply single-field shadow on title — small enough that 768-d re-embed is feasible.
    r = runner.invoke(app, [
        "apply", "--collection", atlas_collection_name,
        "--field", "title",
        "--mode", "shadow",
    ])
    assert r.exit_code == 0, r.output

    r = runner.invoke(app, ["index", "--collection", atlas_collection_name])
    assert r.exit_code == 0, r.output

    # Embed enough docs for a meaningful neighborhood. ~640 covers tier-2-like density.
    provider = get_provider("local-fast")
    for _ in range(10):
        process_batch(db, provider, "atlas-tier6-pre", 64)

    # Wait for the vector index to be queryable before sampling top hits.
    shadow = db[f"{atlas_collection_name}_embeddings"]
    cfg = load_config(db, atlas_collection_name)
    idx_pre = (cfg.vector_index_names or {}).get("title") or vector_index_name(
        atlas_collection_name, "title"
    )
    wait_for_search_index_queryable(shadow, idx_pre, timeout=180)

    # Capture a control top hit BEFORE migration.
    control_query = "gangster crime"
    pre_top = _top_hit_text(runner, atlas_collection_name, control_query)
    assert pre_top, "pre-migration search returned no results — need at least one"

    # Run the migration to local-better (768-d).
    r = runner.invoke(app, [
        "migrate", "--collection", atlas_collection_name,
        "--model", "local-better",
    ])
    # Atlas M0/M2/M5 caps FTS indexes at 3 per cluster. Online migration
    # temporarily needs 4 (old vector + old BM25 + new vector + new BM25
    # during the swap window) so it's structurally impossible on the free
    # tier. Skip (not fail) when we detect that specific Atlas error —
    # users on M10+ will see the full migration path exercised.
    if r.exit_code != 0 and r.exception is not None:
        if "maximum number of FTS indexes" in str(r.exception):
            pytest.skip(
                "Online migration requires 4 concurrent FTS indexes during "
                "the swap window; Atlas M0/M2/M5 caps at 3. Re-run on M10+ "
                "to exercise this path."
            )
    assert r.exit_code == 0, r.output

    # Post-migration: cfg should reflect new model and dim.
    cfg_post = load_config(db, atlas_collection_name)
    assert cfg_post.embedding_model == "local-better"
    assert cfg_post.embedding_dim == 768

    # Wait for the migrated vector index to be queryable.
    idx_post = (cfg_post.vector_index_names or {}).get("title") or vector_index_name(
        atlas_collection_name, "title"
    )
    wait_for_search_index_queryable(shadow, idx_post, timeout=240)

    # Same control query should still find the same document at the top.
    # (The model changed so absolute scores will differ, but the top neighbor
    # for a clear keyword query like 'gangster crime' should remain stable.)
    post_top = _top_hit_text(runner, atlas_collection_name, control_query)
    assert post_top == pre_top, (
        f"top hit drifted across migration: pre={pre_top!r} post={post_top!r}"
    )

    # Archive collection from the rename should exist with the pre-migration shape.
    archives = [
        n for n in db.list_collection_names()
        if "_archive_" in n and atlas_collection_name in n
    ]
    assert archives, (
        f"no migration archive collection found; "
        f"collections present: {sorted(db.list_collection_names())}"
    )
    # The archive should still hold the old (pre-migration) 384-d vectors.
    archive_sample = db[archives[0]].find_one({"embedding": {"$exists": True}})
    assert archive_sample is not None, f"archive {archives[0]} has no embedding docs"
    assert isinstance(archive_sample["embedding"], list)
    assert len(archive_sample["embedding"]) == 384, (
        f"archive should contain 384-d (pre-migration) vectors; "
        f"got dim={len(archive_sample['embedding'])}"
    )


# Suppress unused-import warning — re module retained in case top-hit parsing
# needs to be tightened (e.g. fall back to regex against an ObjectId).
_re_unused = re
