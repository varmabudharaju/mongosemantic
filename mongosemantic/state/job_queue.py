from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from pymongo import ASCENDING
from pymongo.database import Database

JOBS_COLLECTION = "mongosemantic_jobs"
MAX_ATTEMPTS = 3


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ensure_indexes(db: Database) -> None:
    db[JOBS_COLLECTION].create_index(
        [
            ("collection", ASCENDING),
            ("source_id", ASCENDING),
            ("field_path", ASCENDING),
            ("chunk_index", ASCENDING),
            ("kind", ASCENDING),
            ("model", ASCENDING),
            ("status", ASCENDING),
        ],
        name="job_dedup_idx",
    )
    db[JOBS_COLLECTION].create_index([("status", ASCENDING)], name="status_idx")


def enqueue_embed(
    db: Database,
    collection: str,
    source_id: Any,
    field_path: str,
    chunk_index: int | None,
    input_text: str,
    input_hash: str,
    model: str,
) -> None:
    filter_ = {
        "collection": collection,
        "source_id": source_id,
        "field_path": field_path,
        "chunk_index": chunk_index,
        "kind": "embed",
        "model": model,
        "status": {"$in": ["pending", "in_flight"]},
    }
    update = {
        "$setOnInsert": {
            "collection": collection,
            "source_id": source_id,
            "field_path": field_path,
            "chunk_index": chunk_index,
            "kind": "embed",
            "model": model,
            "status": "pending",
            "attempts": 0,
            "last_error": None,
            "enqueued_at": _utcnow(),
            "started_at": None,
            "completed_at": None,
            "owner": None,
            "input_text": input_text,
            "input_hash": input_hash,
        }
    }
    db[JOBS_COLLECTION].update_one(filter_, update, upsert=True)


def enqueue_delete_all(db: Database, collection: str, source_id: Any) -> None:
    db[JOBS_COLLECTION].insert_one({
        "collection": collection,
        "source_id": source_id,
        "field_path": None,
        "chunk_index": None,
        "kind": "delete",
        "model": None,
        "status": "pending",
        "attempts": 0,
        "last_error": None,
        "enqueued_at": _utcnow(),
        "started_at": None,
        "completed_at": None,
        "owner": None,
        "input_text": None,
        "input_hash": None,
    })


def claim_batch(db: Database, worker_id: str, limit: int) -> list[dict]:
    claimed: list[dict] = []
    for _ in range(limit):
        doc = db[JOBS_COLLECTION].find_one_and_update(
            {"status": "pending"},
            {"$set": {
                "status": "in_flight",
                "owner": worker_id,
                "started_at": _utcnow(),
            }},
            return_document=True,
        )
        if not doc:
            break
        claimed.append(doc)
    return claimed


def requeue_stale(db: Database, older_than_seconds: int = 600) -> int:
    """Return in_flight jobs whose worker died mid-batch back to pending.

    A job is stranded when a worker claims it and then crashes before
    complete()/fail() runs — nothing else ever touches it again. Workers
    call this on startup and periodically so a crashed run never
    permanently loses jobs. The cutoff is generous: a healthy batch
    completes in seconds, so anything in_flight for 10 minutes is dead.
    """
    cutoff = _utcnow() - timedelta(seconds=older_than_seconds)
    r = db[JOBS_COLLECTION].update_many(
        {"status": "in_flight", "started_at": {"$lt": cutoff}},
        {"$set": {"status": "pending", "owner": None, "started_at": None}},
    )
    return r.modified_count


def complete(db: Database, job_id: Any) -> None:
    db[JOBS_COLLECTION].update_one(
        {"_id": job_id},
        {"$set": {"status": "completed", "completed_at": _utcnow()}},
    )


def fail(db: Database, job_id: Any, reason: str) -> None:
    doc = db[JOBS_COLLECTION].find_one({"_id": job_id}) or {}
    attempts = (doc.get("attempts") or 0) + 1
    next_status = "failed" if attempts >= MAX_ATTEMPTS else "pending"
    db[JOBS_COLLECTION].update_one(
        {"_id": job_id},
        {"$set": {
            "status": next_status,
            "attempts": attempts,
            "last_error": reason,
            "owner": None,
            "started_at": None,
        }},
    )


