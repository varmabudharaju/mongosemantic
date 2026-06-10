# Changelog

## 0.9.0 — 2026-06-10

Search-quality release: metadata filters, local cross-encoder
reranking, and hybrid search on every topology — not just Atlas.

- **Metadata filtering on every search path.** `search "..." --filter
  '{"year": {"$gte": 1960}}'` (CLI), a Filter input on the web Search
  page, and a `filter` param on the `semantic_search` / `hybrid_search`
  MCP tools. Filters are plain MongoDB queries against source-document
  fields and need no reindex. Local paths (brute-force, HNSW)
  pre-filter matching `_id`s — exact; Atlas `$vectorSearch` paths
  over-fetch ×5 and post-match after the source `$lookup`, so a highly
  selective filter can return fewer than `limit` rows there.
  `$where`/`$function`/`$accumulator`/`$text`/`$expr` are rejected;
  invalid filters exit 2 (CLI), return 400 (web), or raise (MCP) —
  including operators MongoDB itself rejects at runtime.
- **Local cross-encoder reranking.** `--rerank` (CLI), a Rerank toggle
  (web), and a `rerank` param on the MCP search tools (including
  `search_all_collections`). Two-stage retrieval: over-fetch limit×5
  candidates, re-score with `cross-encoder/ms-marco-MiniLM-L-6-v2`
  (~80 MB, local CPU, lazy-loaded once per process), return the top
  limit. Rows keep the original score as `vector_score` and gain
  `reranked: true`. Degrades gracefully with a notice if the model
  can't load; the web UI caps reranking at limit ≤ 1000. Bonus:
  reranked scores are comparable across collections embedded with
  different models.
- **Hybrid search everywhere** — previously Atlas-only. Shadow-mode
  collections on any topology (7.0+ standalone, replica set, Atlas)
  now take `--hybrid`. Non-Atlas — and Atlas with cap-blocked Search
  indexes, e.g. the M0 3-index budget — use client-side
  reciprocal-rank fusion: a classic Mongo `$text` index on the
  shadow's `chunk_text` (created at apply time, or lazily on the first
  hybrid search of an existing collection) plus the vector leg (HNSW
  when available), fused with the same 1/(60+rank), 0.6/0.4 weighting
  as `$rankFusion`. The Atlas native path now verifies both Search
  indexes actually exist before using `$rankFusion` and falls back to
  client-side RRF otherwise — removing both the old "Atlas + shadow
  only" asterisk and the M0-cap dead end.
- **Web UI: score bars normalized per result set.** RRF (~0.016) and
  rerank scores no longer render as invisible 1–2% slivers — the best
  result fills the bar, the worst gets 5%.

## 0.8.2 — 2026-06-09

Atlas live-verification release. Every Atlas-only path was exercised
against a real free-tier M0 cluster (MongoDB 8.0.24); the bugs that
testing surfaced are fixed here.

- **Migration recorded a vector-index name it never created.**
  `create_atlas_vector_index()` ignored the `_mig_<ts>` temp name that
  migration writes to the config, creating the index under its canonical
  name instead — so post-migration `$vectorSearch` referenced a
  nonexistent index. The created index now carries the recorded name
  (verified live: recorded name == live index name, READY/queryable).
- **A failed migration stranded its temp shadow.** An exception between
  temp-shadow creation and the rename (typically the free-tier index cap)
  left `<shadow>_mig_<ts>` plus its search indexes behind — silently
  eating the M0 cluster's 3-index budget. The build phase now drops the
  temp shadow on any failure, and BM25 index creation failure degrades
  to pure-semantic (with a warning) instead of failing the migration.
- **Raw Mongo URIs are never printed.** Seed scripts echoed the full
  URI — password included — in error and success messages. `redact_uri()`
  / `scrub_uri()` moved to `db.client` and are used everywhere a URI or
  driver exception is printed.
- **Seed scripts use the CLI's TLS config.** They built a bare
  `MongoClient`, which fails `CERTIFICATE_VERIFY_FAILED` against Atlas on
  macOS Pythons without a system CA bundle. They now connect through
  `MongoConnection.open` (certifi-backed).
