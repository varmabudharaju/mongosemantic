from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from pymongo.database import Database

from mongosemantic.db.queries import INLINE_ROOT, inline_field_key
from mongosemantic.embeddings.provider import EmbeddingProvider
from mongosemantic.state import (
    claim_batch,
    complete,
    fail,
    load_config,
)

log = logging.getLogger("mongosemantic.worker")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _write_embedding_shadow(
    db: Database, cfg, job: dict, vector: list[float]
) -> None:
    shadow = db[cfg.shadow_collection]
    chunk_index = job.get("chunk_index") if job.get("chunk_index") is not None else 0
    shadow.update_one(
        {
            "source_id": job["source_id"],
            "field_path": job["field_path"],
            "chunk_index": chunk_index,
            "embedding_model": cfg.embedding_model,
        },
        {
            "$set": {
                "source_collection": cfg.collection,
                "chunk_text": job["input_text"],
                "embedding": vector,
                "embedding_model": cfg.embedding_model,
                "embedding_dim": cfg.embedding_dim,
                "embedding_hash": job["input_hash"],
                "updated_at": _utcnow(),
            },
            "$setOnInsert": {"created_at": _utcnow()},
        },
        upsert=True,
    )


def _write_embedding_inline(
    db: Database, cfg, job: dict, vector: list[float]
) -> None:
    key = inline_field_key(job["field_path"])
    base = f"{INLINE_ROOT}.{key}"
    db[cfg.collection].update_one(
        {"_id": job["source_id"]},
        {
            "$set": {
                f"{base}.embedding": vector,
                f"{base}.model": cfg.embedding_model,
                f"{base}.dim": cfg.embedding_dim,
                f"{base}.hash": job["input_hash"],
                f"{base}.text": job["input_text"],
                f"{base}.updated_at": _utcnow(),
            }
        },
    )


def _write_embedding(
    db: Database, cfg_cache: dict, job: dict, vector: list[float]
) -> None:
    coll_name = job["collection"]
    if coll_name not in cfg_cache:
        cfg = load_config(db, coll_name)
        if not cfg:
            return
        cfg_cache[coll_name] = cfg
    cfg = cfg_cache[coll_name]
    if cfg.mode == "inline":
        _write_embedding_inline(db, cfg, job, vector)
    else:
        _write_embedding_shadow(db, cfg, job, vector)


def _handle_delete(db: Database, cfg_cache: dict, job: dict) -> None:
    coll_name = job["collection"]
    if coll_name not in cfg_cache:
        cfg = load_config(db, coll_name)
        if not cfg:
            return
        cfg_cache[coll_name] = cfg
    cfg = cfg_cache[coll_name]
    if cfg.mode == "inline":
        db[cfg.collection].update_one(
            {"_id": job["source_id"]}, {"$unset": {INLINE_ROOT: ""}}
        )
    else:
        db[cfg.shadow_collection].delete_many({"source_id": job["source_id"]})


def process_batch(
    db: Database, provider: EmbeddingProvider, worker_id: str, batch_size: int
) -> int:
    batch = claim_batch(db, worker_id, batch_size)
    if not batch:
        return 0
    cfg_cache: dict[str, Any] = {}
    embed_jobs = [j for j in batch if j.get("kind") == "embed"]
    delete_jobs = [j for j in batch if j.get("kind") == "delete"]
    for job in delete_jobs:
        try:
            _handle_delete(db, cfg_cache, job)
            complete(db, job["_id"])
        except Exception as e:
            log.exception("delete failed")
            fail(db, job["_id"], reason=str(e))
    if embed_jobs:
        texts = [j["input_text"] for j in embed_jobs]
        try:
            vectors = provider.embed_batch(texts)
        except Exception as e:
            log.exception("embed_batch failed")
            for job in embed_jobs:
                fail(db, job["_id"], reason=f"embed: {e}")
            return len(batch)
        for job, vec in zip(embed_jobs, vectors, strict=False):
            try:
                _write_embedding(db, cfg_cache, job, vec.tolist())
                complete(db, job["_id"])
            except Exception as e:
                log.exception("write failed")
                fail(db, job["_id"], reason=f"write: {e}")
    return len(batch)


class WorkerRunner:
    def __init__(
        self,
        db: Database,
        provider: EmbeddingProvider,
        batch_size: int = 32,
        idle_sleep: float = 2.0,
    ) -> None:
        self.db = db
        self.provider = provider
        self.batch_size = batch_size
        self.idle_sleep = idle_sleep
        self.worker_id = f"worker-{uuid.uuid4().hex[:8]}"
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        log.info("worker %s starting", self.worker_id)
        while not self._stop.is_set():
            n = process_batch(self.db, self.provider, self.worker_id, self.batch_size)
            if n == 0:
                time.sleep(self.idle_sleep)
