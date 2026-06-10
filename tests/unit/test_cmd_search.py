from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import mongomock
import numpy as np
from typer.testing import CliRunner

from mongosemantic.cli import app
from mongosemantic.commands.search import _run_one, hybrid_available, run_one_hybrid
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

    def fake_field(_db, _cfg, _coll, field_path, _q, _limit, _topo, source_filter=None):
        calls.append(field_path)
        return [{"source_id": f"id-{field_path}", "field_path": field_path,
                 "chunk_text": f"hit-{field_path}", "score": 0.5}]

    with patch("mongosemantic.commands.search._run_one_field", side_effect=fake_field):
        rows = _run_one(db, cfg, "articles", [0.0, 0.0, 0.0], limit=10, topology=Topology.STANDALONE)

    assert sorted(calls) == ["body", "title"]
    assert {r["field_path"] for r in rows} == {"title", "body"}


def test_resolved_vector_index_name_prefers_stored_over_computed():
    """v0.5.0 migrations rename indexes; search must honor the renamed name."""
    from mongosemantic.commands.search import _resolved_vector_index_name
    from mongosemantic.db.indexes import vector_index_name
    cfg = _multi_field_cfg()
    computed = vector_index_name(cfg.collection, "title")
    # No stored override → computed
    assert _resolved_vector_index_name(cfg, "title") == computed
    # Stored override wins
    cfg.vector_index_names = {"title": "renamed_after_migration"}
    assert _resolved_vector_index_name(cfg, "title") == "renamed_after_migration"
    # Fallback for fields not in the dict
    assert _resolved_vector_index_name(cfg, "body") == vector_index_name(cfg.collection, "body")


def test_search_embeds_with_collection_model_not_global_setting(monkeypatch):
    """Regression: after a migration the collection's stored model may differ
    from MONGOSEMANTIC_MODEL. The query must be embedded with the *collection's*
    model so dimensions match the stored vectors."""
    db = mongomock.MongoClient()["d"]
    save_config(db, CollectionConfig(
        collection="articles", mode="shadow", shadow_collection="articles_embeddings",
        fields=[FieldSpec(path="body")],
        embedding_model="local-better", embedding_dim=768,
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    ))

    monkeypatch.setenv("MONGOSEMANTIC_URI", "mongodb://fake")
    monkeypatch.setenv("MONGOSEMANTIC_DB", "d")
    monkeypatch.setenv("MONGOSEMANTIC_MODEL", "local-fast")  # deliberately wrong

    captured_models: list[str] = []
    def fake_get_provider(model: str):
        captured_models.append(model)
        p = MagicMock()
        p.embed = lambda q: np.array([0.0] * 768, dtype=np.float32)
        return p

    fake_conn = MagicMock()
    fake_conn.db = db
    fake_conn.topology = Topology.STANDALONE
    fake_conn.close = MagicMock()

    with patch("mongosemantic.commands.search.MongoConnection.open", return_value=fake_conn), \
         patch("mongosemantic.commands.search.get_provider", side_effect=fake_get_provider), \
         patch("mongosemantic.commands.search._run_one", return_value=[]):
        r = runner.invoke(app, ["search", "anything", "--collection", "articles"])
        assert r.exit_code == 0, r.output
    # local-better is what's stored in cfg; local-fast is the global default.
    # The provider call must use the collection's model.
    assert captured_models == ["local-better"]


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

    def fake_field(_db, _cfg, _coll, field_path, _q, _limit, _topo, source_filter=None):
        return per_field[field_path]

    with patch("mongosemantic.commands.search._run_one_field", side_effect=fake_field):
        rows = _run_one(db, cfg, "articles", [0.0, 0.0, 0.0], limit=3, topology=Topology.STANDALONE)

    assert [r["score"] for r in rows] == [0.9, 0.8, 0.4]
    assert [r["source_id"] for r in rows] == ["a", "c", "b"]


# --- --filter ---------------------------------------------------------------

def _fake_conn(db, topology=Topology.STANDALONE):
    conn = MagicMock()
    conn.db = db
    conn.topology = topology
    return conn


def test_filter_option_plumbs_parsed_dict_into_run_one(monkeypatch):
    db = _setup(monkeypatch)
    fake_provider = MagicMock()
    fake_provider.embed = lambda q: np.array([1.0, 0.0, 0.0], dtype=np.float32)
    with patch("mongosemantic.commands.search.MongoConnection.open",
               return_value=_fake_conn(db)), \
         patch("mongosemantic.commands.search.get_provider", return_value=fake_provider), \
         patch("mongosemantic.commands.search._run_one", return_value=[]) as run:
        r = runner.invoke(app, ["search", "q", "--collection", "articles",
                                "--filter", '{"year": {"$gte": 1960}}'])
        assert r.exit_code == 0, r.output
        run.assert_called_once()
        assert run.call_args.kwargs["source_filter"] == {"year": {"$gte": 1960}}


