# Atlas Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Live-test every Atlas-only code path against a real M0 cluster on `sample_mflix.embedded_movies`, codify each path as a regression test under `tests/integration/atlas/`, and update `docs/HANDOFF.md` to reflect "live-tested on Atlas".

**Architecture:** New env-gated test directory (`MONGOSEMANTIC_RUN_ATLAS_INTEGRATION=1` + `MONGOSEMANTIC_ATLAS_URI`) with module-scoped fixtures for Atlas client, topology assertion, sample-dataset preflight, and search-index readiness polling. One orchestrated test file per Atlas-only path. Bugs trigger an isolated per-issue feature branch with a failing-test-first regression covered before merge.

**Tech Stack:** Python 3.11+, pytest, typer.testing.CliRunner, pymongo, Atlas M0, mongosemantic CLI.

**Spec:** `docs/superpowers/specs/2026-05-19-atlas-verification-design.md`

---

## Working Branch

All work happens on a single feature branch unless a tier surfaces a bug — see "Per-bug PR workflow" at the bottom.

- [ ] **Setup: Create the feature branch**

```bash
git checkout main
git pull --ff-only
git checkout -b feat/atlas-verification
```

---

## Phase A — Scaffolding (no Atlas cluster needed yet)

### Task 1: Add Atlas env-gating to the pytest collector

**Files:**
- Modify: `tests/conftest.py`

The existing collector skips tests marked `integration` unless `MONGOSEMANTIC_RUN_INTEGRATION=1`. Atlas tests need a second, stricter gate so they never run accidentally even when local integration is enabled.

- [ ] **Step 1: Modify `tests/conftest.py` to add Atlas marker + gating**

Replace the entire file with:

```python
import os

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: requires docker compose (see README)")
    config.addinivalue_line(
        "markers",
        "atlas: requires Atlas M0 cluster (set MONGOSEMANTIC_RUN_ATLAS_INTEGRATION=1 and MONGOSEMANTIC_ATLAS_URI)",
    )


def pytest_collection_modifyitems(config, items):
    if os.environ.get("MONGOSEMANTIC_RUN_INTEGRATION") != "1":
        skip_integration = pytest.mark.skip(reason="set MONGOSEMANTIC_RUN_INTEGRATION=1 to run")
        for item in items:
            if "integration" in item.keywords:
                item.add_marker(skip_integration)

    if os.environ.get("MONGOSEMANTIC_RUN_ATLAS_INTEGRATION") != "1":
        skip_atlas = pytest.mark.skip(
            reason="set MONGOSEMANTIC_RUN_ATLAS_INTEGRATION=1 and MONGOSEMANTIC_ATLAS_URI to run"
        )
        for item in items:
            if "atlas" in item.keywords:
                item.add_marker(skip_atlas)
    elif not os.environ.get("MONGOSEMANTIC_ATLAS_URI"):
        skip_atlas = pytest.mark.skip(reason="MONGOSEMANTIC_ATLAS_URI not set")
        for item in items:
            if "atlas" in item.keywords:
                item.add_marker(skip_atlas)
```

- [ ] **Step 2: Confirm the unit tests still pass**

Run: `python3 -m pytest tests/unit -q`
Expected: 191 passed (or whatever the current baseline is) — exit code 0.

- [ ] **Step 3: Confirm Atlas marker skip works without env vars**

Create a temporary stub `tests/integration/atlas/__init__.py` and a one-line probe:

```bash
mkdir -p tests/integration/atlas
touch tests/integration/atlas/__init__.py
cat > tests/integration/atlas/test_probe.py <<'EOF'
import pytest

@pytest.mark.atlas
def test_atlas_gate_works():
    raise AssertionError("This test should never execute without env gating")
EOF
```

Run: `python3 -m pytest tests/integration/atlas -q`
Expected: 1 skipped, with reason mentioning `MONGOSEMANTIC_RUN_ATLAS_INTEGRATION`.

Run: `MONGOSEMANTIC_RUN_ATLAS_INTEGRATION=1 python3 -m pytest tests/integration/atlas -q`
Expected: 1 skipped, with reason mentioning `MONGOSEMANTIC_ATLAS_URI not set`.

- [ ] **Step 4: Remove the probe file**

```bash
rm tests/integration/atlas/test_probe.py
```

- [ ] **Step 5: Commit**

```bash
git add tests/conftest.py tests/integration/atlas/__init__.py
git commit -m "test(atlas): add MONGOSEMANTIC_RUN_ATLAS_INTEGRATION gating + atlas pytest marker"
```

---

### Task 2: Write the Atlas conftest fixtures

**Files:**
- Create: `tests/integration/atlas/conftest.py`

These fixtures are shared across all tier test files: an Atlas-aware client, topology assertion, sample-dataset preflight, and a `wait_for_search_index_queryable` poller.

- [ ] **Step 1: Create `tests/integration/atlas/conftest.py`**

