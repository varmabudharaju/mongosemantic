# mongosemantic

**Zero-config semantic search for any MongoDB database.**

<img src="docs/screenshots/v1/05-search.png" width="100%" alt="Semantic search in the web UI — a natural-language query over 23k movie plots returns Cold-War spy films with scores, score bars, and CSV/JSONL/JSON export"/>

*A meaning-only query — none of these results contain the words "spies" or "blackmail" as keywords. 17 ms over 45k embedded chunks via the embedded HNSW index, on a plain self-hosted replica set.*

## What is it

mongosemantic is a Python toolkit — CLI, web dashboard, and MCP server —
that adds search-by-meaning to the MongoDB you already run. Point it at a
database, pick a text field, and it embeds your documents with a local
model, keeps the embeddings in sync as your data changes, and answers
natural-language queries ("a washed-up boxer gets one last shot at
redemption") from the CLI, a browser, or directly to AI agents over MCP.

No separate vector database. No ETL. No embedding API bill. Works on
Atlas, self-hosted replica sets, and standalone MongoDB 7.0+.

## Why it exists

Adding semantic search to an existing MongoDB app today usually means one
of three things:

- **Bolt on a vector database** (Pinecone, Weaviate, Qdrant, …) — new
  infrastructure, an ETL pipeline to keep two systems consistent, and a
  second source of truth to operate forever.
- **Use Atlas Vector Search directly** — solid when you're on Atlas with
  Search-index slots to spare, but the embedding *pipeline* is still on
  you: chunking, batching, re-embedding on update, model upgrades. The
  managed alternatives are constrained — Atlas auto-embedding is a metered
  preview tied to one model family, and the Community 8.2 search preview
  requires running a separate `mongot` binary.
- **Send documents to an embedding API** — your data leaves your machines,
  and you pay per token, forever.

mongosemantic is the missing pipeline-and-product layer on top of the
database you already have: free local embeddings (your data never leaves
the box), any MongoDB 7.0+ topology with zero extra infrastructure, and
all the unglamorous parts handled — change-stream sync, a self-healing job
queue, chunking, index management, online model migration. Search by
meaning is one `apply` away.

## Why you'd pick it

- **Local-first and free** — embeddings are computed on your machine with
  sentence-transformers; documents never leave your network and there is
  no per-token meter. OpenAI/Ollama models are opt-in, not required.
- **Any MongoDB, zero new infra** — Atlas, replica set, or standalone
  7.0+. No vector database, no sidecar process, no ETL. Embeddings live in
  MongoDB next to your data (a shadow collection, or inline on the doc).
- **Fast without Atlas** — an embedded HNSW index makes self-hosted search
  ~15 ms over 45k chunks instead of a 2.5 s brute-force scan.
- **Search quality built in** — hybrid semantic+keyword search on every
  topology, metadata filters that need no reindex, and a local
  cross-encoder reranker. Capabilities that usually require Atlas Search
  tiers or external services, all local.
- **Never stale** — change streams (or polling on standalone) re-embed
  documents as they change; a self-healing job queue reclaims stalled work
  automatically.
- **Model freedom** — five models from free-local to OpenAI, and an online
  migration command that swaps a collection to a new model with near-zero
  downtime and a rollback archive.
- **Three interfaces, one state** — CLI for scripts, a web dashboard for
  humans, and an MCP server so Claude Desktop / Cursor / any AI agent can
  query your data by meaning. All share the same saved connection.

## How to use it

```bash
pip install mongosemantic

export MONGOSEMANTIC_URI="mongodb+srv://user:pass@cluster.mongodb.net/my_db"
export MONGOSEMANTIC_DB="my_db"

mongosemantic inspect --collection articles        # 1. score fields for suitability
mongosemantic apply   --collection articles --field body   # 2. configure + create indexes
mongosemantic index   --collection articles        # 3. bulk-embed existing docs
mongosemantic worker &                             # 4. keep embeddings in sync
mongosemantic search  "budget travel"              # 5. search by meaning
```

That's the whole loop: **inspect** tells you which fields are worth
embedding, **apply** configures the collection (and creates Atlas indexes
where applicable), **index** enqueues existing documents, the **worker**
embeds them and stays running to catch changes, and **search** queries by
meaning — with `--filter`, `--rerank`, and `--hybrid` available from day
one.

Prefer a UI? The dashboard does everything the CLI does, plus
observability:

```bash
mongosemantic ui                          # http://127.0.0.1:8080
```

It runs an embedded worker, so `ui` alone is a complete deployment.
Localhost-bound by default with CSRF protection, rate limiting, and
security headers — bind to a non-loopback address only behind your own
auth proxy.

Wiring it into an AI agent is one command:

```bash
mongosemantic integrate claude          # writes Claude Desktop config (restart Claude)
mongosemantic serve --transport sse     # or run as a standalone SSE server on :8090
```

Want data to try it on? See [Demo data](#demo-data) below — a seeded
movies collection makes every example in the next section reproducible.

## See it in action

Every screenshot below is real and reproducible — `.capture.yaml` defines
each shot and [`capture`](https://github.com/varmabudharaju/capture)` run`
regenerates the full set against a seeded database.
[`docs/test-evidence-0.9.md`](docs/test-evidence-0.9.md) collects the
feature-by-feature proof.

### Semantic search

Natural-language queries over any configured collection — from the CLI,
the dashboard, or MCP. Results carry the matched chunk, a similarity
score, and the full source document one click away.

<img src="docs/screenshots/v1/12-cli-search.png" width="100%" alt="CLI semantic search over 23k movie plots — finds Cold-War spy thrillers from a meaning-only query"/>

### Metadata filtering

Narrow any semantic search with a plain MongoDB query over the source
documents — no reindex, works on every search path:

```bash
mongosemantic search "a detective hunting a serial killer" -c movies \
  --filter '{"year": {"$lt": 1960}}'
```

The same noir query, constrained to pre-1960 — every modern serial-killer
film drops out and the classics surface:

<img src="docs/screenshots/v1/17-search-filter.png" width="100%" alt="Metadata filtering — the same noir query constrained to year < 1960 with a plain MongoDB filter; only pre-1960 classics return"/>

Local paths (brute-force, embedded HNSW) pre-filter the matching `_id`s
and are exact; Atlas paths over-fetch ×5 and post-match.
`$where`/`$function`/`$accumulator`/`$text`/`$expr` are rejected; invalid
filters error loudly (exit 2 on the CLI, HTTP 400 in the web UI).

### Cross-encoder reranking

Two-stage retrieval for noticeably better ordering: over-fetch limit×5
candidates, re-score each (query, chunk) pair with a local cross-encoder
(`cross-encoder/ms-marco-MiniLM-L-6-v2`, ~80 MB, CPU, loaded once per
process), return the top hits. The original similarity is kept as
`vector_score`:

```bash
mongosemantic search "a washed-up boxer gets one last shot at redemption" \
  -c movies --rerank
```

Note the **Reranked** badge on every row and the score bars normalized per
result set. A bonus: cross-encoder scores are comparable across
collections, even ones embedded with different models.

<img src="docs/screenshots/v1/18-search-rerank.png" width="100%" alt="Cross-encoder reranking — top candidates re-scored locally; every hit carries a reranked badge and bars are normalized per result set"/>

### Hybrid search — on every topology

Combine semantic similarity with keyword matching. Useful when a query
mixes meaning and specific terms — *"MongoDB 7.0 replica set issues"*
benefits from semantic (catches "replica set" → "replication") plus
keyword (anchors on "7.0").

```bash
mongosemantic search "Godzilla attacks a city" -c movies --hybrid
```

Two paths, picked automatically:

- **Atlas with Search indexes** — native `$rankFusion` over `$vectorSearch`
  plus BM25 `$search`. `apply` auto-creates both indexes, and the search
  path verifies they actually exist before using `$rankFusion`.
- **Everywhere else** — self-hosted 7.0+ (standalone or replica set), and
  Atlas clusters whose Search indexes are cap-blocked (e.g. the free-tier
  3-index budget): client-side reciprocal-rank fusion. A classic MongoDB
  `$text` index on the shadow's chunk text supplies the keyword leg, the
  vector leg uses HNSW when available, and the two are fused with the same
  1/(60+rank), 0.6/0.4 weighting as `$rankFusion`.

This shot is hybrid running against a plain **self-hosted replica set** —
no Atlas anywhere:

<img src="docs/screenshots/v1/19-search-hybrid-local.png" width="100%" alt="Hybrid search on a self-hosted replica set — semantic + $text keyword legs fused with client-side RRF; no Atlas required"/>

Inline-mode collections fall back to pure semantic with a clear notice (no
error). All three search upgrades — filter, rerank, hybrid — compose, and
all are available in the CLI, the web UI, and the MCP tools.

### The web dashboard

Connection setup with topology detection, a collections browser with
per-field suitability scoring, one-click configuration, bulk indexing with
live progress, a read-only aggregation runner (10 s timeout, 100-doc
limit), a job-queue dashboard with retry/reindex, and an embedding
explorer (2D PCA + K-means with keyword labels).

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
</table>

### Configure and migrate without downtime

`apply` discovers fields and scores them; pick shadow or inline storage,
chunking, and a model. Later, switch a shadow-mode collection to a
different embedding model with near-zero downtime:

```bash
mongosemantic migrate --collection articles --model local-better
```

New embeddings build into a temp shadow collection, then an atomic
`renameCollection` swaps it into place — search serves the old model up to
the swap instant, the new model immediately after. The previous shadow is
kept as `articles_embeddings_archive_{timestamp}` for rollback; drop it
with `--drop-archive` once verified. Also available as the `migrate_model`
MCP tool. Shadow-mode only; inline-mode collections are rejected with a
clear error.

<table>
  <tr>
    <td width="50%"><img src="docs/screenshots/v1/15-apply.png" width="100%" alt="Configure semantic search — discovered fields as checkboxes with suitability badges, shadow/inline mode, chunking, model picker"/></td>
    <td width="50%"><img src="docs/screenshots/v1/16-migrate-modal.png" width="100%" alt="Migrate model — per-collection embedding-model swap with near-zero downtime; the old shadow is archived for rollback"/></td>
  </tr>
</table>

### MCP — let Claude Desktop / Cursor query your MongoDB

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
