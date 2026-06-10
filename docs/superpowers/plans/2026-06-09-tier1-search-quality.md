# Tier 1 Search Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship 0.9.0 with three search-quality features across CLI, web UI, and MCP: metadata filtering, local cross-encoder reranking, and hybrid (semantic+keyword RRF) search on every topology — all working on existing data with no reindex.

**Architecture:** Filters are MongoDB query documents applied to **source documents**. Local paths (brute-force shadow, HNSW) pre-filter source `_id`s and constrain the candidate set exactly; Atlas `$vectorSearch` paths over-fetch (limit×5) and post-`$match` after the source `$lookup` (shadow docs carry no metadata — this avoids any schema change or reindex). Reranking is a lazily-loaded `CrossEncoder` singleton that re-scores the top limit×5 candidates. Hybrid on non-Atlas topologies runs a Mongo `$text` query on the shadow's `chunk_text` plus the vector leg, fused client-side with RRF (`w/(60+rank)`, weights 0.6/0.4 matching the Atlas `$rankFusion` defaults); the same client-side path is the fallback when Atlas index slots are cap-blocked.

**Tech Stack:** Python 3.10+, pymongo, sentence-transformers (CrossEncoder), hnswlib, FastAPI, typer, mongomock (unit) + real replica set (integration).

**Version:** 0.9.0. Branch: `feat/0.9-tier1-search-quality`. Commit per task, push at the end.

---

## Process rules (from `_session-handoff.md` — violating these cost real time before)

- NEVER pipe pytest/CLI output through `tail`/`head` when the exit code matters. Write to a log file and check `$?` / `EXIT=` separately.
- mongomock cannot run `$reduce`/`$zip`/`$vectorSearch`/`$text`. Unit tests patch `_run_one`/`_run_one_field` or assert on **pipeline structure** (pure dicts). Real-pipeline coverage lives in `@pytest.mark.integration` tests (gated by `MONGOSEMANTIC_RUN_INTEGRATION=1`, replica set at `mongodb://localhost:27117/?replicaSet=rs0`).
- `tests/conftest.py` has an autouse `XDG_CONFIG_HOME` isolation fixture — do not remove.
- Never construct `Settings()` directly — `Settings.from_environment()`.
- Anything printing a URI must go through `redact_uri()`/`scrub_uri()` from `mongosemantic/db/client.py`.
- Commits in the user's name only. NO Co-Authored-By trailers, ever.
- Run `ruff check .` before every commit.

## Canonical row shape (all backends return this; do not break it)

```python
{
    "source_id": Any, "source_collection": str, "field_path": str,
    "chunk_index": int, "chunk_text": str, "source_doc": dict | None,
    "score": float,
}
```
New optional keys added by this plan: `vector_score` (float, original score before rerank), `reranked` (bool).

---

### Task 1: `search/filtering.py` — filter parsing, prefixing, pre-filtering

**Files:**
- Create: `mongosemantic/search/filtering.py`
- Test: `tests/unit/test_filtering.py`

- [ ] **Step 1: Write failing tests**

```python
"""tests/unit/test_filtering.py"""
import mongomock
import pytest

from mongosemantic.search.filtering import (
    FilterError,
    parse_filter,
    prefilter_source_ids,
    prefix_source_filter,
)


def test_parse_filter_valid():
    assert parse_filter('{"year": {"$gte": 1960}}') == {"year": {"$gte": 1960}}


def test_parse_filter_rejects_non_object():
    with pytest.raises(FilterError):
        parse_filter('[1, 2]')
    with pytest.raises(FilterError):
        parse_filter('"year"')


def test_parse_filter_rejects_bad_json():
    with pytest.raises(FilterError):
        parse_filter('{year: 1960}')


def test_parse_filter_rejects_forbidden_operators():
    with pytest.raises(FilterError):
        parse_filter('{"$where": "this.x == 1"}')
    with pytest.raises(FilterError):
        parse_filter('{"$or": [{"$where": "1"}]}')
    with pytest.raises(FilterError):
        parse_filter('{"$text": {"$search": "x"}}')
    with pytest.raises(FilterError):
        parse_filter('{"$expr": {"$gt": ["$a", 1]}}')


def test_prefix_simple_fields():
    assert prefix_source_filter({"year": {"$gte": 1960}}) == {
        "source_doc.year": {"$gte": 1960}
    }


def test_prefix_recurses_logical_operators():
    flt = {"$or": [{"year": 1960}, {"$and": [{"genre": "Drama"}, {"rated": "PG"}]}]}
    assert prefix_source_filter(flt) == {
        "$or": [
            {"source_doc.year": 1960},
            {"$and": [{"source_doc.genre": "Drama"}, {"source_doc.rated": "PG"}]},
        ]
    }


def test_prefilter_source_ids():
    db = mongomock.MongoClient()["t"]
    db["movies"].insert_many(
        [{"_id": 1, "year": 1950}, {"_id": 2, "year": 1970}, {"_id": 3, "year": 1990}]
    )
    ids = prefilter_source_ids(db, "movies", {"year": {"$gte": 1960}})
    assert sorted(ids) == [2, 3]
```

- [ ] **Step 2: Run, verify FAIL** — `python3 -m pytest tests/unit/test_filtering.py -v` → ModuleNotFoundError.

- [ ] **Step 3: Implement**

