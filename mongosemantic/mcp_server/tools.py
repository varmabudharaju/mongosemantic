"""MCP tool implementations as plain Python functions.

Each tool takes a `pymongo.database.Database` plus user-facing kwargs and
returns a JSON-serializable dict. Keeping the MCP wiring out of these
functions makes them trivial to unit-test with mongomock and lets us
reuse them from any transport (stdio, SSE, future HTTP, etc.).
"""
from __future__ import annotations

import logging
from typing import Any

from pymongo.database import Database
from pymongo.errors import OperationFailure

from mongosemantic.commands.search import _run_one, hybrid_available, run_one_hybrid
from mongosemantic.db.client import Topology
from mongosemantic.db.schema import inspect_collection as _inspect
from mongosemantic.db.schema import score_field
from mongosemantic.embeddings.provider import get_provider
from mongosemantic.migration import MigrationError, migrate_collection
from mongosemantic.search.cross_collection import (
    min_max_normalize,
    per_collection_targets,
)
from mongosemantic.search.filtering import FilterError, validate_filter
from mongosemantic.search.rerank import (
    RERANK_CANDIDATE_MULTIPLIER,
    get_reranker,
    rerank_reason,
)
from mongosemantic.state import (
    count_by_status,
    list_configured,
    load_config,
)
from mongosemantic.web.safe_pipeline import PipelineSafetyError, validate_pipeline

log = logging.getLogger(__name__)

MAX_AGG_DOCS = 100
MAX_AGG_TIME_MS = 10_000


def _band(score: int) -> str:
    if score >= 80:
        return "great"
    if score >= 60:
        return "good"
    if score >= 40:
        return "usable"
    return "not_recommended"


