# Architecture

A technical map of mongosemantic — what each package does, how a query
flows through the system, and the invariants that hold across modules.

For a feature-level overview, see [`README.md`](../README.md). For the
project's current state and design rationale, see
[`HANDOFF.md`](HANDOFF.md).

---

## High-level shape

mongosemantic is a thin layer between **your MongoDB** and **whichever
embedding provider you pick**. It writes embeddings on insert/update,
keeps them in sync as documents change, and runs vector similarity at
query time.

```
                              ┌─────────────────────────────────────────┐
                              │            mongosemantic CLI            │
                              │  inspect / apply / index / search …     │
                              └────────────────┬────────────────────────┘
                                               │
        ┌──────────────────────────────────────┼────────────────────────────────────┐
        │                                      │                                    │
┌───────▼────────┐    ┌──────────────┐  ┌──────▼──────┐    ┌────────────┐  ┌────────▼────────┐
│  Web (FastAPI) │    │  MCP server  │  │  Embedding  │    │   Sync     │  │   Migration     │
│  · pages       │    │  · 11 tools  │  │  providers  │    │  · streams │  │  · atomic swap  │
│  · /api/*      │    │  · stdio+SSE │  │  · 5 models │    │  · polling │  │  · resume-safe  │
└────────┬───────┘    └──────┬───────┘  └──────┬──────┘    └─────┬──────┘  └────────┬────────┘
         │                   │                 │                 │                  │
         └───────────────────┴─────────────────┼─────────────────┴──────────────────┘
                                               │
                              ┌────────────────▼─────────────────┐
                              │            MongoDB               │
                              │  source collections + state +    │
                              │  shadow collections (or _msem)   │
                              └──────────────────────────────────┘
```

Every entry point (CLI, FastAPI route, MCP tool) is a thin shell that calls
into the same shared modules. There is exactly one source of truth for each
operation: `apply` lives in `commands/apply.py`, `search` in
`commands/search.py`, and so on.

---

## Package map

```
mongosemantic/
├── __init__.py            __version__
├── cli.py                 Typer entry — registers every command
├── __main__.py            python -m mongosemantic
├── config.py              Settings, MODEL_DIMS lookup table
├── exceptions.py          mongosemantic-specific errors
├── chunking/              Splits long text into overlapping chunks
├── commands/              One module per CLI command
│   ├── apply.py
│   ├── index.py
│   ├── inspect.py
│   ├── integrate.py       writes Claude Desktop config
│   ├── migrate.py
│   ├── reindex.py
│   ├── retry.py
│   ├── search.py          also re-used by web + MCP
│   ├── serve.py           MCP server bootstrap
│   ├── status.py
│   ├── teardown.py
│   ├── ui.py              uvicorn bootstrap
│   └── worker_cmd.py      worker + change streams + polling driver
├── db/                    pymongo + Atlas-Search helpers
│   ├── client.py          MongoConnection, Topology enum
│   ├── indexes.py         vector + search index name + create helpers
│   ├── queries.py         shared aggregation stages, inline path helpers
│   └── schema.py          inspect_collection, score_field
├── embeddings/
│   ├── provider.py        EmbeddingProvider protocol + factory
│   ├── local.py           sentence-transformers (MiniLM, MPNet)
│   ├── openai.py
│   └── ollama.py
├── search/
│   ├── atlas.py           build_atlas_pipeline ($vectorSearch)
│   ├── brute_force.py     build_brute_pipeline ($reduce dot-product)
│   ├── inline.py          inline-mode equivalents of the two above
│   ├── hybrid.py          build_hybrid_pipeline ($rankFusion)
│   └── cross_collection.py  min_max_normalize, per_collection_targets
├── state/
│   ├── config_store.py    CollectionConfig (per-collection settings)
│   ├── job_queue.py       enqueue / claim / complete / fail
│   ├── heartbeat.py       worker liveness tracking
│   └── resume_tokens.py   change-stream resume + polling watermark
├── sync/
│   ├── change_stream.py   ChangeStreamListener + process_event
│   ├── enqueue.py         enqueue_for_doc — central dedup + chunking logic
│   └── polling.py         standalone-mode fallback
├── worker/
│   └── runner.py          process_batch (embed + write); WorkerRunner loop
├── migration/
│   ├── __init__.py
│   └── migrate.py         migrate_collection — temp shadow + atomic rename
├── mcp_server/
│   ├── server.py          FastMCP wrapper exposing 11 tools
│   └── tools.py           Tool implementations as plain functions
└── web/
    ├── app.py             create_app — wires middleware + routes
    ├── content.py         Single source of truth for UI strings
    ├── identifiers.py     IdentifierError + validate_identifier
    ├── progress.py        Indexing progress (in-memory)
    ├── migration_progress.py  Migration progress (in-memory)
    ├── safe_pipeline.py   Aggregation allowlist parser
    ├── security.py        CSRF, rate limit, security headers
    ├── routes/            One module per resource
    └── static/            index.html + app.js + style.css (no build)
```

