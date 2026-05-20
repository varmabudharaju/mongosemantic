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
