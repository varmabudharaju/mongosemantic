"""In-process HNSW vector index for non-Atlas topologies.

Atlas has $vectorSearch (HNSW under Lucene). On standalone / replica-set
Mongo we previously fell back to a brute-force aggregation pipeline that
scanned every embedding — fine for hundreds of docs, broken past tens
of thousands.

This module wraps `hnswlib` so the same `(collection, field, model)`
shadow data can be served as an HNSW graph at ~O(log N) instead of O(N).
Indexes are built from the shadow collection, persisted under
``~/.cache/mongosemantic/hnsw/``, and rebuilt opportunistically when the
shadow data has changed enough to be worth it.

Only shadow-mode collections are supported here; inline-mode collections
still take the brute path. That's intentional — most "big" datasets use
shadow mode because the index management story is cleaner.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import hnswlib
import numpy as np
from bson import json_util
from pymongo.database import Database

from mongosemantic.state.config_store import CollectionConfig

log = logging.getLogger("mongosemantic.hnsw")

# HNSW build/query parameters. M and ef_construction trade build time
# and memory for recall; the defaults give ~98% recall on cosine similarity
# at our scale targets (1e4 – 1e6 vectors).
DEFAULT_M = 16
DEFAULT_EF_CONSTRUCTION = 200
DEFAULT_EF_SEARCH = 64

# How long a freshly-built index is considered fresh before staleness
# heuristics can trigger a rebuild.
MIN_REBUILD_INTERVAL_S = 60.0
# Trigger a background rebuild once stale_count / total > this ratio.
STALENESS_RATIO_THRESHOLD = 0.05


IndexKey = tuple[str, str, str]  # (collection, field_path, model)


def _default_cache_dir() -> Path:
    """``~/.cache/mongosemantic/hnsw`` unless ``XDG_CACHE_HOME`` is set."""
    import os
    base = os.environ.get("XDG_CACHE_HOME")
    root = Path(base) if base else Path.home() / ".cache"
    return root / "mongosemantic" / "hnsw"


def _safe_filename(key: IndexKey) -> str:
    coll, field_path, model = key
    # Field paths can contain dots; collections + model keys are restricted
    # by validate_identifier elsewhere. Replace path separators just in case.
    safe = f"{coll}__{field_path}__{model}".replace("/", "_").replace(" ", "_")
    return safe


@dataclass
class _LoadedIndex:
    """A built HNSW index plus the int_id → (source_id, chunk_index) map.

    The mapping is parallel to the int IDs added to the hnswlib index
    (0..N-1). We don't try to keep this in BSON or Mongo because it's
    cheap to regenerate from the shadow collection.
    """
    index: hnswlib.Index
    mapping: list[tuple[Any, int]]  # parallel to int_id
    dim: int
    built_at: float
    stale_count: int = 0


@dataclass
class HnswIndexManager:
    """Owns HNSW indexes per (collection, field, model) tuple.

    Thread-safe lookups. Build/load takes a process-wide lock to keep
    concurrent first-access cold paths from racing each other.
    """
    cache_dir: Path = field(default_factory=_default_cache_dir)
    _indexes: dict[IndexKey, _LoadedIndex] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # -- public API ----------------------------------------------------

    def query(
        self,
        db: Database,
        cfg: CollectionConfig,
        field_path: str,
        query_vec: list[float],
        limit: int,
        allowed_ids: list | None = None,
    ) -> list[dict] | None:
        """Return top-k rows from the HNSW index, or None if no index
        is loaded for this (collection, field, model) or the query itself
        fails (callers treat None as "fall back to exact brute force").

        If *allowed_ids* is given (not None), only chunks whose source_id
        appears in that list are eligible.  An empty list returns [] immediately.
        """
        if cfg.mode != "shadow" or not cfg.shadow_collection:
            return None
        key = (cfg.collection, field_path, cfg.embedding_model)
        loaded = self._indexes.get(key)
        if loaded is None:
            # Try a lazy disk-load before giving up to brute force.
            loaded = self._load_from_disk(key, cfg.embedding_dim)
            if loaded is None:
                return None
        try:
            k = min(int(limit), loaded.index.get_current_count())
            if k <= 0:
                return []
            qv = np.asarray([query_vec], dtype=np.float32)

            filter_fn = None
            if allowed_ids is not None:
                allowed = set(allowed_ids)
                allowed_labels = {
                    i for i, (sid, _ci) in enumerate(loaded.mapping)
                    if sid in allowed
                }
                if not allowed_labels:
                    return []
                k = min(k, len(allowed_labels))
                filter_fn = allowed_labels.__contains__

            try:
                ids, distances = loaded.index.knn_query(qv, k=k, filter=filter_fn)
            except RuntimeError as e:
                # hnswlib can fail to fill k results under a tight filter; signal
                # "no HNSW answer" so the caller falls back to exact brute force.
                log.warning("HNSW knn_query failed for %s (%s); falling back to brute", key, e)
                return None
        except Exception:
            log.exception("HNSW query failed for %s; falling back to brute", key)
            return None
        # hnswlib cosine "distance" = 1 - cosine_similarity. For L2-normalized
        # input vectors (our provider enforces this), score == 1 - distance.
        tuples = [loaded.mapping[int(i)] for i in ids[0]]
        scores = [float(1.0 - d) for d in distances[0]]
        return self._hydrate(db, cfg, field_path, tuples, scores)

    def build(
        self,
        db: Database,
        cfg: CollectionConfig,
        field_path: str,
    ) -> int:
        """Build (or rebuild) the HNSW index for (collection, field).

        Returns the number of vectors indexed. Persists to disk on success.
        """
        if cfg.mode != "shadow" or not cfg.shadow_collection:
            return 0
        key = (cfg.collection, field_path, cfg.embedding_model)
        shadow = db[cfg.shadow_collection]
        cursor = shadow.find(
            {"field_path": field_path, "embedding_model": cfg.embedding_model},
            {"source_id": 1, "chunk_index": 1, "embedding": 1},
        )
        vectors: list[list[float]] = []
        mapping: list[tuple[Any, int]] = []
        for row in cursor:
            emb = row.get("embedding")
            if not emb:
                continue
            vectors.append(emb)
            mapping.append((row.get("source_id"), int(row.get("chunk_index") or 0)))
        if not vectors:
            log.info("HNSW build: no rows for %s", key)
            return 0
        dim = len(vectors[0])
        if dim != cfg.embedding_dim:
            # Defensive: surface dim mismatch instead of silently building
            # an unusable index. This is the kind of bug the provider fix
            # was meant to prevent — keep the seatbelt anyway.
            raise ValueError(
                f"shadow embedding dim {dim} != cfg.embedding_dim {cfg.embedding_dim} "
                f"for {key} — refusing to build"
            )
        idx = hnswlib.Index(space="cosine", dim=dim)
        idx.init_index(
            max_elements=len(vectors),
            ef_construction=DEFAULT_EF_CONSTRUCTION,
            M=DEFAULT_M,
        )
        idx.add_items(np.asarray(vectors, dtype=np.float32), np.arange(len(vectors)))
        idx.set_ef(DEFAULT_EF_SEARCH)
        loaded = _LoadedIndex(
            index=idx, mapping=mapping, dim=dim, built_at=time.time()
        )
        self._save(key, loaded)
        with self._lock:
            self._indexes[key] = loaded
        log.info("HNSW build: %d vectors indexed for %s", len(vectors), key)
        return len(vectors)

    def mark_stale(self, key: IndexKey, n: int = 1) -> None:
        """Bump the staleness counter. Cheap; safe to call from worker hot
        path. Background rebuild logic reads this and decides when to act."""
        loaded = self._indexes.get(key)
        if loaded is None:
            return
        loaded.stale_count += n

    def should_rebuild(self, key: IndexKey) -> bool:
        loaded = self._indexes.get(key)
        if loaded is None:
            return False
        if time.time() - loaded.built_at < MIN_REBUILD_INTERVAL_S:
            return False
        total = loaded.index.get_current_count() or 1
        return (loaded.stale_count / total) >= STALENESS_RATIO_THRESHOLD

    def loaded_keys(self) -> list[IndexKey]:
        return list(self._indexes.keys())

    # -- internals -----------------------------------------------------

    def _hydrate(
        self,
        db: Database,
        cfg: CollectionConfig,
        field_path: str,
        tuples: list[tuple[Any, int]],
        scores: list[float],
    ) -> list[dict]:
        """Fetch chunk_text + source_doc for the top-k tuples in two batched
        round-trips, then assemble result rows in the same shape as the
        brute-force aggregation."""
        if not tuples:
            return []
        shadow = db[cfg.shadow_collection]
        source = db[cfg.collection]
        # Batch shadow lookups by source_id (chunks for one doc cluster).
        source_ids = list({t[0] for t in tuples})
        shadow_rows = list(shadow.find(
            {
                "source_id": {"$in": source_ids},
                "field_path": field_path,
                "embedding_model": cfg.embedding_model,
            },
            {"source_id": 1, "chunk_index": 1, "chunk_text": 1},
        ))
        shadow_by_key = {
            (r["source_id"], int(r.get("chunk_index") or 0)): r for r in shadow_rows
        }
        source_docs = {d["_id"]: d for d in source.find({"_id": {"$in": source_ids}})}
        out: list[dict] = []
        for (sid, ci), score in zip(tuples, scores, strict=False):
            shadow_row = shadow_by_key.get((sid, ci))
            if not shadow_row:
                # Vector survived in the HNSW graph but the shadow row was
                # deleted (e.g., a reconfigure dropped this chunk). Skip.
                continue
            out.append({
                "source_id": sid,
                "source_collection": cfg.collection,
                "field_path": field_path,
                "chunk_index": ci,
                "chunk_text": shadow_row.get("chunk_text", ""),
                "source_doc": source_docs.get(sid),
                "score": score,
            })
        return out

    def _path_for(self, key: IndexKey) -> Path:
        return self.cache_dir / _safe_filename(key)

    def _save(self, key: IndexKey, loaded: _LoadedIndex) -> None:
        path = self._path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        bin_path = path.with_suffix(".bin")
        meta_path = path.with_suffix(".json")
        loaded.index.save_index(str(bin_path))
        meta = {
            "dim": loaded.dim,
            "built_at": loaded.built_at,
            "count": loaded.index.get_current_count(),
            # bson.json_util preserves ObjectId, Binary, etc.
            "mapping": [
                {"sid": sid, "ci": int(ci)} for sid, ci in loaded.mapping
            ],
        }
        meta_path.write_text(json_util.dumps(meta))

    def _load_from_disk(self, key: IndexKey, dim: int) -> _LoadedIndex | None:
        path = self._path_for(key)
        bin_path = path.with_suffix(".bin")
        meta_path = path.with_suffix(".json")
        if not bin_path.exists() or not meta_path.exists():
            return None
        try:
            meta = json_util.loads(meta_path.read_text())
        except Exception:
            log.exception("HNSW: corrupt meta file %s", meta_path)
            return None
        if meta.get("dim") != dim:
            log.warning(
                "HNSW: on-disk dim %s != expected %s for %s — ignoring",
                meta.get("dim"), dim, key,
            )
            return None
        idx = hnswlib.Index(space="cosine", dim=dim)
        try:
            idx.load_index(str(bin_path))
        except Exception:
            log.exception("HNSW: failed to load %s", bin_path)
            return None
        idx.set_ef(DEFAULT_EF_SEARCH)
        mapping = [(m["sid"], int(m["ci"])) for m in meta.get("mapping", [])]
        loaded = _LoadedIndex(
            index=idx, mapping=mapping, dim=dim,
            built_at=float(meta.get("built_at", time.time())),
        )
        with self._lock:
            self._indexes[key] = loaded
        return loaded