```python
from __future__ import annotations

import os
import time

import pytest
from pymongo import MongoClient
from pymongo.collection import Collection

from mongosemantic.db.client import Topology, detect_topology


@pytest.fixture(scope="session")
def atlas_uri() -> str:
    uri = os.environ.get("MONGOSEMANTIC_ATLAS_URI")
    if not uri:
        pytest.skip("MONGOSEMANTIC_ATLAS_URI not set")
    return uri


@pytest.fixture(scope="session")
def atlas_client(atlas_uri: str) -> MongoClient:
    client = MongoClient(atlas_uri, serverSelectionTimeoutMS=10000)
    client.admin.command("hello")  # surface auth/allowlist failures immediately
    yield client
    client.close()


@pytest.fixture(scope="session")
def atlas_topology(atlas_client: MongoClient, atlas_uri: str) -> Topology:
    topology = detect_topology(atlas_client, atlas_uri)
    if topology is not Topology.ATLAS:
        pytest.skip(f"URI did not detect as Atlas (got {topology}); skipping atlas suite")
    return topology


@pytest.fixture(scope="session")
def atlas_db_name() -> str:
    return "sample_mflix"


@pytest.fixture(scope="session")
def atlas_collection_name() -> str:
    return "movies"


@pytest.fixture(scope="session")
def atlas_dataset_loaded(
    atlas_client: MongoClient, atlas_db_name: str, atlas_collection_name: str
) -> Collection:
    coll = atlas_client[atlas_db_name][atlas_collection_name]
    count = coll.estimated_document_count()
    if count < 5000:
        pytest.fail(
            f"{atlas_db_name}.{atlas_collection_name} has {count} docs (need >= 5000).\n"
            "In the Atlas console: Database -> '...' -> Load Sample Dataset."
        )
    return coll


def wait_for_search_index_queryable(
    coll: Collection, index_name: str, timeout: float = 180.0, poll: float = 3.0
) -> dict:
    """Poll listSearchIndexes until the index is queryable. Raises TimeoutError on miss."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        for idx in coll.list_search_indexes():
            if idx.get("name") == index_name:
                last = idx
                if idx.get("queryable") is True and idx.get("status") == "READY":
                    return idx
        time.sleep(poll)
    raise TimeoutError(
        f"Atlas search index {index_name!r} not queryable within {timeout}s. Last seen: {last}"
    )


@pytest.fixture
def env_pointing_at_atlas(monkeypatch, atlas_uri: str, atlas_db_name: str):
    """Sets MONGOSEMANTIC_* env vars pointing CliRunner invocations at Atlas."""
    monkeypatch.setenv("MONGOSEMANTIC_URI", atlas_uri)
    monkeypatch.setenv("MONGOSEMANTIC_DB", atlas_db_name)
    monkeypatch.setenv("MONGOSEMANTIC_MODEL", "local-fast")
    return None
```

- [ ] **Step 2: Sanity check — the file imports cleanly**

Run: `python3 -c "from tests.integration.atlas import conftest; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Re-run the unit suite to confirm nothing breaks**

Run: `python3 -m pytest tests/unit -q`
Expected: same passing baseline.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/atlas/conftest.py
git commit -m "test(atlas): conftest fixtures (atlas_client, atlas_topology, dataset preflight, index poller)"
```

---

## Phase B — User Atlas setup

### Task 3: Spin up the Atlas M0 cluster (user-driven)

This is the only manual / user-driven block. The plan documents the exact steps so the user can do them, then resume with a single connectivity check.

**Files:** none (manual)

- [ ] **Step 1: User creates the cluster**

Browser: <https://www.mongodb.com/cloud/atlas/register>
- Sign up (or sign in).
- Create an M0 free cluster. Name: `mongosemantic-test`. Any region.
- Wait ~3 minutes for provisioning.

- [ ] **Step 2: User creates a database user**

Atlas console -> Database Access -> Add New Database User.
- Username: `mongosemantic`
- Auth: password (save it locally; never commit it)
- Role: **Atlas admin** (needed to create search indexes)

- [ ] **Step 3: User adds an IP allowlist entry**

Atlas console -> Network Access -> Add IP Address -> "Add Current IP".

- [ ] **Step 4: User loads the sample dataset**

Atlas console -> Database -> cluster (`mongosemantic-test`) -> "..." menu -> Load Sample Dataset.
Wait ~2 minutes for `sample_mflix`, `sample_mflix`, etc. to populate.

- [ ] **Step 5: User grabs the connection URI**

Atlas console -> Database -> Connect -> Drivers. Copy URI, replace `<password>` with the password from step 2:

```
mongodb+srv://mongosemantic:<password>@mongosemantic-test.xxxxx.mongodb.net/?retryWrites=true&w=majority
```

- [ ] **Step 6: Export env vars in the working shell**

```bash
export MONGOSEMANTIC_ATLAS_URI="mongodb+srv://mongosemantic:<password>@mongosemantic-test.xxxxx.mongodb.net/?retryWrites=true&w=majority"
export MONGOSEMANTIC_RUN_ATLAS_INTEGRATION=1
```

- [ ] **Step 7: Connectivity check via mongosemantic status**

```bash
MONGOSEMANTIC_URI="$MONGOSEMANTIC_ATLAS_URI" \
MONGOSEMANTIC_DB=sample_mflix \
MONGOSEMANTIC_MODEL=local-fast \
mongosemantic status
```

Expected output includes a line: `Topology: atlas`.
If it errors with "ServerSelection" -> IP allowlist drift; re-add current IP.
If it errors with auth -> password typo or wrong user role.

(No git commit here — this is environment setup, not code.)

