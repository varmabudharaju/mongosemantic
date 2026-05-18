"""Inline-mode tests: embeddings live on the source doc under `_msem.{field}`."""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import mongomock
import numpy as np
from typer.testing import CliRunner

from mongosemantic.cli import app
from mongosemantic.db.client import Topology
from mongosemantic.db.queries import inline_field_key
from mongosemantic.state import count_by_status, enqueue_delete_all, enqueue_embed, load_config
from mongosemantic.state.config_store import CollectionConfig, FieldSpec, save_config
from mongosemantic.sync.change_stream import hash_text, process_event
from mongosemantic.sync.enqueue import enqueue_for_doc
from mongosemantic.worker.runner import process_batch

runner = CliRunner()


def _db():
    return mongomock.MongoClient()["d"]


def _inline_cfg(db, fields=("body",)):
    save_config(
        db,
        CollectionConfig(
            collection="articles",
            mode="inline",
            shadow_collection=None,
            fields=[FieldSpec(path=p) for p in fields],
            embedding_model="local-fast",
            embedding_dim=3,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        ),
    )


def _patch_env(monkeypatch):
    monkeypatch.setenv("MONGOSEMANTIC_URI", "mongodb://fake")
    monkeypatch.setenv("MONGOSEMANTIC_DB", "d")
    monkeypatch.setenv("MONGOSEMANTIC_MODEL", "local-fast")


# --- apply ---

def test_apply_inline_saves_inline_config_without_shadow(monkeypatch):
    _patch_env(monkeypatch)
    fake_db = _db()
    fake_conn = MagicMock()
    fake_conn.db = fake_db
    fake_conn.topology = Topology.STANDALONE
    with patch("mongosemantic.commands.apply.MongoConnection.open", return_value=fake_conn):
        r = runner.invoke(
            app,
            ["apply", "--collection", "articles", "--field", "body", "--mode", "inline"],
        )
        assert r.exit_code == 0, r.output
    cfg = load_config(fake_db, "articles")
    assert cfg.mode == "inline"
    assert cfg.shadow_collection is None


def test_apply_inline_with_chunked_is_rejected(monkeypatch):
    _patch_env(monkeypatch)
    fake_db = _db()
    fake_conn = MagicMock()
    fake_conn.db = fake_db
    fake_conn.topology = Topology.STANDALONE
    with patch("mongosemantic.commands.apply.MongoConnection.open", return_value=fake_conn):
        r = runner.invoke(
            app,
            ["apply", "--collection", "articles", "--field", "body",
             "--mode", "inline", "--chunked"],
        )
        # Either non-zero exit or a clear error message — but config must NOT be saved as inline+chunked.
        cfg = load_config(fake_db, "articles")
        if cfg is not None:
            assert not (cfg.mode == "inline" and cfg.fields[0].chunked)
        assert "chunk" in r.output.lower() and ("shadow" in r.output.lower() or "inline" in r.output.lower())


# --- enqueue dedup ---

def test_enqueue_inline_skips_when_source_hash_matches():
    db = _db()
    _inline_cfg(db)
    cfg = load_config(db, "articles")
    key = inline_field_key("body")
    full_doc = {
        "_id": "doc1",
        "body": "same text",
        "_msem": {key: {"hash": hash_text("local-fast", "same text"),
                        "embedding": [0.0, 0.0, 0.0], "model": "local-fast"}},
    }
    db["articles"].insert_one(full_doc)
    n = enqueue_for_doc(db, cfg, source_id="doc1", doc=full_doc)
    assert n == 0


def test_enqueue_inline_enqueues_when_source_hash_changes():
    db = _db()
    _inline_cfg(db)
    cfg = load_config(db, "articles")
    key = inline_field_key("body")
    full_doc = {
        "_id": "doc1",
        "body": "new text",
        "_msem": {key: {"hash": "sha1:stale", "embedding": [0.0] * 3,
                        "model": "local-fast"}},
    }
    db["articles"].insert_one(full_doc)
    n = enqueue_for_doc(db, cfg, source_id="doc1", doc=full_doc)
    assert n == 1


# --- worker write/delete ---

