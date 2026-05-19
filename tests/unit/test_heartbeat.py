from datetime import datetime, timedelta, timezone

import mongomock

from mongosemantic.state import list_heartbeats, prune_dead, remove_heartbeat, write_heartbeat
from mongosemantic.state.heartbeat import WORKERS_COLLECTION


def _db():
    return mongomock.MongoClient()["test"]


def test_write_then_list_returns_running_status():
    db = _db()
    write_heartbeat(db, "w1", jobs_processed=5, started_at=datetime.now(timezone.utc))
    hbs = list_heartbeats(db)
    assert len(hbs) == 1
    assert hbs[0].worker_id == "w1"
    assert hbs[0].status == "running"
    assert hbs[0].jobs_processed == 5


def test_stale_and_dead_classification():
    db = _db()
    now = datetime.now(timezone.utc)
    db[WORKERS_COLLECTION].insert_many([
        {"_id": "fresh", "started_at": now, "last_heartbeat": now, "jobs_processed": 1},
        {"_id": "stale", "started_at": now, "last_heartbeat": now - timedelta(seconds=120), "jobs_processed": 2},
        {"_id": "dead",  "started_at": now, "last_heartbeat": now - timedelta(seconds=600), "jobs_processed": 3},
    ])
    by_id = {h.worker_id: h.status for h in list_heartbeats(db)}
    assert by_id == {"fresh": "running", "stale": "stale", "dead": "dead"}


def test_repeated_write_preserves_started_at():
    db = _db()
    started = datetime.now(timezone.utc) - timedelta(minutes=5)
    write_heartbeat(db, "w", jobs_processed=1, started_at=started)
    write_heartbeat(db, "w", jobs_processed=10, started_at=datetime.now(timezone.utc))
    [hb] = list_heartbeats(db)
    # started_at must be the original, not the later one (setOnInsert semantics).
    # mongomock strips tzinfo on roundtrip, so compare naive timestamps.
    saved = hb.started_at.replace(tzinfo=None, microsecond=0)
    original = started.replace(tzinfo=None, microsecond=0)
    assert saved == original
    assert hb.jobs_processed == 10


def test_remove_heartbeat_drops_entry():
    db = _db()
    write_heartbeat(db, "w", jobs_processed=0, started_at=datetime.now(timezone.utc))
    remove_heartbeat(db, "w")
    assert list_heartbeats(db) == []


def test_prune_dead_drops_old_entries_only():
    db = _db()
    now = datetime.now(timezone.utc)
    db[WORKERS_COLLECTION].insert_many([
        {"_id": "fresh", "started_at": now, "last_heartbeat": now, "jobs_processed": 0},
        {"_id": "ancient", "started_at": now, "last_heartbeat": now - timedelta(days=2), "jobs_processed": 0},
    ])
    n = prune_dead(db, older_than_seconds=86_400)
    assert n == 1
    remaining = [h.worker_id for h in list_heartbeats(db)]
    assert remaining == ["fresh"]
