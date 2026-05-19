"""Hybrid search: combine semantic (`$vectorSearch`) and keyword (`$search`)
results via `$rankFusion`, MongoDB Atlas's reciprocal-rank-fusion stage.

Atlas-only — `$rankFusion` and `$search` don't exist on self-hosted Mongo.
The CLI / web / MCP layers detect the topology and fall back to pure
semantic with a clear notice.
"""
from __future__ import annotations

import hashlib
from typing import Any

from mongosemantic.db.queries import (
    base_projection,
    lookup_source_stage,
    unwind_source_stage,
)


def search_index_name(collection: str, field_path: str) -> str:
    """Name of the Atlas Search (BM25) index for a given source field.

    Mirrors `db.indexes.vector_index_name` but lives in its own namespace so
    a collection can carry both indexes without collision.
    """
    digest = hashlib.sha1(field_path.encode()).hexdigest()[:8]
    return f"mongosemantic_search_{collection}_{digest}"


def build_hybrid_pipeline(
    source_collection: str,
    field_path: str,
    query_text: str,
    query_vector: list[float],
    limit: int,
    vector_index_name: str,
    search_index_name: str,
    vector_weight: float = 0.6,
    text_weight: float = 0.4,
) -> list[dict[str, Any]]:
    """Build a `$rankFusion`-based hybrid search pipeline against a shadow collection.

    Both sub-pipelines are scoped to a single `field_path` so collections
    configured with multiple fields don't bleed across them.
    """
    if vector_weight <= 0 or text_weight <= 0:
        raise ValueError("hybrid weights must be positive")

    num_candidates = max(10 * limit, 100)

    vector_sub = [
        {
            "$vectorSearch": {
                "index": vector_index_name,
                "path": "embedding",
                "queryVector": query_vector,
                "numCandidates": num_candidates,
                "limit": limit,
            }
        },
        {"$match": {"field_path": field_path}},
    ]

    text_sub = [
        {
            "$search": {
                "index": search_index_name,
                "text": {"query": query_text, "path": "chunk_text"},
            }
        },
        {"$match": {"field_path": field_path}},
        {"$limit": limit},
    ]

    return [
        {
            "$rankFusion": {
                "input": {"pipelines": {"vector": vector_sub, "text": text_sub}},
                "combination": {"weights": {"vector": vector_weight, "text": text_weight}},
                "scoreDetails": True,
            }
        },
        {"$match": {"field_path": field_path}},
        {"$limit": limit},
        lookup_source_stage(source_collection),
        unwind_source_stage(),
        # Numeric fused score (sortable downstream). scoreDetails is a dict
        # and would break commands/search.py's sort-by-score.
        base_projection({"$meta": "score"}),
    ]
