from datetime import datetime, timezone

import pytest
from typer.testing import CliRunner

from mongosemantic.cli import app
from mongosemantic.embeddings.provider import get_provider
from mongosemantic.state import count_by_status
from mongosemantic.state.config_store import CollectionConfig, FieldSpec, save_config
from mongosemantic.worker.runner import process_batch


@pytest.mark.integration
def test_end_to_end_index_and_worker(clean_db, monkeypatch):
    db = clean_db
    save_config(db, CollectionConfig(
        collection="articles", mode="shadow", shadow_collection="articles_embeddings",
        fields=[FieldSpec(path="body")], embedding_model="local-fast", embedding_dim=384,
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    ))
    db["articles"].insert_many([
        {"body": "semantic search with mongodb"},
        {"body": "a totally different document about sports"},
    ])
    monkeypatch.setenv(
        "MONGOSEMANTIC_URI",
        "mongodb://localhost:27117/?replicaSet=rs0",
    )
    monkeypatch.setenv("MONGOSEMANTIC_DB", db.name)
    monkeypatch.setenv("MONGOSEMANTIC_MODEL", "local-fast")
    runner = CliRunner()
    r = runner.invoke(app, ["index", "--collection", "articles"])
    assert r.exit_code == 0, r.output
    assert count_by_status(db).get("pending", 0) == 2
    provider = get_provider("local-fast")
    process_batch(db, provider, "test-worker", 32)
    assert db["articles_embeddings"].count_documents({}) == 2
