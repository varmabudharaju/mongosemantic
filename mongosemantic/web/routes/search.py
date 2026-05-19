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


def _serialize(row: dict) -> dict:
    out = {
        k: row[k]
        for k in ("source_id", "source_collection", "field_path", "chunk_index", "chunk_text", "score")
        if k in row
    }
    src = row.get("source_doc")
    if isinstance(src, dict):
        clean = {k: v for k, v in src.items() if not k.startswith("_") or k == "_id"}
        if "_id" in clean:
            clean["_id"] = str(clean["_id"])
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
        provider = get_provider(settings.model)
        qvec = provider.embed(q).tolist()

        def _run(cfg, name):
            if hybrid and hybrid_available(cfg, conn.topology):
                return run_one_hybrid(db, cfg, name, q, qvec, limit, conn.topology)
            return _run_one(db, cfg, name, qvec, limit, conn.topology)

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
