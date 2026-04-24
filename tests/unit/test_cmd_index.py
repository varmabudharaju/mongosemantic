from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import mongomock
from typer.testing import CliRunner

from mongosemantic.cli import app
from mongosemantic.state import count_by_status
from mongosemantic.state.config_store import CollectionConfig, FieldSpec, save_config

runner = CliRunner()

def test_index_enqueues_all_existing_docs(monkeypatch):
    monkeypatch.setenv("MONGOSEMANTIC_URI", "mongodb://fake")
    monkeypatch.setenv("MONGOSEMANTIC_DB", "d")
    monkeypatch.setenv("MONGOSEMANTIC_MODEL", "local-fast")
    db = mongomock.MongoClient()["d"]
    save_config(db, CollectionConfig(
        collection="articles", mode="shadow", shadow_collection="articles_embeddings",
        fields=[FieldSpec(path="body")], embedding_model="local-fast", embedding_dim=384,
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    ))
    db["articles"].insert_many([{"_id": i, "body": f"text {i}"} for i in range(5)])
    fake_conn = MagicMock()
    fake_conn.db = db
    with patch("mongosemantic.commands.index.MongoConnection.open", return_value=fake_conn):
        r = runner.invoke(app, ["index", "--collection", "articles"])
        assert r.exit_code == 0, r.output
    assert count_by_status(db).get("pending", 0) == 5
