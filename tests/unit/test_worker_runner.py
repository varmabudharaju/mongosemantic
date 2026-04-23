from datetime import datetime, timezone
from unittest.mock import MagicMock

import mongomock
import numpy as np

from mongosemantic.state import count_by_status, enqueue_delete_all, enqueue_embed
from mongosemantic.state.config_store import CollectionConfig, FieldSpec, save_config
from mongosemantic.worker.runner import process_batch


def _db():
    return mongomock.MongoClient()["test"]


def _cfg(db):
    save_config(db, CollectionConfig(
        collection="articles", mode="shadow",
        shadow_collection="articles_embeddings",
        fields=[FieldSpec(path="body")],
        embedding_model="local-fast", embedding_dim=3,
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    ))


def test_worker_embeds_and_writes_to_shadow():
    db = _db()
    _cfg(db)
    enqueue_embed(db, "articles", "doc1", "body", None, "hello", "sha1:x", "local-fast")
    provider = MagicMock()
    provider.model_name = "local-fast"
    provider.dim = 3
    provider.embed_batch = lambda texts: np.array([[1.0, 0.0, 0.0]] * len(texts), dtype=np.float32)
    n = process_batch(db, provider, worker_id="w1", batch_size=32)
    assert n == 1
    assert count_by_status(db).get("completed", 0) == 1
    row = db["articles_embeddings"].find_one({"source_id": "doc1"})
    assert row is not None
    assert row["embedding_model"] == "local-fast"
    assert len(row["embedding"]) == 3


def test_worker_delete_kind_removes_all_vectors():
    db = _db()
    _cfg(db)
    db["articles_embeddings"].insert_many([
        {"source_id": "doc1", "field_path": "body", "chunk_index": 0, "embedding_model": "local-fast"},
        {"source_id": "doc2", "field_path": "body", "chunk_index": 0, "embedding_model": "local-fast"},
    ])
    enqueue_delete_all(db, "articles", "doc1")
    provider = MagicMock()
    provider.model_name = "local-fast"
    process_batch(db, provider, "w1", 32)
    remaining = list(db["articles_embeddings"].find({}))
    assert len(remaining) == 1
    assert remaining[0]["source_id"] == "doc2"


def test_worker_fails_on_provider_error():
    db = _db()
    _cfg(db)
    enqueue_embed(db, "articles", "doc1", "body", None, "hello", "sha1:x", "local-fast")
    provider = MagicMock()
    provider.model_name = "local-fast"
    provider.dim = 3

    def boom(_):
        raise RuntimeError("provider down")

    provider.embed_batch = boom
    process_batch(db, provider, "w1", 32)
    counts = count_by_status(db)
    # First attempt fails -> back to pending (3-strike retry)
    assert counts.get("pending", 0) == 1