- `search --hybrid` prints a hint when hybrid returns zero rows on a
  hybrid-capable collection (indexes still building, or blocked by the
  free-tier cap) instead of showing a bare empty table.
- Docs: `docs/atlas-setup.md` rewritten around the free-tier 3-index
  budget (the full three-collection runbook needs 7 slots → M10+), the
  `$rankFusion` version claim corrected (Atlas ships it on 8.0.x —
  verified on 8.0.24; self-managed needs 8.1+), and hybrid's
  reciprocal-rank-fusion score scale (~0.01) documented.

## 0.8.1 — 2026-06-09

Bug-fix release. Everything here was found by putting the full feature
surface under real-browser/real-terminal screenshot tests (`.capture.yaml`),
which now ships in the repo along with README screenshots.

- **CLI commands now honor the saved connection.** `status`, `search`,
  `inspect`, `apply`, `index`, `migrate`, `retry`, `reindex`, `teardown`,
  `integrate`, and the MCP server constructed `Settings()` directly, which
  reads env vars only — contradicting the documented
  flag > env > saved-config precedence. They all go through
  `Settings.from_environment()` now, so a connection saved via the UI (or
  `--uri/--db`) just works from the CLI.
- **Stranded `in_flight` jobs are reclaimed.** A worker that died between
  claiming and completing a job left it `in_flight` forever — nothing ever
  touched it again. New `requeue_stale()` returns jobs stuck >10 min to
  `pending`; the worker runs it (plus `prune_dead()`, which existed but was
  never called) at startup and every 60 s, and `worker --once` runs both
  before draining.
- **MCP page tools table never rendered.** A duplicate `mcp` key in the SPA
  handlers object meant an empty stub silently overwrote the real page
  handler. Removed the stub.
- **`mongosemantic ui` no longer swallows its own logs.** uvicorn's default
  log config only attaches handlers to `uvicorn.*` loggers, so every
  `mongosemantic.*` line — including HNSW warmup crashes and embedded-worker
  errors — vanished. The `ui` command now attaches a stderr handler for the
  `mongosemantic` logger family, and the warmup thread logs
  `HNSW warmup finished` as an operational "fully responsive" signal.
- Tests: unit tests now isolate `XDG_CONFIG_HOME` per test so a developer's
  real saved connection can't leak in; two new `requeue_stale` tests.

## 0.8.0 — 2026-05-27

Headline: **fast search on plain Mongo, zero-friction worker, much
more useful dashboards.** A single `mongosemantic ui` is now enough —
no second terminal for `worker`, no Atlas required for ~15 ms search.

### Performance — embedded HNSW vector index

- New `mongosemantic.search.hnsw_index.HnswIndexManager` wraps `hnswlib`
  to serve `(collection, field, model)` shadow data as an HNSW graph.
  On a 45k-chunk wines corpus we measured **2,400 ms brute-force →
  ~15 ms HNSW** — about a 150× speedup, same top results.
- Indexes persist under `~/.cache/mongosemantic/hnsw/`, lazy-load on
  cold start, rebuild automatically when the staleness ratio crosses
  5% with at least 60 s since the last build.
- New CLI: `mongosemantic reindex-hnsw --collection NAME` (or `--all`)
  forces a sweep.
- `/api/search` tries HNSW first on non-Atlas topologies, falls back
  to the existing brute-force aggregation when an index isn't loaded.
  Atlas keeps using `$vectorSearch`.
- `hnswlib>=0.8` added as a required dep.

### Worker

- **Embedded worker.** `mongosemantic ui` now spawns the worker in a
  background thread by default. End users don't have to know about
  the separate `worker` command. Pass `--no-worker` to opt out.
- Supervisor watches the saved connection — switching connection in
  the UI tears down the old worker and starts a fresh one.
- **Per-collection model fix.** The worker used to load a single
  global provider from `MONGOSEMANTIC_MODEL` and use it for every
  job, silently producing wrong-dim vectors for collections
  configured with a different model. Now a `ProviderRegistry` lazy-
  loads each model the first time its jobs arrive; failed loads fail
  only that model's jobs with a clear `last_error`.
- Provider cache is shared between web routes and the worker — the
  SentenceTransformer loads once per process instead of once per
  search request. Warm-path latency dropped ~2 s.