def test_worker_inline_writes_to_source_doc():
    db = _db()
    _inline_cfg(db)
    db["articles"].insert_one({"_id": "doc1", "body": "hello"})
    enqueue_embed(db, "articles", "doc1", "body", None, "hello",
                  hash_text("local-fast", "hello"), "local-fast")
    provider = MagicMock()
    provider.model_name = "local-fast"
    provider.dim = 3
    provider.embed_batch = lambda texts: np.array(
        [[0.5, 0.25, 0.125]] * len(texts), dtype=np.float32
    )
    process_batch(db, provider, worker_id="w1", batch_size=32)
    src = db["articles"].find_one({"_id": "doc1"})
    key = inline_field_key("body")
    assert "_msem" in src and key in src["_msem"]
    assert src["_msem"][key]["embedding"] == [0.5, 0.25, 0.125]
    assert src["_msem"][key]["model"] == "local-fast"
    assert src["_msem"][key]["hash"].startswith("sha1:")
    # shadow collection should not exist (or be empty)
    assert "articles_embeddings" not in db.list_collection_names() or \
        db["articles_embeddings"].count_documents({}) == 0


def test_worker_inline_delete_clears_msem():
    db = _db()
    _inline_cfg(db)
    key = inline_field_key("body")
    db["articles"].insert_one({
        "_id": "doc1", "body": "x",
        "_msem": {key: {"embedding": [0.0] * 3, "hash": "sha1:abc"}},
    })
    enqueue_delete_all(db, "articles", "doc1")
    provider = MagicMock()
    process_batch(db, provider, worker_id="w1", batch_size=32)
    src = db["articles"].find_one({"_id": "doc1"})
    assert "_msem" not in src or src.get("_msem") in (None, {})


# --- self-write filter in change stream ---

def test_change_stream_ignores_pure_msem_update():
    db = _db()
    _inline_cfg(db)
    key = inline_field_key("body")
    # Update event whose updateDescription touches ONLY _msem.* — that's us writing back.
    event = {
        "operationType": "update",
        "ns": {"coll": "articles"},
        "documentKey": {"_id": "doc1"},
        "updateDescription": {
            "updatedFields": {f"_msem.{key}.embedding": [0.0, 0.0, 0.0],
                              f"_msem.{key}.hash": "sha1:abc"},
            "removedFields": [],
        },
        "fullDocument": {
            "_id": "doc1", "body": "unchanged",
            "_msem": {key: {"embedding": [0.0, 0.0, 0.0], "hash": "sha1:abc"}},
        },
    }
    process_event(db, event)
    assert count_by_status(db) == {}


def test_change_stream_processes_real_user_update():
    db = _db()
    _inline_cfg(db)
    event = {
        "operationType": "update",
        "ns": {"coll": "articles"},
        "documentKey": {"_id": "doc1"},
        "updateDescription": {
            "updatedFields": {"body": "new text from user"},
            "removedFields": [],
        },
        "fullDocument": {"_id": "doc1", "body": "new text from user"},
    }
    process_event(db, event)
    counts = count_by_status(db)
    assert counts.get("pending", 0) == 1


# --- search pipeline shape (inline) ---

def test_inline_search_brute_pipeline_uses_source_collection():
    from mongosemantic.search.inline import build_inline_brute_pipeline
    pipeline = build_inline_brute_pipeline(
        field_path="body", query_vector=[0.0, 0.0, 0.0], limit=5,
    )
    # Match stage filters on the inline embedding path
    match = next(s for s in pipeline if "$match" in s)
    key = inline_field_key("body")
    assert f"_msem.{key}.embedding" in str(match)
    # Result has chunk_text and score
    proj = next(s for s in pipeline if "$project" in s)
    assert "score" in proj["$project"]
    assert "chunk_text" in proj["$project"]
    # No $lookup needed — source doc IS the result
    assert not any("$lookup" in s for s in pipeline)


def test_inline_search_atlas_pipeline_points_at_inline_path():
    from mongosemantic.search.inline import build_inline_atlas_pipeline
    pipeline = build_inline_atlas_pipeline(
        field_path="body",
        query_vector=[0.0, 0.0, 0.0],
        limit=5,
        index_name="idx",
    )
    key = inline_field_key("body")
    assert pipeline[0]["$vectorSearch"]["path"] == f"_msem.{key}.embedding"
    assert pipeline[0]["$vectorSearch"]["index"] == "idx"
