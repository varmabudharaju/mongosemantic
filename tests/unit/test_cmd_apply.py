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

def test_apply_atlas_partial_index_failure_exits_non_zero_and_explains(monkeypatch):
    """Regression: when an Atlas index creation fails partway (e.g. multi-field
    apply hits the M0 cap of 3 search indexes after creating 2 of them),
    `mongosemantic apply` must:

      - Return a non-zero exit code (currently exits 0 — silent partial success).
      - Print which fields succeeded and which failed.
      - Surface the M0-cap message specifically when that's the underlying cause.

    Surfaced by the Atlas verification suite (tier 3): on M0, apply with two
    fields needs 4 indexes but only 3 can fit; the second field's vector index
    fails, the loop bails, and the user sees a yellow warning + exit 0.
    """
    _patch_env(monkeypatch)
    fake_db = mongomock.MongoClient()["d"]
    fake_conn = MagicMock()
    fake_conn.db = fake_db
    from mongosemantic.db.client import Topology
    fake_conn.topology = Topology.ATLAS

    call_log: list[str] = []

    def fake_vector(target, collection, field_path, dim, path="embedding"):
        call_log.append(f"vector:{field_path}")
        if field_path == "plot":
            raise RuntimeError(
                "The maximum number of FTS indexes has been reached for this instance size."
            )
        return f"mongosemantic_{collection}_{field_path}_v"

    def fake_search(target, name, path="chunk_text"):
        call_log.append(f"search:{name}")
        return name

    with (
        patch("mongosemantic.commands.apply.MongoConnection.open", return_value=fake_conn),
        patch("mongosemantic.commands.apply.create_atlas_vector_index", side_effect=fake_vector),
        patch("mongosemantic.commands.apply.create_atlas_search_index", side_effect=fake_search),
    ):
        r = runner.invoke(
            app,
            ["apply", "--collection", "embedded_movies",
             "--field", "title", "--field", "plot",
             "--mode", "shadow"],
        )

    # Non-zero exit code is the main behavioral fix.
    assert r.exit_code != 0, (
        f"expected non-zero exit on partial Atlas index failure; got 0\noutput:\n{r.output}"
    )
    # Must name the failing field so the user can act.
    assert "plot" in r.output, f"output must name the failing field; got:\n{r.output}"
    # Must surface the M0-cap hint when the error mentions FTS cap.
    out = r.output.lower()
    assert "m0" in out or "free tier" in out or "fts" in out, (
        f"output should hint at the M0 / FTS-cap cause; got:\n{r.output}"
    )
    # First field's indexes must have been attempted (partial state is OK; we
    # don't roll back working indexes).
    assert "vector:title" in call_log
    assert "vector:plot" in call_log  # attempted (and failed)


def test_apply_atlas_all_fields_fail_summary_says_none_succeeded(monkeypatch):
    """When every field fails, the summary must say 'succeeded: none' rather
    than printing an empty list. Belt-and-suspenders for an ugly format that
    could otherwise read 'succeeded: []' in the user's terminal."""
    _patch_env(monkeypatch)
    fake_db = mongomock.MongoClient()["d"]
    fake_conn = MagicMock()
    fake_conn.db = fake_db
    from mongosemantic.db.client import Topology
    fake_conn.topology = Topology.ATLAS

    def fail_all(*args, **kwargs):
        raise RuntimeError("synthetic Atlas failure for every field")

    with (
        patch("mongosemantic.commands.apply.MongoConnection.open", return_value=fake_conn),
        patch("mongosemantic.commands.apply.create_atlas_vector_index", side_effect=fail_all),
        patch("mongosemantic.commands.apply.create_atlas_search_index", side_effect=fail_all),
    ):
        r = runner.invoke(
            app,
            ["apply", "--collection", "embedded_movies",
             "--field", "title", "--field", "plot",
             "--mode", "shadow"],
        )

    assert r.exit_code != 0, r.output
    assert "succeeded: none" in r.output.lower()


def test_apply_atlas_inline_mode_partial_failure_also_exits_non_zero(monkeypatch):
    """Inline mode uses a different code path inside the per-field loop —
    confirm it surfaces failures the same way as shadow mode."""
    _patch_env(monkeypatch)
    fake_db = mongomock.MongoClient()["d"]
    fake_conn = MagicMock()
    fake_conn.db = fake_db
    from mongosemantic.db.client import Topology
    fake_conn.topology = Topology.ATLAS

    def fake_vector(target, collection, field_path, dim, path="embedding"):
        if field_path == "plot":
            raise RuntimeError("synthetic Atlas failure on inline plot")
        return f"mongosemantic_{collection}_{field_path}_v"

    with (
        patch("mongosemantic.commands.apply.MongoConnection.open", return_value=fake_conn),
        patch("mongosemantic.commands.apply.create_atlas_vector_index", side_effect=fake_vector),
    ):
        r = runner.invoke(
            app,
            ["apply", "--collection", "embedded_movies",
             "--field", "title", "--field", "plot",
             "--mode", "inline"],
        )

    assert r.exit_code != 0, r.output
    assert "plot" in r.output
    assert "remain in place" in r.output  # partial-state hint fired


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


def test_apply_shadow_creates_text_index(monkeypatch):
    """Shadow-mode apply must eagerly create the msem_chunk_text_text $text index
    on the shadow collection so the first hybrid search doesn't pay the build cost."""
    _patch_env(monkeypatch)
    fake_db = mongomock.MongoClient()["d"]
    fake_conn = MagicMock()
    fake_conn.db = fake_db
    from mongosemantic.db.client import Topology
    fake_conn.topology = Topology.STANDALONE
    with patch("mongosemantic.commands.apply.MongoConnection.open", return_value=fake_conn):
        r = runner.invoke(app, ["apply", "--collection", "articles", "--field", "body"])
        assert r.exit_code == 0, r.output
    shadow = fake_db["articles_embeddings"]
    assert "msem_chunk_text_text" in shadow.index_information(), (
        "expected msem_chunk_text_text text index on shadow collection after apply"
    )


def test_apply_inline_does_not_create_text_index(monkeypatch):
    """Inline-mode apply must NOT create the text index (no shadow collection exists)."""
    _patch_env(monkeypatch)
    fake_db = mongomock.MongoClient()["d"]
    fake_conn = MagicMock()
    fake_conn.db = fake_db
    from mongosemantic.db.client import Topology
    fake_conn.topology = Topology.STANDALONE
    with patch("mongosemantic.commands.apply.MongoConnection.open", return_value=fake_conn):
        r = runner.invoke(
            app,
            ["apply", "--collection", "articles", "--field", "body", "--mode", "inline"],
        )
        assert r.exit_code == 0, r.output
    # The text index should not appear on the source collection
    source_indexes = fake_db["articles"].index_information()
    assert "msem_chunk_text_text" not in source_indexes, (
        "inline-mode apply must not create msem_chunk_text_text on the source collection"
    )