### CLI

- New `--uri` / `--db` global flags on the `mongosemantic` root.
  Precedence: flag > env var > saved config file. Partial input
  errors loudly.

### Web UI

- **Inspect** — field-analysis table promoted above the fold, the
  "Configure semantic search" CTA goes with it. Sample documents
  moved to a scrollable list at the bottom; clicking any row slides
  in a detail panel with the full pretty-printed JSON.
- **Collection tabs** — when you're scoped to a collection, a
  shared tab strip mounts: Inspect / Configure / Index / Search.
  Search arriving via tab pre-selects the collection.
- **Sidebar progress badge** — completion %, jobs/sec, worker
  liveness dot (live/stale/down). Flashes "✓ All embedded" briefly
  on busy → idle.
- **Search**:
  - Results: free-form number input (was a 1–100 slider).
  - Min-score threshold slider with empty-state hint.
  - Stats line: "N results in X ms · scores Y–Z".
  - Click any row → slide-in detail panel with the full source doc.
  - Visual score bar per row.
  - **Export current results as CSV / JSONL / JSON** with a
    streaming response and `Content-Disposition` filename.
- **Indexing** — replaced the bare progress bar with a real
  dashboard: tiles for completed / in flight / pending / failed;
  worker liveness tile; per-field breakdown table; recent activity
  feed (last N completes/failures); failed-jobs panel with error
  text. Polls `/api/indexing/status` every 1.5 s. The page no
  longer re-enqueues on every visit — auto-enqueue only when the
  collection has never been indexed. Manual "Re-index now" button
  surfaces in steady state.
- **Query (aggregation)** — quick-example dropdown (Sample 5 /
  Count / Group by / Top N / Distinct), pre-filled default
  pipeline, Run button states, stats line with `took_ms`,
  table-vs-JSON view toggle that auto-detects flat rows, and
  CSV / JSON export of the current rows.
- **Visualize** — was unlabeled dots. Now runs K-means on the full
  embeddings (configurable 4–20 clusters), TF-IDF keyword
  extraction with domain-stopword filtering, colored dots by
  cluster, right-rail legend with size % + click-to-highlight,
  click-any-dot detail panel, and a stats line including the
  variance captured by the top two PCA components.

### Scripts

- `scripts/seed_wines.py` — 130k Wine Reviews dataset from the
  TidyTuesday GitHub mirror, no Kaggle login. Mirrors `seed_mflix.py`
  with `--wipe`, `--from-file`, `--limit`.

### Docs

- README topology matrix rewritten: realistic scale targets per
  topology, embedded-HNSW path documented, `reindex-hnsw` flagged.

## 0.7.1 — 2026-05-19

- **Live indexing visibility.** Dashboard gets a new "Indexing activity"
  table that breaks job counts down per collection — pending, in_flight,
  completed, failed, and a per-collection % complete progress bar.
- Dashboard auto-refreshes every 3 seconds while it's the active page;
  the interval clears the moment you navigate away.
- **Global queue indicator in the sidebar footer** — visible from any
  page. Shows `N running · M pending · K failed` and tints green when
  the worker is active, amber when there's a backlog with no worker,
  red when there are failures. Clicking it jumps to the Dashboard.
- Backend: new `count_by_collection()` helper and the dashboard endpoint
  now returns `jobs_by_collection` alongside the existing global counts.
- Search bar: input + Search button merged into a single rounded
  container with a leading magnifier icon and a unified focus ring.

## 0.7.0 — 2026-05-19

UI completeness — every CLI feature now has a UI surface.

- **New `teardown` command** (CLI + `POST /api/collections/{name}/teardown`)
  removes semantic-search config + drops the shadow collection (or clears
  inline `_msem`). `--keep-data` flag preserves embeddings for a later
  rebuild without re-embedding.
- **Per-row actions on the Collections page**: Inspect · Reconfigure ·
  Reindex · Migrate · Remove. Reindex and Remove fire confirm dialogs;
  Remove is styled in red. Was previously only Inspect + Migrate.
- **Apply page detects existing config** and prefills the form when
  used as Reconfigure (title morphs to "Reconfigure {name}", button
  becomes "Save changes", a hint reminds the user to Reindex after a
  field-set change).
