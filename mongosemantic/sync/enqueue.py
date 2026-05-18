from __future__ import annotations

from typing import Any

from pymongo.database import Database

from mongosemantic.chunking.splitter import ChunkConfig, chunk_text
from mongosemantic.db.queries import inline_field_key
from mongosemantic.state import enqueue_embed
from mongosemantic.state.config_store import CollectionConfig, FieldSpec
from mongosemantic.sync.change_stream import _get_path, _resolve_text, hash_text


def _chunks_for(text: str, spec: FieldSpec) -> list[str]:
    """Return the list of (text) chunks to embed for one field on one doc.

    Non-chunked → a single-element list containing the whole text.
    Chunked     → splitter output.
    """
    if not spec.chunked:
        return [text]
    return chunk_text(
        text,
        ChunkConfig(
            chunk_size_tokens=spec.chunk_size,
            overlap_tokens=spec.chunk_overlap,
        ),
    )


def enqueue_for_doc(
    db: Database,
    cfg: CollectionConfig,
    source_id: Any,
    doc: dict,
    *,
    force: bool = False,
) -> int:
    """Enqueue all embed jobs needed to cover `doc` per `cfg`.

    For each field spec:
      • Resolve the text, split if chunked, enqueue one job per chunk
        (with chunk_index = 0..N-1 when chunked, None when not).
      • Skip enqueue when an existing shadow row already matches the new hash
        (unless `force=True`).
      • If chunked, also drop any shadow rows for chunk indices past the new
        chunk count, so shrinking text doesn't leave stale chunks behind.

    Returns the number of jobs enqueued.
    """
    enqueued = 0
    shadow = db[cfg.shadow_collection] if cfg.shadow_collection else None
    is_inline = cfg.mode == "inline"
    for spec in cfg.fields:
        text = _resolve_text(_get_path(doc, spec.path))
        if not text:
            continue
        chunks = _chunks_for(text, spec)
        if not chunks:
            continue
        for i, chunk in enumerate(chunks):
            chunk_index_in_job = i if spec.chunked else None
            chunk_index_in_shadow = i if spec.chunked else 0
            new_hash = hash_text(cfg.embedding_model, chunk)
            if not force:
                if is_inline:
                    existing_hash = (
                        (doc.get("_msem") or {}).get(inline_field_key(spec.path), {})
                        .get("hash")
                    )
                    if existing_hash == new_hash:
                        continue
                elif shadow is not None:
                    existing = shadow.find_one(
                        {
                            "source_id": source_id,
                            "field_path": spec.path,
                            "chunk_index": chunk_index_in_shadow,
                            "embedding_model": cfg.embedding_model,
                        },
                        {"embedding_hash": 1},
                    )
                    if existing and existing.get("embedding_hash") == new_hash:
                        continue
            enqueue_embed(
                db,
                collection=cfg.collection,
                source_id=source_id,
                field_path=spec.path,
                chunk_index=chunk_index_in_job,
                input_text=chunk,
                input_hash=new_hash,
                model=cfg.embedding_model,
            )
            enqueued += 1
        if spec.chunked and shadow is not None:
            shadow.delete_many(
                {
                    "source_id": source_id,
                    "field_path": spec.path,
                    "embedding_model": cfg.embedding_model,
                    "chunk_index": {"$gte": len(chunks)},
                }
            )
    return enqueued