---

## Data flow

### Apply (set up semantic search)

```
CLI:apply / POST /api/collections/{name}/apply / mcp:apply
    │
    ▼
commands/apply.py
    │  validates model, mode, chunked combination
    ▼
state/config_store.save_config()
    │  upserts a doc in `mongosemantic_config`
    ▼
db/indexes.ensure_shadow_indexes()     (shadow mode)
    │
    ▼ (Atlas only)
db/indexes.create_atlas_vector_index() + create_atlas_search_index()
```

### Embed (one document → one or more vectors)

```
Source doc change          insert / update via change stream OR
                           updated_at watermark via polling
    │
    ▼
sync/change_stream.process_event  OR  sync/polling.poll_once
    │
    ▼
sync/enqueue.enqueue_for_doc      ← single source of truth
    │  · resolves text from each configured field
    │  · chunks if spec.chunked, otherwise one chunk = full text
    │  · skips if existing hash matches (dedup)
    │  · also deletes stale chunk rows when text shrinks
    ▼
state.enqueue_embed (writes to mongosemantic_jobs collection)
    │
    ▼ (worker picks up the job)
worker/runner.process_batch
    │  · claim_batch  (atomic find_one_and_update)
    │  · provider.embed_batch(texts)
    │  · _write_embedding_shadow OR _write_embedding_inline
    │  · complete(job) OR fail(job, reason)
```

The same `enqueue_for_doc` function is called by **every** code path that
needs to embed a doc — change streams, polling, `index`, `reindex`, and
the migration loop. This is intentional. It centralizes the chunking,
dedup, and stale-chunk-cleanup logic in one place.

### Search

```
CLI:search "query" / GET /api/search / mcp:semantic_search
    │
    ▼
commands/search.search_cmd or _run_one
    │  · provider lookup: cfg.embedding_model (NOT global model env var)
    │  · provider.embed(query) → query_vector
    │
    ▼ For each configured field on the collection:
search/_run_one_field
    │ topology + mode + Atlas-index-exists →
    │  ┌────────────────────────────────────────────────┐
    │  │ Atlas + shadow + index   build_atlas_pipeline   │
    │  │ Atlas + inline + index   build_inline_atlas …   │
    │  │ self-hosted + shadow     build_brute_pipeline   │
    │  │ self-hosted + inline     build_inline_brute …   │
    │  └────────────────────────────────────────────────┘
    │
    ▼
db.<shadow_or_source>.aggregate(pipeline)
    │
    ▼
merge across fields, sort by score, top-k → response
```

`hybrid_search` follows the same shape but builds `build_hybrid_pipeline`
when both Atlas and shadow mode are available; otherwise it falls back to
pure semantic with an explicit `notice` in the response.

### Migration

```
mongosemantic migrate -c X -m new-model
    │
    ▼
migration/migrate.migrate_collection
    1. Validate target model + dim
    2. Reject inline mode (would mutate user docs mid-flight)
    3. Create temp shadow: {shadow}_mig_{ts}
    4. Atlas: create vector + search indexes on temp with new dim,
       unique names (so they survive the rename)
    5. For each source doc: _embed_one_doc → temp shadow
       (resume-friendly — re-running skips already-written chunks)
    6. Update CollectionConfig FIRST (model, dim, vector/search index names)
    7. Atomic rename:
         live shadow → {shadow}_archive_{ts}
         temp shadow → live shadow name
    8. Return result (archive collection preserved for rollback)
```

The cfg-update-before-rename ordering matters: search reads model + dim
from cfg, so we want cfg pointing at the new model the moment the new
shadow becomes live. There's a ms-wide window where search may briefly
return zero results (no rows match the new field/dim filter), but it
never returns dimension-mismatched garbage.

---

## Storage layout

### `mongosemantic_config` (one row per configured collection)
```
{
  _id: "articles",          # collection name = key
  mode: "shadow" | "inline",
  shadow_collection: "articles_embeddings" | None,
  fields: [
    {path: "body", chunked: true, chunk_size: 512, chunk_overlap: 64}
  ],
  embedding_model: "local-fast",
  embedding_dim: 384,
  vector_index_names: {"body": "mongosemantic_articles_..."}, # v0.5+
  search_index_names: {"body": "mongosemantic_search_articles_..."},
  migrated_at: ISODate | None,
  disabled: false,
  created_at, updated_at
}
```

