"""In-memory progress registry for indexing operations.

Single-process, single-host. Restarting the server drops the registry. The web
UI polls /api/collections/{name}/index/progress to show a progress bar; the
authoritative state of any enqueued job lives in the `mongosemantic_jobs`
collection and outlives the process.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from time import time


@dataclass
class IndexProgress:
    collection: str
    total: int = 0
    enqueued: int = 0
    started_at: float = field(default_factory=time)
    finished_at: float | None = None


_LOCK = threading.Lock()
_REGISTRY: dict[str, IndexProgress] = {}


def start(collection: str, total: int) -> IndexProgress:
    with _LOCK:
        p = IndexProgress(collection=collection, total=total)
        _REGISTRY[collection] = p
        return p


def bump(collection: str, n: int = 1) -> None:
    with _LOCK:
        p = _REGISTRY.get(collection)
        if p is not None:
            p.enqueued += n


def finish(collection: str) -> None:
    with _LOCK:
        p = _REGISTRY.get(collection)
        if p is not None:
            p.finished_at = time()


def get(collection: str) -> IndexProgress | None:
    with _LOCK:
        return _REGISTRY.get(collection)
