from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import mongomock
import numpy as np
from typer.testing import CliRunner

from mongosemantic.cli import app
from mongosemantic.commands.search import _run_one
from mongosemantic.db.client import Topology
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


def _multi_field_cfg() -> CollectionConfig:
    return CollectionConfig(
        collection="articles",
        mode="shadow",
        shadow_collection="articles_embeddings",
        fields=[FieldSpec(path="title"), FieldSpec(path="body")],
        embedding_model="local-fast",
        embedding_dim=3,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def test_run_one_searches_every_configured_field():
    """Multi-field collections must search all fields, not just fields[0]."""
    db = mongomock.MongoClient()["d"]
    cfg = _multi_field_cfg()
    save_config(db, cfg)
    calls: list[str] = []

    def fake_field(_db, _cfg, _coll, field_path, _q, _limit, _topo):
        calls.append(field_path)
        return [{"source_id": f"id-{field_path}", "field_path": field_path,
                 "chunk_text": f"hit-{field_path}", "score": 0.5}]

    with patch("mongosemantic.commands.search._run_one_field", side_effect=fake_field):
        rows = _run_one(db, cfg, "articles", [0.0, 0.0, 0.0], limit=10, topology=Topology.STANDALONE)

    assert sorted(calls) == ["body", "title"]
    assert {r["field_path"] for r in rows} == {"title", "body"}


def test_run_one_merges_and_top_k_across_fields():
    """When fields each return rows, results merge, sort by score desc, then top-limit."""
    db = mongomock.MongoClient()["d"]
    cfg = _multi_field_cfg()
    save_config(db, cfg)

    per_field = {
        "title": [{"source_id": "a", "field_path": "title", "chunk_text": "t-a", "score": 0.9},
                  {"source_id": "b", "field_path": "title", "chunk_text": "t-b", "score": 0.4}],
        "body":  [{"source_id": "c", "field_path": "body",  "chunk_text": "b-c", "score": 0.8},
                  {"source_id": "d", "field_path": "body",  "chunk_text": "b-d", "score": 0.3}],
    }

    def fake_field(_db, _cfg, _coll, field_path, _q, _limit, _topo):
        return per_field[field_path]

    with patch("mongosemantic.commands.search._run_one_field", side_effect=fake_field):
        rows = _run_one(db, cfg, "articles", [0.0, 0.0, 0.0], limit=3, topology=Topology.STANDALONE)

    assert [r["score"] for r in rows] == [0.9, 0.8, 0.4]
    assert [r["source_id"] for r in rows] == ["a", "c", "b"]
