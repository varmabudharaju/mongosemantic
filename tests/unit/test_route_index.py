from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import mongomock
from fastapi.testclient import TestClient

from mongosemantic.state.config_store import CollectionConfig, FieldSpec, save_config
from mongosemantic.web.app import create_app
from mongosemantic.web.security import CSRF_HEADER


def _client_db(monkeypatch):
    monkeypatch.setenv("MONGOSEMANTIC_URI", "mongodb://x")
    monkeypatch.setenv("MONGOSEMANTIC_DB", "d")
    monkeypatch.setenv("MONGOSEMANTIC_MODEL", "local-fast")
    db = mongomock.MongoClient()["d"]
    return TestClient(create_app()), db


def _conn(db):
    from mongosemantic.db.client import Topology
    fake = MagicMock()
    fake.db = db
    fake.topology = Topology.STANDALONE
    fake.close = MagicMock()
    return fake


def test_start_index_enqueues_jobs(monkeypatch):
    client, db = _client_db(monkeypatch)
    save_config(db, CollectionConfig(
        collection="articles", mode="shadow", shadow_collection="articles_embeddings",
        fields=[FieldSpec(path="body")], embedding_model="local-fast", embedding_dim=384,
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    ))
    db["articles"].insert_many([{"_id": i, "body": f"t{i}"} for i in range(4)])
    seed = client.get("/healthz")
    token = seed.cookies.get("csrftoken")
    with patch("mongosemantic.web.routes.index.MongoConnection.open", return_value=_conn(db)):
        r = client.post("/api/collections/articles/index", headers={CSRF_HEADER: token})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["enqueued"] == 4
    r2 = client.get("/api/collections/articles/index/progress")
    assert r2.status_code == 200
    assert r2.json()["enqueued"] == 4


def test_start_index_rejects_unconfigured(monkeypatch):
    client, db = _client_db(monkeypatch)
    seed = client.get("/healthz")
    token = seed.cookies.get("csrftoken")
    with patch("mongosemantic.web.routes.index.MongoConnection.open", return_value=_conn(db)):
        r = client.post("/api/collections/articles/index", headers={CSRF_HEADER: token})
        assert r.status_code == 400
