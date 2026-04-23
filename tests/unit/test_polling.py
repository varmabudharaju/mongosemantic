from datetime import datetime, timedelta, timezone

import mongomock

from mongosemantic.state import count_by_status
from mongosemantic.state.config_store import CollectionConfig, FieldSpec, save_config
from mongosemantic.state.resume_tokens import load_polling_watermark
from mongosemantic.sync.polling import poll_once


def _db():
    return mongomock.MongoClient()["test"]


def _config(db):
    save_config(db, CollectionConfig(
        collection="articles", mode="shadow",
        shadow_collection="articles_embeddings",
        fields=[FieldSpec(path="body")],
        embedding_model="local-fast", embedding_dim=384,
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    ))


def test_polling_picks_up_new_docs():
    db = _db()
    _config(db)
    now = datetime.now(timezone.utc)
    db["articles"].insert_many([
        {"_id": "a", "body": "one", "updated_at": now - timedelta(seconds=1)},
        {"_id": "b", "body": "two", "updated_at": now},
    ])
    poll_once(db, "articles", watermark_field="updated_at")
    assert count_by_status(db).get("pending", 0) == 2


def test_polling_skips_docs_below_watermark():
    db = _db()
    _config(db)
    t0 = datetime.now(timezone.utc) - timedelta(seconds=10)
    t1 = datetime.now(timezone.utc)
    db["articles"].insert_one({"_id": "a", "body": "one", "updated_at": t0})
    poll_once(db, "articles", watermark_field="updated_at")
    assert count_by_status(db).get("pending", 0) == 1
    db["articles"].insert_one({"_id": "b", "body": "two", "updated_at": t1})
    poll_once(db, "articles", watermark_field="updated_at")
    assert count_by_status(db).get("pending", 0) == 2


def test_polling_watermark_updates():
    db = _db()
    _config(db)
    now = datetime.now(timezone.utc)
    db["articles"].insert_one({"_id": "a", "body": "one", "updated_at": now})
    poll_once(db, "articles", watermark_field="updated_at")
    wm = load_polling_watermark(db, "articles")
    assert wm is not None
