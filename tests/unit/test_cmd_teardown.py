"""Tests for `mongosemantic teardown`.

Particularly: teardown must also drop the collection's pending jobs from the
shared `mongosemantic_jobs` queue. Without this, a teardown -> re-apply
sequence leaves orphan jobs for the OLD config in the queue. When the worker
runs next, it pulls those orphan jobs FIFO ahead of the new config's jobs,
embeds documents under the old field path, and the new config never makes
progress. This was the actual cause of tier 5 of the Atlas verification
suite failing (chunked + inline both ended up processing stale title-field
jobs left over from tier 2).
"""
from unittest.mock import MagicMock, patch

import mongomock
from typer.testing import CliRunner

from mongosemantic.cli import app
from mongosemantic.state import CollectionConfig, FieldSpec, save_config
from mongosemantic.state.job_queue import JOBS_COLLECTION, enqueue_embed

runner = CliRunner()


def _env(monkeypatch):
    monkeypatch.setenv("MONGOSEMANTIC_URI", "mongodb://fake")
    monkeypatch.setenv("MONGOSEMANTIC_DB", "d")
    monkeypatch.setenv("MONGOSEMANTIC_MODEL", "local-fast")


def _seed(db, collection: str, field_path: str, n: int):
    from datetime import datetime, timezone
    save_config(db, CollectionConfig(
        collection=collection, mode="shadow",
        shadow_collection=f"{collection}_embeddings",
        fields=[FieldSpec(path=field_path)],
        embedding_model="local-fast", embedding_dim=384,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    ))
    for i in range(n):
        enqueue_embed(
            db, collection=collection, source_id=f"doc-{i}",
            field_path=field_path, chunk_index=None,
            input_text=f"text-{i}", input_hash=f"sha1:{i:040x}",
            model="local-fast",
        )


def test_teardown_clears_pending_jobs_for_that_collection(monkeypatch):
    """Regression: teardown must delete this collection's pending jobs from
    `mongosemantic_jobs`. Otherwise the worker keeps embedding under the
    torn-down config's field path after the user re-applies with a different
    field — silently masking the new config's progress."""
    _env(monkeypatch)
    fake_db = mongomock.MongoClient()["d"]
    fake_conn = MagicMock()
    fake_conn.db = fake_db
    from mongosemantic.db.client import Topology
    fake_conn.topology = Topology.REPLICA_SET

    _seed(fake_db, "movies", "title", n=10)
    assert fake_db[JOBS_COLLECTION].count_documents({"collection": "movies"}) == 10

    with patch("mongosemantic.commands.teardown.MongoConnection.open", return_value=fake_conn):
        r = runner.invoke(app, ["teardown", "--collection", "movies", "--yes"])
        assert r.exit_code == 0, r.output

    assert fake_db[JOBS_COLLECTION].count_documents({"collection": "movies"}) == 0, (
        "teardown must clear pending jobs for the torn-down collection"
    )


def test_teardown_with_no_pending_jobs_does_not_print_cleared_notice(monkeypatch):
    """The 'Cleared N pending job(s)' line should only fire when N > 0.
    Pins the no-op branch so future refactors don't surprise users with a
    'Cleared 0 pending job(s)' message."""
    _env(monkeypatch)
    fake_db = mongomock.MongoClient()["d"]
    fake_conn = MagicMock()
    fake_conn.db = fake_db
    from datetime import datetime, timezone

    from mongosemantic.db.client import Topology
    fake_conn.topology = Topology.REPLICA_SET

    # Save a config but DON'T enqueue any jobs.
    save_config(fake_db, CollectionConfig(
        collection="movies", mode="shadow", shadow_collection="movies_embeddings",
        fields=[FieldSpec(path="title")],
        embedding_model="local-fast", embedding_dim=384,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    ))
    assert fake_db[JOBS_COLLECTION].count_documents({}) == 0

    with patch("mongosemantic.commands.teardown.MongoConnection.open", return_value=fake_conn):
        r = runner.invoke(app, ["teardown", "--collection", "movies", "--yes"])
        assert r.exit_code == 0, r.output

    assert "Cleared" not in r.output and "pending job" not in r.output, (
        f"no jobs to clear -> no 'Cleared N' line; got:\n{r.output}"
    )


