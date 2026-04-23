from __future__ import annotations

import hashlib
from typing import Any

from pymongo import ASCENDING
from pymongo.collection import Collection


def shadow_collection_name(source: str) -> str:
    return f"{source}_embeddings"


def vector_index_name(collection: str, field_path: str) -> str:
    digest = hashlib.sha1(field_path.encode()).hexdigest()[:8]
    return f"mongosemantic_{collection}_{digest}"


def vector_index_definition(dim: int) -> dict[str, Any]:
    return {
        "fields": [
            {
                "type": "vector",
                "path": "embedding",
                "numDimensions": dim,
                "similarity": "cosine",
            }
        ]
    }


def ensure_shadow_indexes(shadow: Collection) -> None:
    shadow.create_index(
        [
            ("source_id", ASCENDING),
            ("field_path", ASCENDING),
            ("chunk_index", ASCENDING),
            ("embedding_model", ASCENDING),
        ],
        unique=True,
        name="source_field_chunk_model_uniq",
    )
    shadow.create_index([("source_id", ASCENDING)], name="source_id_idx")
    shadow.create_index(
        [("embedding_model", ASCENDING)], name="embedding_model_idx"
    )


def create_atlas_vector_index(
    shadow: Collection, collection: str, field_path: str, dim: int
) -> str:
    """Create an Atlas Search vector index. Returns the index name.

    Safe to call repeatedly - an already-existing index is left in place.
    """
    name = vector_index_name(collection, field_path)
    existing = {idx.get("name") for idx in list(shadow.list_search_indexes())}
    if name in existing:
        return name
    definition = vector_index_definition(dim)
    shadow.create_search_index(
        {"name": name, "type": "vectorSearch", "definition": definition}
    )
    return name


def atlas_vector_index_exists(
    shadow: Collection, collection: str, field_path: str
) -> bool:
    name = vector_index_name(collection, field_path)
    return any(
        idx.get("name") == name for idx in shadow.list_search_indexes()
    )


def suggested_atlas_command(
    collection: str, field_path: str, shadow_coll: str, dim: int
) -> str:
    name = vector_index_name(collection, field_path)
    definition = vector_index_definition(dim)
    return (
        f"db.{shadow_coll}.createSearchIndex("
        f'{{"name": "{name}", "type": "vectorSearch", '
        f'"definition": {definition}}})'
    )
