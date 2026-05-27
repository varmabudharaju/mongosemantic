from __future__ import annotations

import time
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Query

from mongosemantic.commands.search import _run_one, hybrid_available, run_one_hybrid
from mongosemantic.config import Settings
from mongosemantic.db.client import MongoConnection
from mongosemantic.embeddings.provider import get_provider
from mongosemantic.search.cross_collection import min_max_normalize, per_collection_targets
from mongosemantic.state import load_config
from mongosemantic.web.identifiers import IdentifierError, validate_identifier

router = APIRouter()

# Common BSON / Python scalars that pydantic v2 either chokes on or serializes
# inconsistently across versions. We stringify these so the UI gets a useful
# value rather than a silent drop (e.g. `released` datetime, `imdb.rating`
# Decimal128, nested ObjectId in `cast` for collections that store refs).
_STRINGIFY_TYPES = (datetime, date, UUID, Decimal, ObjectId)


def _safe(v: object) -> object | None:
    """Recursively convert a value to something JSON-safe.

    - Primitives (None, str, int, float, bool) pass through.
    - Common BSON / Python scalars (datetime, Decimal, UUID, ObjectId) get
      stringified.
    - Lists/tuples are recursed; opaque elements drop out.
    - Dicts (string keys only) are recursed; opaque values drop out.
    - Anything else — notably `bytes` and `bson.binary.Binary` (Atlas's
      pre-computed `plot_embedding` blobs are 6+ KB of these) — returns
      None and the caller drops the field. Pydantic v2 would otherwise
      raise UnicodeDecodeError mid-encoding.
    """
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, _STRINGIFY_TYPES):
        return str(v)
    if isinstance(v, (list, tuple)):
        return [s for s in (_safe(x) for x in v) if s is not None]
    if isinstance(v, dict):
        out: dict = {}
        for k, x in v.items():
            if not isinstance(k, str):
                continue
            s = _safe(x)
            if s is not None:
                out[k] = s
        return out
    return None  # opaque (bytes, Binary, custom classes) -> drop


def _serialize(row: dict) -> dict:
    out = {
        k: row[k]
        for k in ("source_id", "source_collection", "field_path", "chunk_index", "chunk_text", "score")
        if k in row
    }
    src = row.get("source_doc")
    if isinstance(src, dict):
        # Keep user-visible fields and `_id`; drop other underscore-prefixed
        # internals. Run every kept value through _safe so BSON blobs
        # (e.g. plot_embedding) don't crash pydantic and useful BSON
        # scalars (datetime, Decimal128) survive as strings.
        clean: dict = {}
        for k, v in src.items():
            if k.startswith("_") and k != "_id":
                continue
            safe_v = _safe(v)
            if safe_v is not None:
                clean[k] = safe_v
        out["source_doc"] = clean
    if "source_id" in out:
        out["source_id"] = str(out["source_id"])
    return out


@router.get("/api/search")
def search(
    q: str = Query(..., min_length=1, max_length=2000),
    collection: str | None = Query(None),
    limit: int = Query(10, ge=1, le=100),
    hybrid: bool = Query(False),
    min_score: float = Query(0.0, ge=0.0, le=1.0),
) -> dict:
    if collection:
        try:
            validate_identifier(collection)
        except IdentifierError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
    settings = Settings.from_environment()
    conn = MongoConnection.open(settings.uri, settings.database)
    notice: str | None = None
    started = time.perf_counter()
    try:
        db = conn.db

        # Embed the query with the collection's *own* model — not the
        # global default. Otherwise after a migration the dims mismatch
        # and the search returns noise.
        qvec_cache: dict[str, list[float]] = {}
        def _qvec(model: str) -> list[float]:
            if model not in qvec_cache:
                qvec_cache[model] = get_provider(model).embed(q).tolist()
            return qvec_cache[model]

        def _run(cfg, name):
            qv = _qvec(cfg.embedding_model)
            if hybrid and hybrid_available(cfg, conn.topology):
                return run_one_hybrid(db, cfg, name, q, qv, limit, conn.topology)
            return _run_one(db, cfg, name, qv, limit, conn.topology)

        if collection:
            cfg = load_config(db, collection)
            if not cfg:
                raise HTTPException(status_code=400, detail=f"{collection} is not configured")
            if hybrid and not hybrid_available(cfg, conn.topology):
                notice = "hybrid requires Atlas + shadow mode — falling back to pure semantic"
            rows = _run(cfg, collection)
        else:
            targets = per_collection_targets(db)
            if not targets:
                raise HTTPException(status_code=400, detail="no collections configured")
            all_rows: list[dict] = []
            models: dict[str, str] = {}
            for name in targets:
                cfg = load_config(db, name)
                if cfg is None:
                    continue
                models[name] = cfg.embedding_model
                all_rows.extend(_run(cfg, name))
            if len(set(models.values())) > 1:
                all_rows = min_max_normalize(all_rows, "score")
            all_rows.sort(key=lambda r: r.get("score", 0), reverse=True)
            rows = all_rows[:limit]
        # Apply score threshold AFTER ranking and limit so users still see
        # the top N even if all are below threshold (we surface the gap in
        # the UI rather than returning an empty list silently).
        if min_score > 0:
            rows = [r for r in rows if r.get("score", 0) >= min_score]
        took_ms = int((time.perf_counter() - started) * 1000)
        return {
            "query": q,
            "rows": [_serialize(r) for r in rows],
            "notice": notice,
            "took_ms": took_ms,
        }
    finally:
        conn.close()
