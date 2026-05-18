from __future__ import annotations

import hashlib
import logging
import threading
import time
from typing import Any

from pymongo.database import Database
from pymongo.errors import PyMongoError

from mongosemantic.state import (
    enqueue_delete_all,
    load_config,
    load_resume_token,
    save_resume_token,
)

log = logging.getLogger("mongosemantic.sync.change_stream")

def hash_text(model: str, text: str) -> str:
    h = hashlib.sha1()
    h.update(model.encode())
    h.update(b"\x00")
    h.update(text.encode("utf-8", errors="ignore"))
    return f"sha1:{h.hexdigest()}"

def _get_path(doc: dict, path: str) -> Any:
    """Dotted-path field access. Does not support array-of-subdocs in v0.1.0."""
    current: Any = doc
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current

def _resolve_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " ".join(str(v) for v in value)
    return str(value)

def _is_self_write(event: dict) -> bool:
    """True when an update event touches only `_msem.*` fields — i.e., us writing back."""
    desc = event.get("updateDescription") or {}
    updated = desc.get("updatedFields") or {}
    removed = desc.get("removedFields") or []
    if not updated and not removed:
        return False
    all_keys = list(updated.keys()) + list(removed)
    return all(k == "_msem" or k.startswith("_msem.") for k in all_keys)


def process_event(db: Database, event: dict) -> None:
    coll = event.get("ns", {}).get("coll")
    if not coll:
        return
    cfg = load_config(db, coll)
    if not cfg:
        return
    op = event.get("operationType")
    key = event.get("documentKey", {}).get("_id")
    if op == "delete":
        if key is not None:
            enqueue_delete_all(db, coll, key)
        return
    if op not in ("insert", "update", "replace"):
        return
    if cfg.mode == "inline" and op == "update" and _is_self_write(event):
        return
    full = event.get("fullDocument") or {}
    if not full:
        return
    from mongosemantic.sync.enqueue import enqueue_for_doc
    enqueue_for_doc(db, cfg, source_id=key, doc=full)

class ChangeStreamListener:
    def __init__(self, db: Database, collections: list[str]) -> None:
        self.db = db
        self.collections = collections
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        pipeline = [{"$match": {"ns.coll": {"$in": self.collections}}}]
        resume = load_resume_token(self.db)
        kwargs: dict[str, Any] = {
            "pipeline": pipeline,
            "full_document": "updateLookup",
        }
        if resume:
            kwargs["resume_after"] = resume
        try:
            with self.db.watch(**kwargs) as stream:
                while not self._stop.is_set():
                    event = stream.try_next()
                    if event is None:
                        time.sleep(0.1)
                        continue
                    try:
                        process_event(self.db, event)
                    except Exception:
                        log.exception("process_event failed for %s", event.get("ns"))
                    save_resume_token(self.db, stream.resume_token)
        except PyMongoError:
            log.exception("change stream crashed")
            raise