- **Inspect page** now shows 3 randomly-sampled documents (embedding
  sub-doc stripped) below the suitability table so you can see the
  shape of the data before configuring.
- **New endpoints**: `GET /api/collections/{name}/config` (used by
  prefill) and `GET /api/collections/{name}/sample` (used by Inspect).

## 0.6.3 — 2026-05-19

- UI layout: navigation moved from the top bar into a 240-px left
  sidebar. Brand mark + nav items + version footer all live there;
  main content gets the full content width on the right.
- Each nav item now has a small icon for faster scanning.
- Active item gets an inset green bar instead of an underline.
- On screens under 920 px the sidebar collapses behind a hamburger
  toggle in the top-left corner; tapping outside closes it.

## 0.6.2 — 2026-05-19

- Every page now shows a compact "How to use" callout at the top with a
  numbered 3-4 step recipe and a "Try:" line. Designed to make the UI
  self-teaching — no need to alt-tab to docs.
- Added `scripts/seed_mflix.py` for loading MongoDB's official
  `sample_mflix` dataset (23,539 movies with plots/genres/cast). Pairs
  with `mongosemantic apply -c movies -f title -f plot` for a realistic
  semantic-search demo.

## 0.6.1 — 2026-05-19

UI polish from user feedback after the v0.6.0 demo.

- **MCP page is now real** — was still showing "MCP integration arrives in
  v0.3.0" from the original v0.2.0 content dict. Now shows the
  `integrate claude` and `serve --transport sse` commands with copy
  buttons, plus the full 11-tool table.
- **Query page** — collection field is now a dropdown populated from
  `/api/collections`, not a free-text input.
- **Search page** — explicit Search button + Enter-to-submit; removed
  the search-as-you-type debounce. Filter changes still re-run, but
  only if there's already a query.
- **New Guide page** — walks through every page in the nav with
  concrete queries to try and a `try this:` callout per section.
- **Bigger demo dataset** — `scripts/seed_demo.py` now produces ~185
  articles across 8 categories (added science, music, gardening) so
  the visualize page actually shows distinct clusters.
- **Visualize copy fix** — removed "Visualization arrives in v0.4.0"
  placeholder from the content dict; subtitle updated to reflect what
  the page actually does (PCA scatter).

## 0.6.0 — 2026-05-19

Polish pass — heartbeat, failed-job visibility, in-UI migrations, embedding visualization.

- `mongosemantic worker --once` drains the queue and exits. Useful for cron,
  ad-hoc catch-up, and scripted demos.
- Workers now write heartbeats to `mongosemantic_workers` every 10 seconds.
  `status` and the dashboard show running / stale / dead state per worker
  with last-heartbeat and jobs-processed counts.
- `status` and the dashboard surface the 10 most-recent failed jobs with
  their `last_error`, so a failed embed is actionable, not just a count.
- Web `POST /api/collections/{name}/migrate` runs in a background thread
  by default; new `GET .../migrate/progress` powers the in-UI progress
  bar. CLI behavior unchanged.
- Collections UI gets a per-row "Migrate model" action with a modal
  (target-model dropdown, drop-archive toggle, polled progress).
- New Visualize page: 2D PCA projection of sampled embeddings rendered
  on `<canvas>`, hover for source snippet. Reads shadow rows or inline
  `_msem` depending on the collection's mode.
- New web endpoint `GET /api/collections/{name}/visualize?field=…&sample=…`
  returns normalized (x, y) points + snippets.

## 0.5.0 — 2026-05-18

- New `mongosemantic migrate -c X -m Y` command — switches a shadow-mode
  collection's embedding model with near-zero downtime by building new
  embeddings into a temp shadow and atomically renaming it into place.
  `--drop-archive` removes the old shadow after a successful swap; default
  keeps it for rollback.
- New `migrate_model` MCP tool (now 11 total) and `POST
  /api/collections/{name}/migrate` web endpoint.
- `CollectionConfig` gains optional `vector_index_names`,
  `search_index_names`, and `migrated_at` fields. Search code prefers
  stored index names over computed ones, so migrations can swap in
  uniquely-named Atlas indexes without a search-path change. Older
  configs without these fields keep working unchanged.
