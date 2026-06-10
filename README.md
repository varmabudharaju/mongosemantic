# mongosemantic

**Zero-config semantic search for any MongoDB database.**

`mongosemantic` connects to your existing MongoDB, picks a text field, and makes it searchable by meaning. No separate vector database. No ETL. Works on Atlas, self-hosted replica sets, and standalone MongoDB 7.0+.

<img src="docs/screenshots/v1/05-search.png" width="100%" alt="Semantic search in the web UI — a natural-language query over 23k movie plots returns Cold-War spy films with scores, score bars, and CSV/JSONL/JSON export"/>

*A meaning-only query — none of these results contain the words "spies" or "blackmail" as keywords. 17 ms over 45k embedded chunks via the embedded HNSW index, on a plain self-hosted replica set.*

## Quick start

```bash
pip install mongosemantic

export MONGOSEMANTIC_URI="mongodb+srv://user:pass@cluster.mongodb.net/my_db"
export MONGOSEMANTIC_DB="my_db"

mongosemantic inspect --collection articles
mongosemantic apply   --collection articles --field body
mongosemantic index   --collection articles        # bulk-embed existing docs
mongosemantic worker &                             # keep embeddings in sync
mongosemantic search  "budget travel"              # search by meaning
mongosemantic ui                                   # browser dashboard on :8080
mongosemantic integrate claude                     # wire into Claude Desktop
mongosemantic serve                                # MCP server for AI agents
```

<img src="docs/screenshots/v1/12-cli-search.png" width="100%" alt="CLI semantic search over 23k movie plots — finds Cold-War spy thrillers from a meaning-only query"/>

## Web dashboard

```bash
mongosemantic ui                          # http://127.0.0.1:8080
```

Localhost-bound by default with CSRF protection, rate limiting, and security
headers. Bind to a non-loopback address only behind your own auth proxy.

The dashboard provides:

- Connection setup with topology detection
- Collections browser with per-field suitability scoring
- One-click semantic-search configuration (shadow or inline, Atlas index auto-creation)
- Bulk indexing with progress
- Live-search across one or all configured collections
- Read-only aggregation runner (10s timeout, 100-doc limit)
- Job queue dashboard with retry / reindex
- Embedding explorer — 2D PCA scatter with K-means clusters and keyword labels

<table>
  <tr>
    <td width="50%"><img src="docs/screenshots/v1/02-collections.png" width="100%" alt="Collections browser — configured collections show model and storage mode; the rest are one click from setup"/></td>
    <td width="50%"><img src="docs/screenshots/v1/04-indexing.png" width="100%" alt="Indexing dashboard — completed/in-flight/pending/failed tiles, live worker dot, per-field progress, activity feed"/></td>
  </tr>
  <tr>
    <td width="50%"><img src="docs/screenshots/v1/08-visualize.png" width="100%" alt="Explore embeddings — K-means clusters over a 2D PCA projection, TF-IDF keyword labels per cluster"/></td>
    <td width="50%"><img src="docs/screenshots/v1/09-dashboard.png" width="100%" alt="Overview dashboard — topology, embedding totals, job-queue health, per-collection indexing activity"/></td>
  </tr>
  <tr>
    <td width="50%"><img src="docs/screenshots/v1/07-query.png" width="100%" alt="Read-only aggregation runner — quick examples, table view, stats line, CSV/JSON export"/></td>
    <td width="50%"><img src="docs/screenshots/v1/06-search-detail.png" width="100%" alt="Click any search result to slide in the full source document"/></td>
  </tr>
  <tr>
    <td width="50%"><img src="docs/screenshots/v1/15-apply.png" width="100%" alt="Configure semantic search — discovered fields as checkboxes with suitability badges, shadow/inline mode, chunking, model picker"/></td>
    <td width="50%"><img src="docs/screenshots/v1/16-migrate-modal.png" width="100%" alt="Migrate model — per-collection embedding-model swap with near-zero downtime; the old shadow is archived for rollback"/></td>
  </tr>
</table>

## Online model migration

Switch a shadow-mode collection to a different embedding model with
near-zero downtime:

```bash
mongosemantic migrate --collection articles --model local-better
```

Builds new embeddings into a temp shadow collection, then atomically
swaps it into place via `renameCollection`. Search keeps serving the
old model up to the swap instant, then the new model immediately after.
The previous shadow is kept as `articles_embeddings_archive_{timestamp}`
for rollback — drop it with `--drop-archive` (or manually) once verified.

Available as the `migrate_model` MCP tool too. Shadow-mode only;
inline-mode collections are rejected with a clear error.

## Filtering and reranking

Narrow any semantic search with a plain MongoDB query over the source
documents — works on every search path, no reindex needed:

```bash
mongosemantic search "noir thrillers" -c movies --filter '{"year": {"$gte": 1960}}'
```

Re-score the top candidates with a local cross-encoder
(`cross-encoder/ms-marco-MiniLM-L-6-v2`, ~80 MB, CPU, loaded once per
process) for noticeably better ordering — over-fetches limit×5 candidates,
re-scores, returns the top limit, keeping the original score as
`vector_score`:

