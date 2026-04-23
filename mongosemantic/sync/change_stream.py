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
    enqueue_embed,
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
    full = event.get("fullDocument") or {}
    if not full:
        return
    shadow = db[cfg.shadow_collection]
    for spec in cfg.fields:
        text = _resolve_text(_get_path(full, spec.path))
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
            collection=coll,
            source_id=key,
            field_path=spec.path,
            chunk_index=None if not spec.chunked else 0,
            input_text=text,
            input_hash=new_hash,
            model=cfg.embedding_model,
        )

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
