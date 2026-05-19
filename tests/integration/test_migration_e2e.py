"""Online model migration end-to-end against a real replica set.

Verifies the real Mongo `renameCollection` path (which mongomock can't
emulate) and confirms that semantic search keeps returning sane results
after the swap.
"""
from datetime import datetime, timezone

import pytest

from mongosemantic.commands.search import _run_one
from mongosemantic.db.client import MongoConnection
from mongosemantic.embeddings.provider import get_provider
from mongosemantic.migration import migrate_collection
from mongosemantic.state import load_config
from mongosemantic.state.config_store import CollectionConfig, FieldSpec, save_config
from mongosemantic.sync.enqueue import enqueue_for_doc
from mongosemantic.worker.runner import process_batch


@pytest.mark.integration
def test_migrate_local_fast_to_local_better_round_trip(clean_db):
    db = clean_db

    save_config(db, CollectionConfig(
        collection="articles", mode="shadow", shadow_collection="articles_embeddings",
        fields=[FieldSpec(path="body")], embedding_model="local-fast", embedding_dim=384,
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    ))
    db["articles"].insert_many([
        {"_id": "a", "body": "wild yeast sourdough bread starter, twelve-hour feeding schedule"},
        {"_id": "b", "body": "trail running shoes with aggressive lugs and waterproof upper"},
        {"_id": "c", "body": "espresso machine PID temperature control for shot consistency"},
    ])

    # Bake the initial embeddings with local-fast.
    cfg = load_config(db, "articles")
    for doc in db["articles"].find({}):
        enqueue_for_doc(db, cfg, source_id=doc["_id"], doc=doc)
    process_batch(db, get_provider("local-fast"), "w1", 32)

    # Pre-migration search sanity check
    before = _run_one(
        db, cfg, "articles",
        get_provider("local-fast").embed("homemade bread").tolist(),
        3, conn_topology(db),
    )
    assert any("sourdough" in r.get("chunk_text", "") for r in before)

    # Migrate to local-better (768 dim).
    conn = MongoConnection.open("mongodb://localhost:27117/?replicaSet=rs0", db.name)
    try:
        result = migrate_collection(conn, "articles", "local-better")
    finally:
        conn.close()

    assert result.old_model == "local-fast" and result.new_model == "local-better"
    assert result.documents == 3

    # The live shadow now holds local-better embeddings (768d, not 384d).
    cfg_after = load_config(db, "articles")
    assert cfg_after.embedding_model == "local-better"
    assert cfg_after.embedding_dim == 768
    sample = db[cfg_after.shadow_collection].find_one({})
    assert len(sample["embedding"]) == 768

    # The archive collection still has the old 384d embeddings.
    assert result.archive_collection in db.list_collection_names()
    archived = db[result.archive_collection].find_one({})
    assert len(archived["embedding"]) == 384

    # And search still works after the rename.
    after = _run_one(
        db, cfg_after, "articles",
        get_provider("local-better").embed("homemade bread").tolist(),
        3, conn_topology(db),
    )
    assert any("sourdough" in r.get("chunk_text", "") for r in after)


def conn_topology(db):
    """Helper: open a real connection just to grab the Topology enum value."""
    conn = MongoConnection.open("mongodb://localhost:27117/?replicaSet=rs0", db.name)
    try:
        return conn.topology
    finally:
        conn.close()
