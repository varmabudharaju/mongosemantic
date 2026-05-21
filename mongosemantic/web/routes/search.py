from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from mongosemantic.commands.search import _run_one, hybrid_available, run_one_hybrid
from mongosemantic.config import Settings
from mongosemantic.db.client import MongoConnection
from mongosemantic.embeddings.provider import get_provider
from mongosemantic.search.cross_collection import min_max_normalize, per_collection_targets
from mongosemantic.state import load_config
from mongosemantic.web.identifiers import IdentifierError, validate_identifier

router = APIRouter()


def _is_json_safe(v: object) -> bool:
    """A value is JSON-safe (after the explicit ObjectId stringification
    below) iff it's a primitive, a list/tuple of safe values, or a dict
    with string keys and safe values. Anything else — notably `bytes`,
    `bson.binary.Binary`, raw `ObjectId` in nested fields — would either
    crash pydantic v2 with UnicodeDecodeError or serialize as garbage."""
    if v is None or isinstance(v, (str, int, float, bool)):
        return True
    if isinstance(v, (list, tuple)):
        return all(_is_json_safe(x) for x in v)
    if isinstance(v, dict):
        return all(isinstance(k, str) and _is_json_safe(x) for k, x in v.items())
    return False


def _serialize(row: dict) -> dict:
    out = {
        k: row[k]
        for k in ("source_id", "source_collection", "field_path", "chunk_index", "chunk_text", "score")
        if k in row
    }
    src = row.get("source_doc")
    if isinstance(src, dict):
        # Keep user-visible fields and `_id`; drop other underscore-prefixed
        # internals AND anything that isn't JSON-safe (e.g. BSON Binary
        # `plot_embedding` from Atlas sample data — pydantic v2 raises
        # UnicodeDecodeError on raw bytes during JSON encoding).
        clean: dict = {}
        for k, v in src.items():
            if k.startswith("_") and k != "_id":
                continue
            if k == "_id":
                clean[k] = str(v)
            elif _is_json_safe(v):
                clean[k] = v
            # else: silently drop — binary blobs, raw ObjectIds in nested
            # fields, datetimes (handled separately by pydantic), etc.
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
) -> dict:
    if collection:
        try:
            validate_identifier(collection)
        except IdentifierError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
    settings = Settings()
    conn = MongoConnection.open(settings.uri, settings.database)
    notice: str | None = None
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
        return {"query": q, "rows": [_serialize(r) for r in rows], "notice": notice}
    finally:
        conn.close()
