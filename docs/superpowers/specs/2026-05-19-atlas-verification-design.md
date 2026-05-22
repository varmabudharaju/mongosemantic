# Atlas verification — design spec

**Date:** 2026-05-19
**Sub-project:** 1 of 6 toward PyPI publish
**Owner:** varmabudharaju
**Status:** approved → ready for plan

---

## Goal

Live-test every Atlas-only code path against a real M0 cluster on
realistic data, codify each path as a regression test, and update
`docs/HANDOFF.md` to move the four flagged paths from "not live-tested"
into "working".

The four Atlas-only paths:

1. `$vectorSearch` aggregation
2. `$search` BM25 aggregation
3. `$rankFusion` hybrid path
4. Migration with vector + search index name carry-over after atomic
   rename

These are logically reviewed and unit-tested (via mongomock) but have
never been exercised against a real Atlas cluster. This is the largest
review-vs-execution gap in the project per `docs/HANDOFF.md`.

## Non-goals

- Performance benchmarking against Atlas (separate concern).
- Atlas Search Nodes / dedicated search tier (M0 is the target).
- Multi-region / sharded deployments.
- Editing or extending unrelated subsystems while we're in Atlas paths.

## Dataset

`sample_mflix.embedded_movies`, loaded via Atlas console's "Load
Sample Dataset" button.

Rationale: 3,483 documents, Atlas's own curated subset of the mflix
corpus shipped specifically as the Vector Search demo dataset. Has rich
text fields (`title`, `plot`, `fullplot`), nested arrays (`genres`,
`cast`, `directors`), and fits M0 with embeddings. Single collection
covers shadow + inline + chunked + multi-field + migration scenarios
without juggling multiple seeders.

**Dataset pivot history (on-execution):**

1. Original spec: `sample_airbnb.listingsAndReviews` (5,555 docs). Pivoted
   on execution because only `sample_mflix` had been loaded on the
   verification cluster and re-loading would have added a manual round trip.
2. Second choice: `sample_mflix.movies` (21,349 docs). Pivoted again because
   Atlas per-doc latency made `mongosemantic index` over 21k docs take ~60 min
   — impractical for a verification suite that re-runs before every release.
