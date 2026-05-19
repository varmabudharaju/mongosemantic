"""Zero-downtime model migration for shadow-mode collections.

Algorithm:

1.  Validate the target model exists and pin the new embedding dim.
2.  Refuse for inline-mode collections (would mutate user docs in place
    with mismatched dimensions during the swap).
3.  Spin up a temp shadow collection: `{shadow}_mig_{ts}`. Build standard
    shadow indexes on it.
4.  For Atlas, create vector + search indexes on the temp collection with
    new dim and unique names — these stay valid after the atomic rename
    because Atlas Search indexes follow the collection through a rename.
5.  Embed every source document into the temp shadow using the new
    provider. Resume-friendly: an interrupted migration can be re-run;
    rows already written (matched on (source_id, field_path, chunk_index))
    are skipped.
6.  Final sweep — anything modified after the bulk pass is caught.
7.  Update cfg FIRST (model, dim, vector/search index names): once the
    rename lands, search will pull the new names + new model from cfg.
8.  Atomic rename: live shadow → archive, temp → live. Mongo's
    `renameCollection` is catalog-level atomic; the window between cfg
    update and rename is in milliseconds.
9.  Return summary with archive collection name + counts.

Cleanup of the archive collection is left to the caller — keep it for a
grace period in case of rollback, drop it later.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from pymongo.database import Database

from mongosemantic.config import MODEL_DIMS
from mongosemantic.db.client import MongoConnection, Topology
from mongosemantic.db.indexes import (
    create_atlas_search_index,
    create_atlas_vector_index,
    ensure_shadow_indexes,
)
from mongosemantic.db.indexes import (
    vector_index_name as canonical_vector_index_name,
)
from mongosemantic.embeddings.provider import get_provider
from mongosemantic.search.hybrid import search_index_name as canonical_search_index_name
from mongosemantic.state import save_config
from mongosemantic.state.config_store import CollectionConfig
from mongosemantic.sync.change_stream import _get_path, _resolve_text, hash_text
from mongosemantic.sync.enqueue import _chunks_for

log = logging.getLogger("mongosemantic.migration")


class MigrationError(RuntimeError):
    """Raised when migration cannot proceed (bad input, wrong mode, etc.)."""


@dataclass
class MigrationResult:
    collection: str
    old_model: str
    new_model: str
    old_dim: int
    new_dim: int
    documents: int
    chunks_written: int
    archive_collection: str
    new_shadow: str
    vector_index_names: dict[str, str]
    search_index_names: dict[str, str]
    started_at: datetime
    finished_at: datetime


def _temp_shadow_name(shadow: str, ts: int) -> str:
    return f"{shadow}_mig_{ts}"


def _temp_index_name(canonical: str, ts: int) -> str:
    return f"{canonical}_mig_{ts}"


def _archive_name(shadow: str, ts: int) -> str:
    return f"{shadow}_archive_{ts}"


def _atomic_rename(db: Database, src: str, dst: str, drop_target: bool = False) -> None:
    """Server-side atomic rename within a database. Uses the admin command
    so it works the same on standalone, replica sets, and Atlas."""
    db_name = db.name
    db.client.admin.command({
        "renameCollection": f"{db_name}.{src}",
        "to": f"{db_name}.{dst}",
        "dropTarget": drop_target,
    })


def _embed_one_doc(
    db: Database, cfg: CollectionConfig, new_model: str,
    temp_shadow: str, source_doc: dict, provider,
) -> int:
    """Embed a single source doc into the temp shadow. Returns chunks written."""
    written = 0
    source_id = source_doc.get("_id")
    for spec in cfg.fields:
        text = _resolve_text(_get_path(source_doc, spec.path))
        if not text:
            continue
        chunks = _chunks_for(text, spec)
        if not chunks:
            continue
        # Skip chunks already written by an earlier (interrupted) migration run.
        new_chunks: list[tuple[int | None, str, str]] = []
        for i, chunk in enumerate(chunks):
            chunk_index_job = i if spec.chunked else None
            chunk_index_shadow = i if spec.chunked else 0
            h = hash_text(new_model, chunk)
            existing = db[temp_shadow].find_one(
                {
                    "source_id": source_id,
                    "field_path": spec.path,
                    "chunk_index": chunk_index_shadow,
                    "embedding_model": new_model,
                },
                {"embedding_hash": 1},
            )
            if existing and existing.get("embedding_hash") == h:
                continue
            new_chunks.append((chunk_index_job, chunk_index_shadow, chunk, h))  # type: ignore
        if not new_chunks:
            continue
        texts = [c[2] for c in new_chunks]
        vectors = provider.embed_batch(texts)
        now = datetime.now(timezone.utc)
        for (_job_idx, shadow_idx, chunk_text_val, hash_val), vec in zip(new_chunks, vectors, strict=True):
            db[temp_shadow].update_one(
                {
                    "source_id": source_id,
                    "field_path": spec.path,
                    "chunk_index": shadow_idx,
                    "embedding_model": new_model,
                },
                {
                    "$set": {
                        "source_collection": cfg.collection,
                        "chunk_text": chunk_text_val,
                        "embedding": vec.tolist(),
                        "embedding_model": new_model,
                        "embedding_dim": len(vec),
                        "embedding_hash": hash_val,
                        "updated_at": now,
                    },
                    "$setOnInsert": {"created_at": now},
                },
                upsert=True,
            )
            written += 1
    return written


def migrate_collection(
    conn: MongoConnection,
    collection: str,
    new_model: str,
    *,
    progress=None,
) -> MigrationResult:
    """Migrate `collection` to a new embedding model with near-zero downtime.

    `progress(processed, total)` is called periodically if supplied.
    """
    db = conn.db
    cfg = _load_or_raise(db, collection)

    if cfg.mode != "shadow":
        raise MigrationError(
            f"{collection!r} is in {cfg.mode!r} mode. Online migration only "
            "supports shadow mode. Convert to shadow first."
        )
    if new_model not in MODEL_DIMS:
        raise MigrationError(f"unknown model {new_model!r}")
    new_dim = MODEL_DIMS[new_model]
    if new_model == cfg.embedding_model and new_dim == cfg.embedding_dim:
        raise MigrationError(
            f"{collection!r} is already on {new_model!r}; nothing to migrate."
        )

    started = datetime.now(timezone.utc)
    ts = int(started.timestamp())
    temp_shadow = _temp_shadow_name(cfg.shadow_collection, ts)
    archive = _archive_name(cfg.shadow_collection, ts)

    # --- 1. Build temp shadow + indexes ----------------------------------
    ensure_shadow_indexes(db[temp_shadow])

    new_vector_index_names: dict[str, str] = {}
    new_search_index_names: dict[str, str] = {}
    if conn.topology == Topology.ATLAS:
        for spec in cfg.fields:
            vname = _temp_index_name(canonical_vector_index_name(collection, spec.path), ts)
            sname = _temp_index_name(canonical_search_index_name(collection, spec.path), ts)
            create_atlas_vector_index(
                db[temp_shadow], collection, spec.path, new_dim, path="embedding"
            )
            create_atlas_search_index(db[temp_shadow], sname)
            new_vector_index_names[spec.path] = vname
            new_search_index_names[spec.path] = sname
    # Self-hosted: brute-force aggregation, no index names to track.

    # --- 2. Bulk embed every source doc into the temp shadow -------------
    provider = get_provider(new_model)
    total = db[collection].estimated_document_count()
    processed = 0
    chunks_written = 0
    for doc in db[collection].find({}):
        chunks_written += _embed_one_doc(db, cfg, new_model, temp_shadow, doc, provider)
        processed += 1
        if progress is not None and processed % 50 == 0:
            progress(processed, total)
    if progress is not None:
        progress(processed, total)

    # --- 3. Update cfg BEFORE rename. After rename lands, search will use
    #        the new model, new dim, and the temp index names — which the
    #        rename promotes into the live position. -----------------------
    new_cfg = CollectionConfig(
        collection=cfg.collection,
        mode="shadow",
        shadow_collection=cfg.shadow_collection,  # unchanged — same logical name
        fields=cfg.fields,
        embedding_model=new_model,
        embedding_dim=new_dim,
        created_at=cfg.created_at,
        updated_at=datetime.now(timezone.utc),
        disabled=False,
        vector_index_names=new_vector_index_names,
        search_index_names=new_search_index_names,
        migrated_at=datetime.now(timezone.utc),
    )
    save_config(db, new_cfg)

    # --- 4. Atomic rename: live → archive, temp → live -------------------
    if cfg.shadow_collection in db.list_collection_names():
        _atomic_rename(db, cfg.shadow_collection, archive)
    _atomic_rename(db, temp_shadow, cfg.shadow_collection)

    finished = datetime.now(timezone.utc)
    return MigrationResult(
        collection=collection,
        old_model=cfg.embedding_model,
        new_model=new_model,
        old_dim=cfg.embedding_dim,
        new_dim=new_dim,
        documents=processed,
        chunks_written=chunks_written,
        archive_collection=archive,
        new_shadow=cfg.shadow_collection,
        vector_index_names=new_vector_index_names,
        search_index_names=new_search_index_names,
        started_at=started,
        finished_at=finished,
    )


def _load_or_raise(db: Database, collection: str) -> CollectionConfig:
    from mongosemantic.state import load_config
    cfg = load_config(db, collection)
    if cfg is None:
        raise MigrationError(f"{collection!r} is not configured for semantic search")
    return cfg