---

## Phase C — Tier-by-tier verification

Each tier task follows the same shape: write the test → run against Atlas → on pass, commit; on fail, fork to the per-bug workflow at the bottom of this document.

### Task 4: Tier 1 — Smoke test

**Files:**
- Create: `tests/integration/atlas/test_smoke.py`

Simplest possible Atlas exercise: connect, verify topology, apply on one field, index, run worker once, search, get hits.

- [ ] **Step 1: Write `tests/integration/atlas/test_smoke.py`**

```python
"""Tier 1 — Smoke: connectivity, topology detection, apply/index/worker/search."""
from __future__ import annotations

import pytest
from typer.testing import CliRunner

from mongosemantic.cli import app
from mongosemantic.db.client import Topology
from mongosemantic.embeddings.provider import get_provider
from mongosemantic.state import count_by_status
from mongosemantic.worker.runner import process_batch


@pytest.mark.atlas
def test_topology_is_atlas(atlas_topology: Topology):
    assert atlas_topology is Topology.ATLAS


@pytest.mark.atlas
def test_dataset_preflight(atlas_dataset_loaded):
    assert atlas_dataset_loaded.estimated_document_count() >= 5000


@pytest.mark.atlas
def test_smoke_apply_index_worker_search(
    atlas_client, atlas_dataset_loaded, env_pointing_at_atlas, atlas_db_name, atlas_collection_name
):
    runner = CliRunner()
    db = atlas_client[atlas_db_name]

    # Clean slate: tear down any prior config for this collection.
    runner.invoke(app, ["teardown", "--collection", atlas_collection_name, "--yes"])

    # Apply: shadow mode, single field (summary).
    r = runner.invoke(app, [
        "apply", "--collection", atlas_collection_name,
        "--field", "summary",
        "--mode", "shadow",
    ])
    assert r.exit_code == 0, r.output

    # Index: enqueue jobs for all 5,555 docs.
    r = runner.invoke(app, ["index", "--collection", atlas_collection_name])
    assert r.exit_code == 0, r.output
    pending = count_by_status(db).get("pending", 0)
    assert pending > 5000, f"expected >5000 pending jobs, got {pending}"

    # Worker — process one batch to keep the smoke quick.
    provider = get_provider("local-fast")
    process_batch(db, provider, "atlas-smoke", 64)
    assert db[f"{atlas_collection_name}_embeddings"].count_documents({}) >= 64

    # Search — even with partial embedding coverage, top-k should return hits.
    r = runner.invoke(app, [
        "search", "heist gone wrong",
        "--collection", atlas_collection_name,
        "--limit", "3",
    ])
    assert r.exit_code == 0, r.output
    assert "score" in r.output.lower() or "results" in r.output.lower()
```

- [ ] **Step 2: Run the smoke test against Atlas**

```bash
python3 -m pytest tests/integration/atlas/test_smoke.py -v
```

Expected: 3 passed.

If `test_topology_is_atlas` fails -> URI is wrong (not a `*.mongodb.net` host).
If `test_dataset_preflight` fails -> "Load Sample Dataset" wasn't run in Atlas console.
If `test_smoke_apply_index_worker_search` fails -> jump to "Per-bug PR workflow" at the bottom.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/atlas/test_smoke.py
git commit -m "test(atlas): tier 1 smoke — connectivity, topology, apply/index/worker/search"
```

---

### Task 5: Tier 2 — `$vectorSearch` multi-field

**Files:**
- Create: `tests/integration/atlas/test_vector_search.py`

Re-apply to shadow multi-field on `title,plot`. Wait for the vector index to become queryable, then verify scores fall in the cosine range and result count > 0.

- [ ] **Step 1: Write `tests/integration/atlas/test_vector_search.py`**

```python
"""Tier 2 — $vectorSearch with multi-field embedding (summary + description)."""
from __future__ import annotations

import pytest
from typer.testing import CliRunner

from mongosemantic.cli import app
from mongosemantic.db.indexes import vector_index_name
from mongosemantic.embeddings.provider import get_provider
from mongosemantic.state import load_config
from mongosemantic.worker.runner import process_batch

from tests.integration.atlas.conftest import wait_for_search_index_queryable


@pytest.mark.atlas
def test_vector_search_multi_field(
    atlas_client, atlas_dataset_loaded, env_pointing_at_atlas,
    atlas_db_name, atlas_collection_name,
):
    runner = CliRunner()
    db = atlas_client[atlas_db_name]

    runner.invoke(app, ["teardown", "--collection", atlas_collection_name, "--yes"])

    # Apply multi-field: summary + description.
    r = runner.invoke(app, [
        "apply", "--collection", atlas_collection_name,
        "--field", "summary",
        "--field", "description",
        "--mode", "shadow",
    ])
    assert r.exit_code == 0, r.output

    r = runner.invoke(app, ["index", "--collection", atlas_collection_name])
    assert r.exit_code == 0, r.output

    # Process a representative batch — full corpus would burn time on every run.
    provider = get_provider("local-fast")
    for _ in range(20):  # ~1,280 docs with batch size 64
        process_batch(db, provider, "atlas-tier2", 64)

    # Wait for the vector index on the embeddings collection.
    shadow = db[f"{atlas_collection_name}_embeddings"]
    cfg = load_config(db, atlas_collection_name)
    idx_summary = (cfg.vector_index_names or {}).get("summary") or vector_index_name(atlas_collection_name, "summary")
    wait_for_search_index_queryable(shadow, idx_summary, timeout=180)

    # Search and assert atlas-side ranking shape.
    r = runner.invoke(app, [
        "search", "heist gone wrong",
        "--collection", atlas_collection_name,
        "--limit", "5",
    ])
    assert r.exit_code == 0, r.output
    # Atlas-side $vectorSearch produces cosine-ish scores in [0, 1].
    # Brute-force fallback would show dot-product scores well above 1.
    assert "0." in r.output, f"expected fractional similarity scores, got:\n{r.output}"