def test_invalid_filter_json_exits_2(monkeypatch):
    db = _setup(monkeypatch)
    with patch("mongosemantic.commands.search.MongoConnection.open",
               return_value=_fake_conn(db)):
        r = runner.invoke(app, ["search", "q", "--collection", "articles",
                                "--filter", "{not json"])
    assert r.exit_code == 2
    assert "filter" in r.output.lower()
    assert "json" in r.output.lower()  # FilterError message, not typer usage error


# --- --rerank ---------------------------------------------------------------

def _fake_rows(n):
    return [
        {"source_id": f"id-{i}", "source_collection": "articles", "field_path": "body",
         "chunk_index": 0, "chunk_text": f"row-{i}", "score": round(1.0 - i / 100, 3)}
        for i in range(n)
    ]


def test_rerank_overfetches_and_applies_reranker_order(monkeypatch):
    db = _setup(monkeypatch)
    fake_provider = MagicMock()
    fake_provider.embed = lambda q: np.array([1.0, 0.0, 0.0], dtype=np.float32)
    fake_reranker = MagicMock()
    fake_reranker.rerank = lambda q, rows, limit: list(reversed(rows))[:limit]
    with patch("mongosemantic.commands.search.MongoConnection.open",
               return_value=_fake_conn(db)), \
         patch("mongosemantic.commands.search.get_provider", return_value=fake_provider), \
         patch("mongosemantic.commands.search.get_reranker", return_value=fake_reranker), \
         patch("mongosemantic.commands.search._run_one", return_value=_fake_rows(10)) as run:
        r = runner.invoke(app, ["search", "q", "--collection", "articles",
                                "--limit", "2", "--rerank"])
        assert r.exit_code == 0, r.output
        # Over-fetch: limit * RERANK_CANDIDATE_MULTIPLIER (2 * 5) candidates.
        assert run.call_args.args[4] == 10
    # Fake reranker reversed the rows: last two candidates win.
    assert "row-9" in r.stdout
    assert "row-8" in r.stdout
    assert "row-0" not in r.stdout


def test_rerank_unavailable_warns_and_truncates(monkeypatch):
    db = _setup(monkeypatch)
    fake_provider = MagicMock()
    fake_provider.embed = lambda q: np.array([1.0, 0.0, 0.0], dtype=np.float32)
    with patch("mongosemantic.commands.search.MongoConnection.open",
               return_value=_fake_conn(db)), \
         patch("mongosemantic.commands.search.get_provider", return_value=fake_provider), \
         patch("mongosemantic.commands.search.get_reranker", return_value=None), \
         patch("mongosemantic.commands.search.rerank_reason", return_value="nope"), \
         patch("mongosemantic.commands.search._run_one", return_value=_fake_rows(5)):
        r = runner.invoke(app, ["search", "q", "--collection", "articles",
                                "--limit", "1", "--rerank"])
    assert r.exit_code == 0, r.output
    assert "nope" in r.output          # warning includes rerank_reason()
    assert "row-0" in r.stdout         # results still printed...
    assert "row-1" not in r.stdout     # ...truncated to --limit


# --- hybrid on every topology -------------------------------------------------

def test_hybrid_available_shadow_any_topology_inline_never():
    shadow_cfg = _multi_field_cfg()
    assert hybrid_available(shadow_cfg, Topology.STANDALONE) is True
    inline_cfg = CollectionConfig(
        collection="products", mode="inline", shadow_collection=None,
        fields=[FieldSpec(path="description")], embedding_model="local-fast",
        embedding_dim=3,
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    )
    assert hybrid_available(inline_cfg, Topology.ATLAS) is False


def test_run_one_hybrid_standalone_uses_client_side_rrf():
    """Non-Atlas hybrid fuses the vector leg + $text leg with RRF client-side."""
    db = mongomock.MongoClient()["d"]
    cfg = CollectionConfig(
        collection="articles", mode="shadow", shadow_collection="articles_embeddings",
        fields=[FieldSpec(path="body")], embedding_model="local-fast", embedding_dim=3,
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    )
    save_config(db, cfg)

    def row(sid, score):
        return {"source_id": sid, "field_path": "body", "chunk_index": 0,
                "chunk_text": f"text-{sid}", "score": score}

    vec_rows = [row("a", 0.9), row("b", 0.8)]
    txt_rows = [row("b", 5.0), row("c", 3.0)]

    with patch("mongosemantic.commands.search._run_one_field",
               return_value=vec_rows) as vec, \
         patch("mongosemantic.commands.search.text_leg",
               return_value=txt_rows) as txt:
        rows = run_one_hybrid(db, cfg, "articles", "q", [0.0, 0.0, 0.0],
                              limit=10, topology=Topology.STANDALONE)
        vec.assert_called_once()
        txt.assert_called_once()

    # RRF: "b" appears in both legs (rank 2 vector, rank 1 text) and must
    # outrank "a" (rank-1 vector only). All three docs survive the fuse.
    assert [r["source_id"] for r in rows][0] == "b"
    assert {r["source_id"] for r in rows} == {"a", "b", "c"}
    assert all(r["source_collection"] == "articles" for r in rows)
