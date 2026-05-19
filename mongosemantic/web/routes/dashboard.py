from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from mongosemantic.config import Settings
from mongosemantic.db.client import MongoConnection
from mongosemantic.state import (
    count_by_status,
    delete_config,
    ensure_indexes,
    list_configured,
    list_heartbeats,
    load_config,
    recent_failed_jobs,
    reset_failed,
)
from mongosemantic.sync.enqueue import enqueue_for_doc
from mongosemantic.web.identifiers import IdentifierError, validate_identifier

router = APIRouter()


@router.get("/api/dashboard")
def dashboard() -> dict:
    settings = Settings()
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
    settings = Settings()
    conn = MongoConnection.open(settings.uri, settings.database)
    try:
        return {"jobs": count_by_status(conn.db)}
    finally:
        conn.close()


@router.post("/api/jobs/retry")
def retry_failed() -> dict:
    settings = Settings()
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
def teardown(name: str, req: TeardownRequest = TeardownRequest()) -> dict:
    from mongosemantic.web.identifiers import IdentifierError, validate_identifier
    try:
        validate_identifier(name)
    except IdentifierError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    settings = Settings()
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
    settings = Settings()
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
