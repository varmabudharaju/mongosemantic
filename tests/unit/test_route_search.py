from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import mongomock
import numpy as np
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


def test_search_returns_rows(monkeypatch):
    client, db = _client_db(monkeypatch)
    save_config(db, CollectionConfig(
        collection="articles", mode="shadow", shadow_collection="articles_embeddings",
        fields=[FieldSpec(path="body")], embedding_model="local-fast", embedding_dim=3,
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    ))
    fake_provider = MagicMock()
    fake_provider.embed = lambda q: np.array([1.0, 0.0, 0.0], dtype=np.float32)
    fake_rows = [
        {"source_id": "a", "source_collection": "articles", "field_path": "body",
         "chunk_index": 0, "chunk_text": "match me", "score": 0.97},
    ]
    with patch("mongosemantic.web.routes.search.MongoConnection.open", return_value=_conn(db)), \
         patch("mongosemantic.web.routes.search.get_provider", return_value=fake_provider), \
         patch("mongosemantic.web.routes.search._run_one", return_value=fake_rows):
        r = client.get("/api/search?q=hello&collection=articles&limit=10")
        assert r.status_code == 200
        body = r.json()
        assert body["query"] == "hello"
        assert len(body["rows"]) == 1
        assert body["rows"][0]["chunk_text"] == "match me"


def test_search_serializes_doc_with_bson_binary_field(monkeypatch):
    """Regression: source documents may contain BSON Binary values (e.g.
    Atlas's pre-computed `plot_embedding` on `sample_mflix.embedded_movies`
    is stored as BSON Binary). The serializer must not pass those through
    to pydantic — they'd raise `UnicodeDecodeError: invalid utf-8` during
    JSON encoding and break the entire web search route.

    Surfaced by tier 7 (UI smoke) of the Atlas verification.
    """
    from bson.binary import Binary

    client, db = _client_db(monkeypatch)
    save_config(db, CollectionConfig(
        collection="movies", mode="shadow", shadow_collection="movies_embeddings",
        fields=[FieldSpec(path="title")], embedding_model="local-fast", embedding_dim=3,
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    ))
    fake_provider = MagicMock()
    fake_provider.embed = lambda q: np.array([1.0, 0.0, 0.0], dtype=np.float32)
    # `source_doc` mimics the embedded_movies shape — bytes / Binary fields
    # that pydantic v2 will choke on if not filtered.
    fake_rows = [{
        "source_id": "m1", "source_collection": "movies", "field_path": "title",
        "chunk_index": 0, "chunk_text": "Scarface", "score": 0.87,
        "source_doc": {
            "_id": "m1",
            "title": "Scarface",
            "plot_embedding": Binary(b"\x27\x00\x13\x42\x7f\xbc\x88\x6c" * 100),
            "plot_embedding_voyage_3_large": b"\x27\x00\xa2\xc7\xf5\xbc\x9f\x3b" * 200,
            "genres": ["Action", "Crime"],
        },
    }]
    with patch("mongosemantic.web.routes.search.MongoConnection.open", return_value=_conn(db)), \
         patch("mongosemantic.web.routes.search.get_provider", return_value=fake_provider), \
         patch("mongosemantic.web.routes.search._run_one", return_value=fake_rows):
        r = client.get("/api/search?q=heists&collection=movies&limit=5")
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["rows"]) == 1
    src = body["rows"][0]["source_doc"]
    # Useful text fields preserved.
    assert src["title"] == "Scarface"
    assert src["genres"] == ["Action", "Crime"]
    # Binary fields dropped from the response payload (they're either
    # filtered out or stringified — never raw bytes).
    assert not isinstance(src.get("plot_embedding"), (bytes, bytearray))
    assert not isinstance(src.get("plot_embedding_voyage_3_large"), (bytes, bytearray))


def test_search_rejects_unconfigured(monkeypatch):
    client, db = _client_db(monkeypatch)
    fake_provider = MagicMock()
    fake_provider.embed = lambda q: np.array([1.0, 0.0, 0.0], dtype=np.float32)
    with patch("mongosemantic.web.routes.search.MongoConnection.open", return_value=_conn(db)), \
         patch("mongosemantic.web.routes.search.get_provider", return_value=fake_provider):
        r = client.get("/api/search?q=hello&collection=missing")
        assert r.status_code == 400
