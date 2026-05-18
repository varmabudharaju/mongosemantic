from unittest.mock import MagicMock, patch

import mongomock
from typer.testing import CliRunner

from mongosemantic.cli import app

runner = CliRunner()

def _patch_env(monkeypatch):
    monkeypatch.setenv("MONGOSEMANTIC_URI", "mongodb://fake")
    monkeypatch.setenv("MONGOSEMANTIC_DB", "d")
    monkeypatch.setenv("MONGOSEMANTIC_MODEL", "local-fast")

def test_apply_creates_shadow_indexes_and_saves_config(monkeypatch):
    _patch_env(monkeypatch)
    fake_db = mongomock.MongoClient()["d"]
    fake_conn = MagicMock()
    fake_conn.db = fake_db
    from mongosemantic.db.client import Topology
    fake_conn.topology = Topology.STANDALONE
    with patch("mongosemantic.commands.apply.MongoConnection.open", return_value=fake_conn):
        r = runner.invoke(app, ["apply", "--collection", "articles", "--field", "body"])
        assert r.exit_code == 0, r.output
    from mongosemantic.state import load_config
    cfg = load_config(fake_db, "articles")
    assert cfg is not None
    assert cfg.fields[0].path == "body"
    assert cfg.shadow_collection == "articles_embeddings"

def test_apply_rejects_chunk_with_inline(monkeypatch):
    """--chunked is incompatible with --mode inline; reject loudly, don't silently downgrade."""
    _patch_env(monkeypatch)
    fake_db = mongomock.MongoClient()["d"]
    fake_conn = MagicMock()
    fake_conn.db = fake_db
    from mongosemantic.db.client import Topology
    fake_conn.topology = Topology.STANDALONE
    with patch("mongosemantic.commands.apply.MongoConnection.open", return_value=fake_conn):
        r = runner.invoke(
            app,
            ["apply", "--collection", "articles", "--field", "body",
             "--mode", "inline", "--chunked"],
        )
        assert r.exit_code != 0
        assert "chunk" in r.output.lower() and "shadow" in r.output.lower()
    from mongosemantic.state import load_config
    assert load_config(fake_db, "articles") is None
