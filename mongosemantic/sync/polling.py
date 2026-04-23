from __future__ import annotations

from typing import Any

from pymongo.database import Database

from mongosemantic.state import (
    enqueue_embed,
    load_config,
    load_polling_watermark,
    save_polling_watermark,
)
from mongosemantic.sync.change_stream import _get_path, _resolve_text, hash_text


def poll_once(
    db: Database,
    collection: str,
    watermark_field: str = "updated_at",
    batch_size: int = 200,
) -> int:
    """Scan for new/updated docs. Returns number of jobs enqueued."""
    cfg = load_config(db, collection)
    if not cfg:
        return 0
    last = load_polling_watermark(db, collection)
    filter_ = {} if last is None else {watermark_field: {"$gt": last}}
    cursor = db[collection].find(filter_).sort(watermark_field, 1).limit(batch_size)
    new_wm: Any = last
    enqueued = 0
    shadow = db[cfg.shadow_collection]
    for doc in cursor:
        wm_val = doc.get(watermark_field)
        if wm_val is not None and (new_wm is None or wm_val > new_wm):
            new_wm = wm_val
        key = doc.get("_id")
        for spec in cfg.fields:
            text = _resolve_text(_get_path(doc, spec.path))
            if not text:
                continue
            new_hash = hash_text(cfg.embedding_model, text)
            existing = shadow.find_one(
                {
                    "source_id": key,
                    "field_path": spec.path,
                    "chunk_index": 0,
                    "embedding_model": cfg.embedding_model,
                },
                {"embedding_hash": 1},
            )
            if existing and existing.get("embedding_hash") == new_hash:
                continue
            enqueue_embed(
                db,
                collection=collection,
                source_id=key,
                field_path=spec.path,
                chunk_index=None if not spec.chunked else 0,
                input_text=text,
                input_hash=new_hash,
                model=cfg.embedding_model,
            )
            enqueued += 1
    if new_wm is not None and new_wm != last:
        save_polling_watermark(db, collection, new_wm)
    return enqueued