```

- [ ] **Step 2: Run the tier 2 test**

```bash
python3 -m pytest tests/integration/atlas/test_vector_search.py -v
```

Expected: 1 passed.

If the search command emits "fell back to brute force" or scores look like raw dot products -> bug in the Atlas detection path; jump to "Per-bug PR workflow".

- [ ] **Step 3: Commit**

```bash
git add tests/integration/atlas/test_vector_search.py
git commit -m "test(atlas): tier 2 \$vectorSearch with multi-field embedding"
```

---

### Task 6: Tier 3 — `$search` BM25

**Files:**
- Create: `tests/integration/atlas/test_search_bm25.py`

Verify the `mongosemantic_search_*` BM25 index is created and queryable, and `$search` returns BM25-ranked hits independent of vector similarity.

- [ ] **Step 1: Write `tests/integration/atlas/test_search_bm25.py`**

```python
"""Tier 3 — Atlas $search BM25 index creation and queryability."""
from __future__ import annotations

import pytest

from mongosemantic.search.hybrid import search_index_name
from mongosemantic.state import load_config

from tests.integration.atlas.conftest import wait_for_search_index_queryable


@pytest.mark.atlas
def test_bm25_index_present_and_queryable(
    atlas_client, atlas_dataset_loaded, atlas_db_name, atlas_collection_name,
):
    # Tier 2 already applied multi-field shadow; reuse that state.
    db = atlas_client[atlas_db_name]
    cfg = load_config(db, atlas_collection_name)
    assert cfg is not None, "Tier 2 must run before tier 3 (apply state)"

    shadow = db[f"{atlas_collection_name}_embeddings"]
    bm25_idx = search_index_name(atlas_collection_name, "summary")
    info = wait_for_search_index_queryable(shadow, bm25_idx, timeout=180)
    assert info.get("queryable") is True

    # Run a literal $search query against the BM25 index.
    pipeline = [
        {"$search": {"index": bm25_idx, "text": {"query": "beach", "path": "summary"}}},
        {"$limit": 5},
        {"$project": {"_id": 0, "summary": 1, "score": {"$meta": "searchScore"}}},
    ]
    hits = list(shadow.aggregate(pipeline))
    assert len(hits) > 0, "BM25 $search returned zero hits for 'beach'"
    assert all(h["score"] > 0 for h in hits)
```

- [ ] **Step 2: Run the tier 3 test**

```bash
python3 -m pytest tests/integration/atlas/test_search_bm25.py -v
```

Expected: 1 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/atlas/test_search_bm25.py
git commit -m "test(atlas): tier 3 \$search BM25 index creation + queryability"
```

---

### Task 7: Tier 4 — `$rankFusion` hybrid

**Files:**
- Create: `tests/integration/atlas/test_hybrid_rankfusion.py`

Detect Atlas's MongoDB version. On 8.1+, verify hybrid returns the union of semantic + keyword. On 8.0 or older, verify the documented `notice` fallback path is taken.

- [ ] **Step 1: Write `tests/integration/atlas/test_hybrid_rankfusion.py`**

```python
"""Tier 4 — $rankFusion hybrid path (8.1+) or documented fallback (8.0-)."""
from __future__ import annotations

import pytest
from typer.testing import CliRunner

from mongosemantic.cli import app


def _major_minor(client) -> tuple[int, int]:
    build = client.admin.command("buildInfo")
    parts = build.get("version", "0.0").split(".")
    return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0


@pytest.mark.atlas
def test_hybrid_path_or_documented_fallback(
    atlas_client, env_pointing_at_atlas, atlas_collection_name,
):
    runner = CliRunner()
    major, minor = _major_minor(atlas_client)
    r = runner.invoke(app, [
        "search", "MongoDB 7.0 beach cottage",
        "--collection", atlas_collection_name,
        "--hybrid",
        "--limit", "5",
    ])
    assert r.exit_code == 0, r.output

    if (major, minor) >= (8, 1):
        # 8.1+ supports $rankFusion natively. No fallback banner.
        assert "fell back" not in r.output.lower()
        assert "fallback" not in r.output.lower()
    else:
        # 8.0 or older: hybrid must emit the documented notice rather than silently degrading.
        assert "fallback" in r.output.lower() or "notice" in r.output.lower(), (
            f"Expected hybrid fallback notice on MongoDB {major}.{minor}, got:\n{r.output}"
        )
```

- [ ] **Step 2: Run the tier 4 test**

```bash
python3 -m pytest tests/integration/atlas/test_hybrid_rankfusion.py -v
```