```python
"""mongosemantic/search/filtering.py

Metadata filters are MongoDB query documents that apply to SOURCE
documents (not shadow chunks — those carry no metadata). Local search
paths pre-filter source _ids; Atlas paths over-fetch and post-$match
after the source $lookup using the source_doc.-prefixed rewrite.
"""

from __future__ import annotations

import json
from typing import Any

from pymongo.database import Database

_MAX_FILTER_BYTES = 10_000
# Server-side JS execution or stages that cannot run mid-pipeline.
_FORBIDDEN_KEYS = {"$where", "$function", "$accumulator", "$text", "$expr"}
_LOGICAL_KEYS = ("$and", "$or", "$nor")


class FilterError(ValueError):
    """A user-supplied search filter is invalid."""


def parse_filter(raw: str) -> dict[str, Any]:
    if len(raw) > _MAX_FILTER_BYTES:
        raise FilterError("filter too large (max 10 KB)")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise FilterError(f"filter is not valid JSON: {e}") from e
    if not isinstance(parsed, dict):
        raise FilterError("filter must be a JSON object, e.g. {\"year\": {\"$gte\": 1960}}")
    _reject_forbidden(parsed)
    return parsed


def _reject_forbidden(node: Any) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _FORBIDDEN_KEYS:
                raise FilterError(f"{key} is not allowed in search filters")
            _reject_forbidden(value)
    elif isinstance(node, list):
        for item in node:
            _reject_forbidden(item)


def prefix_source_filter(flt: dict[str, Any], prefix: str = "source_doc") -> dict[str, Any]:
    """Rewrite field keys to `<prefix>.<field>` for post-$lookup matching."""
    out: dict[str, Any] = {}
    for key, value in flt.items():
        if key in _LOGICAL_KEYS:
            out[key] = [prefix_source_filter(v, prefix) for v in value]
        elif key.startswith("$"):
            out[key] = value
        else:
            out[f"{prefix}.{key}"] = value
    return out


def prefilter_source_ids(db: Database, collection: str, flt: dict[str, Any]) -> list[Any]:
    """The _ids of source docs matching the filter (exact pre-filter for local paths)."""
    return [d["_id"] for d in db[collection].find(flt, {"_id": 1})]
```

- [ ] **Step 4: Run, verify PASS.** Also `ruff check mongosemantic/search/filtering.py tests/unit/test_filtering.py`.
- [ ] **Step 5: Commit** — `feat(search): filter parsing, source_doc prefixing, source-id pre-filtering`

---

### Task 2: `search/rerank.py` — lazy CrossEncoder singleton

**Files:**
- Create: `mongosemantic/search/rerank.py`
- Test: `tests/unit/test_rerank.py`

- [ ] **Step 1: Write failing tests**

```python
"""tests/unit/test_rerank.py"""
from unittest.mock import MagicMock, patch

import numpy as np

import mongosemantic.search.rerank as rr


def _rows():
    return [
        {"source_id": "a", "chunk_text": "weak match", "score": 0.9},
        {"source_id": "b", "chunk_text": "strong match", "score": 0.5},
        {"source_id": "c", "chunk_text": "medium match", "score": 0.7},
    ]


def _fake_model(logits):
    m = MagicMock()
    m.predict = lambda pairs: np.array(logits, dtype=np.float32)
    return m


def test_rerank_reorders_and_annotates():
    r = rr.Reranker.__new__(rr.Reranker)
    r.model_name = "fake"
    r._model = _fake_model([-2.0, 3.0, 0.5])  # b should win, then c, then a
    out = r.rerank("q", _rows(), limit=2)
    assert [row["source_id"] for row in out] == ["b", "c"]
    assert out[0]["reranked"] is True
    assert out[0]["vector_score"] == 0.5          # original score preserved
    assert 0.0 < out[0]["score"] < 1.0            # sigmoid(logit)
    assert out[0]["score"] > out[1]["score"]


def test_rerank_empty_rows():
    r = rr.Reranker.__new__(rr.Reranker)
    r.model_name = "fake"
    r._model = _fake_model([])
    assert r.rerank("q", [], limit=5) == []


def test_get_reranker_caches_failure():
    rr.reset_for_tests()
    with patch.object(rr, "_load_model", side_effect=RuntimeError("no model")):
        assert rr.get_reranker() is None
        assert rr.get_reranker() is None  # cached, _load_model not retried
        assert "no model" in rr.rerank_reason()
    rr.reset_for_tests()


def test_get_reranker_caches_instance():
    rr.reset_for_tests()
    with patch.object(rr, "_load_model", return_value=_fake_model([1.0])) as load:
        first = rr.get_reranker()
        second = rr.get_reranker()
        assert first is second
        assert load.call_count == 1
    rr.reset_for_tests()
```

- [ ] **Step 2: Run, verify FAIL.**
- [ ] **Step 3: Implement**

```python
"""mongosemantic/search/rerank.py

Two-stage retrieval: callers over-fetch limit * RERANK_CANDIDATE_MULTIPLIER
candidates, then rerank(query, rows, limit) re-scores each (query, chunk_text)
pair with a local cross-encoder and returns the top `limit`.

The model (~80 MB, CPU-fast) loads lazily exactly once per process; a failed
load is remembered so a broken install degrades to vector-only search instead
of retrying the import on every request. Mirrors ProviderRegistry semantics
(worker/runner.py) without the per-model keying — there is one rerank model.
"""

from __future__ import annotations

import logging
import math
import threading
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_RERANK_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"
RERANK_CANDIDATE_MULTIPLIER = 5

_lock = threading.Lock()
_instance: "Reranker | None" = None
_failed: str | None = None


def _load_model(model_name: str) -> Any:
    # Lazy import: unit tests and non-rerank paths never pay for it.
    from sentence_transformers import CrossEncoder

    return CrossEncoder(model_name)


class Reranker:
    def __init__(self, model_name: str = DEFAULT_RERANK_MODEL) -> None:
        self.model_name = model_name
        self._model = _load_model(model_name)

    def rerank(self, query: str, rows: list[dict], limit: int) -> list[dict]:
        if not rows:
            return []
        pairs = [(query, r.get("chunk_text") or "") for r in rows]
        logits = self._model.predict(pairs)
        out: list[dict] = []
        for r, logit in zip(rows, logits, strict=True):
            row = dict(r)
            row["vector_score"] = row.get("score")
            row["score"] = float(1.0 / (1.0 + math.exp(-float(logit))))
            row["reranked"] = True
            out.append(row)
        out.sort(key=lambda r: r["score"], reverse=True)
        return out[:limit]


def get_reranker() -> Reranker | None:
    global _instance, _failed
    if _instance is not None:
        return _instance
    if _failed is not None:
        return None
    with _lock:
        if _instance is not None:
            return _instance
        if _failed is not None:
            return None
        try:
            _instance = Reranker()
        except Exception as e:
            log.exception("failed to load rerank model")
            _failed = str(e)
            return None
        return _instance


def rerank_reason() -> str:
    return _failed or "unknown error"


def reset_for_tests() -> None:
    global _instance, _failed
    _instance = None
    _failed = None
```

