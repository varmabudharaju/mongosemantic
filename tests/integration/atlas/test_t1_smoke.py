"""Tier 1 — Smoke: connectivity, topology detection, apply/index/worker/search."""
from __future__ import annotations

import pytest
from typer.testing import CliRunner

from mongosemantic.cli import app
from mongosemantic.db.client import Topology
from mongosemantic.embeddings.provider import get_provider
from mongosemantic.state import count_by_status
from mongosemantic.worker.runner import process_batch


@pytest.mark.atlas
def test_topology_is_atlas(atlas_topology: Topology):
    assert atlas_topology is Topology.ATLAS


@pytest.mark.atlas
def test_dataset_preflight(atlas_dataset_loaded):
    assert atlas_dataset_loaded.estimated_document_count() >= 3000


@pytest.mark.atlas
def test_smoke_apply_index_worker_search(
    atlas_client,
    atlas_dataset_loaded,
    env_pointing_at_atlas,
    atlas_db_name,
    atlas_collection_name,
):
    runner = CliRunner()
    db = atlas_client[atlas_db_name]

    # Clean slate: tear down any prior config for this collection.
    runner.invoke(app, ["teardown", "--collection", atlas_collection_name, "--yes"])

    # Apply: shadow mode, single field (title).
    r = runner.invoke(app, [
        "apply", "--collection", atlas_collection_name,
        "--field", "title",
        "--mode", "shadow",
    ])
    assert r.exit_code == 0, r.output

    # Index: enqueue jobs for all ~3,483 docs.
    r = runner.invoke(app, ["index", "--collection", atlas_collection_name])
    assert r.exit_code == 0, r.output
    pending = count_by_status(db).get("pending", 0)
    assert pending > 3000, f"expected >3000 pending jobs, got {pending}"

    # Worker — process one batch to keep the smoke quick.
    provider = get_provider("local-fast")
    process_batch(db, provider, "atlas-smoke", 64)
    assert db[f"{atlas_collection_name}_embeddings"].count_documents({}) >= 64

    # Search — even with partial embedding coverage, top-k should return hits.
    r = runner.invoke(app, [
        "search", "heist gone wrong",
        "--collection", atlas_collection_name,
        "--limit", "3",
    ])
    assert r.exit_code == 0, r.output
    assert "score" in r.output.lower() or "results" in r.output.lower()
