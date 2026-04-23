from __future__ import annotations

from datetime import datetime, timezone
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