Expected: 1 passed. Test branches automatically by detected MongoDB version.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/atlas/test_hybrid_rankfusion.py
git commit -m "test(atlas): tier 4 \$rankFusion hybrid path with 8.0 fallback branch"
```

---

### Task 8: Tier 5 — Chunked + inline modes

**Files:**
- Create: `tests/integration/atlas/test_chunked_inline.py`

Two scenarios in one orchestrated test: (a) re-apply with `--chunked` on `fullplot`, verify multiple `_chunks` per source doc; (b) re-apply with `--mode inline` on `plot`, verify embeddings written under `_msem.{field}`.

- [ ] **Step 1: Write `tests/integration/atlas/test_chunked_inline.py`**

```python
"""Tier 5 — chunked indexing + inline mode on Atlas."""
from __future__ import annotations

import pytest
from typer.testing import CliRunner

from mongosemantic.cli import app
from mongosemantic.embeddings.provider import get_provider
from mongosemantic.worker.runner import process_batch


@pytest.mark.atlas
def test_chunked_indexing_produces_multiple_chunks(
    atlas_client, atlas_dataset_loaded, env_pointing_at_atlas,
    atlas_db_name, atlas_collection_name,
):
    runner = CliRunner()
    db = atlas_client[atlas_db_name]

    runner.invoke(app, ["teardown", "--collection", atlas_collection_name, "--yes"])
    r = runner.invoke(app, [
        "apply", "--collection", atlas_collection_name,
        "--field", "house_rules",
        "--mode", "shadow",
        "--chunked",
        "--chunk-size", "60",
        "--chunk-overlap", "10",
    ])
    assert r.exit_code == 0, r.output

    r = runner.invoke(app, ["index", "--collection", atlas_collection_name])
    assert r.exit_code == 0, r.output

    provider = get_provider("local-fast")
    for _ in range(10):
        process_batch(db, provider, "atlas-tier5-chunked", 64)

    shadow = db[f"{atlas_collection_name}_embeddings"]
    # Any single source doc with non-trivial house_rules should produce > 1 chunk.
    pipeline = [
        {"$group": {"_id": "$source_id", "n": {"$sum": 1}}},
        {"$match": {"n": {"$gt": 1}}},
        {"$limit": 1},
    ]
    multi_chunk = list(shadow.aggregate(pipeline))
    assert multi_chunk, "expected at least one source doc to chunk into >1 embeddings"


@pytest.mark.atlas
def test_inline_mode_writes_under_msem(
    atlas_client, atlas_dataset_loaded, env_pointing_at_atlas,
    atlas_db_name, atlas_collection_name,
):
    runner = CliRunner()
    db = atlas_client[atlas_db_name]

    runner.invoke(app, ["teardown", "--collection", atlas_collection_name, "--yes"])
    r = runner.invoke(app, [
        "apply", "--collection", atlas_collection_name,
        "--field", "neighborhood_overview",
        "--mode", "inline",
    ])
    assert r.exit_code == 0, r.output

    r = runner.invoke(app, ["index", "--collection", atlas_collection_name])
    assert r.exit_code == 0, r.output

    provider = get_provider("local-fast")
    for _ in range(8):
        process_batch(db, provider, "atlas-tier5-inline", 64)

    # Inline writes the embedding into the source doc under _msem.{field}.
    coll = db[atlas_collection_name]
    sample = coll.find_one({"_msem.neighborhood_overview": {"$exists": True}})
    assert sample is not None, "no doc has _msem.neighborhood_overview after inline indexing"
    vec = sample["_msem"]["neighborhood_overview"].get("vector") or sample["_msem"]["neighborhood_overview"].get("embedding")
    assert isinstance(vec, list) and len(vec) >= 384
```

- [ ] **Step 2: Run the tier 5 test**

```bash
python3 -m pytest tests/integration/atlas/test_chunked_inline.py -v
```

Expected: 2 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/atlas/test_chunked_inline.py
git commit -m "test(atlas): tier 5 chunked + inline modes against Atlas"
```

---

### Task 9: Tier 6 — Migration carry-over

**Files:**
- Create: `tests/integration/atlas/test_migration_carryover.py`

Migrate `summary` from `local-fast` (384-d) to `local-better` (768-d). Verify CLI completes, top-1 control query is stable across migration, both index types exist post-rename, and the archive collection persists.

- [ ] **Step 1: Write `tests/integration/atlas/test_migration_carryover.py`**

