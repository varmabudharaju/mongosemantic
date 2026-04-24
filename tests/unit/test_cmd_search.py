from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import mongomock
import numpy as np
from typer.testing import CliRunner

from mongosemantic.cli import app
from mongosemantic.state.config_store import CollectionConfig, FieldSpec, save_config

runner = CliRunner()

def _setup(monkeypatch):
    monkeypatch.setenv("MONGOSEMANTIC_URI", "mongodb://fake")
    monkeypatch.setenv("MONGOSEMANTIC_DB", "d")
    monkeypatch.setenv("MONGOSEMANTIC_MODEL", "local-fast")
    db = mongomock.MongoClient()["d"]
    save_config(db, CollectionConfig(
        collection="articles", mode="shadow", shadow_collection="articles_embeddings",
        fields=[FieldSpec(path="body")], embedding_model="local-fast", embedding_dim=3,
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    ))
    db["articles_embeddings"].insert_many([
        {"source_id": "a", "source_collection": "articles", "field_path": "body",
         "chunk_index": 0, "chunk_text": "match me",
         "embedding": [1.0, 0.0, 0.0], "embedding_model": "local-fast", "embedding_dim": 3},
        {"source_id": "b", "source_collection": "articles", "field_path": "body",
         "chunk_index": 0, "chunk_text": "no match",
         "embedding": [0.0, 1.0, 0.0], "embedding_model": "local-fast", "embedding_dim": 3},
    ])
    db["articles"].insert_many([
        {"_id": "a", "body": "match me"},
        {"_id": "b", "body": "no match"},
    ])
    return db

def test_search_prints_results_single_collection(monkeypatch):
    db = _setup(monkeypatch)
    fake_provider = MagicMock()
    fake_provider.embed_batch = lambda texts: np.array([[1.0, 0.0, 0.0]], dtype=np.float32)
    fake_conn = MagicMock()
    fake_conn.db = db
    from mongosemantic.db.client import Topology
    fake_conn.topology = Topology.STANDALONE
    with patch("mongosemantic.commands.search.MongoConnection.open", return_value=fake_conn), \
         patch("mongosemantic.commands.search.get_provider", return_value=fake_provider):
        r = runner.invoke(app, ["search", "match me", "--collection", "articles", "--limit", "2"])
        assert r.exit_code == 0, r.output
        assert "match me" in r.stdout
