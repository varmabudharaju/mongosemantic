from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel

from mongosemantic.config import Settings
from mongosemantic.db.client import MongoConnection
from mongosemantic.web.identifiers import IdentifierError, validate_identifier
from mongosemantic.web.safe_pipeline import PipelineSafetyError, validate_pipeline

router = APIRouter()

MAX_DOCS = 100
MAX_TIME_MS = 10_000


class AggregationRequest(BaseModel):
    pipeline: list[dict]


def _stringify(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _stringify(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_stringify(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


@router.post("/api/collections/{name}/aggregation")
def aggregation(name: str = Path(...), req: AggregationRequest = ...) -> dict:
    try:
        validate_identifier(name)
    except IdentifierError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    try:
        validate_pipeline(req.pipeline)
    except PipelineSafetyError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    settings = Settings.from_environment()
    conn = MongoConnection.open(settings.uri, settings.database)
    try:
        cursor = conn.db[name].aggregate(req.pipeline, maxTimeMS=MAX_TIME_MS)
        rows: list[dict] = []
        for i, doc in enumerate(cursor):
            if i >= MAX_DOCS:
                break
            rows.append(_stringify(doc))
        return {"rows": rows, "limit": MAX_DOCS, "truncated": len(rows) >= MAX_DOCS}
    finally:
        conn.close()
