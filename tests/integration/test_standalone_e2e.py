from datetime import datetime, timezone

import pytest
from typer.testing import CliRunner

from mongosemantic.cli import app
from mongosemantic.embeddings.provider import get_provider
from mongosemantic.state.config_store import CollectionConfig, FieldSpec, save_config
from mongosemantic.sync.polling import poll_once
from mongosemantic.worker.runner import process_batch


@pytest.mark.integration
def test_standalone_polling_flow(clean_standalone_db, monkeypatch):
    db = clean_standalone_db
    save_config(db, CollectionConfig(
        collection="articles", mode="shadow", shadow_collection="articles_embeddings",
        fields=[FieldSpec(path="body")], embedding_model="local-fast", embedding_dim=384,
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    ))
    now = datetime.now(timezone.utc)
    db["articles"].insert_many([
        {"_id": "a", "body": "car mechanics and repair", "updated_at": now},
        {"_id": "b", "body": "deep sea fishing tips", "updated_at": now},
    ])
    poll_once(db, "articles")
    process_batch(db, get_provider("local-fast"), "t", 32)
    monkeypatch.setenv("MONGOSEMANTIC_URI", "mongodb://localhost:27219")
    monkeypatch.setenv("MONGOSEMANTIC_DB", db.name)
    monkeypatch.setenv("MONGOSEMANTIC_MODEL", "local-fast")
    runner = CliRunner()
    r = runner.invoke(app, ["search", "engine oil change", "--collection", "articles"])
    assert r.exit_code == 0, r.output
    lines = r.output.splitlines()
    car_idx = next(i for i, line in enumerate(lines) if "car mechanics" in line)
    fish_idx = next(i for i, line in enumerate(lines) if "fishing" in line)
    assert car_idx < fish_idx
