"""End-to-end coverage for the Tier 1 search features on a real replica set:

- metadata filtering (--filter)
- local hybrid search (client-side RRF: $text keyword leg + vector leg)
- local cross-encoder reranking (--rerank)

Assertions key on short, distinctive tokens that do NOT appear in the query
text (the rich table title echoes the query) so cell truncation/wrapping in
the rendered table cannot produce false positives or negatives.
"""

from datetime import datetime, timezone

import pytest
from typer.testing import CliRunner

from mongosemantic.cli import app
from mongosemantic.embeddings.provider import get_provider
from mongosemantic.state.config_store import CollectionConfig, FieldSpec, save_config
from mongosemantic.worker.runner import process_batch

REPLICA_URI = "mongodb://localhost:27117/?replicaSet=rs0"


def _seed_and_embed(db, monkeypatch, docs):
    """save_config + insert + env + CLI index + embed; returns a CliRunner."""
    save_config(db, CollectionConfig(
        collection="articles", mode="shadow", shadow_collection="articles_embeddings",
        fields=[FieldSpec(path="body")], embedding_model="local-fast", embedding_dim=384,
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    ))
    db["articles"].insert_many(docs)
    monkeypatch.setenv("MONGOSEMANTIC_URI", REPLICA_URI)
    monkeypatch.setenv("MONGOSEMANTIC_DB", db.name)
    monkeypatch.setenv("MONGOSEMANTIC_MODEL", "local-fast")
    runner = CliRunner()
    r = runner.invoke(app, ["index", "--collection", "articles"])
    assert r.exit_code == 0, r.output
    process_batch(db, get_provider("local-fast"), "t", 32)
    assert db["articles_embeddings"].count_documents({}) == len(docs)
    return runner


YEAR_DOCS = [
    {"_id": "old", "year": 1950, "body": "vintage wine cellar storage"},
    {"_id": "mid", "year": 1970, "body": "mongodb semantic vector search engine"},
    {"_id": "new", "year": 1990, "body": "vector database retrieval systems"},
]


@pytest.mark.integration
def test_filter_e2e(clean_db, monkeypatch):
    db = clean_db
    runner = _seed_and_embed(db, monkeypatch, YEAR_DOCS)
    r = runner.invoke(app, [
        "search", "vector database", "-c", "articles",
        "--filter", '{"year": {"$gte": 1960}}', "--limit", "5",
    ])
    assert r.exit_code == 0, r.output
    # mid (1970) and new (1990) pass the filter; old (1950) must not.
    # "semantic"/"retrieval" are distinctive tokens absent from the query.
    assert "semantic" in r.output
    assert "retrieval" in r.output
    assert "vintage" not in r.output
    assert "cellar" not in r.output


@pytest.mark.integration
def test_filter_no_matches(clean_db, monkeypatch):
    db = clean_db
    runner = _seed_and_embed(db, monkeypatch, YEAR_DOCS)
    r = runner.invoke(app, [
        "search", "vector database", "-c", "articles",
        "--filter", '{"year": {"$gte": 3000}}', "--limit", "5",
    ])
    assert r.exit_code == 0, r.output
    # No document satisfies the filter: the table renders with zero rows,
    # so none of the seeded body texts may appear in the output.
    for token in ("vintage", "cellar", "semantic", "engine", "retrieval", "mongodb"):
        assert token not in r.output, f"unexpected result row containing {token!r}"


HYBRID_DOCS = [
    # Keyword-only match for the query "XK-9000 blender" ($text leg).
    {"_id": "kw", "year": 1950, "body": "the XK-9000 turbo blender manual"},
    # Semantic-only match (vector leg) - shares no keywords with the query.
    {"_id": "sem", "year": 1990, "body": "kitchen appliance for making smoothies"},
]


@pytest.mark.integration
def test_local_hybrid_e2e(clean_db, monkeypatch):
    db = clean_db
    runner = _seed_and_embed(db, monkeypatch, HYBRID_DOCS)
    r = runner.invoke(app, [
        "search", "XK-9000 blender", "-c", "articles", "--hybrid", "--limit", "5",
    ])
    assert r.exit_code == 0, r.output
    # Both legs contribute: the keyword doc and the semantic doc both surface.
    # ("turbo"/"smoothies" are distinctive tokens absent from the query/title.)
    assert "turbo" in r.output
    assert "smoothies" in r.output
    # `apply` was never run in this test, so the $text index on the shadow
    # can only exist if the search path's lazy ensure_text_index created it.
    assert "msem_chunk_text_text" in db["articles_embeddings"].index_information()


@pytest.mark.integration
def test_hybrid_plus_filter(clean_db, monkeypatch):
    db = clean_db
    runner = _seed_and_embed(db, monkeypatch, HYBRID_DOCS)
    r = runner.invoke(app, [
        "search", "XK-9000 blender", "-c", "articles", "--hybrid",
        "--filter", '{"year": {"$gte": 1960}}', "--limit", "5",
    ])
    assert r.exit_code == 0, r.output
    # The filter excludes the keyword doc (1950) on BOTH legs; the semantic
    # doc (1990) still comes through the vector leg.
    assert "turbo" not in r.output
    assert "smoothies" in r.output


RERANK_DOCS = [
    {"_id": "hit", "body": "mongodb supports vector search via embeddings"},
    {"_id": "pasta", "body": "cooking pasta requires boiling water"},
    {"_id": "sunny", "body": "the weather today is sunny"},
]


@pytest.mark.integration
def test_rerank_e2e(clean_db, monkeypatch):
    # Uses the REAL cross-encoder (first run downloads ~80 MB) - no mocking.
    db = clean_db
    runner = _seed_and_embed(db, monkeypatch, RERANK_DOCS)
    r = runner.invoke(app, [
        "search", "which document describes mongodb vector search",
        "-c", "articles", "--rerank", "--limit", "2",
    ])
    assert r.exit_code == 0, r.output
    # The query echoes in the table title, so key on tokens outside it:
    # "embeddings" identifies the relevant doc; "pasta"/"sunny" the others.
    lines = r.output.splitlines()
    hit_idx = next(
        (i for i, line in enumerate(lines) if "embeddings" in line), None
    )
    assert hit_idx is not None, f"relevant doc missing from output:\n{r.output}"
    for token in ("pasta", "sunny"):
        idx = next((i for i, line in enumerate(lines) if token in line), None)
        if idx is not None:  # may be cut by --limit 2; if shown, ranked below
            assert hit_idx < idx, (
                f"{token!r} row ranked above the relevant doc:\n{r.output}"
            )
