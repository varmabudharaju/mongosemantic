# Changelog

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