```python
"""Tier 6 — migration with vector + search index name carry-over after atomic rename."""
from __future__ import annotations

import re

import pytest
from typer.testing import CliRunner

from mongosemantic.cli import app
from mongosemantic.embeddings.provider import get_provider
from mongosemantic.state import load_config
from mongosemantic.worker.runner import process_batch


def _top_hit_id(runner: CliRunner, collection: str, query: str) -> str:
    r = runner.invoke(app, [
        "search", query,
        "--collection", collection,
        "--limit", "1",
    ])
    assert r.exit_code == 0, r.output
    # The CLI prints the doc _id in the result row; capture it.
    m = re.search(r"\b([0-9a-f]{24})\b", r.output)
    assert m, f"no _id in search output:\n{r.output}"
    return m.group(1)


@pytest.mark.atlas
def test_migration_carries_over_indexes(
    atlas_client, atlas_dataset_loaded, env_pointing_at_atlas,
    atlas_db_name, atlas_collection_name,
):
    runner = CliRunner()
    db = atlas_client[atlas_db_name]

    runner.invoke(app, ["teardown", "--collection", atlas_collection_name, "--yes"])
    r = runner.invoke(app, [
        "apply", "--collection", atlas_collection_name,
        "--field", "summary",
        "--mode", "shadow",
    ])
    assert r.exit_code == 0, r.output

    r = runner.invoke(app, ["index", "--collection", atlas_collection_name])
    assert r.exit_code == 0, r.output

    provider = get_provider("local-fast")
    for _ in range(20):
        process_batch(db, provider, "atlas-tier6-pre", 64)

    query = "heist gone wrong"
    pre_top = _top_hit_id(runner, atlas_collection_name, query)

    # Run the migration.
    r = runner.invoke(app, [
        "migrate", "--collection", atlas_collection_name,
        "--model", "local-better",
    ])
    assert r.exit_code == 0, r.output

    # Verify post-migration: same top hit on the same query.
    post_top = _top_hit_id(runner, atlas_collection_name, query)
    assert pre_top == post_top, (
        f"top hit drifted across migration: pre={pre_top} post={post_top}"
    )

    # Both index types must still exist on the (renamed) embeddings collection.
    cfg = load_config(db, atlas_collection_name)
    assert cfg.embedding_model == "local-better"
    assert cfg.embedding_dim == 768

    shadow = db[f"{atlas_collection_name}_embeddings"]
    idx_names = {i["name"] for i in shadow.list_search_indexes()}
    # Migration renames to *_mig_<ts>; assert at least one vectorSearch + one search index present.
    vector_present = any("mongosemantic" in n and "search" not in n for n in idx_names)
    bm25_present = any("mongosemantic_search" in n for n in idx_names)
    assert vector_present, f"no vector index post-migration; saw {idx_names}"
    assert bm25_present, f"no BM25 index post-migration; saw {idx_names}"

    # Archive collection persists with the old vectors.
    archive_names = [n for n in db.list_collection_names() if "_archive_" in n and atlas_collection_name in n]
    assert archive_names, f"no archive collection found; saw {db.list_collection_names()}"
```

- [ ] **Step 2: Run the tier 6 test**

```bash
python3 -m pytest tests/integration/atlas/test_migration_carryover.py -v
```

Expected: 1 passed. This test is the slowest (re-embedding ~1,280 docs with `local-better`). Allow ~3–5 minutes.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/atlas/test_migration_carryover.py
git commit -m "test(atlas): tier 6 migration with index name carry-over"
```

---

### Task 10: Tier 7 — UI smoke (manual)

**Files:** none (manual verification with a one-line note appended to the PR description).

This tier doesn't get codified — it's eyeball checks against the UI.

- [ ] **Step 1: Start the UI against Atlas**

```bash
MONGOSEMANTIC_URI="$MONGOSEMANTIC_ATLAS_URI" \
MONGOSEMANTIC_DB=sample_mflix \
MONGOSEMANTIC_MODEL=local-fast \
mongosemantic ui --port 8081
```

Open <http://127.0.0.1:8081>.

- [ ] **Step 2: Eyeball-verify each screen**

For each, capture pass/fail in a notes file or PR description:

1. Connection page reports **Atlas cluster** (not replica set / standalone).
2. Collections page lists `embedded_movies` with its configured mode.
3. Search page returns results at Atlas latencies (50–150 ms displayed).
4. Hybrid toggle: search runs, no amber fallback banner (assuming 8.1+).
5. Visualize page renders airbnb embeddings; sample-size dropdown works.
6. Migrate modal opens; you don't have to actually migrate again here.

- [ ] **Step 3: Stop the UI process**

`Ctrl+C` in the UI terminal.

- [ ] **Step 4: Record results**

If all 6 screens pass: nothing to commit, just add the line `Tier 7 UI smoke: all 6 screens pass.` to your PR description draft (kept in your notes for now).
If any screen fails: jump to "Per-bug PR workflow".

---

## Phase D — Documentation updates

### Task 11: Rewrite `docs/atlas-setup.md` for sample_mflix

**Files:**
- Modify: `docs/atlas-setup.md`

The current runbook references `seed_demo.py` and three collections (articles/products/recipes). Rewrite to use the Atlas "Load Sample Dataset" flow and `sample_mflix.embedded_movies`.

- [ ] **Step 1: Replace section 5 (seed) and section 6 (apply+index)**

Open `docs/atlas-setup.md`. Replace section "## 5. Seed the demo data into Atlas" through "## 6. Apply + index" with:

```markdown
## 5. Load the sample dataset

In the Atlas console: **Database** -> your cluster -> **"..."** -> **Load Sample Dataset**.
Wait ~2 minutes. This populates `sample_mflix`, `sample_mflix`, and several other databases — we'll use `sample_mflix.embedded_movies` (3,483 curated movie records, ~40 MB).

```bash
export MONGOSEMANTIC_DB=sample_mflix
```

## 6. Apply + index

```bash
mongosemantic apply -c movies -f summary -f description
mongosemantic index -c movies
mongosemantic worker --once
```

On Atlas, `apply` automatically creates two index types on each shadow collection:

