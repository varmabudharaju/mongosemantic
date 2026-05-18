from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from mongosemantic.config import Settings
from mongosemantic.db.client import MongoConnection

router = APIRouter()


class ConnectRequest(BaseModel):
    uri: str
    database: str


@router.get("/api/topology")
def topology() -> dict:
    settings = Settings()
    conn = MongoConnection.open(settings.uri, settings.database)
    try:
        return {"topology": conn.topology.value}
    finally:
        conn.close()


@router.post("/api/connect")
def connect(req: ConnectRequest) -> dict:
    if not (req.uri.startswith("mongodb://") or req.uri.startswith("mongodb+srv://")):
        raise HTTPException(
            status_code=400,
            detail="URI must start with mongodb:// or mongodb+srv://",
        )
    if not req.database:
        raise HTTPException(status_code=400, detail="database is required")
    try:
        conn = MongoConnection.open(req.uri, req.database)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"could not connect: {e}") from e
    try:
        return {"topology": conn.topology.value}
    finally:
        conn.close()
