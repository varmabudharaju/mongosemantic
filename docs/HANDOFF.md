# mongosemantic — handoff

The state of the project at v0.7.1, written for whoever picks it up next
(future you, a collaborator, an open-source contributor). Reads in
~10 minutes.

For a deeper map of the code, see [`ARCHITECTURE.md`](ARCHITECTURE.md).
For day-to-day dev workflows, see [`CONTRIBUTING.md`](CONTRIBUTING.md).
For the project pitch, see [`../README.md`](../README.md).

---

## What it is

**mongosemantic adds semantic search to any MongoDB database with no
schema changes, no separate vector database, and no manual sync code.**

- Connect (Atlas / self-hosted replica set / self-hosted standalone)
- Pick a collection + field
- Run `apply`, then `index`, then `worker`
- Search by meaning from CLI, a web UI, or via Claude Desktop (MCP)

If you only have time to read one sentence: it's the equivalent of
[pgsemantic](https://github.com/varmabudharaju/pgvector-setup) for
MongoDB, built around the constraints `pymongo` and Atlas Search impose.

---

## Releases shipped (in order of effort spent)

| Tag | What landed |
|---|---|
| v0.1.0 | MVP — apply, index, search, worker, sync (CLI only) |
| v0.1.x | Three real bugs in v0.1.0 fixed: inline mode was dead, chunking flag was ignored, multi-field search only used `fields[0]` |
| v0.2.0 | Web UI — FastAPI + vanilla HTML/JS frontend, 7 pages |
| v0.3.0 | MCP server — 9 tools (later 11), Claude Desktop integration |
| v0.4.0 | Hybrid search — `$rankFusion` (Atlas) with fallback to semantic |
| v0.5.0 | Online model migration — temp shadow + atomic rename |
| v0.6.0 | Worker DX — `--once`, heartbeats, failed-job introspection |
| v0.6.1 | UI polish — MCP page, Query dropdown, Search button, Guide page, bigger seed |
| v0.6.2 | "How to use" callouts on every UI page |
| v0.6.3 | Left sidebar nav (replacing top bar) + mobile hamburger |
| v0.7.0 | UI completeness — Reconfigure / Reindex / Remove / sample-docs / `teardown` |
| v0.7.1 | Live per-collection indexing activity + global queue badge |

Every tag is pushed. Nothing is local-only.

---

## What's working (live-tested)

Confirmed against the docker-compose replica set + standalone on the demo
data, plus a real Atlas M0 cluster for the Atlas-only paths (as of v0.7.5):

- **Apply / index / search** across shadow mode, inline mode, and shadow + chunking
- **Multi-field embedding** with merged scoring (unit-tested; on Atlas use
  single-field on M0 — multi-field needs M10+ due to the FTS-index cap)
- **Cross-collection fanout** ranked by similarity
- **Chunked search** returns paragraph excerpts, not whole docs
- **Change-stream sync** (live insert auto-embeds)
- **Polling sync** on standalone via `updated_at` watermark
- **Worker heartbeat** — dashboard reflects running / stale / dead
- **Online migration** — `local-fast` 384 d → `local-better` 768 d on
  replica set with the search still working after the swap (Atlas: M10+
  only; M0/M2/M5 can't fit the 4-index swap window)
- **Hybrid fallback** with explicit `notice` on self-hosted / inline
- **Safe aggregation** with stage allowlist
- **MCP server** via stdio (Claude Desktop) and SSE (Cursor / others)
- **Web UI** end-to-end across all pages, against 23 k+ real movies
- **Atlas `$vectorSearch`** — live-tested end-to-end on `sample_mflix.embedded_movies`
- **Atlas `$search` (BM25)** — index creation + querying verified
- **Atlas `$rankFusion` hybrid** — RRF-fused results verified
- **Atlas TLS via certifi** — works without manual `SSL_CERT_FILE` on macOS

Test surface: **203 unit + 10 integration + 6 Atlas integration**, all
green, lint clean. Unit tests run offline (mongomock); integration tests
need docker; Atlas tests need `MONGOSEMANTIC_RUN_ATLAS_INTEGRATION=1` +
`MONGOSEMANTIC_ATLAS_URI`.

The 10-minute Atlas runbook at [`atlas-setup.md`](atlas-setup.md) is
re-walkable; each documented path also has a corresponding test under
`tests/integration/atlas/` so you can re-run end-to-end with a single
command. Bugs found during the original walkthrough are all fixed and
shipped as v0.7.2–v0.7.5.

---

## What's intentionally not built

The list of *thought-about-and-decided-against*:

- **Worker control from the UI** (start/stop/kill). Managing a worker
  subprocess from the UI process adds complexity that isn't worth it
  for a single-user tool. Run `mongosemantic worker` in a terminal.
- **Multi-tenant auth on the UI.** It's localhost-only by default with
  CSRF + rate limit + security headers. A real deployment puts it
  behind your own auth proxy.
- **Streaming responses for large search results.** Current limits
  (100 results, 100 aggregation rows) are well under what JSON-over-HTTP
  handles cleanly.
- **Edit a single document from the UI.** mongosemantic is not a
  general-purpose Mongo admin tool. Use Compass or `mongosh` for that.
- **Cancel a running migration.** Migrations are designed to be
  re-runnable from any interruption point. Kill the process; the temp
  shadow persists; the next `migrate` resumes.
- **K-means clustering + keyword labels on the visualize page.**
  Considered as a follow-up. Current PCA scatter is enough to see
  cluster structure visually.

---

## Known limitations

- **Inline mode + chunking is rejected at apply.** Atlas vector indexes
  use a single path per field; arrays of chunks under `_msem.{field}`
  don't map cleanly. Use shadow mode if you need chunking.
- **Inline-mode migration is rejected** for the same reason — would
  require duplicating user data in flight. Convert to shadow first.
- **Self-hosted brute-force scales to ~100 k chunks** per collection
  with reasonable latency. Past that, Atlas Vector Search is the
  recommended path. The "fine up to 100k" line in the README is from
  measured latency on the demo replica set.
- **Visualize caps at 50 000 points.** Above that, the JSON payload
  back to the browser and the canvas hover hit-test become awkward.
- **No retry-per-collection in the dashboard** — only retry-all-failed.
  CLI has `retry --collection X`. UI button would be a small follow-up.
- **`mongosemantic_workers` is not garbage-collected automatically.**
  `prune_dead()` exists in `state.heartbeat` but nothing calls it.
  A cron entry or a startup-time prune would close that loop.

---

## Design decisions worth knowing

These appear in the code but are easy to miss without context.

### 1. `enqueue_for_doc` is the only place that decides "what jobs cover this doc"

Earlier (v0.1.0) every code path that touched embeddings duplicated this
logic: change_stream.py, polling.py, commands/index.py,
commands/reindex.py. That duplication is exactly where the chunking and
dedup bugs hid. They were three lines apart in four different files.

After v0.1.x, every caller goes through `sync/enqueue.py:enqueue_for_doc`.
If you find yourself writing a new flow that needs to embed a doc, **do
not write a new version of the chunking / hash-skip / stale-cleanup
logic**. Call `enqueue_for_doc` and add a flag if necessary.

### 2. Search uses `cfg.embedding_model`, not the global env var

A nasty bug shipped in v0.5: searching a collection that had been
migrated to a different model produced ~0.08 scores and complete
nonsense rankings. The query was embedded with `MONGOSEMANTIC_MODEL`
from the env, not with the model that was actually used to embed the
stored vectors. Now the search code looks up the per-collection model
and caches one provider per distinct model.

Regression covered by `test_search_embeds_with_collection_model_not_global_setting`.

### 3. Migrations write cfg before the rename, not after

The window between cfg-update and atomic rename is in milliseconds. In
that window, search reads "new model + new dim" from cfg but the old
shadow is still backing the collection name. A search may return zero
results for that brief moment. It will never return mismatched-dim
garbage. This ordering is deliberate — the alternative (rename first,
then cfg) creates a window where worker writes land in the wrong shadow
with the wrong model.

### 4. The web UI has one source of truth for copy

All user-facing strings live in `mongosemantic/web/content.py`. The
frontend fetches them via `GET /api/content` and hydrates the page on
load. This means a non-developer can edit copy in one file and reload
the page to see it — no template engine, no rebuild.

### 5. No node_modules, no build step

`index.html` + `app.js` + `style.css` are served verbatim from
`mongosemantic/web/static/`. The CSP is `default-src 'self'`. We
deliberately accept no React / no TypeScript / no bundler so the
project stays trivially installable. If the frontend ever needs a
component framework, that's a real decision to make — it'd ripple
through the install story.

---

## Quick reference

```bash
# Local setup (one-time)
git clone https://github.com/varmabudharaju/mongosemantic
cd mongosemantic
pip install -e ".[dev,openai]"
docker compose up -d              # replica set on 27117, standalone on 27219

# Reset + seed
python3 scripts/seed_demo.py      # small hand-curated (~185 articles)
python3 scripts/seed_mflix.py     # MongoDB's official 23k-movie sample

# Tests
python3 -m pytest tests/unit                                   # offline, fast
MONGOSEMANTIC_RUN_INTEGRATION=1 python3 -m pytest tests/integration

# Lint
ruff check .

# UI
mongosemantic ui --port 8081      # http://127.0.0.1:8081
# (8080 is often squatted by other tools on dev machines)

# CLI flow against the seeded data
export MONGOSEMANTIC_URI="mongodb://localhost:27117/?replicaSet=rs0"
export MONGOSEMANTIC_DB=demo
export MONGOSEMANTIC_MODEL=local-fast
mongosemantic apply -c movies -f title -f plot
mongosemantic index -c movies
mongosemantic worker --once                # processes all pending jobs, then exits
mongosemantic search "robots questioning their existence" -c movies

# MCP into Claude Desktop
mongosemantic integrate claude             # writes the config, then restart Claude
```

---

## If you're going to ship something next

A few ideas, ordered by usefulness:

1. **Publish to PyPI.** Currently install is `pip install -e .` from
   source. The `pyproject.toml` is already set up for `hatchling`; a
   `python3 -m build && twine upload dist/*` should work.
2. **K-means clustering + keyword labels on visualize.** Real value
   bump for the demo story.
3. **Per-collection retry button** in the dashboard's failed-jobs
   section (the API already supports it).
4. **Worker garbage-collection** — wire up `prune_dead` to run at
   `worker` startup or as a cron.
5. **A real changelog publisher** — `release-please` or
   `python-semantic-release` could automate version bumps from commit
   messages, since the commits already follow conventional-commits.
