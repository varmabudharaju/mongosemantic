from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from pymongo.database import Database

from mongosemantic.db.queries import INLINE_ROOT, inline_field_key
from mongosemantic.embeddings.provider import EmbeddingProvider, get_provider
from mongosemantic.state import (
    claim_batch,
    complete,
    fail,
    load_config,
    remove_heartbeat,
    write_heartbeat,
)

HEARTBEAT_INTERVAL_S = 10.0

log = logging.getLogger("mongosemantic.worker")


class ProviderRegistry:
    """Lazy, per-model provider cache used by `process_batch`.

    A worker can process jobs across collections that use different
    embedding models. Loading providers lazily (and remembering which
    ones failed to load) keeps a missing OpenAI key or unreachable
    Ollama from stopping work on the models that *do* work.
    """

    def __init__(self) -> None:
        self._cache: dict[str, EmbeddingProvider] = {}
        self._failed: dict[str, str] = {}  # model_key → reason

    def get(self, model_key: str) -> EmbeddingProvider | None:
        if model_key in self._cache:
            return self._cache[model_key]
        if model_key in self._failed:
            return None
        try:
            self._cache[model_key] = get_provider(model_key)
            return self._cache[model_key]
        except Exception as e:
            log.exception("failed to load provider for model %r", model_key)
            self._failed[model_key] = str(e)
            return None

    def reason(self, model_key: str) -> str:
        return self._failed.get(model_key, "unknown error")


class _SingleProviderRegistry(ProviderRegistry):
    """Adapter so legacy callers passing a single provider keep working.

    The provider serves any model whose key matches `provider.model_name`;
    other models load lazily through the normal path.
    """

    def __init__(self, provider: EmbeddingProvider) -> None:
        super().__init__()
        if getattr(provider, "model_name", None):
            self._cache[provider.model_name] = provider


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
    db: Database,
    provider: EmbeddingProvider | ProviderRegistry,
    worker_id: str,
    batch_size: int,
) -> int:
    """Claim and process up to `batch_size` jobs.

    `provider` may be either a single EmbeddingProvider (legacy) or a
    ProviderRegistry. With a registry, embed jobs are grouped by model
    so a worker can serve collections configured with different models
    in a single pass. If a model's provider can't be loaded, only that
    model's jobs are failed — others continue.
    """
    if isinstance(provider, ProviderRegistry):
        registry = provider
    else:
        registry = _SingleProviderRegistry(provider)
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
    # Group embed jobs by the model recorded on the job (set at enqueue
    # time from the collection's config). Each group is embedded with its
    # own provider — never with a substitute, never silently.
    by_model: dict[str, list[dict]] = defaultdict(list)
    for j in embed_jobs:
        by_model[j.get("model") or ""].append(j)
    for model_key, jobs in by_model.items():
        prov = registry.get(model_key) if model_key else None
        if prov is None:
            reason = (
                f"no provider for model {model_key!r}: {registry.reason(model_key)}"
                if model_key else "job has no model"
            )
            for job in jobs:
                fail(db, job["_id"], reason=reason)
            continue
        texts = [j["input_text"] for j in jobs]
        try:
            vectors = prov.embed_batch(texts)
        except Exception as e:
            log.exception("embed_batch failed for model %s", model_key)
            for job in jobs:
                fail(db, job["_id"], reason=f"embed ({model_key}): {e}")
            continue
        for job, vec in zip(jobs, vectors, strict=False):
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
        provider: EmbeddingProvider | ProviderRegistry,
        batch_size: int = 32,
        idle_sleep: float = 2.0,
    ) -> None:
        self.db = db
        # Wrap a single provider so the runner always operates against a
        # ProviderRegistry — keeps the lazy-load behavior consistent
        # whether callers pass one or the other.
        self.provider: ProviderRegistry = (
            provider
            if isinstance(provider, ProviderRegistry)
            else _SingleProviderRegistry(provider)
        )
        self.batch_size = batch_size
        self.idle_sleep = idle_sleep
        self.worker_id = f"worker-{uuid.uuid4().hex[:8]}"
        self._stop = threading.Event()
        self._started_at = _utcnow()
        self._jobs_processed = 0
        self._last_heartbeat = 0.0

    def stop(self) -> None:
        self._stop.set()
        try:
            remove_heartbeat(self.db, self.worker_id)
        except Exception:
            log.exception("failed to remove heartbeat for %s", self.worker_id)

    def _maybe_heartbeat(self) -> None:
        now = time.monotonic()
        if now - self._last_heartbeat >= HEARTBEAT_INTERVAL_S:
            try:
                write_heartbeat(
                    self.db, self.worker_id, self._jobs_processed, self._started_at
                )
            except Exception:
                log.exception("heartbeat write failed for %s", self.worker_id)
            self._last_heartbeat = now

    def run(self) -> None:
        log.info("worker %s starting", self.worker_id)
        self._maybe_heartbeat()
        while not self._stop.is_set():
            n = process_batch(self.db, self.provider, self.worker_id, self.batch_size)
            self._jobs_processed += n
            self._maybe_heartbeat()
            if n == 0:
                time.sleep(self.idle_sleep)
