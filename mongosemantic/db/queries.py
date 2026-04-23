from __future__ import annotations

from typing import Any


def lookup_source_stage(source_collection: str) -> dict[str, Any]:
    return {
        "$lookup": {
            "from": source_collection,
            "localField": "source_id",
            "foreignField": "_id",
            "as": "source_doc",
        }
    }

def unwind_source_stage() -> dict[str, Any]:
    return {"$unwind": {"path": "$source_doc", "preserveNullAndEmptyArrays": True}}

def base_projection(score_expr: dict[str, Any]) -> dict[str, Any]:
    return {
        "$project": {
            "source_id": 1,
            "field_path": 1,
            "chunk_index": 1,
            "chunk_text": 1,
            "source_doc": 1,
            "score": score_expr,
        }
    }
