from __future__ import annotations

from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel

from mongosemantic.config import Settings
from mongosemantic.db.client import MongoConnection
from mongosemantic.migration import MigrationError, migrate_collection
from mongosemantic.web.identifiers import IdentifierError, validate_identifier

router = APIRouter()


class MigrateRequest(BaseModel):
    model: str
    drop_archive: bool = False


@router.post("/api/collections/{name}/migrate")
def migrate(name: str = Path(...), req: MigrateRequest = ...) -> dict:
    try:
        validate_identifier(name)
    except IdentifierError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    settings = Settings()
    conn = MongoConnection.open(settings.uri, settings.database)
    try:
        try:
            result = migrate_collection(conn, name, req.model)
        except MigrationError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        archive = result.archive_collection
        if req.drop_archive:
            conn.db.drop_collection(archive)
            archive = None
        return {
            "collection": result.collection,
            "old_model": result.old_model,
            "new_model": result.new_model,
            "old_dim": result.old_dim,
            "new_dim": result.new_dim,
            "documents": result.documents,
            "chunks_written": result.chunks_written,
            "archive_collection": archive,
            "started_at": result.started_at.isoformat(),
            "finished_at": result.finished_at.isoformat(),
        }
    finally:
        conn.close()
