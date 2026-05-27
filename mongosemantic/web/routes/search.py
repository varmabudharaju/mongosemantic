from __future__ import annotations

import csv
import io
import re
import time
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from bson import ObjectId, json_util
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from mongosemantic.commands.search import _run_one, hybrid_available, run_one_hybrid
from mongosemantic.config import Settings
from mongosemantic.db.client import MongoConnection, Topology
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


_EXPORT_COLUMNS = (
    "score",
    "source_collection",
    "source_id",
    "field_path",
    "chunk_index",
    "chunk_text",
    "source_doc_json",
)


def _slugify_for_filename(s: str, max_len: int = 40) -> str:
    """Make a filename-safe slug from the query: spaces -> dashes, drop
    anything outside [A-Za-z0-9_-], trim."""
    s = re.sub(r"\s+", "-", s.strip())
    s = re.sub(r"[^A-Za-z0-9_-]", "", s)
    return s[:max_len] or "search"


def _row_to_export_dict(row: dict) -> dict:
    """Pick the columns we export. source_doc is collapsed to a JSON string
    so CSV stays flat — readers who want structure can parse it back."""
    return {
        "score": row.get("score"),
        "source_collection": row.get("source_collection"),
        "source_id": row.get("source_id") if row.get("source_id") is not None else "",
        "field_path": row.get("field_path"),
        "chunk_index": row.get("chunk_index"),
        "chunk_text": row.get("chunk_text"),
        # `_serialize` already passed source_doc through, but it may still
        # contain BSON scalars; json_util.dumps is the canonical way to
        # serialize them.
        "source_doc_json": json_util.dumps(row.get("source_doc") or {}),
    }


def _csv_stream(rows: list[dict]):
    """Yield CSV bytes one row at a time so huge exports don't buffer
    the whole payload in memory before the first byte goes out."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_EXPORT_COLUMNS)
    writer.writeheader()
    yield buf.getvalue().encode("utf-8")
    for row in rows:
        buf.seek(0); buf.truncate(0)
        writer.writerow(_row_to_export_dict(row))
        yield buf.getvalue().encode("utf-8")


def _jsonl_stream(rows: list[dict]):
    """One JSON object per line, BSON-safe via json_util."""
    for row in rows:
        out = {
            "score": row.get("score"),
            "source_collection": row.get("source_collection"),
            "source_id": row.get("source_id"),
            "field_path": row.get("field_path"),
            "chunk_index": row.get("chunk_index"),
            "chunk_text": row.get("chunk_text"),
            "source_doc": row.get("source_doc"),
        }
        yield (json_util.dumps(out) + "\n").encode("utf-8")


@router.get("/api/search")
def search(
    request: Request,
    q: str = Query(..., min_length=1, max_length=2000),
    collection: str | None = Query(None),
    # The HNSW / brute paths each clamp to the actual row count, so asking
    # for more than exists is harmless — we just get back what's there. The
    # 100k ceiling is a sanity guard against runaway client inputs, not a
    # product cap.
    limit: int = Query(10, ge=1, le=100_000),
    hybrid: bool = Query(False),
    min_score: float = Query(0.0, ge=0.0, le=1.0),
    # `format=csv` and `format=jsonl` stream the same result set as a
    # download instead of returning the in-page JSON envelope.
    format: str = Query("json", pattern="^(json|csv|jsonl)$"),
):
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
        # and the search returns noise. The provider is fetched from a
        # process-wide registry so the SentenceTransformer is loaded once
        # per process instead of once per request.
        providers = request.app.state.providers
        qvec_cache: dict[str, list[float]] = {}
        def _qvec(model: str) -> list[float]:
            if model not in qvec_cache:
                prov = providers.get(model)
                if prov is None:
                    raise HTTPException(
                        status_code=503,
                        detail=f"embedding provider for {model!r} unavailable: "
                               f"{providers.reason(model)}",
                    )
                qvec_cache[model] = prov.embed(q).tolist()
            return qvec_cache[model]

        hnsw = getattr(request.app.state, "hnsw", None)

        def _try_hnsw(cfg, qv) -> list[dict] | None:
            """Fan out across configured fields, query each HNSW, merge.

            Returns None if ANY field is missing an index — falling back
            to the brute-force path then is safer than mixing fast hits
            from one field with no hits from another and pretending it's
            the full top-k.
            """
            if hnsw is None or cfg.mode != "shadow":
                return None
            merged: list[dict] = []
            for spec in cfg.fields:
                rows = hnsw.query(db, cfg, spec.path, qv, limit)
                if rows is None:
                    return None
                merged.extend(rows)
            merged.sort(key=lambda r: r.get("score", 0), reverse=True)
            return merged[:limit]

        def _run(cfg, name):
            qv = _qvec(cfg.embedding_model)
            if hybrid and hybrid_available(cfg, conn.topology):
                return run_one_hybrid(db, cfg, name, q, qv, limit, conn.topology)
            # Non-Atlas shadow setups try HNSW first; brute-force agg is
            # the universal fallback for inline mode, missing indexes, or
            # any unexpected runtime error inside the HNSW path.
            if conn.topology != Topology.ATLAS:
                fast = _try_hnsw(cfg, qv)
                if fast is not None:
                    return fast
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
        serialized = [_serialize(r) for r in rows]
        if format == "json":
            return {
                "query": q,
                "rows": serialized,
                "notice": notice,
                "took_ms": took_ms,
            }
        # Non-JSON: stream as a download. Filename includes the query slug
        # + a row count so it's easy to identify on disk.
        slug = _slugify_for_filename(q)
        n = len(serialized)
        if format == "csv":
            filename = f"mongosemantic-{slug}-{n}.csv"
            return StreamingResponse(
                _csv_stream(serialized),
                media_type="text/csv",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
        if format == "jsonl":
            filename = f"mongosemantic-{slug}-{n}.jsonl"
            return StreamingResponse(
                _jsonl_stream(serialized),
                media_type="application/x-ndjson",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
        # Unreachable — Query() pattern enforces the allowed values.
        raise HTTPException(status_code=400, detail=f"unknown format {format!r}")
    finally:
        conn.close()
