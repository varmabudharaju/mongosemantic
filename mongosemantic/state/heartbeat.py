"""Worker heartbeat — small writes to mongosemantic_workers so operators
can tell which workers are alive, stale, or dead from the status command
or the dashboard."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from pymongo.database import Database

WORKERS_COLLECTION = "mongosemantic_workers"

# A worker is "running" if it heartbeated within this window; "stale" past
# this; "dead" past the stale cutoff. Picked to forgive a missed beat or
# a brief embed batch but flag a hung process quickly.
RUNNING_WINDOW_S = 30
STALE_WINDOW_S = 300


@dataclass
class WorkerHeartbeat:
    worker_id: str
    started_at: datetime
    last_heartbeat: datetime
    jobs_processed: int
    status: str  # "running" | "stale" | "dead"


def write_heartbeat(
    db: Database, worker_id: str, jobs_processed: int, started_at: datetime
) -> None:
    db[WORKERS_COLLECTION].update_one(
        {"_id": worker_id},
        {
            "$set": {
                "last_heartbeat": datetime.now(timezone.utc),
                "jobs_processed": jobs_processed,
            },
            "$setOnInsert": {"started_at": started_at},
        },
        upsert=True,
    )


def remove_heartbeat(db: Database, worker_id: str) -> None:
    db[WORKERS_COLLECTION].delete_one({"_id": worker_id})


def _classify(now: datetime, last: datetime) -> str:
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    age = (now - last).total_seconds()
    if age <= RUNNING_WINDOW_S:
        return "running"
    if age <= STALE_WINDOW_S:
        return "stale"
    return "dead"


def list_heartbeats(db: Database) -> list[WorkerHeartbeat]:
    now = datetime.now(timezone.utc)
    out: list[WorkerHeartbeat] = []
    for doc in db[WORKERS_COLLECTION].find({}):
        last = doc.get("last_heartbeat", now)
        out.append(WorkerHeartbeat(
            worker_id=doc["_id"],
            started_at=doc.get("started_at", now),
            last_heartbeat=last,
            jobs_processed=doc.get("jobs_processed", 0),
            status=_classify(now, last),
        ))
    out.sort(key=lambda h: h.last_heartbeat, reverse=True)
    return out


def prune_dead(db: Database, older_than_seconds: int = 86_400) -> int:
    """Drop heartbeats older than `older_than_seconds`. Default: 1 day."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=older_than_seconds)
    r = db[WORKERS_COLLECTION].delete_many({"last_heartbeat": {"$lt": cutoff}})
    return r.deleted_count
