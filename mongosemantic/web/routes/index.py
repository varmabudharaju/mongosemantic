from __future__ import annotations

from fastapi import APIRouter, HTTPException, Path

from mongosemantic.config import Settings
from mongosemantic.db.client import MongoConnection
from mongosemantic.state import ensure_indexes, load_config
from mongosemantic.sync.enqueue import enqueue_for_doc
from mongosemantic.web import progress
from mongosemantic.web.identifiers import IdentifierError, validate_identifier

router = APIRouter()


@router.post("/api/collections/{name}/index")
def start_index(name: str = Path(...)) -> dict:
    try:
        validate_identifier(name)
    except IdentifierError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    settings = Settings()
    conn = MongoConnection.open(settings.uri, settings.database)
    try:
        db = conn.db
        ensure_indexes(db)
        cfg = load_config(db, name)
        if not cfg:
            raise HTTPException(status_code=400, detail=f"{name} is not configured")
        total = db[name].estimated_document_count()
        progress.start(name, total)
        enqueued = 0
        for doc in db[name].find({}):
            enqueued += enqueue_for_doc(db, cfg, source_id=doc.get("_id"), doc=doc)
            progress.bump(name)
        progress.finish(name)
        return {"ok": True, "enqueued": enqueued, "total": total}
    finally:
        conn.close()


@router.get("/api/collections/{name}/index/progress")
def get_progress(name: str = Path(...)) -> dict:
    try:
        validate_identifier(name)
    except IdentifierError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    p = progress.get(name)
    if p is None:
        return {"collection": name, "running": False, "enqueued": 0, "total": 0}
    return {
        "collection": name,
        "running": p.finished_at is None,
        "enqueued": p.enqueued,
        "total": p.total,
        "started_at": p.started_at,
        "finished_at": p.finished_at,
    }