- Migration is resume-friendly: an interrupted run can be re-invoked
  and will skip chunks already written to the temp shadow.
- Inline-mode collections are rejected with a clear error (would require
  duplicating user data); convert to shadow mode first.

## 0.4.0 — 2026-05-18

- Hybrid search: combines `$vectorSearch` (semantic) and `$search` (BM25) via
  Atlas `$rankFusion`. Wired in three places: `mongosemantic search ... --hybrid`,
  a "Hybrid" toggle on the web Search page, and a new `hybrid_search` MCP tool
  (now 10 tools total).
- `apply` on Atlas + shadow mode now auto-creates both the vector index and
  the BM25 Atlas Search index (`mongosemantic_search_{coll}_{digest}`).
- Self-hosted topologies and inline-mode collections fall back to pure semantic
  with a clear `notice` (no error). The web UI surfaces this in an amber banner.
- New helpers: `mongosemantic.search.hybrid` (`build_hybrid_pipeline`,
  `search_index_name`) and `mongosemantic.db.indexes` (`create_atlas_search_index`,
  `search_index_definition`, `atlas_search_index_exists`).

## 0.3.0 — 2026-05-18

- New `mongosemantic serve` command — boots an MCP server over stdio (default,
  for Claude Desktop) or SSE (`--transport sse` on `127.0.0.1:8090`).
- New `mongosemantic integrate claude` command — writes the `mongosemantic`
  entry into Claude Desktop's `claude_desktop_config.json` with your current
  `MONGOSEMANTIC_URI`/`DB`/`MODEL` passed through as env. `--dry-run` prints
  the JSON without touching the file.
- Nine MCP tools: `semantic_search`, `search_all_collections`,
  `list_collections`, `list_configured`, `inspect_collection`,
  `get_sample_documents`, `get_status`, `safe_aggregation`,
  `get_schema_context`.
- Embedding sub-document (`_msem`) is stripped from sample/aggregation results
  so AI agents don't see raw vectors.
- `safe_aggregation` reuses the same allowlist as the web UI's Query page —
  `$out`, `$merge`, `$function`, `$accumulator`, `$where`, `$jsonSchema` blocked,
  10s execution cap, 100-row cap.

## 0.2.0 — 2026-05-18

- New `mongosemantic ui` command — boots a FastAPI dashboard on `127.0.0.1:8080`.
- Web pages: connection, collections browser, inspect, apply, indexing progress,
  search, aggregation runner, dashboard.
- Visualize and MCP-integration pages stubbed as placeholders for v0.4.0 and v0.3.0.
- Safe-aggregation API: stage allowlist, 10s `maxTimeMS`, 100-doc limit.
- All UI strings centralized in `mongosemantic/web/content.py` for design-layer separation.
- Security: CSRF (double-submit cookie), rate limit 120 req/min/IP, security headers,
  identifier validation.
- **Bug fixes shipped on top of v0.1.0** (carried into 0.2.0):
  - `apply --mode inline` now actually writes embeddings to source docs at
    `_msem.{field}` and a change-stream filter prevents self-write loops.
  - `apply --chunked` now splits text via the chunker, enqueues one job per chunk,
    and prunes stale shadow rows when text shrinks.
  - `search` now searches every configured field, merges, and top-k's the result.
  - `apply --mode inline --chunked` is rejected loudly (exit 2) instead of
    silently downgrading.

## 0.1.0 — 2026-04-22

Initial MVP release.

- Connect to MongoDB Atlas, self-hosted replica sets, and standalone MongoDB 7.0+.
- `inspect`: sample a collection and score each field for semantic-search suitability.
- `apply`: configure shadow-mode semantic search on one or more fields.
- `index`: bulk-enqueue embed jobs for existing documents.
- `worker`: background daemon (change streams on replica sets, polling on standalone) + embed pipeline.
- `search`: native Atlas `$vectorSearch` when available; brute-force aggregation otherwise.
- `status`, `retry`, `reindex`: operational commands.
- 5 embedding providers: MiniLM, MPNet, OpenAI small/large, Ollama (nomic-embed-text).
