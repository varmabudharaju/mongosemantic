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
    return db

def test_search_prints_results_single_collection(monkeypatch):
    db = _setup(monkeypatch)
    fake_provider = MagicMock()
    fake_provider.embed_batch = lambda texts: np.array([[1.0, 0.0, 0.0]], dtype=np.float32)
    fake_conn = MagicMock()
    fake_conn.db = db
    from mongosemantic.db.client import Topology
    fake_conn.topology = Topology.STANDALONE

    # Patch _run_one to return stub rows directly — this bypasses mongomock's
    # aggregation engine (which doesn't support $reduce / $zip) and keeps the
    # production pipeline code path real for integration tests.
    fake_rows = [
        {
            "source_id": "a",
            "source_collection": "articles",
            "field_path": "body",
            "chunk_index": 0,
            "chunk_text": "match me",
            "score": 0.97,
        },
        {
            "source_id": "b",
            "source_collection": "articles",
            "field_path": "body",
            "chunk_index": 0,
            "chunk_text": "no match",
            "score": 0.12,
        },
    ]
    with patch("mongosemantic.commands.search.MongoConnection.open", return_value=fake_conn), \
         patch("mongosemantic.commands.search.get_provider", return_value=fake_provider), \
         patch("mongosemantic.commands.search._run_one", return_value=fake_rows):
        r = runner.invoke(app, ["search", "match me", "--collection", "articles", "--limit", "2"])
        assert r.exit_code == 0, r.output
        assert "match me" in r.stdout
