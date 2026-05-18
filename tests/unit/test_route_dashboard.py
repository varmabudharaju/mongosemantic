from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import mongomock
from fastapi.testclient import TestClient

from mongosemantic.state import claim_batch, count_by_status, enqueue_embed, fail
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


def _seed(db):
    save_config(db, CollectionConfig(
        collection="articles", mode="shadow", shadow_collection="articles_embeddings",
        fields=[FieldSpec(path="body")], embedding_model="local-fast", embedding_dim=384,
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    ))


def test_dashboard_returns_overview(monkeypatch):
    client, db = _client_db(monkeypatch)
    _seed(db)
    enqueue_embed(db, "articles", "id1", "body", None, "t", "h", "local-fast")
    with patch("mongosemantic.web.routes.dashboard.MongoConnection.open", return_value=_conn(db)):
        r = client.get("/api/dashboard")
        assert r.status_code == 200
        body = r.json()
        assert body["topology"] == "standalone"
        assert body["configured_count"] == 1
        assert body["jobs"]["pending"] == 1


def test_dashboard_handles_inline_collection_without_shadow(monkeypatch):
    """An inline-mode collection has shadow_collection=None; total_embeddings must not crash."""
    client, db = _client_db(monkeypatch)
    save_config(db, CollectionConfig(
        collection="products", mode="inline", shadow_collection=None,
        fields=[FieldSpec(path="description")], embedding_model="local-fast", embedding_dim=384,
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    ))
    with patch("mongosemantic.web.routes.dashboard.MongoConnection.open", return_value=_conn(db)):
        r = client.get("/api/dashboard")
        assert r.status_code == 200


def test_retry_resets_failed(monkeypatch):
    client, db = _client_db(monkeypatch)
    _seed(db)
    enqueue_embed(db, "articles", "id1", "body", None, "t", "h", "local-fast")
    for _ in range(3):
        b = claim_batch(db, "w", 10)
        fail(db, b[0]["_id"], "boom")
    seed = client.get("/healthz")
    token = seed.cookies.get("csrftoken")
    with patch("mongosemantic.web.routes.dashboard.MongoConnection.open", return_value=_conn(db)):
        r = client.post("/api/jobs/retry", headers={CSRF_HEADER: token})
        assert r.status_code == 200
    assert count_by_status(db).get("pending", 0) == 1


def test_reindex_enqueues_jobs_for_all_docs(monkeypatch):
    client, db = _client_db(monkeypatch)
    _seed(db)
    db["articles"].insert_many([{"_id": i, "body": f"b{i}"} for i in range(3)])
    seed = client.get("/healthz")
    token = seed.cookies.get("csrftoken")
    with patch("mongosemantic.web.routes.dashboard.MongoConnection.open", return_value=_conn(db)):
        r = client.post("/api/reindex", json={"collection": "articles"},
                        headers={CSRF_HEADER: token})
        assert r.status_code == 200
        assert r.json()["enqueued"] == 3
