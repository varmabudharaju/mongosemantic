from datetime import datetime

import mongomock

from mongosemantic.state.config_store import (
    CollectionConfig,
    FieldSpec,
    disable_config,
    list_configured,
    load_config,
    save_config,
)


def _db():
    return mongomock.MongoClient()["test"]

def test_save_and_load_config():
    db = _db()
    cfg = CollectionConfig(
        collection="articles",
        mode="shadow",
        shadow_collection="articles_embeddings",
        fields=[FieldSpec(path="body", chunked=True, chunk_size=256, chunk_overlap=32)],
        embedding_model="local-fast",
        embedding_dim=384,
        created_at=datetime(2026, 4, 22),
        updated_at=datetime(2026, 4, 22),
    )
    save_config(db, cfg)
    loaded = load_config(db, "articles")
    assert loaded is not None
    assert loaded.collection == "articles"
    assert loaded.fields[0].path == "body"
    assert loaded.fields[0].chunked is True

def test_load_missing_returns_none():
    db = _db()
    assert load_config(db, "nope") is None

def test_list_configured_returns_only_active():
    db = _db()
    save_config(db, CollectionConfig(
        collection="a", mode="shadow", shadow_collection="a_embeddings",
        fields=[FieldSpec(path="body")], embedding_model="local-fast", embedding_dim=384,
        created_at=datetime.now(), updated_at=datetime.now(),
    ))
    save_config(db, CollectionConfig(
        collection="b", mode="shadow", shadow_collection="b_embeddings",
        fields=[FieldSpec(path="body")], embedding_model="local-fast", embedding_dim=384,
        created_at=datetime.now(), updated_at=datetime.now(),
    ))
    names = {c.collection for c in list_configured(db)}
    assert names == {"a", "b"}

def test_disable_config():
    db = _db()
    save_config(db, CollectionConfig(
        collection="a", mode="shadow", shadow_collection="a_embeddings",
        fields=[FieldSpec(path="body")], embedding_model="local-fast", embedding_dim=384,
        created_at=datetime.now(), updated_at=datetime.now(),
    ))
    disable_config(db, "a")
    assert load_config(db, "a") is None
