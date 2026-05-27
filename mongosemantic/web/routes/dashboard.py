from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from mongosemantic.config import Settings
from mongosemantic.db.client import MongoConnection
from mongosemantic.state import (
    count_by_collection,
    count_by_field,
    count_by_status,
    delete_config,
    ensure_indexes,
    list_configured,
    list_heartbeats,
    load_config,
    recent_failed_jobs,
    recent_jobs,
    reset_failed,
)
from mongosemantic.sync.enqueue import enqueue_for_doc
from mongosemantic.web.identifiers import IdentifierError, validate_identifier

router = APIRouter()


@router.get("/api/dashboard")
def dashboard() -> dict:
    settings = Settings.from_environment()
    conn = MongoConnection.open(settings.uri, settings.database)
    try:
        db = conn.db
        cfgs = list_configured(db)
        total_embeddings = 0
        for cfg in cfgs:
            if cfg.mode == "inline":
                total_embeddings += db[cfg.collection].count_documents(
                    {"_msem": {"$exists": True}}
                )
            elif cfg.shadow_collection:
                total_embeddings += db[cfg.shadow_collection].count_documents({})
        return {
            "topology": conn.topology.value,
            "configured_count": len(cfgs),
            "configured": [
                {
                    "collection": c.collection,
                    "fields": [f.path for f in c.fields],
                    "embedding_model": c.embedding_model,
                    "mode": c.mode,
                }
                for c in cfgs
            ],
            "total_embeddings": total_embeddings,
            "jobs": count_by_status(db),
            "jobs_by_collection": count_by_collection(db),
            "workers": [
                {
                    "worker_id": hb.worker_id,
                    "status": hb.status,
                    "started_at": hb.started_at.isoformat(),
                    "last_heartbeat": hb.last_heartbeat.isoformat(),
                    "jobs_processed": hb.jobs_processed,
                }
                for hb in list_heartbeats(db)
            ],
            "recent_failed": [
                {
                    "collection": f.get("collection"),
                    "source_id": f.get("source_id"),
                    "field_path": f.get("field_path"),
                    "kind": f.get("kind"),
                    "attempts": f.get("attempts"),
                    "last_error": f.get("last_error"),
                }
                for f in recent_failed_jobs(db, limit=10)
            ],
        }
    finally:
        conn.close()


@router.get("/api/jobs/status")
def jobs_status() -> dict:
    settings = Settings.try_from_environment()
    if settings is None:
        return {"jobs": {}, "not_connected": True}
    conn = MongoConnection.open(settings.uri, settings.database)
    try:
        # Surface the most recent heartbeat so the global queue badge can
        # tell "worker idle" from "worker down". Older entries are kept on
        # the Dashboard page itself.
        latest = None
        for hb in list_heartbeats(conn.db):
            if latest is None or hb.last_heartbeat > latest.last_heartbeat:
                latest = hb
        worker = None
        if latest is not None:
            worker = {
                "worker_id": latest.worker_id,
                "last_heartbeat": latest.last_heartbeat.isoformat(),
                "jobs_processed": latest.jobs_processed,
            }
        return {"jobs": count_by_status(conn.db), "worker": worker}
    finally:
        conn.close()


