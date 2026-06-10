"""Hybrid search without Atlas Search.

A classic Mongo `$text` index on the shadow collection's chunk_text (works on
7.0 standalone, replica sets, and Atlas regular indexes - no Search-index slot
needed) supplies the keyword leg; reciprocal-rank fusion (same 1/(60+rank)
formula and 0.6/0.4 weights as Atlas $rankFusion) combines it with the vector
leg client-side.
"""

from __future__ import annotations

import logging
from typing import Any

from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.errors import OperationFailure

log = logging.getLogger(__name__)

TEXT_INDEX_NAME = "msem_chunk_text_text"
RRF_K = 60
HYBRID_WEIGHTS = (0.6, 0.4)  # vector, text - matches the Atlas path defaults


def ensure_text_index(shadow: Collection) -> bool:
    """Create the $text index on chunk_text (idempotent). False if impossible."""
    try:
        shadow.create_index([("chunk_text", "text")], name=TEXT_INDEX_NAME)
        return True
    except OperationFailure as e:
        # E.g. a different text index already exists (Mongo allows only one).
        log.warning("could not create text index on %s: %s", shadow.name, e)
        return False


def text_leg(
    db: Database,
    cfg: Any,
    collection: str,
    field_path: str,
    query_text: str,
    limit: int,
    allowed_ids: list[Any] | None = None,
) -> list[dict]:
    """Keyword search over shadow chunk_text, hydrated to the canonical row shape."""
    shadow = db[cfg.shadow_collection]
    if not ensure_text_index(shadow):
        return []
    query: dict[str, Any] = {
        "$text": {"$search": query_text},
        "field_path": field_path,
        "embedding_model": cfg.embedding_model,
    }
    if allowed_ids is not None:
        query["source_id"] = {"$in": list(allowed_ids)}
    try:
        rows = list(
            shadow.find(
                query,
                {
                    "score": {"$meta": "textScore"},
                    "source_id": 1,
                    "field_path": 1,
                    "chunk_index": 1,
                    "chunk_text": 1,
                },
            )
            .sort([("score", {"$meta": "textScore"})])
            .limit(limit)
        )
    except OperationFailure as e:
        log.warning("text leg failed on %s: %s", shadow.name, e)
        return []
    sids = list({r["source_id"] for r in rows})
    docs = {d["_id"]: d for d in db[collection].find({"_id": {"$in": sids}})}
    return [
        {
            "source_id": r["source_id"],
            "source_collection": collection,
            "field_path": field_path,
            "chunk_index": int(r.get("chunk_index") or 0),
            "chunk_text": r.get("chunk_text", ""),
            "source_doc": docs.get(r["source_id"]),
            "score": float(r.get("score", 0.0)),
        }
        for r in rows
    ]


def rrf_fuse(
    result_lists: list[list[dict]],
    weights: list[float] | tuple[float, ...] | None = None,
    k: int = RRF_K,
    limit: int = 10,
) -> list[dict]:
    """Reciprocal-rank fusion: score(doc) = sum(weight_i / (k + rank_i)), rank 1-based."""
    if weights is None:
        weights = [1.0] * len(result_lists)
    scores: dict[tuple, float] = {}
    best: dict[tuple, dict] = {}
    for rows, w in zip(result_lists, weights, strict=True):
        for rank, row in enumerate(rows, start=1):
            key = (str(row.get("source_id")), row.get("field_path"), row.get("chunk_index"))
            scores[key] = scores.get(key, 0.0) + w / (k + rank)
            best.setdefault(key, row)
    fused = []
    for key, s in scores.items():
        row = dict(best[key])
        row["score"] = s
        fused.append(row)
    fused.sort(key=lambda r: r["score"], reverse=True)
    return fused[:limit]