```bash
mongosemantic search "noir thrillers" -c movies --rerank
```

Both ship as a Filter input and Rerank toggle on the web Search page and as
`filter` / `rerank` params on the MCP search tools.
`$where`/`$function`/`$accumulator`/`$text`/`$expr` are rejected in filters;
invalid filters error loudly (exit 2 on the CLI, HTTP 400 in the web UI). A
reranking bonus: scores become comparable across collections, even ones
embedded with different models.

## Hybrid search

Combine semantic similarity with keyword matching. Useful when a query
mixes meaning and specific terms — e.g. *"MongoDB 7.0 replica set issues"*
benefits from semantic (catches "replica set" → "replication") plus keyword
(anchors on "7.0").

```bash
mongosemantic search "MongoDB 7.0 replica set issues" --hybrid
```

Works on every topology for shadow-mode collections, via two paths:

- **Atlas with Search indexes** — native `$rankFusion` over `$vectorSearch`
  plus BM25 `$search`. `apply` auto-creates both indexes, and the search
  path verifies they actually exist before using `$rankFusion`.
- **Everywhere else** — self-hosted 7.0+ (standalone or replica set), and
  Atlas clusters whose Search indexes are cap-blocked (e.g. the free-tier
  3-index budget): client-side reciprocal-rank fusion. A classic MongoDB
  `$text` index on the shadow's chunk text (created during `apply`, or
  lazily on the first hybrid search for existing collections) supplies the
  keyword leg, the vector leg uses HNSW when available, and the two are
  fused with the same 1/(60+rank), 0.6/0.4 weighting as `$rankFusion`.

CLI flag, web UI toggle, and `hybrid_search` MCP tool are all wired.
Inline-mode collections fall back to pure semantic with a clear notice
(no error).

## MCP — let Claude Desktop / Cursor query your MongoDB

```bash
mongosemantic integrate claude          # writes Claude Desktop config (restart Claude)
mongosemantic serve --transport sse     # or run as a standalone SSE server on :8090
```

<img src="docs/screenshots/v1/10-mcp.png" width="100%" alt="MCP page — one command wires mongosemantic into Claude Desktop; eleven tools exposed to any MCP client"/>

Eleven tools are exposed:

| Tool | What it does |
|---|---|
| `semantic_search` | Find rows in one collection by meaning; optional `filter` (MongoDB query) and `rerank` params |
| `hybrid_search` | Semantic + keyword, fused via Atlas `$rankFusion` or client-side RRF on every other topology; takes `filter` / `rerank` too |
| `search_all_collections` | Cross-collection fanout, merged by score; `rerank` makes scores comparable across models |
| `list_collections` | Every collection + its configured/not-configured status |
| `list_configured` | Just the ones with semantic search wired up |
| `inspect_collection` | Field-by-field suitability scoring |
| `get_sample_documents` | Real rows, embedding sub-doc stripped |
| `get_status` | Topology + total embeddings + job-queue counts |
| `safe_aggregation` | Read-only pipeline runner (10s, 100-row, no `$out`/`$merge`/`$function`) |
| `get_schema_context` | Compact schema summary for AI-generated aggregations |
| `migrate_model` | Switch a collection's embedding model with near-zero downtime |

## Status (v0.9.0)

- [x] Connect to Atlas / replica set / standalone — saved connection shared by UI, CLI, and MCP server
- [x] Inspect a collection, score fields for suitability
- [x] Configure shadow-mode **or inline-mode** semantic search on one or more fields
- [x] Real chunking — long documents split into overlapping chunks, search ranks per chunk
- [x] Bulk-embed existing documents
- [x] Sync in real time (change streams) or on a schedule (polling)
- [x] Search via native Atlas `$vectorSearch`, embedded HNSW (non-Atlas), or brute-force aggregation
- [x] CLI: inspect / apply / index / search / worker / status / retry / reindex / reindex-hnsw / migrate / teardown / ui / serve / integrate
- [x] Web UI — connection, collections, inspect, configure, indexing, search, query, dashboard, visualize, MCP, guide
- [x] **Embedded worker** — `mongosemantic ui` alone keeps embeddings in sync; no second terminal
- [x] **Self-healing job queue** — stale in-flight jobs reclaimed, dead worker heartbeats pruned automatically
- [x] **MCP server** for Claude Desktop / Cursor / any MCP client (stdio + SSE)
- [x] **Hybrid search on every topology** — Atlas `$rankFusion` (live-verified on 8.0.24) or client-side RRF with a `$text` index elsewhere (`--hybrid` / UI toggle / `hybrid_search` MCP tool)
- [x] **Metadata filtering** — plain MongoDB queries over source fields on every search path (`--filter` / UI input / MCP `filter` param), no reindex needed
- [x] **Local cross-encoder reranking** — two-stage retrieval with `ms-marco-MiniLM-L-6-v2` (`--rerank` / UI toggle / MCP `rerank` param)
- [x] **Online model migration** — `mongosemantic migrate` + `migrate_model` MCP tool, atomic `renameCollection` swap
- [x] **Visualize page** — K-means clusters over a 2D PCA projection, TF-IDF keyword labels, click-to-inspect
- [x] **Search & query export** — CSV / JSONL / JSON from the search page, CSV / JSON from the aggregation runner

