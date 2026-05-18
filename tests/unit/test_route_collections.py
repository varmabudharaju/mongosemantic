from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import mongomock
from fastapi.testclient import TestClient

from mongosemantic.state.config_store import CollectionConfig, FieldSpec, save_config
from mongosemantic.web.app import create_app


def _client_and_db(monkeypatch):
    monkeypatch.setenv("MONGOSEMANTIC_URI", "mongodb://x")
    monkeypatch.setenv("MONGOSEMANTIC_DB", "d")
    monkeypatch.setenv("MONGOSEMANTIC_MODEL", "local-fast")
    db = mongomock.MongoClient()["d"]
    return TestClient(create_app()), db


def _patch_conn(db):
    from mongosemantic.db.client import Topology
    fake_conn = MagicMock()
    fake_conn.db = db
    fake_conn.topology = Topology.STANDALONE
    fake_conn.close = MagicMock()
    return fake_conn


def test_collections_list_includes_all_user_collections(monkeypatch):
    client, db = _client_and_db(monkeypatch)
    db["articles"].insert_many([{"_id": i, "body": f"b{i}"} for i in range(3)])
    db["products"].insert_one({"_id": 1, "name": "x"})
    save_config(db, CollectionConfig(
        collection="articles", mode="shadow", shadow_collection="articles_embeddings",
        fields=[FieldSpec(path="body")], embedding_model="local-fast", embedding_dim=384,
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    ))
    with patch(
        "mongosemantic.web.routes.collections.MongoConnection.open",
        return_value=_patch_conn(db),
    ):
        r = client.get("/api/collections")
        assert r.status_code == 200
        rows = {row["name"]: row for row in r.json()["collections"]}
        assert "articles" in rows and "products" in rows
        assert rows["articles"]["status"] == "configured"
        assert rows["articles"]["fields_count"] == 1
        assert rows["products"]["status"] == "not_configured"


def test_inspect_returns_field_stats(monkeypatch):
    client, db = _client_and_db(monkeypatch)
    db["articles"].insert_many([
        {"title": "a", "body": "lorem ipsum dolor sit amet" * 20} for _ in range(20)
    ])
    with patch(
        "mongosemantic.web.routes.collections.MongoConnection.open",
        return_value=_patch_conn(db),
    ):
        r = client.get("/api/collections/articles/inspect?sample=20")
        assert r.status_code == 200
        body = r.json()
        paths = {f["path"] for f in body["fields"]}
        assert "title" in paths and "body" in paths


def test_inspect_rejects_bad_collection_name(monkeypatch):
    client, db = _client_and_db(monkeypatch)
    with patch(
        "mongosemantic.web.routes.collections.MongoConnection.open",
        return_value=_patch_conn(db),
    ):
        r = client.get("/api/collections/$evil/inspect")
        assert r.status_code == 400