- `mongosemantic_<coll>_<digest>` — the **vectorSearch** index used by `$vectorSearch`.
- `mongosemantic_search_<coll>_<digest>` — the **search** index used by `$search` and hybrid.

Both indexes take **30–90 seconds** to come online. The CLI returns immediately; the indexes finish building in the background. You can watch progress in Atlas -> **Database** -> cluster -> **Search** tab.
```

- [ ] **Step 2: Update section 7 query examples to use airbnb fields**

Find each `mongosemantic search` example in section 7. Update the `-c embedded_movies` references to `-c movies` and use natural-language queries that match airbnb listings (e.g., "heist gone wrong", "robots questioning their existence", "dystopian future government").

- [ ] **Step 3: Update the migration example in section 7**

Replace the `migrate -c recipes` example with:

```bash
mongosemantic migrate -c movies -m local-better
```

Update the verification bullets to reference `movies_embeddings_archive_<ts>`.

- [ ] **Step 4: Add a "Verified via test suite" section**

After section 7, add:

```markdown
## 7a. Verified automatically

The verification above is also codified as a pytest suite under
`tests/integration/atlas/`. Re-run it against any Atlas cluster with:

```bash
export MONGOSEMANTIC_RUN_ATLAS_INTEGRATION=1
export MONGOSEMANTIC_ATLAS_URI="mongodb+srv://..."
python3 -m pytest tests/integration/atlas -v
```

Each of tiers 1–6 from this runbook has a corresponding `test_*.py`.
Tier 7 (UI) is manual.
```

- [ ] **Step 5: Commit**

```bash
git add docs/atlas-setup.md
git commit -m "docs(atlas): rewrite runbook for sample_mflix + add automated-suite pointer"
```

---

### Task 12: Update `docs/HANDOFF.md` "live-tested" status

**Files:**
- Modify: `docs/HANDOFF.md`

Move the four Atlas paths from the "not live-tested" section into "What's working".

- [ ] **Step 1: Delete the un-verified Atlas section**

In `docs/HANDOFF.md`, delete the entire section starting at `## What's working but **not live-tested against real Atlas**` through (and including) its bullet list and trailing paragraph that ends "...largest review-vs-execute gap in the project."

- [ ] **Step 2: Add the Atlas paths to "What's working"**

In the "What's working (live-tested)" section, append these bullets to the existing list:

```markdown
- **Atlas `$vectorSearch`** end-to-end on `sample_mflix.embedded_movies`
- **Atlas `$search` (BM25)** index creation + queryability
- **Atlas `$rankFusion` hybrid** (with documented 8.0 fallback)
- **Atlas migration** with vector + search index carry-over after atomic rename
```

Update the test surface line in that section:

```markdown
Test surface: **191 unit + 10 integration + 7 Atlas integration**, all green, lint clean.
Unit tests run offline (mongomock); integration tests need docker; Atlas tests
need `MONGOSEMANTIC_RUN_ATLAS_INTEGRATION=1` + a cluster URI.
```

(Adjust the Atlas test count to match what was actually shipped — one per tier file 1, 2, 3, 4, 5×2-fns, 6 = 7 distinct test functions.)

- [ ] **Step 3: Remove the Atlas item from the "next" list**

In the section "If you're going to ship something next" (or "What's worth doing next"), delete bullet #2 ("Run the Atlas runbook"). Re-number the remaining bullets.

- [ ] **Step 4: Commit**

```bash
git add docs/HANDOFF.md
git commit -m "docs(handoff): Atlas paths moved to live-tested; runbook bullet removed from next-up"
```

---

## Phase E — Final review and PR

### Task 13: Full local test pass + lint

**Files:** none (verification only)

- [ ] **Step 1: Unit tests**

```bash
python3 -m pytest tests/unit -q
```

Expected: prior passing baseline.

- [ ] **Step 2: Local integration suite (docker)**

```bash
MONGOSEMANTIC_RUN_INTEGRATION=1 python3 -m pytest tests/integration -q --ignore=tests/integration/atlas
```

Expected: 10 passed (the existing baseline).

- [ ] **Step 3: Atlas suite end-to-end**

```bash
MONGOSEMANTIC_RUN_ATLAS_INTEGRATION=1 \
MONGOSEMANTIC_ATLAS_URI="$MONGOSEMANTIC_ATLAS_URI" \
python3 -m pytest tests/integration/atlas -v
```

Expected: 7 passed (or whatever count matches what was shipped).

- [ ] **Step 4: Lint**

```bash
ruff check .
```

Expected: clean.

---

### Task 14: Independent code review (subagent dispatch)

**Files:** none (review only)

- [ ] **Step 1: Dispatch the `general-purpose` agent**

Send to a fresh `general-purpose` subagent (via the Agent tool) with this prompt:

> Review the diff on the current `feat/atlas-verification` branch against `main`. Focus on:
> 1. **Correctness** — do the assertions actually verify the Atlas-only behavior, or could they pass on a brute-force fallback?
> 2. **Scope** — any unrelated refactoring snuck in?
> 3. **Test isolation** — does each tier clean up after itself? Are fixtures session-scoped where appropriate?
> 4. **Missed regressions** — for each Atlas-only path in `docs/atlas-setup.md`, point to the test that covers it. Flag any gap.
> 5. **Security** — no Atlas credentials committed; gating prevents accidental runs against prod.
>
> Output: a punch list of must-fix vs nice-to-have findings, under 400 words. Use file:line citations.

