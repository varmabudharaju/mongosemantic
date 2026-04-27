from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import mongomock
from typer.testing import CliRunner

from mongosemantic.cli import app
from mongosemantic.state import claim_batch, count_by_status, enqueue_embed, fail
from mongosemantic.state.config_store import CollectionConfig, FieldSpec, save_config

runner = CliRunner()


def _env(monkeypatch):
    monkeypatch.setenv("MONGOSEMANTIC_URI", "mongodb://fake")
    monkeypatch.setenv("MONGOSEMANTIC_DB", "d")
    monkeypatch.setenv("MONGOSEMANTIC_MODEL", "local-fast")


def _seed(db):
    save_config(db, CollectionConfig(
        collection="articles", mode="shadow", shadow_collection="articles_embeddings",
        fields=[FieldSpec(path="body")], embedding_model="local-fast", embedding_dim=384,
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    ))


def test_status_prints_counts(monkeypatch):
    _env(monkeypatch)
    db = mongomock.MongoClient()["d"]
    _seed(db)
    enqueue_embed(db, "articles", "id1", "body", None, "t", "h", "local-fast")
    fake_conn = MagicMock()
    fake_conn.db = db
    from mongosemantic.db.client import Topology
    fake_conn.topology = Topology.STANDALONE
    with patch("mongosemantic.commands.status.MongoConnection.open", return_value=fake_conn):
        r = runner.invoke(app, ["status"])
        assert r.exit_code == 0
        assert "pending" in r.output.lower()
        assert "1" in r.output


def test_retry_resets_failed(monkeypatch):
    _env(monkeypatch)
    db = mongomock.MongoClient()["d"]
    _seed(db)
    enqueue_embed(db, "articles", "id1", "body", None, "t", "h", "local-fast")
    for _ in range(3):
        batch = claim_batch(db, "w", 10)
        fail(db, batch[0]["_id"], "boom")
    assert count_by_status(db).get("failed", 0) == 1
    fake_conn = MagicMock()
    fake_conn.db = db
    with patch("mongosemantic.commands.retry.MongoConnection.open", return_value=fake_conn):
        r = runner.invoke(app, ["retry", "--all"])
        assert r.exit_code == 0
    assert count_by_status(db).get("pending", 0) == 1


def test_reindex_enqueues_everything(monkeypatch):
    _env(monkeypatch)
    db = mongomock.MongoClient()["d"]
    _seed(db)
    db["articles"].insert_many([{"_id": i, "body": f"t{i}"} for i in range(3)])
    db["articles_embeddings"].insert_many([
        {"source_id": i, "field_path": "body", "chunk_index": 0,
         "embedding_model": "local-fast", "embedding_hash": "sha1:OLD"}
        for i in range(3)
    ])
    fake_conn = MagicMock()
    fake_conn.db = db
    with patch("mongosemantic.commands.reindex.MongoConnection.open", return_value=fake_conn):
        r = runner.invoke(app, ["reindex", "--collection", "articles", "--yes"])
        assert r.exit_code == 0
    assert count_by_status(db).get("pending", 0) == 3
