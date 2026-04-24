from datetime import datetime, timezone

import pytest
from typer.testing import CliRunner

from mongosemantic.cli import app
from mongosemantic.embeddings.provider import get_provider
from mongosemantic.state.config_store import CollectionConfig, FieldSpec, save_config
from mongosemantic.worker.runner import process_batch


@pytest.mark.integration
def test_search_end_to_end(clean_db, monkeypatch):
    db = clean_db
    save_config(db, CollectionConfig(
        collection="articles", mode="shadow", shadow_collection="articles_embeddings",
        fields=[FieldSpec(path="body")], embedding_model="local-fast", embedding_dim=384,
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    ))
    db["articles"].insert_many([
        {"_id": "a", "body": "semantic vector search over mongodb"},
        {"_id": "b", "body": "completely unrelated: basketball scores"},
    ])
    monkeypatch.setenv(
        "MONGOSEMANTIC_URI",
        "mongodb://localhost:27117/?replicaSet=rs0",
    )
    monkeypatch.setenv("MONGOSEMANTIC_DB", db.name)
    monkeypatch.setenv("MONGOSEMANTIC_MODEL", "local-fast")
    runner = CliRunner()
    r = runner.invoke(app, ["index", "--collection", "articles"])
    assert r.exit_code == 0
    process_batch(db, get_provider("local-fast"), "t", 32)
    assert db["articles_embeddings"].count_documents({}) == 2
    r2 = runner.invoke(app, ["search", "vector database", "--collection", "articles", "--limit", "2"])
    assert r2.exit_code == 0
    output_lines = r2.output.splitlines()
    semantic_line_idx = next(i for i, line in enumerate(output_lines) if "semantic" in line)
    basketball_line_idx = next(i for i, line in enumerate(output_lines) if "basketball" in line)
    assert semantic_line_idx < basketball_line_idx