def _stringify(value: Any) -> Any:
    """JSON-safe conversion for pymongo result values (ObjectId, datetime, …)."""
    if isinstance(value, dict):
        return {k: _stringify(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_stringify(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _strip_embedding_fields(doc: dict) -> dict:
    """Drop the inline `_msem` sub-doc from a result — AI agents don't need to
    see raw embedding vectors when they ask for sample documents."""
    return {k: v for k, v in doc.items() if k != "_msem"}


# -----------------------------------------------------------------------------
# Tool: list_collections
# -----------------------------------------------------------------------------
def t_list_collections(db: Database) -> dict:
    configured = {c.collection: c for c in list_configured(db)}
    out: list[dict] = []
    for name in db.list_collection_names():
        if name.startswith("mongosemantic_") or name.endswith("_embeddings"):
            continue
        cfg = configured.get(name)
        out.append({
            "name": name,
            "status": "configured" if cfg else "not_configured",
            "fields": [f.path for f in cfg.fields] if cfg else [],
            "embedding_model": cfg.embedding_model if cfg else None,
            "mode": cfg.mode if cfg else None,
        })
    return {"collections": out}


# -----------------------------------------------------------------------------
# Tool: list_configured
# -----------------------------------------------------------------------------
def t_list_configured(db: Database) -> dict:
    return {
        "configured": [
            {
                "collection": c.collection,
                "fields": [f.path for f in c.fields],
                "embedding_model": c.embedding_model,
                "mode": c.mode,
                "chunked": any(f.chunked for f in c.fields),
                "shadow_collection": c.shadow_collection,
            }
            for c in list_configured(db)
        ]
    }


# -----------------------------------------------------------------------------
# Tool: inspect_collection
# -----------------------------------------------------------------------------
def t_inspect_collection(db: Database, name: str, sample: int = 500) -> dict:
    stats = _inspect(db[name], sample_size=sample)
    fields = []
    for path, fs in stats.items():
        score = score_field(fs)
        coverage = 1 - (fs.null_count / max(1, fs.count))
        fields.append({
            "path": path,
            "type": fs.type_name,
            "coverage": round(coverage, 3),
            "avg_len": round(fs.avg_len, 1),
            "score": score,
            "band": _band(score),
        })
    fields.sort(key=lambda f: -f["score"])
    return {"collection": name, "sample_size": sample, "fields": fields}


# -----------------------------------------------------------------------------
# Tool: get_sample_documents
# -----------------------------------------------------------------------------
def t_get_sample_documents(db: Database, name: str, limit: int = 5) -> dict:
    docs = list(db[name].aggregate([{"$sample": {"size": min(limit, 25)}}]))
    return {
        "collection": name,
        "documents": [_stringify(_strip_embedding_fields(d)) for d in docs],
    }


# -----------------------------------------------------------------------------
# Tool: get_status
# -----------------------------------------------------------------------------
def t_get_status(db: Database, topology: Topology) -> dict:
    cfgs = list_configured(db)
    total_embeddings = 0
    for cfg in cfgs:
        if cfg.mode == "inline":
            total_embeddings += db[cfg.collection].count_documents(
                {"_msem": {"$exists": True}}
            )
        elif cfg.shadow_collection:
            total_embeddings += db[cfg.shadow_collection].count_documents({})
    return {
        "topology": topology.value,
        "configured_count": len(cfgs),
        "configured": [c.collection for c in cfgs],
        "total_embeddings": total_embeddings,
        "jobs": count_by_status(db),
    }


# -----------------------------------------------------------------------------
# Tool: semantic_search
# -----------------------------------------------------------------------------
def _serialize_row(row: dict) -> dict:
    out = {
        k: row[k]
        for k in ("source_id", "source_collection", "field_path", "chunk_index",
                  "chunk_text", "score", "vector_score", "reranked")
        if k in row
    }
    if "source_id" in out:
        out["source_id"] = str(out["source_id"])
    src = row.get("source_doc")
    if isinstance(src, dict):
        out["source_doc"] = _stringify(_strip_embedding_fields(src))
    return out


def _validated_filter(flt: dict | None) -> dict | None:
    """Validate an MCP-supplied filter dict, surfacing failures as ValueError."""
    if flt is None:
        return None
    try:
        return validate_filter(flt)
    except FilterError as e:
        raise ValueError(f"invalid filter: {e}") from e


def _apply_rerank(query: str, rows: list[dict], limit: int) -> tuple[list[dict], str | None]:
    """Second retrieval stage: cross-encode (query, chunk) pairs and keep the
    top `limit`. If the rerank model can't load, degrade to the vector ranking
    (truncated) and explain why in the returned notice."""
    reranker = get_reranker()
    if reranker is None:
        return rows[:limit], f"rerank unavailable: {rerank_reason()}"
    try:
        return reranker.rerank(query, rows, limit), None
    except Exception as e:  # degrade to vector order, never fail the tool call
        log.exception("rerank failed; returning vector-ranked results")
        return rows[:limit], f"rerank failed: {e}"


def t_semantic_search(
    db: Database,
    topology: Topology,
    query: str,
    collection: str,
    limit: int = 10,
    filter: dict | None = None,
    rerank: bool = False,
) -> dict:
    if rerank and limit > 1000:
        raise ValueError("rerank supports limit <= 1000")
    source_filter = _validated_filter(filter)
    cfg = load_config(db, collection)
    if not cfg:
        raise ValueError(f"{collection!r} is not configured for semantic search")
    provider = get_provider(cfg.embedding_model)
    qvec = provider.embed(query).tolist()
    # Rerank is two-stage retrieval: over-fetch candidates, cross-encode, cut.
    fetch_limit = limit * RERANK_CANDIDATE_MULTIPLIER if rerank else limit
    try:
        rows = _run_one(db, cfg, collection, qvec, fetch_limit, topology,
                        source_filter=source_filter)
    except OperationFailure as e:
        # Filters pass through to MongoDB verbatim; a runtime rejection
        # (unknown operator, type mismatch, ...) is user input, not a bug.
        # Without a filter an OperationFailure is a genuine server error
        # and keeps propagating.
        if not source_filter:
            raise
        raise ValueError(f"filter rejected by MongoDB: {e}") from e
    notice = None
    if rerank:
        rows, notice = _apply_rerank(query, rows, limit)
    result = {
        "query": query,
        "collection": collection,
        "rows": [_serialize_row(r) for r in rows],
    }
    if notice:
        result["notice"] = notice
    return result


# -----------------------------------------------------------------------------
# Tool: hybrid_search (shadow mode, any topology)
# -----------------------------------------------------------------------------
def t_hybrid_search(
    db: Database,
    topology: Topology,
    query: str,
    collection: str,
    limit: int = 10,
    filter: dict | None = None,
    rerank: bool = False,
) -> dict:
    if rerank and limit > 1000:
        raise ValueError("rerank supports limit <= 1000")
    source_filter = _validated_filter(filter)
    cfg = load_config(db, collection)
    if not cfg:
        raise ValueError(f"{collection!r} is not configured for semantic search")
    provider = get_provider(cfg.embedding_model)
    qvec = provider.embed(query).tolist()
    fetch_limit = limit * RERANK_CANDIDATE_MULTIPLIER if rerank else limit
    notices: list[str] = []
    try:
        if hybrid_available(cfg, topology):
            mode = "hybrid"
            rows = run_one_hybrid(db, cfg, collection, query, qvec, fetch_limit,
                                  topology, source_filter=source_filter, hnsw=None)
        else:
            # Inline-mode collections have no chunk_text column to keyword-search.
            mode = "semantic_fallback"
            rows = _run_one(db, cfg, collection, qvec, fetch_limit, topology,
                            source_filter=source_filter)
            notices.append("hybrid requires shadow mode; returned pure semantic results")
    except OperationFailure as e:
        # Same contract as t_semantic_search: only a supplied filter turns a
        # runtime OperationFailure into user-input ValueError.
        if not source_filter:
            raise
        raise ValueError(f"filter rejected by MongoDB: {e}") from e
    if rerank:
        rows, rerank_notice = _apply_rerank(query, rows, limit)
        if rerank_notice:
            notices.append(rerank_notice)
    return {
        "query": query,
        "collection": collection,
        "mode": mode,
        "notice": "; ".join(notices) or None,
        "rows": [_serialize_row(r) for r in rows],
    }


# -----------------------------------------------------------------------------
# Tool: search_all_collections
# -----------------------------------------------------------------------------
def t_search_all_collections(
    db: Database, topology: Topology, query: str, limit: int = 10,
    rerank: bool = False,
) -> dict:
    if rerank and limit > 1000:
        raise ValueError("rerank supports limit <= 1000")
    targets = per_collection_targets(db)
    if not targets:
        return {"query": query, "rows": [], "message": "no collections configured"}
    fetch_limit = limit * RERANK_CANDIDATE_MULTIPLIER if rerank else limit
    all_rows: list[dict] = []
    models: dict[str, str] = {}
    # Embed once per distinct model, reuse across collections that share a model.
    qvec_cache: dict[str, list[float]] = {}
    for name in targets:
        cfg = load_config(db, name)
        if cfg is None:
            continue
        models[name] = cfg.embedding_model
        if cfg.embedding_model not in qvec_cache:
            qvec_cache[cfg.embedding_model] = (
                get_provider(cfg.embedding_model).embed(query).tolist()
            )
        qvec = qvec_cache[cfg.embedding_model]
        all_rows.extend(_run_one(db, cfg, name, qvec, fetch_limit, topology))
    if len(set(models.values())) > 1:
        all_rows = min_max_normalize(all_rows, "score")
    all_rows.sort(key=lambda r: r.get("score", 0), reverse=True)
    notice = None
    if rerank:
        # One rerank over the merged rows: cross-encoder scores are comparable
        # across embedding models, so this also fixes mixed-model ordering.
        rows, notice = _apply_rerank(query, all_rows, limit)
    else:
        rows = all_rows[:limit]
    result = {
        "query": query,
        "rows": [_serialize_row(r) for r in rows],
    }
    if notice:
        result["notice"] = notice
    return result


# -----------------------------------------------------------------------------
# Tool: safe_aggregation
# -----------------------------------------------------------------------------
def t_safe_aggregation(db: Database, name: str, pipeline: list[dict]) -> dict:
    try:
        validate_pipeline(pipeline)
    except PipelineSafetyError as e:
        raise ValueError(f"pipeline rejected: {e}") from e
    cursor = db[name].aggregate(pipeline, maxTimeMS=MAX_AGG_TIME_MS)
    rows: list[dict] = []
    for i, doc in enumerate(cursor):
        if i >= MAX_AGG_DOCS:
            break
        rows.append(_stringify(_strip_embedding_fields(doc)))
    return {
        "collection": name,
        "rows": rows,
        "limit": MAX_AGG_DOCS,
        "truncated": len(rows) >= MAX_AGG_DOCS,
    }


# -----------------------------------------------------------------------------
# Tool: migrate_model
# -----------------------------------------------------------------------------
def t_migrate_model(conn, collection: str, new_model: str) -> dict:
    try:
        result = migrate_collection(conn, collection, new_model)
    except MigrationError as e:
        raise ValueError(str(e)) from e
    return {
        "collection": result.collection,
        "old_model": result.old_model,
        "new_model": result.new_model,
        "old_dim": result.old_dim,
        "new_dim": result.new_dim,
        "documents": result.documents,
        "chunks_written": result.chunks_written,
        "archive_collection": result.archive_collection,
        "started_at": result.started_at.isoformat(),
        "finished_at": result.finished_at.isoformat(),
    }


# -----------------------------------------------------------------------------
# Tool: get_schema_context
# -----------------------------------------------------------------------------
def t_get_schema_context(db: Database, name: str, sample: int = 100) -> dict:
    """A compact, AI-friendly summary of a collection's schema.

    Returns one line per field: path, inferred type, coverage, and an example
    value. Designed to fit comfortably in a system prompt so an AI agent
    can write reasonable aggregations against the collection.
    """
    stats = _inspect(db[name], sample_size=sample)
    sample_doc = db[name].find_one() or {}
    sample_clean = _stringify(_strip_embedding_fields(sample_doc))

    def _example(path: str) -> Any:
        cur: Any = sample_clean
        for part in path.split("."):
            if not isinstance(cur, dict):
                return None
            cur = cur.get(part)
        return cur

    fields = []
    for path, fs in stats.items():
        coverage = 1 - (fs.null_count / max(1, fs.count))
        fields.append({
            "path": path,
            "type": fs.type_name,
            "coverage": round(coverage, 3),
            "example": _example(path),
        })
    fields.sort(key=lambda f: f["path"])
    return {
        "collection": name,
        "sample_size": sample,
        "fields": fields,
        "note": (
            "Use safe_aggregation to run read-only pipelines against this "
            "collection. $out, $merge, $function, $accumulator are blocked."
        ),
    }
