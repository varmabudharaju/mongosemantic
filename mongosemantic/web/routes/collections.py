from __future__ import annotations

from fastapi import APIRouter, HTTPException, Path, Query

from mongosemantic.config import Settings
from mongosemantic.db.client import MongoConnection
from mongosemantic.db.schema import inspect_collection, score_field
from mongosemantic.state import list_configured, load_config
from mongosemantic.web.identifiers import IdentifierError, validate_identifier

router = APIRouter()


def _band(score: int) -> str:
    if score >= 80:
        return "great"
    if score >= 60:
        return "good"
    if score >= 40:
        return "usable"
    return "not_recommended"


@router.get("/api/collections")
def list_collections() -> dict:
    settings = Settings()
    conn = MongoConnection.open(settings.uri, settings.database)
    try:
        configured = {c.collection: c for c in list_configured(conn.db)}
        rows = []
        for name in conn.db.list_collection_names():
            if name.startswith("mongosemantic_") or name.endswith("_embeddings"):
                continue
            cfg = configured.get(name)
            rows.append({
                "name": name,
                "status": "configured" if cfg else "not_configured",
                "fields_count": len(cfg.fields) if cfg else 0,
                "embedding_model": cfg.embedding_model if cfg else None,
                "mode": cfg.mode if cfg else None,
            })
        return {"collections": rows, "topology": conn.topology.value}
    finally:
        conn.close()


@router.get("/api/collections/{name}/config")
def get_config(name: str = Path(...)) -> dict:
    """Return the existing config for a collection (or {configured: false}).
    Used by the Apply page to prefill the form when reconfiguring."""
    try:
        validate_identifier(name)
    except IdentifierError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    settings = Settings()
    conn = MongoConnection.open(settings.uri, settings.database)
    try:
        cfg = load_config(conn.db, name)
        if cfg is None:
            return {"collection": name, "configured": False}
        return {
            "collection": name,
            "configured": True,
            "mode": cfg.mode,
            "fields": [f.path for f in cfg.fields],
            "chunked": any(f.chunked for f in cfg.fields),
            "chunk_size": cfg.fields[0].chunk_size if cfg.fields else 512,
            "chunk_overlap": cfg.fields[0].chunk_overlap if cfg.fields else 64,
            "model": cfg.embedding_model,
        }
    finally:
        conn.close()


@router.get("/api/collections/{name}/sample")
def sample(name: str = Path(...), limit: int = Query(5, ge=1, le=20)) -> dict:
    """Return a few sample documents (embedding sub-docs stripped) so the user
    sees what the data actually looks like before configuring."""
    try:
        validate_identifier(name)
    except IdentifierError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    settings = Settings()
    conn = MongoConnection.open(settings.uri, settings.database)
    try:
        from mongosemantic.mcp_server.tools import _stringify, _strip_embedding_fields
        docs = list(conn.db[name].aggregate([{"$sample": {"size": limit}}]))
        return {
            "collection": name,
            "documents": [_stringify(_strip_embedding_fields(d)) for d in docs],
        }
    finally:
        conn.close()


@router.get("/api/collections/{name}/inspect")
def inspect(
    name: str = Path(...),
    sample: int = Query(500, ge=1, le=10000),
) -> dict:
    try:
        validate_identifier(name)
    except IdentifierError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    settings = Settings()
    conn = MongoConnection.open(settings.uri, settings.database)
    try:
        stats = inspect_collection(conn.db[name], sample_size=sample)
        fields = []
        for path, fs in stats.items():
            score = score_field(fs)
            coverage = 1 - (fs.null_count / max(1, fs.count))
            fields.append({
                "path": path,
                "type": fs.type_name,
                "count": fs.count,
                "null_count": fs.null_count,
                "avg_len": round(fs.avg_len, 1),
                "coverage": round(coverage, 3),
                "score": score,
                "band": _band(score),
            })
        fields.sort(key=lambda f: -f["score"])
        return {"collection": name, "sample_size": sample, "fields": fields}
    finally:
        conn.close()