- [ ] **Step 4: Run, verify PASS. ruff.**
- [ ] **Step 5: Commit** — `feat(search): local cross-encoder reranker (lazy singleton)`

---

### Task 3: `search/local_hybrid.py` — $text leg + client-side RRF

**Files:**
- Create: `mongosemantic/search/local_hybrid.py`
- Test: `tests/unit/test_local_hybrid.py` (pure RRF tests; `text_leg` is covered by integration Task 11)

- [ ] **Step 1: Write failing tests**

```python
"""tests/unit/test_local_hybrid.py"""
from mongosemantic.search.local_hybrid import rrf_fuse


def _row(sid, score, text="t"):
    return {
        "source_id": sid, "source_collection": "c", "field_path": "body",
        "chunk_index": 0, "chunk_text": text, "source_doc": {"_id": sid},
        "score": score,
    }


def test_rrf_doc_in_both_lists_outranks_single_list():
    vec = [_row("a", 0.9), _row("b", 0.8)]
    txt = [_row("b", 5.0), _row("c", 3.0)]
    fused = rrf_fuse([vec, txt], weights=[0.6, 0.4], limit=10)
    assert fused[0]["source_id"] == "b"  # rank 2 in vec + rank 1 in text
    ids = [r["source_id"] for r in fused]
    assert set(ids) == {"a", "b", "c"}
    # RRF formula: b = 0.6/(60+2) + 0.4/(60+1); a = 0.6/(60+1)
    assert abs(fused[0]["score"] - (0.6 / 62 + 0.4 / 61)) < 1e-9


def test_rrf_respects_limit_and_sorts_desc():
    vec = [_row(i, 1.0 - i / 10) for i in range(5)]
    fused = rrf_fuse([vec, []], weights=[0.6, 0.4], limit=3)
    assert len(fused) == 3
    assert fused[0]["score"] >= fused[1]["score"] >= fused[2]["score"]


def test_rrf_dedup_key_includes_chunk():
    a0 = _row("a", 0.9); a1 = dict(_row("a", 0.8), chunk_index=1)
    fused = rrf_fuse([[a0, a1], []], limit=10)
    assert len(fused) == 2
```

- [ ] **Step 2: Run, verify FAIL.**
- [ ] **Step 3: Implement**

```python
"""mongosemantic/search/local_hybrid.py

Hybrid search without Atlas Search: a classic Mongo `$text` index on the
shadow collection's chunk_text (works on 7.0 standalone, replica sets, and
Atlas regular indexes — no Search-index slot needed) supplies the keyword
leg; reciprocal-rank fusion (same 1/(60+rank) formula and 0.6/0.4 weights
as Atlas $rankFusion) combines it with the vector leg client-side.
"""

from __future__ import annotations

import logging
from typing import Any

from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.errors import OperationFailure

log = logging.getLogger(__name__)

TEXT_INDEX_NAME = "msem_chunk_text_text"
RRF_K = 60
HYBRID_WEIGHTS = (0.6, 0.4)  # vector, text — matches the Atlas path defaults


def ensure_text_index(shadow: Collection) -> bool:
    """Create the $text index on chunk_text (idempotent). False if impossible."""
    try:
        shadow.create_index([("chunk_text", "text")], name=TEXT_INDEX_NAME)
        return True
    except OperationFailure as e:
        # E.g. a different text index already exists (Mongo allows only one).
        log.warning("could not create text index on %s: %s", shadow.name, e)
        return False


def text_leg(
    db: Database,
    cfg: Any,
    collection: str,
    field_path: str,
    query_text: str,
    limit: int,
    allowed_ids: list[Any] | None = None,
) -> list[dict]:
    """Keyword search over shadow chunk_text, hydrated to the canonical row shape."""
    shadow = db[cfg.shadow_collection]
    if not ensure_text_index(shadow):
        return []
    query: dict[str, Any] = {
        "$text": {"$search": query_text},
        "field_path": field_path,
        "embedding_model": cfg.embedding_model,
    }
    if allowed_ids is not None:
        query["source_id"] = {"$in": list(allowed_ids)}
    try:
        rows = list(
            shadow.find(
                query,
                {
                    "score": {"$meta": "textScore"},
                    "source_id": 1, "field_path": 1,
                    "chunk_index": 1, "chunk_text": 1,
                },
            )
            .sort([("score", {"$meta": "textScore"})])
            .limit(limit)
        )
    except OperationFailure as e:
        log.warning("text leg failed on %s: %s", shadow.name, e)
        return []
    sids = list({r["source_id"] for r in rows})
    docs = {d["_id"]: d for d in db[collection].find({"_id": {"$in": sids}})}
    return [
        {
            "source_id": r["source_id"],
            "source_collection": collection,
            "field_path": field_path,
            "chunk_index": int(r.get("chunk_index") or 0),
            "chunk_text": r.get("chunk_text", ""),
            "source_doc": docs.get(r["source_id"]),
            "score": float(r.get("score", 0.0)),
        }
        for r in rows
    ]


def rrf_fuse(
    result_lists: list[list[dict]],
    weights: list[float] | tuple[float, ...] | None = None,
    k: int = RRF_K,
    limit: int = 10,
) -> list[dict]:
    """Reciprocal-rank fusion: score(doc) = Σ weight_i / (k + rank_i), rank 1-based."""
    if weights is None:
        weights = [1.0] * len(result_lists)
    scores: dict[tuple, float] = {}
    best: dict[tuple, dict] = {}
    for rows, w in zip(result_lists, weights, strict=True):
        for rank, row in enumerate(rows, start=1):
            key = (str(row.get("source_id")), row.get("field_path"), row.get("chunk_index"))
            scores[key] = scores.get(key, 0.0) + w / (k + rank)
            best.setdefault(key, row)
    fused = []
    for key, s in scores.items():
        row = dict(best[key])
        row["score"] = s
        fused.append(row)
    fused.sort(key=lambda r: r["score"], reverse=True)
    return fused[:limit]
```

