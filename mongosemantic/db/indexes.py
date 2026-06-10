from __future__ import annotations

import hashlib
from typing import Any

from pymongo import ASCENDING
from pymongo.collection import Collection
from pymongo.errors import OperationFailure


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
    name: str | None = None,
) -> str:
    """Create an Atlas Search vector index on `target`. Returns the index name.

    `path` is the dotted field where the vector lives on each document in `target`.
    Defaults to `"embedding"` (shadow-mode layout). For inline mode the caller
    passes the inline path (e.g. `_msem.body.embedding`).

    `name` overrides the deterministic index name — migrations build the new
    index on a temp shadow under a `_mig_<ts>` name and record that name in
    the config, so the index actually created MUST match what gets recorded.

    Safe to call repeatedly — an already-existing index is left in place.
    """
    name = name or vector_index_name(collection, field_path)
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


# --- Atlas Search (BM25 / keyword) index — used by hybrid search ----------

def search_index_definition(path: str = "chunk_text") -> dict[str, Any]:
    """Atlas Search index definition for a single text field."""
    return {
        "mappings": {
            "dynamic": False,
            "fields": {
                path: {"type": "string", "analyzer": "lucene.standard"},
                "field_path": {"type": "string", "analyzer": "lucene.keyword"},
            },
        }
    }


def create_atlas_search_index(
    target: Collection,
    name: str,
    path: str = "chunk_text",
) -> str:
    """Create an Atlas Search (BM25) index on `target`. Returns the index name.

    Safe to call repeatedly — an already-existing index is left in place.
    """
    existing = {idx.get("name") for idx in list(target.list_search_indexes())}
    if name in existing:
        return name
    target.create_search_index(
        {"name": name, "type": "search", "definition": search_index_definition(path)}
    )
    return name


def atlas_search_index_exists(target: Collection, name: str) -> bool:
    """True if an Atlas Search (BM25) index with this exact name exists.

    Catches OperationFailure so non-Atlas deployments (where
    $listSearchIndexes is unsupported) read as "no index" instead of crashing.
    """
    try:
        return any(idx.get("name") == name for idx in target.list_search_indexes())
    except OperationFailure:
        return False