### `mongosemantic_jobs` (the embed queue)
```
{
  _id, collection, source_id, field_path, chunk_index,
  kind: "embed" | "delete",
  model, status: "pending" | "in_flight" | "completed" | "failed",
  attempts, last_error,
  enqueued_at, started_at, completed_at, owner,
  input_text, input_hash      # sha1(model, text) for dedup
}
```

Unique index on `(collection, source_id, field_path, chunk_index, kind, model, status)`
prevents duplicate pending/in_flight rows for the same (doc, field, chunk).

### `{collection}_embeddings` (shadow mode storage)
```
{
  source_id, field_path, chunk_index, embedding_model,
  source_collection, chunk_text, embedding (float[]),
  embedding_dim, embedding_hash, created_at, updated_at
}
```

Unique index on `(source_id, field_path, chunk_index, embedding_model)`.

### Inline mode (`_msem` sub-doc on the source doc itself)
```
_msem: {
  body: {
    embedding: [float, ...],
    model: "local-fast",
    dim: 384,
    hash: "sha1:...",
    text: "...the text we embedded...",
    updated_at: ISODate
  }
}
```

### `mongosemantic_state`
Single-collection holder for cross-cutting state: the most recent
change-stream resume token (`_id: "change_stream"`), and per-collection
polling watermarks (`_id: "polling:articles"`).

### `mongosemantic_workers` (since v0.6)
```
{_id: worker_id, started_at, last_heartbeat, jobs_processed}
```
Workers heartbeat every 10 s. Dashboard classifies as running (< 30 s),
stale (< 5 min), or dead.

---

## Key design decisions

| Decision | Why |
|---|---|
| **`enqueue_for_doc` is the single source of truth** for "what jobs cover this doc" | Earlier code duplicated the logic across change_stream / polling / index / reindex. Chunking and dedup bugs hid behind that duplication for an entire release. Centralizing it made the v0.1.x bugfix trivial and prevented the same shape of bug in v0.4+. |
| **Search reads model from `cfg.embedding_model`, not the global env var** | Mismatched-dim embeddings produce silent garbage (scores collapse to ~0.08). After a migration the global env may not reflect the collection's actual model. The CLI fix is also covered by a regression test. |
| **Atomic rename for migration** | `db.command("renameCollection")` is catalog-level atomic on every Mongo topology including standalone. Search reads from the canonical shadow name; the rename swaps which physical collection backs that name in one operation. |
| **Vector index names stored in cfg** | Atlas Search indexes follow the collection through a rename. After a migration, the temp shadow's index becomes the live shadow's index — but with a `_mig_{ts}` suffixed name. Storing the actual name in cfg means search uses it directly instead of computing a stale canonical name. |
| **CSRF via double-submit cookie, not session-bound** | The web layer has no sessions and we'd rather not introduce one. Double-submit (cookie + matching header on POST) is a single decision the middleware enforces. |
| **No build step on the frontend** | index.html + app.js + style.css are served verbatim. Three files, no node_modules, no transpiler, no dependency on the JS ecosystem. The trade-off is no React/TypeScript ergonomics — accepted because the UI is small. |
| **MCP tools wrap pure functions in `tools.py`, registered via `server.py`** | Tools are tested directly against mongomock without spinning up an MCP transport. The FastMCP layer is a thin wrapper that opens a connection and calls through. |
| **Hybrid search is Atlas-only, falls back with a `notice`** | `$rankFusion` and `$search` don't exist on self-hosted Mongo. Falling back to semantic and tagging the response is more useful than erroring out, especially for the demo flow. |

---

## Tests

**Unit (191)** — run with `python3 -m pytest tests/unit`. All tests use
mongomock; no Docker required. Cover every module's behavior in
isolation, plus integration of routes / commands / MCP tools.

**Integration (10)** — `MONGOSEMANTIC_RUN_INTEGRATION=1 python3 -m pytest
tests/integration`. Requires the docker-compose replica set + standalone
instances to be running (`docker compose up -d`). Exercises:

- Change-stream sync on the real replica set
- Polling sync on the standalone
- E2E web flow (uvicorn → index → worker → search)
- Migration round-trip (`local-fast` → `local-better` on real Mongo)
- Standalone topology end-to-end

`MAX_SAMPLE` was raised to 50 k in v0.7+; if you wire larger visualize
datasets, the test (`test_route_visualize.py`) still validates the
contract.