- [ ] **Step 4: Run, verify PASS. ruff.**
- [ ] **Step 5: Commit** — `feat(search): client-side RRF + $text keyword leg for hybrid everywhere`

---

### Task 4: pipeline builders — `source_filter` over-fetch support

**Files:**
- Modify: `mongosemantic/search/atlas.py` (build_atlas_pipeline)
- Modify: `mongosemantic/search/inline.py` (build_inline_atlas_pipeline only — the brute variant's existing `filter_match` already matches on the source doc directly and is exact)
- Modify: `mongosemantic/search/hybrid.py` (build_hybrid_pipeline)
- Test: `tests/unit/test_pipelines_filter.py`

Pattern for all three: new params `source_filter: dict | None = None, oversample: int = 5`. When `source_filter` is set, the `$vectorSearch` `limit` becomes `limit * oversample` (and `numCandidates = max(10 * fetch_limit, 100)`); after the stage where the source document is available, append `{"$match": <rewritten filter>}` then `{"$limit": limit}` before the final projection.

- For `build_atlas_pipeline` and `build_hybrid_pipeline`: source doc appears after `lookup_source_stage` + `unwind_source_stage` → match on `prefix_source_filter(source_filter)` (import from `mongosemantic.search.filtering`).
- For `build_inline_atlas_pipeline`: the hit *is* the source doc → match on `source_filter` unprefixed, immediately after the `$vectorSearch` (+ existing field/filter matching), then `{"$limit": limit}`.
- For `build_hybrid_pipeline`: both sub-pipeline `limit`s and the post-fusion `{"$limit": ...}` use `fetch_limit`; the post-lookup filter match + final `{"$limit": limit}` come before `base_projection`.

- [ ] **Step 1: Write failing tests** (pure dict assertions — no Mongo needed)

```python
"""tests/unit/test_pipelines_filter.py"""
from mongosemantic.search.atlas import build_atlas_pipeline
from mongosemantic.search.hybrid import build_hybrid_pipeline
from mongosemantic.search.inline import build_inline_atlas_pipeline

QV = [0.1, 0.2]
FLT = {"year": {"$gte": 1960}}


def _stages(pipeline, name):
    return [s for s in pipeline if name in s]


def test_atlas_pipeline_overfetches_and_postmatches():
    p = build_atlas_pipeline("movies", "plot", QV, limit=10,
                             index_name="ix", source_filter=FLT)
    vs = p[0]["$vectorSearch"]
    assert vs["limit"] == 50
    assert vs["numCandidates"] == 500
    matches = _stages(p, "$match")
    assert {"source_doc.year": {"$gte": 1960}} in [m["$match"] for m in matches]
    # filter match must come AFTER the lookup/unwind and BEFORE the final limit
    limits = [s["$limit"] for s in _stages(p, "$limit")]
    assert limits[-1] == 10


def test_atlas_pipeline_no_filter_unchanged():
    p = build_atlas_pipeline("movies", "plot", QV, limit=10, index_name="ix")
    assert p[0]["$vectorSearch"]["limit"] == 10
    assert not any("source_doc.year" in str(s) for s in p)


def test_inline_atlas_pipeline_matches_unprefixed():
    p = build_inline_atlas_pipeline("plot", QV, limit=10, index_name="ix",
                                    source_filter=FLT)
    assert p[0]["$vectorSearch"]["limit"] == 50
    assert {"year": {"$gte": 1960}} in [m["$match"] for m in _stages(p, "$match")]


def test_hybrid_pipeline_filter():
    p = build_hybrid_pipeline("movies", "plot", "q", QV, limit=10,
                              vector_index_name="v", search_index_name="s",
                              source_filter=FLT)
    sub = p[0]["$rankFusion"]["input"]["pipelines"]
    assert sub["vector"][0]["$vectorSearch"]["limit"] == 50
    assert {"source_doc.year": {"$gte": 1960}} in [m["$match"] for m in _stages(p, "$match")]
    assert [s["$limit"] for s in _stages(p, "$limit")][-1] == 10
```

- [ ] **Step 2: Run, verify FAIL** (TypeError: unexpected keyword argument).
- [ ] **Step 3: Implement** in the three builders per the pattern above. Read each file first; keep existing `filter_match` behavior untouched.
- [ ] **Step 4: Run new tests + existing `tests/unit/` pipeline tests, verify PASS. ruff.**
- [ ] **Step 5: Commit** — `feat(search): source-doc filters in Atlas/inline/hybrid pipelines (over-fetch + post-match)`

---

### Task 5: HNSW `allowed_ids` filtering

**Files:**
- Modify: `mongosemantic/search/hnsw_index.py` (`HnswIndexManager.query`, currently lines 97–129)
- Test: extend `tests/unit/test_hnsw_index.py`

- [ ] **Step 1: Write failing test** (this test file already builds real hnswlib indexes against mongomock — follow its existing fixtures/style; read it first). New test sketch:

```python
def test_query_with_allowed_ids_filters_results(...existing fixture...):
    # build index over docs a, b, c (existing pattern in this file)
    rows = mgr.query(db, cfg, "body", query_vec, limit=10, allowed_ids=["b", "c"])
    assert {r["source_id"] for r in rows} <= {"b", "c"}

def test_query_with_empty_allowed_ids_returns_empty(...):
    rows = mgr.query(db, cfg, "body", query_vec, limit=10, allowed_ids=[])
    assert rows == []
```

- [ ] **Step 2: Run, verify FAIL.**
- [ ] **Step 3: Implement.** New signature: `def query(self, db, cfg, field_path, query_vec, limit, allowed_ids=None)`. Inside, before `knn_query`:

```python
filter_fn = None
if allowed_ids is not None:
    allowed = set(allowed_ids)
    allowed_labels = {
        i for i, (sid, _ci) in enumerate(loaded.mapping) if sid in allowed
    }
    if not allowed_labels:
        return []
    k = min(k, len(allowed_labels))
    filter_fn = allowed_labels.__contains__
try:
    ids, distances = loaded.index.knn_query(qv, k=k, filter=filter_fn)
except RuntimeError:
    # hnswlib can fail to fill k results under a tight filter; fall back to
    # the exact brute-force path by signalling "no HNSW answer".
    return None
```
(Keep the no-filter call path identical; `knn_query(qv, k=k, filter=None)` is fine on hnswlib>=0.8.)

- [ ] **Step 4: Run the whole `tests/unit/test_hnsw_index.py`, verify PASS. ruff.**
- [ ] **Step 5: Commit** — `feat(search): HNSW label filtering via allowed source ids`

---

### Task 6: `commands/search.py` — plumbing + hybrid strategy dispatch + CLI flags

**Files:**
- Modify: `mongosemantic/commands/search.py`
- Modify: `mongosemantic/db/indexes.py` (add `atlas_search_index_exists` mirroring `atlas_vector_index_exists`)
- Test: extend `tests/unit/test_cmd_search.py`

This is the core wiring task. Read `commands/search.py` fully first.

- [ ] **Step 1: Write failing tests** (style: patch `MongoConnection.open`, `get_provider`, and the runner functions; capture kwargs):

```python
def test_search_filter_flag_plumbs_to_run_one(monkeypatch):
    # patch _run_one, invoke: search "q" -c articles --filter '{"year": {"$gte": 1960}}'
    # assert _run_one called with source_filter={"year": {"$gte": 1960}}

def test_search_filter_flag_invalid_json_exits_2():
    # invoke with --filter '{bad'; assert exit_code == 2 and "filter" in output

def test_search_rerank_flag_overfetches_and_reranks(monkeypatch):
    # patch _run_one returning 6 rows, patch get_reranker -> fake returning top-2
    # invoke with --limit 2 --rerank
    # assert _run_one called with limit 2*5; assert output shows reranked order

def test_search_rerank_unavailable_degrades(monkeypatch):
    # patch get_reranker -> None; assert exit 0, warning printed, vector order kept

def test_hybrid_available_now_shadow_any_topology():
    # hybrid_available(shadow_cfg, Topology.STANDALONE) is True
    # hybrid_available(inline_cfg, Topology.ATLAS) is False

def test_run_one_hybrid_local_path_fuses(monkeypatch):
    # topology STANDALONE; patch _run_one_field -> vec rows, patch
    # mongosemantic.commands.search.text_leg -> txt rows;
    # assert run_one_hybrid returns RRF-fused order
```

Write these as real, complete tests following the existing patterns in `tests/unit/test_cmd_search.py` (CliRunner, mongomock `_setup`, MagicMock provider).

- [ ] **Step 2: Run, verify FAIL.**
- [ ] **Step 3: Implement.**

1. `db/indexes.py`: add

```python
def atlas_search_index_exists(target: Collection, name: str) -> bool:
    """True if an Atlas Search (BM25) index with this exact name exists."""
    try:
        return any(ix.get("name") == name for ix in target.list_search_indexes())
    except OperationFailure:
        return False
```
(Match the error-handling style of `atlas_vector_index_exists` — read it first.)

2. `_run_one_field(..., source_filter: dict | None = None)`:
   - inline + Atlas-index path → pass `source_filter=source_filter` to `build_inline_atlas_pipeline`.
   - inline brute path → pass `filter_match=source_filter` (exact, matches source doc directly).
   - shadow + Atlas-index path → pass `source_filter=source_filter` to `build_atlas_pipeline`.
   - shadow brute path → when `source_filter`:
     ```python
     ids = prefilter_source_ids(db, collection, source_filter)
     pipeline = build_brute_pipeline(..., filter_match={"source_id": {"$in": ids}})
     ```
3. `_run_one(..., source_filter=None)` → forwards to `_run_one_field`.
4. Hybrid restructure:
   ```python
   def hybrid_available(cfg, topology: Topology) -> bool:
       """Hybrid needs a chunk_text column to keyword-search → shadow mode, any topology."""
       return cfg.mode == "shadow"

   def _atlas_native_hybrid_ready(db, cfg, collection, field_path) -> bool:
       shadow = db[cfg.shadow_collection]
       stored_search = (cfg.search_index_names or {}).get(field_path)
       sname = stored_search or search_index_name(collection, field_path)
       return atlas_vector_index_exists(shadow, collection, field_path) and \
              atlas_search_index_exists(shadow, sname)
   ```
   `_run_hybrid_field(..., source_filter=None, hnsw=None)`:
   - If `topology == Topology.ATLAS and _atlas_native_hybrid_ready(...)` → existing `$rankFusion` path with `source_filter=source_filter`.
   - Else (non-Atlas, or Atlas cap-blocked) → client-side RRF:
     ```python
     allowed = prefilter_source_ids(db, collection, source_filter) if source_filter else None
     vec_rows = None
     if hnsw is not None:
         vec_rows = hnsw.query(db, cfg, field_path, query_vec, limit, allowed_ids=allowed)
     if vec_rows is None:
         vec_rows = _run_one_field(db, cfg, collection, field_path, query_vec,
                                   limit, topology, source_filter=source_filter)
     txt_rows = text_leg(db, cfg, collection, field_path, query_text, limit,
                         allowed_ids=allowed)
     rows = rrf_fuse([vec_rows, txt_rows], weights=HYBRID_WEIGHTS, limit=limit)
     ```
   `run_one_hybrid(..., source_filter=None, hnsw=None)` forwards both.
5. CLI options on `search_cmd`:
   ```python
   filter_json: str | None = typer.Option(
       None, "--filter",
       help='MongoDB filter on source documents, e.g. \'{"year": {"$gte": 1960}}\'.'),
   rerank: bool = typer.Option(
       False, "--rerank",
       help="Re-score results with a local cross-encoder (better precision, slower)."),
   ```
   - Parse early: `source_filter = parse_filter(filter_json) if filter_json else None`; on `FilterError` print the message via the existing console-error style and `raise typer.Exit(2)`.
   - When `rerank`: `fetch_limit = limit * RERANK_CANDIDATE_MULTIPLIER` passed to the runners; after the final merged+sorted rows: `reranker = get_reranker()`; if `None` → print warning with `rerank_reason()` and truncate to `limit`; else `rows = reranker.rerank(query, rows, limit)`.
   - Update the `--hybrid` help text: no longer "Atlas + shadow mode only" → "Combine semantic + keyword search (shadow mode; any topology)."
- [ ] **Step 4: Run `tests/unit/test_cmd_search.py` + full unit dir, verify PASS. ruff.**
- [ ] **Step 5: Commit** — `feat(cli): --filter and --rerank; hybrid search on every topology via client-side RRF`

---

### Task 7: web route — filter/rerank params + hybrid notices

**Files:**
- Modify: `mongosemantic/web/routes/search.py`
- Test: extend `tests/unit/test_route_search.py`

- [ ] **Step 1: Write failing tests** (style: patch `MongoConnection.open`, providers, `_run_one`):

```python
def test_search_filter_param_plumbs_through(monkeypatch):
    # GET /api/search?q=x&collection=articles&filter={"year":{"$gte":1960}} (url-encoded)
    # patched _run_one must receive source_filter={"year": {"$gte": 1960}}

def test_search_filter_param_invalid_returns_400(monkeypatch):
    # filter={bad → status 400, detail mentions filter

def test_search_rerank_param(monkeypatch):
    # patch get_reranker -> fake; rerank=true&limit=2 → _run_one called with limit 10,
    # response rows reranked (fake reverses order), rows carry reranked/vector_score keys

def test_search_rerank_unavailable_degrades_with_notice(monkeypatch):
    # patch get_reranker -> None → 200, notice mentions rerank, vector rows returned

def test_hybrid_non_atlas_no_longer_falls_back(monkeypatch):
    # shadow cfg, STANDALONE topology, hybrid=true → patched run_one_hybrid called
    # (not _run_one), notice is None
```

- [ ] **Step 2: Run, verify FAIL.**
- [ ] **Step 3: Implement.**
   - New query params: `filter: str | None = Query(None)`, `rerank: bool = Query(False)`.
   - `source_filter = parse_filter(filter)` inside try/except `FilterError` → `HTTPException(400, detail=str(e))`.
   - Thread `source_filter` through `_try_hnsw` (compute `allowed = prefilter_source_ids(db, cfg.collection, source_filter)` once per collection, pass `allowed_ids=allowed`; when `source_filter is None` pass `allowed_ids=None`), `_run_one`, and `run_one_hybrid(..., hnsw=request.app.state.hnsw)`.
   - Hybrid gating: replace the Atlas-only check with `hybrid_available(cfg, topology)` (shadow any topology); inline collections keep a notice: `"hybrid requires shadow mode; returned pure semantic results"`.
   - Rerank: `fetch_limit = limit * RERANK_CANDIDATE_MULTIPLIER if rerank else limit`; after rows are merged/sorted, apply reranker; if unavailable set `notice = f"rerank unavailable: {rerank_reason()}"` and truncate to `limit`.
   - `_serialize` (line ~62): add `"vector_score"` and `"reranked"` to the copied keys (only when present).
   - `min_score` filtering must apply AFTER rerank (scores change scale).
- [ ] **Step 4: Run `tests/unit/test_route_search.py` + full unit dir, verify PASS. ruff.**
- [ ] **Step 5: Commit** — `feat(web): filter + rerank params on /api/search; hybrid on any topology`

---

### Task 8: MCP tools — filter/rerank params

**Files:**
- Modify: `mongosemantic/mcp_server/tools.py`
- Modify: `mongosemantic/mcp_server/server.py` (registered signatures + docstrings)
- Test: extend `tests/unit/test_mcp_tools.py`

- [ ] **Step 1: Write failing tests** (style: call `t_semantic_search` etc. directly with mongomock db + patched `_run_one`/`get_provider`):

```python
def test_t_semantic_search_filter_and_rerank(...):
    # t_semantic_search(db, topo, "q", "articles", limit=2,
    #                   filter={"year": {"$gte": 1960}}, rerank=True)
    # patched _run_one receives source_filter + limit*5; fake reranker applied;
    # rows serialize vector_score/reranked

def test_t_semantic_search_invalid_filter_raises_value_error(...):
    # filter={"$where": "1"} → ValueError mentioning filter

def test_t_hybrid_search_local_topology_runs_hybrid(...):
    # STANDALONE + shadow cfg → run_one_hybrid called, mode == "hybrid"
```

- [ ] **Step 2: Run, verify FAIL.**
- [ ] **Step 3: Implement.**
   - `t_semantic_search(db, topology, query, collection, limit=10, filter=None, rerank=False)`; validate dict filters with `_reject_forbidden` semantics by round-tripping through `parse_filter(json.dumps(filter))` or refactor a `validate_filter(dict)` helper in `filtering.py` (do the refactor: `validate_filter(flt: dict) -> dict` runs `_reject_forbidden` + returns; `parse_filter` calls it). Raise `ValueError(str(FilterError))` for MCP-friendly errors.
   - Same params on `t_hybrid_search`; its availability notice changes to the shadow-mode wording; non-Atlas now actually runs hybrid (pass `hnsw=None`).
   - `t_search_all_collections(..., rerank=False)` — rerank after the cross-collection merge (it also fixes the mixed-model score-scale problem).
   - `_serialize_row`: include `vector_score`/`reranked` when present.
   - `server.py`: update the `@app.tool()` wrappers: add `filter: dict | None = None, rerank: bool = False` params with docstrings explaining filter applies to source documents.
- [ ] **Step 4: Run tests, verify PASS. ruff.**
- [ ] **Step 5: Commit** — `feat(mcp): filter + rerank on search tools; hybrid everywhere`

---

### Task 9: apply creates the shadow $text index

**Files:**
- Modify: `mongosemantic/commands/apply.py`
- Modify: `mongosemantic/web/routes/apply.py`
- Test: extend `tests/unit/test_cmd_apply.py` (or the existing apply test file — find it: `ls tests/unit | grep apply`)

- [ ] **Step 1: Write failing test**

```python
def test_apply_shadow_creates_text_index(...):
    # run apply (existing test setup pattern) on mongomock, shadow mode
    # assert "msem_chunk_text_text" in [ix index names of db["articles_embeddings"]]
    # (mongomock supports create_index and index_information)
```

- [ ] **Step 2: Run, verify FAIL.**
- [ ] **Step 3: Implement.** In both apply paths, after config save for shadow mode (ALL topologies — on Atlas it backs the cap-blocked client-side RRF fallback): `ensure_text_index(db[cfg.shadow_collection])`. Already-configured collections are covered lazily by `text_leg`'s `ensure_text_index` call — note this in the commit body.
- [ ] **Step 4: Run, verify PASS. ruff.**
- [ ] **Step 5: Commit** — `feat(apply): create shadow chunk_text $text index for hybrid keyword leg`

---

### Task 10: web UI — filter input, rerank toggle, score-bar normalization

**Files:**
- Modify: `mongosemantic/web/static/index.html` (Search page form)
- Modify: `mongosemantic/web/static/app.js` (search handler ~lines 1252–1400)
- Modify: `mongosemantic/web/static/style.css` only if a new input needs sizing

No JS test harness exists; correctness is proven by Task 13's screenshots and by the route tests (Task 7). Keep markup/style consistent with the existing form controls.

- [ ] **Step 1: index.html** — in the search form add:
   - a text input `#search-filter` with placeholder `{"year": {"$gte": 1960}}` and a small label "Filter (Mongo query on source docs)";
   - a checkbox `#search-rerank` labeled "Rerank (cross-encoder)" next to the existing `#search-hybrid` checkbox;
   - update the hybrid checkbox label/title if it says Atlas-only.
- [ ] **Step 2: app.js `run()`** — read `#search-filter`; if non-empty, `JSON.parse` client-side first and show the existing error style on parse failure (don't send); else `params.set("filter", raw)`. If `#search-rerank` checked → `params.set("rerank", "true")`.
- [ ] **Step 3: score-bar normalization** (fixes RRF ~0.016 and rerank scales rendering as 1–2% bars). Replace the per-row `score * 100`:

```javascript
const scores = _searchRows.map((r) => r.score || 0);
const maxS = Math.max(...scores), minS = Math.min(...scores);
const span = maxS - minS;
// Per-result-set min/max normalization: best bar 100%, worst 5%; equal scores → 100%.
const barPct = (s) => (span > 1e-9 ? 5 + (95 * (s - minS)) / span : 100);
```
   and use `barPct(score)` in the row template. Show the raw score number unchanged (3 decimals). If a row has `reranked`, append a small "reranked" badge (reuse an existing badge/pill class).
- [ ] **Step 4: Manual smoke** — `python3 -m mongosemantic ui` is heavy; rely on Task 13. Run `ruff check .` (no Python changes expected) and eyeball the diff.
- [ ] **Step 5: Commit** — `feat(ui): filter input + rerank toggle on Search; normalize score bars per result set`

---

### Task 11: integration tests (real replica set)

**Files:**
- Create: `tests/integration/test_tier1_search.py`

Uses existing fixtures (`clean_db`, env monkeypatching, `process_batch`, `CliRunner`) — copy the structure of `tests/integration/test_search_e2e.py`. All marked `@pytest.mark.integration`.

- [ ] **Step 1: Write the tests**

```python
# 1. test_filter_e2e: seed articles with year metadata (1950/1970/1990),
#    index + process_batch, then CLI:
#    search "vector database" -c articles --filter '{"year": {"$gte": 1960}}' --limit 5
#    → exit 0; output contains only the >=1960 docs' text.
# 2. test_filter_no_matches: --filter '{"year": {"$gte": 3000}}' → exit 0, "no results"-style output.
# 3. test_local_hybrid_e2e: seed one doc that matches the query only by keyword
#    ("XK-9000 turbo blender") and one only semantically ("kitchen appliance for
#    smoothies"); embed; CLI search "XK-9000" --hybrid → both rows present, exit 0;
#    assert the $text index exists on the shadow:
#    "msem_chunk_text_text" in db["articles_embeddings"].index_information()
# 4. test_hybrid_plus_filter: hybrid with --filter excluding the keyword doc →
#    keyword doc absent.
# 5. test_rerank_e2e: real CrossEncoder (first run downloads ~80 MB — fine in this env).
#    search "which doc is about mongodb vector search" --rerank --limit 2 → exit 0;
#    relevant doc ranked first.
```

Write all five fully, runnable, with concrete seed docs and assertions on `r.output`.

- [ ] **Step 2: Run** — `docker compose up -d` first if needed, then:
  `MONGOSEMANTIC_RUN_INTEGRATION=1 python3 -m pytest tests/integration/test_tier1_search.py -v > /tmp/t11.log 2>&1; echo "EXIT=$?"` then read the log. Verify all PASS.
- [ ] **Step 3: Commit** — `test: Tier 1 e2e coverage (filter, local hybrid, rerank) on replica set`

---

### Task 12: full suite, docs, version 0.9.0

- [ ] **Step 1:** `MONGOSEMANTIC_RUN_INTEGRATION=1 python3 -m pytest -q > /tmp/full.log 2>&1; echo EXIT=$?` → expect 283+new passed (Atlas-gated 5 stay skipped without the env). Fix anything red. `ruff check .` clean.
- [ ] **Step 2:** Version: `pyproject.toml` → `0.9.0`; CHANGELOG section "0.9.0 — Search quality: metadata filters, cross-encoder rerank, hybrid everywhere" with one bullet per feature incl. CLI/UI/MCP surfaces and the score-bar fix. Reinstall: `python3 -m pip install --user -e . --no-deps -q` (UI badge reads importlib.metadata).
- [ ] **Step 3:** README: extend the Search section with `--filter`, `--rerank`, and the new hybrid story (works on 7.0 standalone; Atlas $rankFusion when Search indexes exist, client-side RRF otherwise — removes the M0-cap asterisk). Update the MCP tool table if present.
- [ ] **Step 4: Commit** — `chore(release): 0.9.0 — metadata filters, rerank, hybrid everywhere`

---

### Task 13: visual proof (capture)

- [ ] Append new shots to `.capture.yaml` (NEVER renumber 01–16; readiness gates on the `HNSW warmup finished` log line; wait for port 8080 to free between runs):
   1. Web Search page: query on movies with filter `{"year": {"$gte": 1990}}` showing filtered results + visible filter input.
   2. Web Search page: same query with Rerank toggled on, normalized score bars + reranked badge.
   3. Web Search page: hybrid toggle on (local topology — proves hybrid works without Atlas).
   4. CLI: `mongosemantic search "..." -c movies --filter '...' --limit 5`.
   5. CLI: `mongosemantic search "..." -c movies --rerank --limit 5`.
   6. CLI: `mongosemantic search "..." -c movies --hybrid --limit 5` (against localhost replica set).
- [ ] `capture run --only <new shots>`, rename to canonical next numbers, verify PNGs visually (Read tool).
- [ ] Write `docs/test-evidence-0.9.md` — one captioned shot per feature + the integration-test summary line.
- [ ] Embed the best shots in README where the feature docs were added (Task 12).
- [ ] Commit — `docs: 0.9.0 screenshots + test evidence`

### Task 14: ship

- [ ] Merge `feat/0.9-tier1-search-quality` → `main` (fast-forward or merge commit per history), push.
- [ ] Tags: `git tag v0.8.1 085e1dd && git tag v0.8.2 e17d642 && git tag v0.9.0 <release commit> && git push origin v0.8.1 v0.8.2 v0.9.0`.
- [ ] Update `_session-handoff.md` "Where things stand".

---

## Self-review notes

- Spec coverage: roadmap items 1 (filter — pre-filter/post-match design chosen over shadow-metadata copy: zero reindex, exact on local paths; documented trade-off: Atlas paths over-fetch ×5 so heavily-selective filters on Atlas may return < limit), 2 (rerank + score-bar fix — Tasks 2/6/7/8/10), 3 (hybrid everywhere + cap-blocked fallback unification — Tasks 3/6/9, the `_atlas_native_hybrid_ready` check fixes the known wart). All three exposed on CLI + web + MCP.
- Type consistency: `source_filter: dict | None` everywhere; `allowed_ids: list | None`; rows keep canonical shape + optional `vector_score`/`reranked`; `RERANK_CANDIDATE_MULTIPLIER=5` shared; `HYBRID_WEIGHTS=(0.6, 0.4)`; `TEXT_INDEX_NAME="msem_chunk_text_text"`.
- Known simplifications (intentional): no filter UI for `search_all_collections` MCP tool; inline-mode hybrid stays unavailable (notice); `$expr`/`$text`/`$where` rejected in filters.
