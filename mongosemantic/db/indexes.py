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


def vector_index_definition(dim: int, path: str = "embedding") -> dict[str, Any]:
    return {
        "fields": [
            {
                "type": "vector",
                "path": path,
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
    target: Collection,
    collection: str,
    field_path: str,
    dim: int,
    path: str = "embedding",
) -> str:
    """Create an Atlas Search vector index on `target`. Returns the index name.

    `path` is the dotted field where the vector lives on each document in `target`.
    Defaults to `"embedding"` (shadow-mode layout). For inline mode the caller
    passes the inline path (e.g. `_msem.body.embedding`).

    Safe to call repeatedly — an already-existing index is left in place.
    """
    name = vector_index_name(collection, field_path)
    existing = {idx.get("name") for idx in list(target.list_search_indexes())}
    if name in existing:
        return name
    definition = vector_index_definition(dim, path=path)
    target.create_search_index(
        {"name": name, "type": "vectorSearch", "definition": definition}
    )
    return name


def atlas_vector_index_exists(
    target: Collection, collection: str, field_path: str
) -> bool:
    name = vector_index_name(collection, field_path)
    return any(
        idx.get("name") == name for idx in target.list_search_indexes()
    )


def suggested_atlas_command(
    collection: str,
    field_path: str,
    target_coll: str,
    dim: int,
    path: str = "embedding",
) -> str:
    name = vector_index_name(collection, field_path)
    definition = vector_index_definition(dim, path=path)
    return (
        f"db.{target_coll}.createSearchIndex("
        f'{{"name": "{name}", "type": "vectorSearch", '
        f'"definition": {definition}}})'
    )
