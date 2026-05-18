from __future__ import annotations

from typing import Any

from pymongo.database import Database

from mongosemantic.state import (
    load_config,
    load_polling_watermark,
    save_polling_watermark,
)
from mongosemantic.sync.enqueue import enqueue_for_doc


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
    for doc in cursor:
        wm_val = doc.get(watermark_field)
        if wm_val is not None and (new_wm is None or wm_val > new_wm):
            new_wm = wm_val
        enqueued += enqueue_for_doc(db, cfg, source_id=doc.get("_id"), doc=doc)
    if new_wm is not None and new_wm != last:
        save_polling_watermark(db, collection, new_wm)
    return enqueued