## Known limitations

- **Free-tier Atlas (M0/M2/M5) caps search indexes at 3 per cluster.**
  Each shadow-mode field costs 2 (vectorSearch + BM25), each inline field 1.
  `apply` and `migrate` detect the cap and degrade gracefully — cap-blocked
  collections keep hybrid search via client-side RRF over a classic `$text`
  index, so keyword matching survives the cap. The Atlas paths —
  `$vectorSearch`, hybrid `$rankFusion`, migration index carry-over — are
  live-verified against a free-tier M0 on MongoDB 8.0.24; see
  [`docs/atlas-setup.md`](docs/atlas-setup.md) for the runbook. Inline-mode
  with a real Atlas vector index is the one path verified only through its
  brute-force fallback (the M0 cap leaves it no index slot).
- **Atlas-path filters are over-fetch + post-match.** On `$vectorSearch`
  paths a `--filter` is applied after the source `$lookup`, over a ×5
  over-fetched candidate set — a highly selective filter may return fewer
  than `limit` rows. Local paths (brute-force, embedded HNSW) pre-filter
  the matching `_id`s and are exact.

## Embedding models

| Model | Dimensions | Cost | Notes |
|---|---|---|---|
| `local-fast` (MiniLM) | 384 | Free | Default. Runs on your machine. |
| `local-better` (MPNet) | 768 | Free | Higher quality, slower. |
| `openai-small` | 1536 | ~$0.02/1M tokens | Multilingual. |
| `openai-large` | 3072 | ~$0.13/1M tokens | Highest quality. |
| `ollama-nomic` | 768 | Free | Self-hosted via Ollama. |

Select via `MONGOSEMANTIC_MODEL` or `--model` on `apply`.

## Deployment topologies

| Topology | Sync | Search (shadow mode) | Search (inline mode) | Realistic scale |
|---|---|---|---|---|
| **Atlas** | Change streams | `$vectorSearch` (HNSW, native) | `$vectorSearch` | Millions |
| **Self-hosted replica set** | Change streams | **Embedded HNSW** (in-process) | Brute-force aggregation | Hundreds of thousands |
| **Self-hosted standalone** | Polling (`updated_at` watermark) | **Embedded HNSW** (in-process) | Brute-force aggregation | Hundreds of thousands |

**Embedded HNSW**: when you run `mongosemantic ui` against a non-Atlas
cluster, an HNSW graph is built from the shadow collection in a
background thread and persisted under `~/.cache/mongosemantic/hnsw/`.
Queries hit the graph at ~O(log N) — ~15 ms warm on 45k chunks vs
~2.5 s brute-force. Indexes rebuild automatically when enough rows
go stale; force a rebuild with `mongosemantic reindex-hnsw --all`.

Inline-mode collections still take the brute-force path on non-Atlas
(HNSW for inline is a follow-up). For datasets in the hundreds of
thousands, prefer shadow mode or Atlas.

## Development

```bash
git clone https://github.com/varmabudharaju/mongosemantic
cd mongosemantic
pip install -e ".[dev,openai]"
docker compose up -d                          # replica set + standalone
MONGOSEMANTIC_RUN_INTEGRATION=1 python3 -m pytest -v
```

The README screenshots are reproducible: `.capture.yaml` at the repo root
defines every shot (real Chromium renders of the dashboard, real Terminal
captures of the CLI). Regenerate them with
[`capture`](https://github.com/varmabudharaju/capture)` run` against a
seeded database.

### Demo data

Two seed scripts ship with the repo:

```bash
# Small hand-curated corpus (~185 articles + 38 products + 10 recipes).
# Fast, offline, good for fast iteration.
python3 scripts/seed_demo.py

# MongoDB's official sample_mflix — 23,539 movies with plots, genres, cast.
# ~40 MB download, ideal for realistic semantic-search demos.
python3 scripts/seed_mflix.py
```

After seeding either dataset:

```bash
# For mflix:
mongosemantic apply  -c movies -f title -f plot
mongosemantic index  -c movies
mongosemantic worker --once     # processes all pending jobs, then exits
mongosemantic search "spies blackmail and intrigue in cold war Berlin" -c movies
```

## Project docs

If you want to dig in further:

| | |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Module map, data flow diagrams, storage layout, key design decisions. The technical reference. |
| [`docs/HANDOFF.md`](docs/HANDOFF.md) | Current state: what's working, what's not live-tested, what was intentionally left out, what's worth shipping next. |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Dev setup, test strategy, where to add a new CLI command / embedding provider / web route / MCP tool / search mode. |
| [`docs/atlas-setup.md`](docs/atlas-setup.md) | 10-minute runbook for verifying the Atlas-specific paths (`$vectorSearch`, hybrid `$rankFusion`, migration index name carry-over) against a free-tier M0 cluster. |
| [`CHANGELOG.md`](CHANGELOG.md) | Per-version summary of what landed and why. |

## License

MIT
