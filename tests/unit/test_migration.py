"""Unit tests for online model migration on shadow-mode collections.

We use mongomock as the database and a stub embedding provider so the test
suite stays offline. mongomock supports `renameCollection` via the admin
command path, which is what `migrate_collection` calls.
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock

import mongomock
import numpy as np
import pytest

from mongosemantic.config import MODEL_DIMS
from mongosemantic.db.client import Topology
from mongosemantic.migration import MigrationError, migrate_collection
from mongosemantic.state import load_config
from mongosemantic.state.config_store import CollectionConfig, FieldSpec, save_config


def _conn(db, topology: Topology = Topology.REPLICA_SET):
    fake = MagicMock()
    fake.db = db
    fake.topology = topology
    return fake


def _shadow_cfg(db, model="local-fast", dim=384):
    save_config(db, CollectionConfig(
        collection="articles", mode="shadow", shadow_collection="articles_embeddings",
        fields=[FieldSpec(path="body")], embedding_model=model, embedding_dim=dim,
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    ))


def _inline_cfg(db):
    save_config(db, CollectionConfig(
        collection="products", mode="inline", shadow_collection=None,
        fields=[FieldSpec(path="description")], embedding_model="local-fast", embedding_dim=384,
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    ))


def _stub_provider(dim: int):
    p = MagicMock()
    p.model_name = "stub"
    p.dim = dim
    p.embed_batch = lambda texts: np.array(
        [[float(i % 7) / 7.0] * dim for i, _ in enumerate(texts)], dtype=np.float32
    )
    return p


def test_migrate_rejects_inline_mode():
    client = mongomock.MongoClient()
    db = client["test"]
    _inline_cfg(db)
    with pytest.raises(MigrationError, match="shadow"):
        migrate_collection(_conn(db), "products", "local-better")


def test_migrate_rejects_unknown_model():
    client = mongomock.MongoClient()
    db = client["test"]
    _shadow_cfg(db)
    with pytest.raises(MigrationError, match="unknown model"):
        migrate_collection(_conn(db), "articles", "imaginary-mega-3000")


def test_migrate_rejects_when_already_on_target_model():
    client = mongomock.MongoClient()
    db = client["test"]
    _shadow_cfg(db, model="local-fast", dim=MODEL_DIMS["local-fast"])
    with pytest.raises(MigrationError, match="already"):
        migrate_collection(_conn(db), "articles", "local-fast")


def test_migrate_rejects_when_collection_not_configured():
    client = mongomock.MongoClient()
    db = client["test"]
    with pytest.raises(MigrationError, match="not configured"):
        migrate_collection(_conn(db), "missing", "local-better")


def _patch_rename_for_mongomock(monkeypatch, db):
    """mongomock doesn't implement db.command, so substitute a manual copy.
    The integration test in tests/integration covers the real renameCollection path."""
    def fake_rename(_db, src, dst, drop_target=False):
        if dst in db.list_collection_names() and drop_target:
            db.drop_collection(dst)
        for doc in db[src].find({}):
            db[dst].insert_one(dict(doc))
        db.drop_collection(src)
    monkeypatch.setattr("mongosemantic.migration.migrate._atomic_rename", fake_rename)


def test_migrate_swaps_shadow_atomically_and_updates_cfg(monkeypatch):
    client = mongomock.MongoClient()
    db = client["test"]
    _shadow_cfg(db, model="local-fast", dim=384)
    db["articles"].insert_many([
        {"_id": i, "body": f"article body number {i}"} for i in range(4)
    ])
    # Pre-existing shadow row from the old model — should survive only in archive
    db["articles_embeddings"].insert_one({
        "source_id": 0, "field_path": "body", "chunk_index": 0,
        "embedding_model": "local-fast", "embedding_hash": "sha1:legacy",
    })

    monkeypatch.setattr(
        "mongosemantic.migration.migrate.get_provider",
        lambda model: _stub_provider(MODEL_DIMS[model]),
    )
    _patch_rename_for_mongomock(monkeypatch, db)

    result = migrate_collection(_conn(db), "articles", "local-better")

    # cfg now points at the new model + new dim
    cfg = load_config(db, "articles")
    assert cfg.embedding_model == "local-better"
    assert cfg.embedding_dim == MODEL_DIMS["local-better"]
    assert cfg.migrated_at is not None

    # Live shadow holds new-model embeddings; old shadow is now an archive
    live = list(db[cfg.shadow_collection].find({}))
    assert len(live) == 4
    assert all(row["embedding_model"] == "local-better" for row in live)
    assert all(len(row["embedding"]) == MODEL_DIMS["local-better"] for row in live)
    assert result.archive_collection in db.list_collection_names()
    # The legacy row landed in the archive, not in the live shadow
    archived = list(db[result.archive_collection].find({}))
    assert any(r.get("embedding_hash") == "sha1:legacy" for r in archived)

    # Result summary is sane
    assert result.documents == 4
    assert result.chunks_written == 4
    assert result.old_model == "local-fast"
    assert result.new_model == "local-better"


def test_migrate_resume_skips_already_embedded_chunks(monkeypatch):
    """Re-running a migration that was interrupted should not re-embed
    documents that already landed in the temp shadow."""
    client = mongomock.MongoClient()
    db = client["test"]
    _shadow_cfg(db)
    db["articles"].insert_many([{"_id": i, "body": f"b{i}"} for i in range(3)])

    calls: list[int] = []

    def counting_provider(model):
        p = _stub_provider(MODEL_DIMS[model])
        orig = p.embed_batch
        def wrapped(texts):
            calls.append(len(texts))
            return orig(texts)
        p.embed_batch = wrapped
        return p

    monkeypatch.setattr("mongosemantic.migration.migrate.get_provider", counting_provider)
    _patch_rename_for_mongomock(monkeypatch, db)

    # First migration — embeds all 3
    migrate_collection(_conn(db), "articles", "local-better")
    first_run_calls = sum(calls)
    assert first_run_calls == 3
    calls.clear()

    # Reset cfg back to old model (simulating "we want to redo, archive exists")
    # and re-create the source state but reuse the live shadow as if it were
    # the resume target by renaming it back to the next migration's temp name.
    # Simpler: assert the second migration (to a THIRD model) skips chunks
    # whose hash already matches in the temp collection — by reusing the
    # provider's deterministic output, the second pass writes the same hash
    # and we observe re-embedding count.
    #
    # For this test it's enough to confirm the bulk loop *would* skip rows
    # with matching (source_id, field_path, chunk_index, model, hash) tuples.
    # We exercise that by inserting a matching hash into a brand-new temp
    # collection and calling _embed_one_doc directly.
    from mongosemantic.migration.migrate import _embed_one_doc
    from mongosemantic.sync.change_stream import hash_text

    cfg = load_config(db, "articles")
    temp = "articles_embeddings_resume_test"
    # Pre-seed: doc 0's body=b0, model=local-fast (target of this hypothetical migration)
    db[temp].insert_one({
        "source_id": 0, "field_path": "body", "chunk_index": 0,
        "embedding_model": "local-fast",
        "embedding_hash": hash_text("local-fast", "b0"),
    })
    provider = _stub_provider(MODEL_DIMS["local-fast"])
    calls_resume: list[int] = []
    orig = provider.embed_batch
    def wrapped(texts):
        calls_resume.append(len(texts))
        return orig(texts)
    provider.embed_batch = wrapped

    n = _embed_one_doc(db, cfg, "local-fast", temp,
                      {"_id": 0, "body": "b0"}, provider)
    assert n == 0  # skipped, no new chunks
    assert calls_resume == []