def reset_failed(db: Database) -> int:
    r = db[JOBS_COLLECTION].update_many(
        {"status": "failed"},
        {"$set": {"status": "pending", "attempts": 0, "last_error": None}},
    )
    return r.modified_count


def count_by_status(db: Database) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in db[JOBS_COLLECTION].aggregate([
        {"$group": {"_id": "$status", "n": {"$sum": 1}}}
    ]):
        out[row["_id"]] = row["n"]
    return out


def count_by_collection(db: Database) -> dict[str, dict[str, int]]:
    """Per-collection breakdown of job status counts. Used by the dashboard's
    "indexing activity" view so operators can see which collection is actively
    being processed and which has piled-up failures."""
    out: dict[str, dict[str, int]] = {}
    for row in db[JOBS_COLLECTION].aggregate([
        {"$group": {
            "_id": {"collection": "$collection", "status": "$status"},
            "n": {"$sum": 1},
        }}
    ]):
        coll = row["_id"]["collection"] or "<unknown>"
        status = row["_id"]["status"]
        out.setdefault(coll, {}).setdefault(status, 0)
        out[coll][status] = row["n"]
    return out


def count_by_field(db: Database, collection: str) -> dict[str, dict[str, int]]:
    """Per-field status counts for one collection. Powers the indexing
    page's per-field breakdown so operators can spot one field stalled
    while the others finish."""
    out: dict[str, dict[str, int]] = {}
    for row in db[JOBS_COLLECTION].aggregate([
        {"$match": {"collection": collection}},
        {"$group": {
            "_id": {"field": "$field_path", "status": "$status"},
            "n": {"$sum": 1},
        }}
    ]):
        field = row["_id"]["field"] or "<unknown>"
        status = row["_id"]["status"]
        out.setdefault(field, {})[status] = row["n"]
    return out


def recent_jobs(db: Database, collection: str, limit: int = 20) -> list[dict]:
    """Mixed recent-activity feed for one collection: completed + failed
    jobs ordered by whichever timestamp is most recent. Lets the indexing
    page show a live log instead of just numbers."""
    cursor = (
        db[JOBS_COLLECTION]
        .find(
            {
                "collection": collection,
                "status": {"$in": ["completed", "failed"]},
            },
            {
                "status": 1, "field_path": 1, "source_id": 1, "chunk_index": 1,
                "completed_at": 1, "enqueued_at": 1, "last_error": 1, "attempts": 1,
            },
        )
        .sort("completed_at", -1)
        .limit(limit)
    )
    return [
        {
            "status": d.get("status"),
            "field_path": d.get("field_path"),
            "source_id": str(d.get("source_id")) if d.get("source_id") is not None else None,
            "chunk_index": d.get("chunk_index"),
            "completed_at": d.get("completed_at"),
            "enqueued_at": d.get("enqueued_at"),
            "last_error": d.get("last_error"),
            "attempts": d.get("attempts"),
        }
        for d in cursor
    ]


def recent_failed_jobs(db: Database, limit: int = 10) -> list[dict]:
    """Most recently failed jobs, with the last_error message — for surfacing
    in `status` and the dashboard so failures are actionable, not just a count."""
    cursor = (
        db[JOBS_COLLECTION]
        .find({"status": "failed"})
        .sort("enqueued_at", -1)
        .limit(limit)
    )
    return [
        {
            "id": str(doc.get("_id")),
            "collection": doc.get("collection"),
            "source_id": str(doc.get("source_id")) if doc.get("source_id") is not None else None,
            "field_path": doc.get("field_path"),
            "kind": doc.get("kind"),
            "attempts": doc.get("attempts"),
            "last_error": doc.get("last_error"),
            "enqueued_at": doc.get("enqueued_at"),
        }
        for doc in cursor
    ]
