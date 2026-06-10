from __future__ import annotations

from typing import Any

from mongosemantic.db.queries import base_projection, lookup_source_stage, unwind_source_stage
from mongosemantic.search.filtering import prefix_source_filter


def build_atlas_pipeline(
    source_collection: str,
    field_path: str,
    query_vector: list[float],
    limit: int,
    index_name: str,
    filter_match: dict[str, Any] | None = None,
    source_filter: dict[str, Any] | None = None,
    oversample: int = 5,
) -> list[dict[str, Any]]:
    fetch_limit = limit * oversample if source_filter else limit
    num_candidates = max(10 * fetch_limit, 100)
    vector_search: dict[str, Any] = {
        "index": index_name,
        "path": "embedding",
        "queryVector": query_vector,
        "numCandidates": num_candidates,
        "limit": fetch_limit,
    }
    if filter_match:
        vector_search["filter"] = filter_match
    pipeline: list[dict[str, Any]] = [
        {"$vectorSearch": vector_search},
        {"$match": {"field_path": field_path}},
        lookup_source_stage(source_collection),
        unwind_source_stage(),
    ]
    if source_filter:
        pipeline.append({"$match": prefix_source_filter(source_filter)})
        pipeline.append({"$limit": limit})
    pipeline.append(base_projection({"$meta": "vectorSearchScore"}))
    return pipeline
