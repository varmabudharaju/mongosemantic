from __future__ import annotations

import threading

from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel

from mongosemantic.config import Settings
from mongosemantic.db.client import MongoConnection
from mongosemantic.migration import MigrationError, migrate_collection
from mongosemantic.web import migration_progress
from mongosemantic.web.identifiers import IdentifierError, validate_identifier

router = APIRouter()


class MigrateRequest(BaseModel):
    model: str
    drop_archive: bool = False
    # When true (default), run the migration in a background thread and
    # return immediately so the UI can poll progress. When false, block
    # until done — convenient for scripts and the integration test.
    background: bool = True


def _run_migration(collection: str, model: str, drop_archive: bool) -> None:
    """Background-thread entry point. Opens its own connection so it doesn't
    fight the request-thread's connection lifecycle."""
    settings = Settings.from_environment()
    conn = MongoConnection.open(settings.uri, settings.database)
    try:
        try:
            result = migrate_collection(
                conn, collection, model,
                progress=lambda processed, total:
                    migration_progress.update_progress(collection, processed, total),
            )
        except MigrationError as e:
            migration_progress.fail(collection, str(e))
            return
        except Exception as e:
            migration_progress.fail(collection, f"{type(e).__name__}: {e}")
            return
        archive = result.archive_collection
        if drop_archive:
            conn.db.drop_collection(archive)
            archive = None
        migration_progress.succeed(collection, archive or "", result.new_model)
    finally:
        conn.close()


@router.post("/api/collections/{name}/migrate")
def migrate(name: str = Path(...), req: MigrateRequest = ...) -> dict:
    try:
        validate_identifier(name)
    except IdentifierError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    migration_progress.start(name, req.model)
    if req.background:
        t = threading.Thread(
            target=_run_migration,
            args=(name, req.model, req.drop_archive),
            daemon=True,
            name=f"migrate-{name}",
        )
        t.start()
        return {"collection": name, "state": "running", "background": True}
    # Foreground (blocking) — used by tests and scripts that don't poll.
    _run_migration(name, req.model, req.drop_archive)
    p = migration_progress.get(name)
    assert p is not None
    if p.state == "failed":
        raise HTTPException(status_code=400, detail=p.error or "migration failed")
    return {
        "collection": name,
        "state": p.state,
        "new_model": p.new_model,
        "archive_collection": p.archive_collection,
        "processed": p.processed,
        "total": p.total,
    }


@router.get("/api/collections/{name}/migrate/progress")
def progress(name: str = Path(...)) -> dict:
    try:
        validate_identifier(name)
    except IdentifierError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    p = migration_progress.get(name)
    if p is None:
        return {"collection": name, "state": "idle"}
    return {
        "collection": name,
        "target_model": p.target_model,
        "state": p.state,
        "processed": p.processed,
        "total": p.total,
        "error": p.error,
        "new_model": p.new_model,
        "archive_collection": p.archive_collection,
        "started_at": p.started_at,
        "finished_at": p.finished_at,
    }