def test_teardown_inline_drops_atlas_vector_index_on_source(monkeypatch):
    """Regression: for inline mode on Atlas, apply creates a vector index on
    the SOURCE collection (the embedding lives under _msem.{field}.embedding
    on each source doc). teardown must also drop that index. Previously
    teardown only unset _msem and left the index orphaned on the source —
    consuming an Atlas FTS-index slot forever (M0/M2/M5 cap of 3).

    Surfaced by tier 6 of the Atlas verification suite: after tier 5's
    inline phase, the orphan index on embedded_movies blocked tier 6's
    apply because Atlas refused to create a new index over the cap.
    """
    from datetime import datetime, timezone

    _env(monkeypatch)
    fake_db = mongomock.MongoClient()["d"]
    fake_conn = MagicMock()
    fake_conn.db = fake_db
    from mongosemantic.db.client import Topology
    fake_conn.topology = Topology.ATLAS

    # Set up an inline-mode config (no shadow collection, _msem on source).
    save_config(fake_db, CollectionConfig(
        collection="movies", mode="inline", shadow_collection=None,
        fields=[FieldSpec(path="plot"), FieldSpec(path="title")],
        embedding_model="local-fast", embedding_dim=384,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    ))

    dropped_indexes: list[tuple[str, str]] = []

    def fake_drop_search_index(self, name):
        dropped_indexes.append((self.name, name))

    # mongomock doesn't support list/drop search indexes natively; patch the
    # Collection methods used by the teardown path so we can verify the call.
    with (
        patch("mongosemantic.commands.teardown.MongoConnection.open", return_value=fake_conn),
        patch("mongomock.Collection.drop_search_index", new=fake_drop_search_index, create=True),
        patch("mongomock.Collection.list_search_indexes",
              new=lambda self: iter([
                  {"name": "mongosemantic_movies_3c6de1b7"},
                  {"name": "mongosemantic_movies_0fee558e"},
                  {"name": "unrelated_atlas_index"},
              ]),
              create=True),
    ):
        r = runner.invoke(app, ["teardown", "--collection", "movies", "--yes"])
        assert r.exit_code == 0, r.output

    dropped_names = {n for _, n in dropped_indexes}
    # Both mongosemantic_movies_* indexes should have been dropped.
    assert "mongosemantic_movies_3c6de1b7" in dropped_names, (
        f"teardown must drop inline vector indexes on source; dropped={dropped_indexes}"
    )
    assert "mongosemantic_movies_0fee558e" in dropped_names, (
        f"teardown must drop ALL mongosemantic_* indexes on source; dropped={dropped_indexes}"
    )
    # Non-mongosemantic indexes must NOT be touched.
    assert "unrelated_atlas_index" not in dropped_names, (
        f"teardown must only drop mongosemantic-owned indexes; dropped={dropped_indexes}"
    )


def test_teardown_does_not_touch_other_collections_jobs(monkeypatch):
    """Belt-and-suspenders for the deletion query: only THIS collection's
    jobs should disappear, never another collection's."""
    _env(monkeypatch)
    fake_db = mongomock.MongoClient()["d"]
    fake_conn = MagicMock()
    fake_conn.db = fake_db
    from mongosemantic.db.client import Topology
    fake_conn.topology = Topology.REPLICA_SET

    _seed(fake_db, "movies", "title", n=10)
    _seed(fake_db, "articles", "body", n=5)
    assert fake_db[JOBS_COLLECTION].count_documents({"collection": "articles"}) == 5

    with patch("mongosemantic.commands.teardown.MongoConnection.open", return_value=fake_conn):
        r = runner.invoke(app, ["teardown", "--collection", "movies", "--yes"])
        assert r.exit_code == 0, r.output

    assert fake_db[JOBS_COLLECTION].count_documents({"collection": "movies"}) == 0
    assert fake_db[JOBS_COLLECTION].count_documents({"collection": "articles"}) == 5, (
        "teardown must NOT touch jobs for collections other than the targeted one"
    )