- [ ] **Step 2: Address findings**

For each must-fix:
- If it's a code error, edit the relevant test/fixture and re-run the suite.
- If it's a missing test, add it as a new function in the appropriate `test_*.py`.

For each nice-to-have: decide inline whether to take it. If you take it, commit. If you don't, note "considered, declined: <reason>" in the PR description draft.

- [ ] **Step 3: Re-run all three test suites after fixes**

```bash
python3 -m pytest tests/unit -q
MONGOSEMANTIC_RUN_INTEGRATION=1 python3 -m pytest tests/integration -q --ignore=tests/integration/atlas
MONGOSEMANTIC_RUN_ATLAS_INTEGRATION=1 MONGOSEMANTIC_ATLAS_URI="$MONGOSEMANTIC_ATLAS_URI" python3 -m pytest tests/integration/atlas -v
ruff check .
```

Expected: all green.

---

### Task 15: Push branch and open PR

**Files:** none (git/gh)

- [ ] **Step 1: Push the branch**

```bash
git push -u origin feat/atlas-verification
```

- [ ] **Step 2: Open the PR**

```bash
gh pr create --title "Atlas verification: live-tested + codified as pytest suite" --body "$(cat <<'EOF'
## Summary

- Live-tests every Atlas-only path (`$vectorSearch`, `$search`, `$rankFusion`, migration carry-over) against an M0 cluster on `sample_mflix.embedded_movies`.
- Codifies the verification as `tests/integration/atlas/` (7 tests, env-gated on `MONGOSEMANTIC_RUN_ATLAS_INTEGRATION=1`).
- Rewrites `docs/atlas-setup.md` to use the Atlas "Load Sample Dataset" flow.
- Moves the four flagged paths in `docs/HANDOFF.md` from "not live-tested" to "working".

Spec: `docs/superpowers/specs/2026-05-19-atlas-verification-design.md`

## Test plan

- [x] `python3 -m pytest tests/unit -q` — green
- [x] `MONGOSEMANTIC_RUN_INTEGRATION=1 python3 -m pytest tests/integration --ignore=tests/integration/atlas` — green
- [x] `MONGOSEMANTIC_RUN_ATLAS_INTEGRATION=1 python3 -m pytest tests/integration/atlas -v` — green against `mongosemantic-test` M0 cluster
- [x] `ruff check .` — clean
- [x] Tier 7 UI smoke (manual): Connection / Collections / Search / Hybrid / Visualize / Migrate all pass against Atlas

## Out of scope

- CI integration of the Atlas suite (deferred — local pre-release only for now).
- Atlas Search Nodes / dedicated search tier.
- Performance benchmarking.
EOF
)"
```

- [ ] **Step 3: User reviews + merges on GitHub**

The user reviews the PR in the browser. Once merged:

```bash
git checkout main
git pull --ff-only
git branch -d feat/atlas-verification
```

- [ ] **Step 4: Decide on a tag**

If this PR is large enough to warrant a release (it includes new tests + doc changes; not user-visible functionality), tag a patch release:

```bash
# Only if user agrees a release is warranted:
git tag v0.7.2
git push origin v0.7.2
```

Otherwise, no tag — the changes ride on the next feature release.

---

## Per-bug PR workflow

Invoked from any tier whose test fails. Each bug gets its own isolated branch.

1. **Branch off `main`** (not off the in-progress `feat/atlas-verification` branch):

   ```bash
   git stash               # save in-progress tier work
   git checkout main
   git checkout -b fix/atlas-<short-slug>
   ```

2. **Write the failing regression test first.** Put it under `tests/integration/atlas/<tier-file>.py` if it's Atlas-specific, or under `tests/unit/...` if the underlying bug is reproducible offline. Confirm it fails on `main`:

   ```bash
   python3 -m pytest <new-test-path> -v
   # Expected: FAIL
   ```

3. **Fix the code.** Keep the change scoped — no refactoring of adjacent files unless directly required.

4. **Run unit + lint locally:**

   ```bash
   python3 -m pytest tests/unit -q
   ruff check .
   ```

5. **Independent code review.** Dispatch a fresh `general-purpose` subagent with:

   > Review the diff on `fix/atlas-<slug>` vs `main`. Focus on: (1) does the fix address the root cause or paper over the symptom? (2) is the regression test tight enough to fail again if the bug recurs? (3) any scope creep into unrelated files? Output under 200 words.

   Address findings.

6. **Push + PR:**

   ```bash
   git push -u origin fix/atlas-<slug>
   gh pr create --title "fix(atlas): <one-line>" --body "<summary referencing tier and regression test>"
   ```

7. **User reviews + merges on GitHub.**

8. **Tag a patch release if user-facing:**

   ```bash
   git checkout main
   git pull --ff-only
   git tag v0.7.x
   git push origin v0.7.x
   ```

9. **Resume the verification work:**

   ```bash
   git checkout feat/atlas-verification
   git rebase main             # pick up the fix
   git stash pop               # restore in-progress tier work
   ```

   Re-run the failing tier's test. It should now pass; continue to the next tier.
