from __future__ import annotations

from typing import Any

INLINE_ROOT = "_msem"


def inline_field_key(field_path: str) -> str:
    """Encode a (possibly dotted) field path into a single Mongo key.

    `"body"`     → `"body"`
    `"user.bio"` → `"user__bio"`

    Dots in source paths would otherwise be interpreted as sub-document traversal
    when used as a key inside the `_msem` sub-doc.
    """
    return field_path.replace(".", "__")


def inline_embedding_path(field_path: str) -> str:
    """Dotted Mongo path to the embedding for a configured field on a source doc."""
    return f"{INLINE_ROOT}.{inline_field_key(field_path)}.embedding"


def inline_text_path(field_path: str) -> str:
    return f"{INLINE_ROOT}.{inline_field_key(field_path)}.text"


def inline_hash_path(field_path: str) -> str:
    return f"{INLINE_ROOT}.{inline_field_key(field_path)}.hash"


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
