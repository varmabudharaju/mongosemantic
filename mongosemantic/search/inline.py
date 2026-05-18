"""Search pipelines for inline-mode collections (embeddings on the source doc)."""
from __future__ import annotations

from typing import Any

from mongosemantic.db.queries import (
    inline_embedding_path,
    inline_text_path,
)


def build_inline_brute_pipeline(
    field_path: str,
    query_vector: list[float],
    limit: int,
    filter_match: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Brute-force similarity search against inline embeddings on the source collection."""
    emb_path = inline_embedding_path(field_path)
    text_path = inline_text_path(field_path)
    match_stage: dict[str, Any] = {emb_path: {"$exists": True}}
    if filter_match:
        match_stage.update(filter_match)
    similarity_expr = {
        "$reduce": {
            "input": {"$zip": {"inputs": [f"${emb_path}", {"$literal": query_vector}]}},
            "initialValue": 0.0,
            "in": {
                "$add": [
                    "$$value",
                    {
                        "$multiply": [
                            {"$arrayElemAt": ["$$this", 0]},
                            {"$arrayElemAt": ["$$this", 1]},
                        ]
                    },
                ]
            },
        }
    }
    return [
        {"$match": match_stage},
        {"$addFields": {"_msem_score": similarity_expr}},
        {"$sort": {"_msem_score": -1}},
        {"$limit": limit},
        {
            "$project": {
                "source_id": "$_id",
                "field_path": {"$literal": field_path},
                "chunk_index": {"$literal": 0},
                "chunk_text": {"$ifNull": [f"${text_path}", ""]},
                "source_doc": "$$ROOT",
                "score": "$_msem_score",
            }
        },
    ]


def build_inline_atlas_pipeline(
    field_path: str,
    query_vector: list[float],
    limit: int,
    index_name: str,
    filter_match: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Atlas $vectorSearch against the inline embedding path on the source collection."""
    emb_path = inline_embedding_path(field_path)
    text_path = inline_text_path(field_path)
    num_candidates = max(10 * limit, 100)
    vector_search: dict[str, Any] = {
        "index": index_name,
        "path": emb_path,
        "queryVector": query_vector,
        "numCandidates": num_candidates,
        "limit": limit,
    }
    if filter_match:
        vector_search["filter"] = filter_match
    return [
        {"$vectorSearch": vector_search},
        {
            "$project": {
                "source_id": "$_id",
                "field_path": {"$literal": field_path},
                "chunk_index": {"$literal": 0},
                "chunk_text": {"$ifNull": [f"${text_path}", ""]},
                "source_doc": "$$ROOT",
                "score": {"$meta": "vectorSearchScore"},
            }
        },
    ]
