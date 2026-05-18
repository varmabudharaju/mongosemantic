# Changelog

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
