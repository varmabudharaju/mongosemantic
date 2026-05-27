from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import mongomock
from fastapi.testclient import TestClient

from mongosemantic.state.config_store import CollectionConfig, FieldSpec, save_config
from mongosemantic.web.app import create_app


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


def _seed_shadow(db, n: int, dim: int = 4):
    save_config(db, CollectionConfig(
        collection="articles", mode="shadow", shadow_collection="articles_embeddings",
        fields=[FieldSpec(path="body")], embedding_model="local-fast", embedding_dim=dim,
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    ))
    db["articles_embeddings"].insert_many([
        {
            "source_id": f"a{i}",
            "field_path": "body",
            "chunk_index": 0,
            "embedding": [float(i), float(i / 2), float((i + 1) % 7), float(i % 3)],
            "chunk_text": f"text number {i}",
        }
        for i in range(n)
    ])


def test_visualize_returns_normalized_points(monkeypatch):
    client, db = _client_db(monkeypatch)
    _seed_shadow(db, n=12)
    with patch("mongosemantic.web.routes.visualize.MongoConnection.open", return_value=_conn(db)):
        r = client.get("/api/collections/articles/visualize?sample=12")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["field"] == "body"
        assert body["stats"]["embedding_dim"] == 4
        assert body["stats"]["sample_size"] == 12
        assert body["stats"]["k"] >= 2
        assert len(body["points"]) == 12
        assert all("cluster" in p for p in body["points"])
        assert isinstance(body.get("clusters"), list)
        for p in body["points"]:
            assert 0.0 <= p["x"] <= 1.0
            assert 0.0 <= p["y"] <= 1.0
            assert "text number" in p["text"]


def test_visualize_under_threshold_returns_message(monkeypatch):
    client, db = _client_db(monkeypatch)
    _seed_shadow(db, n=2)
    with patch("mongosemantic.web.routes.visualize.MongoConnection.open", return_value=_conn(db)):
        r = client.get("/api/collections/articles/visualize")
        assert r.status_code == 200
        body = r.json()
        assert body["points"] == []
        assert "at least" in body["message"].lower()


def test_visualize_rejects_bad_field(monkeypatch):
    client, db = _client_db(monkeypatch)
    _seed_shadow(db, n=12)
    with patch("mongosemantic.web.routes.visualize.MongoConnection.open", return_value=_conn(db)):
        r = client.get("/api/collections/articles/visualize?field=nonexistent")
        assert r.status_code == 400


def test_visualize_inline_collection_reads_msem(monkeypatch):
    client, db = _client_db(monkeypatch)
    save_config(db, CollectionConfig(
        collection="products", mode="inline", shadow_collection=None,
        fields=[FieldSpec(path="description")], embedding_model="local-fast", embedding_dim=4,
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    ))
    db["products"].insert_many([
        {
            "_id": f"p{i}", "name": f"prod{i}",
            "_msem": {"description": {
                "embedding": [float(i), float(i + 1), float((i * 3) % 5), float(i % 4)],
                "text": f"desc {i}",
            }},
        }
        for i in range(8)
    ])
    with patch("mongosemantic.web.routes.visualize.MongoConnection.open", return_value=_conn(db)):
        r = client.get("/api/collections/products/visualize")
        assert r.status_code == 200
        body = r.json()
        assert body["field"] == "description"
        assert len(body["points"]) == 8