3. Final: `sample_mflix.embedded_movies` (3,483 docs). Same field shape as
   `movies` (it's a curated subset), is Atlas's official Vector Search demo
   dataset (more recognizable in docs), and indexes end-to-end in ~5 min.

## Field strategy

All operations run against `sample_mflix.embedded_movies`:

| Phase | Mode | Fields | Path exercised |
|---|---|---|---|
| Apply A | shadow, multi-field | `title`, `plot` | `$vectorSearch` multi-field merge |
| Apply B | shadow, chunked | `fullplot` | Chunked indexing on Atlas |
| Apply C | inline | `plot` | Inline mode on Atlas |
| Migrate | shadow → `local-better` | `title` | Migration carry-over |

## Architecture

### Test suite layout

```
tests/integration/atlas/
├── __init__.py
├── conftest.py                    # env-gating, atlas_client fixture, index-ready poller
├── test_smoke.py                  # tier 1: connectivity + topology detection + minimal apply/index/search
├── test_vector_search.py          # tier 2
├── test_search_bm25.py            # tier 3
├── test_hybrid_rankfusion.py      # tier 4 (incl. 8.0 fallback notice path)
├── test_chunked_inline.py         # tier 5
└── test_migration_carryover.py    # tier 6
```

### Gating

Tests skip with a clear `pytest.skip` reason unless **both** of:

- `MONGOSEMANTIC_RUN_ATLAS_INTEGRATION=1`
- `MONGOSEMANTIC_ATLAS_URI=<mongodb+srv://…>`

This mirrors the existing `MONGOSEMANTIC_RUN_INTEGRATION` pattern so
the Atlas suite never runs accidentally and never blocks CI.

### Test isolation strategy

Each `test_*.py` is one orchestrated end-to-end scenario, not a bag of
unit tests. Module-scoped fixtures handle setup/teardown (apply →
wait-for-index → assertions → teardown). Inside a file, multiple
assert-style functions may share the module fixture. This honors the
Atlas reality: index builds take 30–90 s and re-applies are stateful,
so sequential orchestration is the right unit, not isolated tests.

Tests share the single `embedded_movies` collection but run in
order via filename (pytest default) and clean up their config in
teardown so the next tier starts from a known state.

### conftest fixtures

- `atlas_client` — session-scoped `MongoClient` pointing at the URI.
- `atlas_topology` — asserts `Topology: atlas` (skip suite cleanly if
  the URI is not Atlas).
- `atlas_dataset_loaded` — asserts `sample_mflix.embedded_movies`
  exists with >= 5,000 docs; fails fast with an actionable message
  ("Load 'Sample Dataset' in the Atlas console") if not.
- `wait_for_search_index_queryable(client, db, coll, name, timeout=120)`
  — polls `listSearchIndexes` until `queryable: true` or timeout.

### Files modified

- `docs/atlas-setup.md` — rewritten to use `sample_mflix` instead of
  `seed_demo.py`; field names updated to embedded_movies fields throughout.
- `docs/HANDOFF.md` — section "What's working but not live-tested
  against real Atlas" → those bullets move into "What's working".
- `docs/superpowers/specs/2026-05-19-atlas-verification-design.md` —
  this document.
- `tests/integration/atlas/**` — new directory + files per layout above.

## Verification tiers

Run in order. Each tier gates the next.

### Tier 1 — Smoke (~5 min, fail fast)

Connect → load sample dataset → `mongosemantic status` reports
`Topology: atlas` → `apply` on `embedded_movies` with `title`
only → `index` → `worker --once` → `search` returns hits.

**Stop here if it fails.** No deeper tier is meaningful until the
basic Atlas wiring works.

### Tier 2 — Vector + multi-field

Re-`apply` to shadow multi-field on `title,plot`. Verify
`$vectorSearch` runs Atlas-side (not brute-force fallback) and merged
scores fall in the 0.6–0.8 cosine range. Codified as
`test_vector_search.py`.

### Tier 3 — `$search` BM25

Verify the `mongosemantic_search_<coll>_<digest>` index is created and
that `$search` queries return BM25-ranked hits independent of vector
similarity. Codified as `test_search_bm25.py`.

### Tier 4 — `$rankFusion` hybrid

Detect cluster MongoDB version. If 8.1+, verify hybrid returns both
semantic neighbors and keyword anchors. If 8.0 or older, verify the
documented `notice` fallback path is taken and CLI emits the warning.
Codified as `test_hybrid_rankfusion.py`.

### Tier 5 — Chunked + inline

Re-`apply` with `--chunked` on `fullplot`. Verify multiple
`_chunks` entries per doc on Atlas, and that chunked search returns
paragraph-level excerpts. Then re-`apply` with `--mode inline` on
`plot`. Verify inline embedding writes under
`_msem.{field}`. Codified as `test_chunked_inline.py`.

### Tier 6 — Migration carry-over

`mongosemantic migrate -c embedded_movies -m local-better` to
move `title` from 384-d to 768-d. Verify:

1. CLI progress bar reaches 100%.
2. Top-1 result for a control query is the same movie before and
   after migration.
3. Both `mongosemantic_*` and `mongosemantic_search_*` indexes exist
   on the renamed embeddings collection with `_mig_<ts>` markers.
4. `embedded_movies_embeddings_archive_<ts>` still holds the old
   384-d vectors.

Codified as `test_migration_carryover.py`.

### Tier 7 — UI smoke (manual)

`mongosemantic ui --port 8081` against the Atlas URI. Eyeball:

- Connection page reports **Atlas cluster**.
- Search returns results at Atlas latencies (50–150 ms).
- Hybrid toggle does **not** show the amber fallback banner (assuming
  8.1+).
- Visualize page renders the embedded_movies embeddings.
- Migrate modal works end-to-end with polled progress.

Not codified. If a UI failure surfaces, the per-bug workflow runs but
the regression-test step is best-effort — for visual/JS bugs a unit
test against the API layer is preferred; if none is feasible, the PR
notes that explicitly rather than skipping the test step silently.

### Tier 8 — Connection page: save, test, error mapping, disconnect

End-to-end API exercise of the connection page against a real Atlas
URI: initial not-connected state, save + topology probe, GET reflects
saved config, `/api/connection/test` pings active connection, wrong-
password save returns a mapped error code without writing config, and
DELETE clears the stored connection. Codified as
`test_t8_connection_page.py`.

| Tier | Description | Status |
|---|---|---|
| 1 | smoke — connect, apply, index, search | (status) |
| 2 | vector + multi-field `$vectorSearch` | (status) |
| 3 | `$search` BM25 | (status) |
| 4 | `$rankFusion` hybrid (8.1+ / 8.0 fallback) | (status) |
| 5 | chunked + inline | (status) |
| 6 | migration carry-over | (status) |
| 7 | UI smoke (manual) | (status) |
| 8 | connection page — save, test, error mapping, disconnect | (status) |

## Per-bug PR workflow

Triggered when any tier surfaces a defect. Each fix gets its own
isolated branch and PR.

1. `git checkout -b fix/atlas-<short-slug>` from latest `main`.
2. **Failing regression test first** — write the test under
   `tests/integration/atlas/` (or the relevant `tests/unit/` location
   if the bug isn't Atlas-specific). Confirm it fails on `main`.
3. Fix the code. Keep the change scoped to the surfaced bug.
4. Local green: `python3 -m pytest tests/unit && ruff check .`. If
   the regression is Atlas-only, also run the Atlas suite locally.
5. **Independent code review.** Dispatch a `general-purpose` agent
   with: branch diff, the failing test, the original tier context,
   and "review for correctness, regressions, scope creep, missing
   tests". Address findings before opening the PR.
6. `git push -u origin fix/atlas-<…>` and `gh pr create` with a
   summary that references the failing tier and the regression test.
7. User reviews the PR on GitHub and merges.
8. If the fix warrants a patch release, tag `v0.7.x` and push.
9. `git checkout main && git pull` and resume the runbook at the
   failed tier.

**Authorship.** All commits and PRs in the user's name only. No
Co-Authored-By Claude/Anthropic trailers.

## If no bugs surface

Single PR (`feat/atlas-verification`) containing:

- The new `tests/integration/atlas/` suite.
- Rewritten `docs/atlas-setup.md` using `sample_mflix`.
- `docs/HANDOFF.md` bullet relocation.
- This design spec.

## Success criteria

- All seven tiers pass (tiers 1–6 via test suite, tier 7 manual).
- `MONGOSEMANTIC_RUN_ATLAS_INTEGRATION=1 pytest tests/integration/atlas`
  exits 0 against the cluster.
- `docs/HANDOFF.md` no longer lists the four paths as un-verified.
- `docs/atlas-setup.md` is re-walkable by a stranger using the Atlas
  console "Load Sample Dataset" flow.
- Every bug found in the process has both a fix commit and a
  regression test that fails on the parent commit.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| M0 free tier has cold-start delays and a queryable-index lag (~30–90 s) | `wait_for_search_index_queryable` fixture with 120 s timeout and actionable error |
| Atlas runs MongoDB 8.0 (not 8.1+); `$rankFusion` skips | Tier 4 explicitly tests both branches (8.1+ path + 8.0 fallback notice) |
| IP allowlist drift during the session | Surface in tier 1 smoke — connection failure points at allowlist |
| Sample dataset not loaded in cluster | `atlas_dataset_loaded` fixture fails fast with the exact Atlas-console step |
| 512 MB cap exceeded by indexes + chunked re-applies | Drop intermediate shadow collections between tiers; tier 5 cleans up before tier 6 starts |

## Out of scope

- Atlas Search Nodes (dedicated tier).
- Performance benchmarking.
- Multi-cluster failover.
- CI integration of the Atlas suite (a future job — for now, local
  pre-release only).
