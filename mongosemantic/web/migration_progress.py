"""In-memory tracker for in-flight migrations spawned by the web UI.

Same pattern as `mongosemantic.web.progress` for indexing: single-process,
authoritative state lives in the database (the temp shadow collection
persists across restarts), this just powers the progress bar.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from time import time


@dataclass
class MigrationProgress:
    collection: str
    target_model: str
    total: int = 0
    processed: int = 0
    state: str = "running"  # "running" | "succeeded" | "failed"
    error: str | None = None
    archive_collection: str | None = None
    new_model: str | None = None
    started_at: float = field(default_factory=time)
    finished_at: float | None = None


_LOCK = threading.Lock()
_REGISTRY: dict[str, MigrationProgress] = {}


def start(collection: str, target_model: str) -> MigrationProgress:
    with _LOCK:
        p = MigrationProgress(collection=collection, target_model=target_model)
        _REGISTRY[collection] = p
        return p


def update_progress(collection: str, processed: int, total: int) -> None:
    with _LOCK:
        p = _REGISTRY.get(collection)
        if p is not None:
            p.processed = processed
            p.total = total


def succeed(collection: str, archive_collection: str, new_model: str) -> None:
    with _LOCK:
        p = _REGISTRY.get(collection)
        if p is not None:
            p.state = "succeeded"
            p.archive_collection = archive_collection
            p.new_model = new_model
            p.finished_at = time()


def fail(collection: str, error: str) -> None:
    with _LOCK:
        p = _REGISTRY.get(collection)
        if p is not None:
            p.state = "failed"
            p.error = error
            p.finished_at = time()


def get(collection: str) -> MigrationProgress | None:
    with _LOCK:
        return _REGISTRY.get(collection)
