from datetime import datetime, timezone

import mongomock

from mongosemantic.state import count_by_status
from mongosemantic.state.config_store import CollectionConfig, FieldSpec, save_config
from mongosemantic.sync.change_stream import hash_text, process_event


def _db():
    return mongomock.MongoClient()["test"]

def _config(db, fields):
    save_config(db, CollectionConfig(
        collection="articles", mode="shadow",
        shadow_collection="articles_embeddings",
        fields=fields,
        embedding_model="local-fast", embedding_dim=384,
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    ))

def test_insert_event_enqueues_embed_job_per_field():
    db = _db()
    _config(db, [FieldSpec(path="title"), FieldSpec(path="body")])
    event = {
        "operationType": "insert",
        "ns": {"coll": "articles"},
        "documentKey": {"_id": "doc1"},
        "fullDocument": {"_id": "doc1", "title": "A", "body": "hello world"},
    }
    process_event(db, event)
    counts = count_by_status(db)
    assert counts["pending"] == 2

def test_update_event_skips_unchanged_field():
    db = _db()
    _config(db, [FieldSpec(path="body")])
    db["articles_embeddings"].insert_one({
        "source_id": "doc1", "field_path": "body", "chunk_index": 0,
        "embedding_model": "local-fast",
        "embedding_hash": hash_text("local-fast", "unchanged"),
    })
    event = {
        "operationType": "update",
        "ns": {"coll": "articles"},
        "documentKey": {"_id": "doc1"},
        "fullDocument": {"_id": "doc1", "body": "unchanged"},
    }
    process_event(db, event)
    counts = count_by_status(db)
    assert counts.get("pending", 0) == 0

def test_update_event_with_changed_field_enqueues_embed():
    db = _db()
    _config(db, [FieldSpec(path="body")])
    db["articles_embeddings"].insert_one({
        "source_id": "doc1", "field_path": "body", "chunk_index": 0,
        "embedding_model": "local-fast",
        "embedding_hash": hash_text("local-fast", "old content"),
    })
    event = {
        "operationType": "update",
        "ns": {"coll": "articles"},
        "documentKey": {"_id": "doc1"},
        "fullDocument": {"_id": "doc1", "body": "new content"},
    }
    process_event(db, event)
    counts = count_by_status(db)
    assert counts["pending"] == 1

def test_delete_event_enqueues_delete_job():
    db = _db()
    _config(db, [FieldSpec(path="body")])
    event = {
        "operationType": "delete",
        "ns": {"coll": "articles"},
        "documentKey": {"_id": "doc1"},
    }
    process_event(db, event)
    counts = count_by_status(db)
    assert counts["pending"] == 1

def test_event_for_unconfigured_collection_is_ignored():
    db = _db()
    _config(db, [FieldSpec(path="body")])
    event = {
        "operationType": "insert",
        "ns": {"coll": "other"},
        "documentKey": {"_id": "x"},
        "fullDocument": {"_id": "x", "body": "irrelevant"},
    }
    process_event(db, event)
    assert count_by_status(db) == {}
