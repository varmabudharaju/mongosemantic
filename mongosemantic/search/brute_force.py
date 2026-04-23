from __future__ import annotations

from typing import Any

from mongosemantic.db.queries import base_projection, lookup_source_stage, unwind_source_stage


def build_brute_pipeline(
    source_collection: str,
    field_path: str,
    query_vector: list[float],
    limit: int,
    filter_match: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    match_stage: dict[str, Any] = {"field_path": field_path}
    if filter_match:
        match_stage.update(filter_match)
    similarity_expr = {
        "$reduce": {
            "input": {"$zip": {"inputs": ["$embedding", {"$literal": query_vector}]}},
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
        {"$addFields": {"similarity": similarity_expr}},
        {"$sort": {"similarity": -1}},
        {"$limit": limit},
        lookup_source_stage(source_collection),
        unwind_source_stage(),
        base_projection("$similarity"),
    ]
