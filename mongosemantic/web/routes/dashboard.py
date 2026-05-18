from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from mongosemantic.config import Settings
from mongosemantic.db.client import MongoConnection
from mongosemantic.state import (
    count_by_status,
    ensure_indexes,
    list_configured,
    load_config,
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