@router.get("/api/indexing/status")
def indexing_status(
    collection: str = Query(..., min_length=1, max_length=128),
    recent_limit: int = Query(15, ge=1, le=100),
) -> dict:
    """One-shot status payload for the Indexing page: totals, per-field
    breakdown, latest worker heartbeat, recent activity feed, and a sample
    of failed jobs. Designed to be polled every ~1-2s while there's work.
    """
    settings = Settings.from_environment()
    conn = MongoConnection.open(settings.uri, settings.database)
    try:
        db = conn.db
        # Per-status totals scoped to this collection (count_by_collection
        # returns a {collection: {status: n}} map, so we just project it).
        per_coll = count_by_collection(db)
        totals = per_coll.get(collection, {})
        completed = totals.get("completed", 0)
        pending = totals.get("pending", 0)
        in_flight = totals.get("in_flight", 0)
        failed = totals.get("failed", 0)
        total = completed + pending + in_flight + failed

        # Worker heartbeat — same shape as /api/jobs/status so the UI can
        # reuse its liveness logic.
        latest = None
        for hb in list_heartbeats(db):
            if latest is None or hb.last_heartbeat > latest.last_heartbeat:
                latest = hb
        worker = (
            {
                "worker_id": latest.worker_id,
                "last_heartbeat": latest.last_heartbeat.isoformat(),
                "jobs_processed": latest.jobs_processed,
            }
            if latest is not None else None
        )

        # Per-field breakdown — flatten the (field -> {status: n}) map
        # into a list the UI can render as a small table.
        per_field_raw = count_by_field(db, collection)
        by_field = [
            {
                "field_path": field,
                "completed": counts.get("completed", 0),
                "pending": counts.get("pending", 0),
                "in_flight": counts.get("in_flight", 0),
                "failed": counts.get("failed", 0),
            }
            for field, counts in sorted(per_field_raw.items())
        ]

        # Recent activity feed: most-recent completed + failed jobs.
        feed = recent_jobs(db, collection, limit=recent_limit)
        # Serialize datetimes for the JSON response.
        for row in feed:
            for k in ("completed_at", "enqueued_at"):
                if row.get(k) is not None:
                    row[k] = row[k].isoformat()

        # Failed-jobs sample (already-failed, last_error visible). Distinct
        # from `recent` so the UI can show them in a separate "needs
        # attention" panel.
        failed_sample = [
            {
                **j,
                "enqueued_at": j["enqueued_at"].isoformat()
                    if j.get("enqueued_at") is not None else None,
            }
            for j in recent_failed_jobs(db, limit=10)
            if j.get("collection") == collection
        ]

        return {
            "collection": collection,
            "totals": {
                "completed": completed,
                "pending": pending,
                "in_flight": in_flight,
                "failed": failed,
                "total": total,
            },
            "by_field": by_field,
            "worker": worker,
            "recent": feed,
            "failed_sample": failed_sample,
        }
    finally:
        conn.close()


@router.post("/api/jobs/retry")
def retry_failed() -> dict:
    settings = Settings.from_environment()
    conn = MongoConnection.open(settings.uri, settings.database)
    try:
        n = reset_failed(conn.db)
        return {"reset": n}
    finally:
        conn.close()


class ReindexRequest(BaseModel):
    collection: str


class TeardownRequest(BaseModel):
    drop_data: bool = True


@router.post("/api/collections/{name}/teardown")
def teardown(name: str, req: TeardownRequest | None = None) -> dict:
    from mongosemantic.web.identifiers import IdentifierError, validate_identifier
    if req is None:
        req = TeardownRequest()
    try:
        validate_identifier(name)
    except IdentifierError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    settings = Settings.from_environment()
    conn = MongoConnection.open(settings.uri, settings.database)
    try:
        db = conn.db
        cfg = load_config(db, name)
        if not cfg:
            raise HTTPException(status_code=400, detail=f"{name} is not configured")
        dropped: list[str] = []
        if req.drop_data:
            if cfg.mode == "inline":
                db[name].update_many({}, {"$unset": {"_msem": ""}})
                dropped.append(f"inline _msem on {name}")
            elif cfg.shadow_collection:
                db.drop_collection(cfg.shadow_collection)
                dropped.append(cfg.shadow_collection)
        delete_config(db, name)
        return {"ok": True, "collection": name, "dropped": dropped}
    finally:
        conn.close()


@router.post("/api/reindex")
def reindex(req: ReindexRequest) -> dict:
    try:
        validate_identifier(req.collection)
    except IdentifierError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    settings = Settings.from_environment()
    conn = MongoConnection.open(settings.uri, settings.database)
    try:
        db = conn.db
        ensure_indexes(db)
        cfg = load_config(db, req.collection)
        if not cfg:
            raise HTTPException(status_code=400, detail=f"{req.collection} not configured")
        # Clear prior embedding state so the worker writes fresh rows.
        if cfg.mode == "inline":
            db[req.collection].update_many({}, {"$unset": {"_msem": ""}})
        elif cfg.shadow_collection:
            db[cfg.shadow_collection].delete_many({"source_collection": req.collection})
        enqueued = 0
        for doc in db[req.collection].find({}):
            enqueued += enqueue_for_doc(db, cfg, source_id=doc.get("_id"), doc=doc, force=True)
        return {"enqueued": enqueued}
    finally:
        conn.close()
