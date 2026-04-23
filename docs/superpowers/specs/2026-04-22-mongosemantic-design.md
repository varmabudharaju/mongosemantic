# mongosemantic — Design

**Date:** 2026-04-22
**Status:** Draft for review
**Author:** Varma Budharaju

---

## 1. Overview

`mongosemantic` is a standalone open-source Python package that turns any MongoDB deployment into a semantic-search engine with no additional infrastructure. Same DNA as [pgsemantic](https://github.com/varmabudharaju/pgsemantic), adapted for MongoDB's document model.

**Three install-and-go commands:**

```
pip install mongosemantic
mongosemantic ui                          # Web dashboard
mongosemantic search "climate policy"     # Or search directly
```

**Ships:** CLI (Typer), Web UI (FastAPI), MCP server (FastMCP), background worker, zero-downtime model migration — all in one package.

### Goals

- **Zero-config semantic search on any MongoDB**: Atlas, self-hosted replica set, self-hosted standalone.
- **No additional infrastructure**: embeddings live in the user's MongoDB; no extra database, no sidecar service.
- **Never mutates user data by default**: shadow-collection pattern mirrors pgsemantic's default.
- **Mongo-native features**: nested-field embedding, array-of-subdoc embedding, hybrid search via Atlas Search, multi-database isolation.
- **Production-safe operations**: change-stream resume tokens, zero-downtime model migration, explicit failure modes, retryable jobs.
- **Full feature parity with pgsemantic at v1.0**: CLI surface, web UI, MCP server with 10 tools, chunking, cross-collection search, visualization, multi-field embedding.

### Non-goals (v1)

- GridFS text extraction (PDF/docx parsing). Separate product territory; defer.
- Time-series collection support. Embedding is rarely meaningful on that data model.
- Multi-cluster federation. One cluster, one (or more) databases.
- Sidecar ANN store (e.g. sqlite-vec). We use Mongo-native storage exclusively.
- Learned rerankers (cross-encoders). Lexical + dense fusion only for v1.
- Automatic schema inference for combined embeddings. User must specify which fields to combine.
- Built-in auth on the web UI. Binds to localhost; remote access is user's concern.

### Audience

- Mongo developers (MERN / MEAN / Atlas users) who need AI search on their existing data.
- Self-hosted Mongo shops that can't or won't use Atlas.
- AI-agent builders who want Claude/Cursor to query Mongo collections semantically via MCP.

---

## 2. System architecture

### 2.1 Components

```
┌─────────────────────────────────────────────────────────────────┐
│                     User Application                            │
└────────────────────────────────┬────────────────────────────────┘
                                 │
   ┌─────────────────────────────┴────────────────────────────────┐
   │                         mongosemantic                        │
   │                                                              │
   │   CLI (Typer) ───┐                                           │
   │   Web UI (FastAPI) ─┤───> Embedding Providers ─┐             │
   │   MCP (FastMCP) ─┘        (local/OpenAI/Ollama)│             │
   │                                                 │             │
   │   Sync Engine ─────> Job Queue ────────────────┘             │
   │    ├─ Change-Stream Listener (replica sets / Atlas)          │
   │    └─ Polling Listener       (standalone, timestamp)         │
   │                                                              │
   └────────────────┬─────────────────────────────────────────────┘
                    │
   ┌────────────────┴─────────────────────────────────────────────┐
   │                           MongoDB                            │
   │                                                              │
   │   Source collection: articles                                │
   │   Shadow collection: articles_embeddings  (shadow mode)      │
   │   OR inline _embedding field on articles  (inline mode)      │
   │                                                              │
   │   Atlas:       $vectorSearch index (HNSW, Lucene-backed)     │
   │   Self-hosted: brute-force $dotProduct aggregation           │
   │                                                              │
   │   State collections: mongosemantic_config                    │
   │                      mongosemantic_jobs                      │
   │                      mongosemantic_state                     │
   └──────────────────────────────────────────────────────────────┘
```

### 2.2 Module boundaries

| Module | Sole responsibility | External deps |
|---|---|---|
| `db/` | Connect to Mongo, detect topology, inspect schema, execute pipelines. **Only module that imports `pymongo`.** | pymongo |
| `embeddings/` | Given text, return a vector. Provider-agnostic. Pure. | provider SDKs |
| `chunking/` | Given a document + field path, return chunks. Works on top-level, nested, and array-of-subdoc paths. Pure. | — |
| `sync/` | Detect new/changed/deleted docs. Emit jobs. Two implementations (change-stream, polling), chosen at runtime. | db, state |
| `worker/` | Consume job queue, embed, write, retry. Pipelined. | embeddings, db, state |
| `state/` | Read/write config, job queue, resume tokens, migration state. | db |
| `search/` | Translate query + filter into aggregation pipeline. Strategies: Atlas native, Atlas hybrid, brute-force. | db, embeddings |
| `migrate/` | Zero-downtime model-switch state machine. | db, embeddings, state |
| `cli/` | Typer commands. No business logic. | everything |
| `web/` | FastAPI app. No business logic. | everything |
| `mcp_server/` | FastMCP wrapping. No business logic. | everything |

**Guiding rule:** `db/` is the only module that touches pymongo; `embeddings/` the only one that touches provider SDKs. Everything else operates on our own types.

### 2.3 Supported topologies

| Topology | Sync mechanism | ANN engine |
|---|---|---|
| Atlas (any cluster) | Change streams | `$vectorSearch` |
| Self-hosted replica set / sharded | Change streams | Brute-force aggregation (v1) |
| Self-hosted standalone | Polling with watermark | Brute-force aggregation (v1) |

### 2.4 Data flow: update → searchable

1. User writes to `articles` (insert / update / delete).
2. Change-stream listener (or polling loop on standalone) sees the event, performs hash-based skip check, enqueues jobs in `mongosemantic_jobs`.
3. Worker pulls batch of 32 pending jobs, calls embedding provider with all inputs at once, writes vectors to shadow collection (or inline field). Pipelines next batch during write.
4. User issues a search. Search layer picks strategy (Atlas / Atlas hybrid / brute-force) based on topology + flags, builds aggregation pipeline, executes, returns results with scores and source fields.

---

## 3. Storage layer

Two modes per collection: **shadow** (default) and **inline** (opt-in).

### 3.1 Shadow mode

Sibling collection `articles_embeddings` stores vectors. User's documents are never mutated.

**Document schema:**

```js
{
  _id: ObjectId("..."),
  source_id: ObjectId("..."),          // points to articles._id (any BSON type)
  source_collection: "articles",
  field_path: "body",                  // or "user.profile.bio" or "comments[].body"
  chunk_index: 0,                      // 0 for unchunked; 0..N for chunked; array index for subdoc
  chunk_text: "...",
  embedding: [0.01, -0.23, ...],
  embedding_model: "all-MiniLM-L6-v2",
  embedding_dim: 384,
  embedding_hash: "sha1:...",          // hash(model + input_text)
  source_updated_at: ISODate("..."),
  created_at: ISODate("..."),
  updated_at: ISODate("...")
}
```

**Indexes:**
- `{source_id: 1, field_path: 1, chunk_index: 1, embedding_model: 1}` — unique compound. Replaces on re-embed.
- `{source_id: 1}` — for delete-cascade.
- `{embedding_model: 1}` — for migration queries.
- **Atlas only:** `$vectorSearch` index on `embedding`, HNSW, cosine. Name: `mongosemantic_{collection}_{field_hash}`.

**Shadow collection name:** deterministic, `{source_collection}_embeddings`. Not user-customizable in v1.

### 3.2 Inline mode

A single `_embedding` sub-document is added to each source doc, keyed by field path:

```js
{
  _id: ObjectId("..."),
  title: "...",
  body: "...",
  _embedding: {
    "body": {
      vector: [0.01, ...],
      model: "all-MiniLM-L6-v2",
      dim: 384,
      hash: "sha1:...",
      updated_at: ISODate("...")
    }
  }
}
```

**Rules:**
- Unchunked only. Chunking requires shadow mode — we detect at `apply` time and force-switch with a message.
- Soft size check: refuse `apply` if `(current_avg_doc_size + expected_vector_bytes) > 0.8 * 16MB`.
- Nested-field embedding: dots in paths escaped as `__` in the `_embedding` key (`user.profile.bio` → `_embedding["user__profile__bio"]`).

**When to pick inline:** max `$vectorSearch` performance on Atlas, and you don't mind the doc mutation. Shadow is the safe default.

### 3.3 Nested-field embedding

Field paths are dotted strings: `"user.profile.bio"`, `"metadata.description.long"`.

- **Shadow:** path stored as `field_path`; no schema change.
- **Inline:** dot-escaped to `__` inside `_embedding`. We reserve `mongosemantic:` prefix on any user field name that already contains `__`.

### 3.4 Array-of-subdocs embedding

Mongo-native differentiator. Example: post with `comments: [{body, author, ts}]`, user embeds each comment body.

**Field-path syntax:** `comments[].body` — the `[]` denotes array fanout.

**Shadow-mode only.** Each subdoc produces one embedding row:

```js
{ source_id: <post_id>, field_path: "comments[].body", chunk_index: 3, chunk_text: "...", embedding: [...], ... }
```

**Deletion semantics:** on every observed UPDATE to the source doc, we count `current_array_length` and delete embedding rows with `chunk_index >= current_array_length`. We re-embed subdocs whose hash changed.

**Polling-mode limitation:** array-element deletions cannot be detected without an observed update. Documented.

**v1 cap:** subdocs are not internally chunked. A 5000-char `comments[].body` is embedded whole (truncated at model max tokens with a warning). Combined fanout + chunking is v2.

### 3.5 Multi-field combined embedding

User can embed `title + body + tags` as one vector:

```js
fields: [
  {
    path: "title+body+tags",
    combine: {
      parts: ["title", "body", "tags"],
      separator: "\n\n",
      tags_join: " "
    },
    chunked: false
  }
]
```

Combined vector is a single row in storage with `field_path: "title+body+tags"`.

### 3.6 Chunking

Sentence-aware splitter with overlap (ported from pgsemantic).

**Defaults by model:**

| Model | Chunk size (tokens) | Overlap |
|---|---|---|
| MiniLM (384d) | 256 | 32 |
| MPNet (768d) | 384 | 48 |
| OpenAI small/large | 512 | 64 |
| Ollama nomic | 512 | 64 |

Tokens estimated as `len(text) / 4`. Actual tokenization happens inside the provider.

Chunked field → one row per chunk. Shadow-mode only.

### 3.7 Vector normalization

All stored vectors are L2-normalized at embed time. Query vectors same. Brute-force uses dot product ≡ cosine similarity — one fewer op per doc.

### 3.8 Model / dim tracking

Every embedding row records `embedding_model` and `embedding_dim`. Enables:
- Zero-downtime model migration.
- Atlas vector-index lifecycle management (indexes are dim-specific).
- Drift detection (config vs stored value surfaced in `status`).

---

## 4. Sync engine

### 4.1 Topology detection

On connect:

```python
info = client.admin.command("hello")
```

Decision:
- `info["setName"]` or `info["msg"] == "isdbgrid"` → **replica set / sharded** → change streams.
- Connection string ends in `.mongodb.net` → Atlas (superset of above).
- Otherwise → **standalone** → polling.

Cached per-process. Re-detected on reconnect.

### 4.2 Change-stream path

**One listener per configured database**, watching all configured collections via `$match`:

```python
pipeline = [{"$match": {"ns.coll": {"$in": configured_collections}}}]
stream = db.watch(pipeline, full_document="updateLookup", resume_after=last_token)
```

`full_document="updateLookup"` is mandatory — gives us the post-image, not just the delta.

**Event → job mapping:**

| Event | Jobs |
|---|---|
| `insert` | `embed` per configured field |
| `update`, `replace` | `embed` if field hash changed; `delete` for shrunk array subdocs |
| `delete` | `delete_all_vectors(source_id)` |
| `invalidate` | Disable config; surface in status |

**Hash-based skip:** compute hash of each configured field value, compare to hash on last embedding row for `(source_id, field_path, chunk_index)`. Equal → no enqueue.

**Resume-token persistence:** after each batch, write last-seen token to `mongosemantic_state`. One token per database.

**Oplog-expired recovery:** on `ChangeStreamHistoryLost`, full rescan via `find().sort({_id: 1})`, idempotent via hash comparison, resume stream from *now*.

### 4.3 Polling path

**One poller per configured collection.** Watermark field (default `updated_at`):

```python
cursor = collection.find({"updated_at": {"$gt": last_watermark}}).sort("updated_at", 1).limit(batch_size)
```

Hash-based skip same as change-stream.

**Can detect:** inserts (new `_id` / higher watermark), updates (bumped `updated_at`).

**Cannot detect (documented):**
- Deletes — unless user enables `soft_delete_field: "deleted_at"` config.
- Updates without watermark bump — user hygiene issue; `mongosemantic reindex` is the escape valve.
- Array-subdoc deletions — only detectable via observed update.

**Default interval:** 30 seconds. Configurable. Exponential backoff on errors.

### 4.4 Job queue

Collection `mongosemantic_jobs`:

```js
{
  _id: ObjectId("..."),
  database: "my_app",
  collection: "articles",
  source_id: <...>,
  field_path: "body",
  chunk_index: null,
  kind: "embed" | "delete" | "reindex" | "invalidate_collection",
  input_text: "...",
  input_hash: "sha1:...",
  status: "pending" | "in_flight" | "completed" | "failed",
  attempts: 0,
  last_error: null,
  model: "all-MiniLM-L6-v2",
  enqueued_at: ISODate("..."),
  started_at: null,
  completed_at: null,
  owner: null
}
```

**Claim:** atomic `findOneAndUpdate({status: "pending"}, {$set: {status: "in_flight", owner: worker_id, started_at: now}})`.

**Retry:** exponential backoff (1s, 4s, 15s, 60s), then `failed`. `mongosemantic retry` resets failed → pending.

**Dedup:** unique index on `(database, collection, source_id, field_path, chunk_index, kind, model)` where `status != "completed"`.

**Pipelining:** batch 32 `pending`, embed in one provider call, write all results, fetch next batch during write. ~2× throughput vs serial.

**Stale-claim reaper:** in-flight jobs with `started_at > now - 5min` and no heartbeat are reclaimed by a periodic job.

### 4.5 Multi-database isolation

- Config + jobs + state collections live **in the same database as the source data**.
- Each database has its own `mongosemantic_config`, `mongosemantic_jobs`, `mongosemantic_state`.
- One worker can service multiple databases: `mongosemantic worker --databases db1,db2`.
- `db.dropDatabase()` removes everything related — matches user expectation.

### 4.6 Failure matrix

| Failure | Behavior |
|---|---|
| Embedding provider 429/500 | Exponential backoff retry |
| Provider returns wrong dim | Fail fast, `DIM_MISMATCH`, requires config fix + retry |
| Shadow write fails (timeout) | Job back to pending, attempts++ |
| Change stream disconnect | Reconnect with resume token |
| Resume token expired | Full rescan, resume from now |
| Worker crash mid-job | In-flight with stale `started_at` reclaimed |
| User drops source collection | `invalidate` → disable config, keep shadow for forensics |
| Clock drift (polling) | Use server time (`$currentDate`), not local |

---

## 5. Semantic intelligence

### 5.1 Schema inspector

`mongosemantic inspect` samples up to 500 docs, walks recursively, outputs field→stats map:

```python
{
  "title":            {"type": "string", "count": 500, "null_count": 2,   "avg_len": 48},
  "body":             {"type": "string", "count": 500, "null_count": 0,   "avg_len": 2847},
  "tags":             {"type": "array<string>", "count": 500, "avg_array_len": 4.2},
  "author.name":      {"type": "string", "count": 500, "avg_len": 22},
  "comments[].body":  {"type": "array<string>", "count": 478, "avg_array_len": 5.6, "avg_len": 312},
}
```

**Suitability score (0–100):**

```
score = 100
  - penalize_non_string(type)        # 0 or 100
  - penalize_short_text(avg_len)     # 0 if ≥100, up to 60 if <20
  - penalize_nulls(null_ratio)       # up to 30
  - penalize_low_entropy(entropy)    # up to 40 for enum-like
```

Bands: `Great` (80–100), `Good` (60–79), `Usable` (40–59), `Not recommended` (0–39).

### 5.2 Search strategies

Three strategies; chosen at query time by topology + flags + index availability.

#### Strategy A: Atlas `$vectorSearch` (pure semantic)

```js
db.articles_embeddings.aggregate([
  { $vectorSearch: {
      index: "mongosemantic_articles_body",
      path: "embedding",
      queryVector: <embed(query)>,
      numCandidates: 200,
      limit: 10,
      filter: { ... }
  } },
  { $lookup: { from: "articles", localField: "source_id", foreignField: "_id", as: "source_doc" } },
  { $unwind: "$source_doc" },
  { $project: { source_id: 1, field_path: 1, chunk_index: 1, chunk_text: 1,
                score: {$meta: "vectorSearchScore"}, source_doc: 1 } }
])
```

`numCandidates = max(10 * limit, 100)`.

#### Strategy B: Atlas hybrid (`$search` + `$vectorSearch` + RRF)

Run both aggregations (text BM25 via `$search`, vector via `$vectorSearch`), top-200 each, merge client-side via **Reciprocal Rank Fusion**:

```
final_score(doc) = Σ_ranker (1 / (k + rank_in_ranker(doc)))
k = 60
```

Requires an Atlas Search text index on source collection. Surfaced in `apply` with exact creation command if missing.

#### Strategy C: Self-hosted brute-force

```js
db.articles_embeddings.aggregate([
  { $match: { field_path: "body" } },
  { $addFields: {
      similarity: { /* dot product via $reduce + $zip, vectors are L2-normalized */ }
  } },
  { $sort: { similarity: -1 } },
  { $limit: 10 },
  { $lookup: ... },
  { $project: ... }
])
```

O(n). Fine up to ~100k chunks. UI warns beyond.

**Self-hosted hybrid:** `$regex` + brute-force vector + RRF. Regex is weaker than BM25 but honest.

#### Strategy selection

```
if Atlas and vector_index:
  A  (or B if hybrid requested and text index exists)
elif Atlas and hybrid requested but no vector index:
  error "run apply first"
else:
  C  (warn if collection > 100k chunks)
```

### 5.3 Cross-collection search

Parallel Strategy A/C on each configured collection; collect top-K; merge by raw score; return top-`limit`. Each result carries `source_collection` label.

**Same-model collections:** direct score compare.
**Mixed-model:** min-max normalize per-collection before merge.

### 5.4 Zero-downtime model migration

State machine triggered by `mongosemantic migrate --collection articles --model openai-large`:

```
INITIATED  →  DUAL-WRITE  →  BACKFILL  →  VERIFIED  →  SWAP  →  (manual) CLEANUP
```

| State | Behavior |
|---|---|
| INITIATED | Validate target. Create new Atlas vector index if dim differs. |
| DUAL-WRITE | Config sets `active_model = old`, `migration_target = new`. Worker writes both old and new vectors for new/changed docs. Search still reads old. |
| BACKFILL | Background job embeds all existing docs under new model. Progress in UI. |
| VERIFIED | Once coverage = 100%, 30s settle period for in-flight events. |
| SWAP | Atomic config update: `active_model = new`. Search now reads new. |
| CLEANUP (manual) | `mongosemantic migrate --finalize articles` deletes old-model rows. |

**Rollback:** until CLEANUP, `active_model` can be flipped back atomically.
**Zero downtime:** search reads active_model at start of query; swap is one doc update.

---

## 6. CLI surface

Typer-based. Mirrors pgsemantic grammar exactly.

```
mongosemantic inspect                  # Scan DB, score fields
mongosemantic apply                    # Configure a collection
mongosemantic index                    # Bulk-embed existing docs
mongosemantic search <query>           # Search (no --collection = cross-collection)
mongosemantic migrate                  # Switch embedding models (zero downtime)
mongosemantic worker                   # Background sync daemon
mongosemantic serve                    # MCP server for AI agents
mongosemantic status                   # Health dashboard (text)
mongosemantic ui                       # Web dashboard
mongosemantic retry                    # Reset failed embedding jobs
mongosemantic reindex                  # Force full re-embed
```

**Global flags:** `--mongo-url`, `--database`, `--env-file`, `--json`, `--verbose`.

**Command-specific (non-exhaustive):**

```
inspect --collection <name> [--sample 500]
apply --collection <name> --field <path> [--field <path> ...]
      [--mode shadow|inline] [--chunked] [--chunk-size 512] [--chunk-overlap 64]
      [--model local-fast|local-better|openai-small|openai-large|ollama-nomic]
      [--combine <path1>+<path2>]
index --collection <name> [--resume] [--batch-size 32] [--workers 4]
search <query> [--collection <name>] [--limit 10] [--hybrid] [--filter '<mql>']
migrate --collection <name> --model <target> [--finalize]
worker [--databases db1,db2] [--poll-interval 30]
retry [--collection <name>] [--all]
reindex --collection <name> [--yes]
```

**Exit codes:** 0 success, 1 user error, 2 infra error, 3 data error.

---

## 7. Web UI content spec

Design is handled separately — visual layout is owned by the user. This section provides every string — page copy, labels, placeholders, empty states, errors, toasts, tooltips — as a paste-ready content spec that survives any visual redesign.

### 7.1 Page: Connection

**Title:** Connect to MongoDB
**Subtitle:** Paste a connection string. We'll detect your deployment and set things up from there.

**Fields:**
- "Connection URI" — placeholder: `mongodb+srv://user:pass@cluster.mongodb.net/your_db`
- "Database" — helper: "Leave blank to use the database in the URI."
- Button: "Test connection"

**Post-submit states:**
- Connecting: `Testing connection…`
- Atlas: `Connected to Atlas cluster "<name>" — native $vectorSearch available.`
- Replica set: `Connected to replica set "<setName>" — change streams enabled, brute-force search until $vectorSearch is configured.`
- Standalone: `Connected to standalone MongoDB — polling mode will be used (check collection has an updated_at field for best results).`
- Auth failed: `Authentication failed. Check username, password, and that your IP is allowed in Atlas > Network Access.`
- Network error: `Couldn't reach that host. Check the URI and try again.`
- Version too old: `MongoDB <version> is below the minimum supported version (7.0). Please upgrade or use a newer cluster.`

**Footer note:** `Your connection string is stored in .env with chmod 600. It's never sent to the browser.`

### 7.2 Page: Collections

**Title:** Collections
**Subtitle:** Pick a collection to inspect. We'll score each field for how well it fits semantic search.

**Table columns:** Collection · Documents · Avg size · Status
**Status values:** `Not configured` · `Configured (N fields)` · `Indexing… (n/N)` · `Ready` · `Migrating` · `Failed`
**Row action:** `Inspect →`

**Empty state:**
- Title: `No collections yet`
- Body: `This database doesn't have any collections. Add some data, then come back.`

### 7.3 Page: Inspect

**Title:** Inspect <collection>
**Subtitle:** We sampled <n> documents. Here's what we found.

**Table columns:** Field path · Type · Coverage · Avg length · Suitability · Action

**Suitability badges:** `Great` (80–100) · `Good` (60–79) · `Usable` (40–59) · `Not recommended` (0–39)

**Badge tooltips:**
- Great: `Text field, well populated, varied content. Embed this.`
- Good: `Usable for search. Try it.`
- Usable: `Short or sparse. Combining with another field may help.`
- Not recommended: `This looks like a label or ID, not content.`

**Actions:** `Embed` · `Combine with…`

**Nested paths:** rendered with dot notation (`user.profile.bio`).
**Array-of-subdocs:** rendered with bracket notation (`comments[].body`) + tooltip: `Each array element gets its own embedding.`

### 7.4 Page: Apply

**Title:** Configure semantic search
**Subtitle:** Pick fields, a mode, and a model. You can change any of this later.

**Section 1 — Fields:** multi-select chips.

**Section 2 — Mode:**
- `Shadow collection (recommended)` — helper: `Embeddings live in "<collection>_embeddings". Your original documents are never modified.`
- `Inline field` — helper: `Embeddings live in an "_embedding" field on each source document. Faster on Atlas, mutates your documents.`
- Notice when chunking is on: `Chunking requires shadow mode. We'll use shadow for this collection.`

**Section 3 — Chunking:**
- Toggle: `Split long text into overlapping chunks`
- Sliders: Chunk size (64–2048, default 512), Overlap (0–256, default 64)
- Help: `Chunking finds the best paragraph, not just the best document. Enable for text longer than ~1000 characters.`

**Section 4 — Model:**
- `Local Fast (MiniLM, 384d)` — `Free. Runs on your machine. Good for most use cases.`
- `Local Better (MPNet, 768d)` — `Free. More accurate, slower. Good for nuanced content.`
- `OpenAI Small (text-embedding-3-small, 1536d)` — `~$0.02/1M tokens. Requires OPENAI_API_KEY. Multilingual.`
- `OpenAI Large (text-embedding-3-large, 3072d)` — `~$0.13/1M tokens. Maximum accuracy.`
- `Ollama (nomic-embed-text, 768d)` — `Self-hosted via Ollama. Requires OLLAMA_HOST.`

**CTA:** `Apply configuration →`

**Topology notices:**
- Atlas: `We'll create a $vectorSearch index named "mongosemantic_<collection>_<field>". This takes ~1 minute.`
- Self-hosted: `No vector index will be created. Search uses brute-force aggregation — fine up to ~100k documents.`

### 7.5 Page: Indexing

**Title:** Indexing <collection>
**Subtitle:** Embedding existing documents. You can close this page — it runs in the background.

**Progress block:**
- `<processed> / <total> documents`
- `<rate> docs/sec`
- `ETA <duration>`
- Bar: % complete

**Controls:** `Pause` · `Cancel`

**Toasts:**
- `Indexing started.`
- `Indexing paused at <n>/<total>.`
- `Indexing resumed.`
- `Indexing complete — <n> documents embedded.`
- `Indexing failed on <n> documents. Run retry from the dashboard.`

### 7.6 Page: Search

**Placeholder:** `Search by meaning — "budget travel", "unhappy customers", "legal risk"`

**Toggles:**
- `Hybrid` — `Combines semantic similarity with keyword matching. Requires Atlas Search index.`
- `Filter…` — opens panel for JSON MQL filter expression.

**Selector:** `All configured collections ▾` or a single collection.

**Result card:**
- Score badge (e.g., `0.87`)
- Collection badge (color-coded)
- Field path + chunk index if chunked
- Highlighted snippet (~300 chars)
- Link: `View full document ↗`

**Empty states:**
- No query: `Type a query above to search by meaning.`
- No results: `No matches. Try a broader phrase, or switch to hybrid search.`
- Not configured: `No collections are configured yet. Go to Collections to set one up.`

### 7.7 Page: Visualize

**Title:** Explore <collection>
**Subtitle:** Documents laid out by meaning. Clusters are grouped by similarity, labeled by their top keywords.

**Controls:** collection dropdown · cluster count (auto / 4 / 8 / 16) · Refresh button

**Empty state:** `Not enough embeddings to visualize. Index at least 50 documents first.`

**Point tooltip:** `Cluster: <label> · Score: <distance-to-center> · Click for details`

### 7.8 Page: Query (aggregations)

**Title:** Aggregation query
**Subtitle:** Run read-only aggregation pipelines. Read-only, 10-second timeout, 100-document limit.

**Editor label:** `Pipeline (JSON array of stages)`
**Default content:** `[{ "$match": {} }, { "$limit": 20 }]`

**Safety banner:** `Blocked stages: $out, $merge, $function, any write operation. We parse before running.`

**Button:** `Run`
**Errors:** `Pipeline rejected: <reason>` inline, offending stage highlighted when possible.

### 7.9 Page: Dashboard

**Cards:**
- `Configured collections: <n>`
- `Total embeddings: <n>`
- `Coverage: <%>`
- `Pending jobs: <n>`
- `Failed jobs: <n>` (red when >0, action: `Retry all →`)
- `Worker status: <Running | Stopped | Lagging>`
- `Last change-stream event: <time ago>`

**Topology banner:**
- Atlas: `Atlas cluster · $vectorSearch enabled`
- Replica set: `Replica set · change streams · brute-force search`
- Standalone: `Standalone · polling (every <n>s)`

### 7.10 Page: MCP integration

**Title:** AI agent integration
**Subtitle:** Connect Claude Desktop, Cursor, or any MCP-compatible AI agent to your MongoDB.

**Claude Desktop config (copy block):**

```json
{
  "mcpServers": {
    "mongosemantic": {
      "command": "mongosemantic",
      "args": ["serve"],
      "env": {
        "MONGOSEMANTIC_URI": "<your-mongo-uri>",
        "MONGOSEMANTIC_DB": "<your-database>"
      }
    }
  }
}
```

**Tool list (read-only display):** one row per tool, with name, one-line description, example prompt.

### 7.11 Global strings

**Toasts:**
- `Saved.`
- `Configuration updated.`
- `Couldn't reach MongoDB. Retrying…`
- `Provider error: <summary>. We'll retry automatically.`
- `Job queue is healthy.`
- `Job queue is lagging by <n> items.`
- `Rate limited. Retrying in <n>s.`

---

## 8. MCP server — 10 tools

Each tool's description is what Claude/Cursor reads at tool-selection time; these are agent-facing prompts.

### `semantic_search`
> Search a configured collection by meaning, not keywords. Returns the top matches ranked by semantic similarity, with the matched field snippet and full source document. Use for open-ended questions like "find the contract about exclusivity" or "what do customers say about shipping delays".
>
> Params: `collection` (string, required), `query` (string, required), `limit` (int, default 10, max 50), `filter` (optional MQL match filter).

### `hybrid_search`
> Search combining semantic similarity with keyword matching (BM25). Better than semantic_search when the user mentions specific proper nouns, product names, or rare terms that must appear literally.
>
> Params: same as semantic_search. Requires Atlas Search index on the source collection.

### `search_all_collections`
> Search every configured collection at once. Returns merged, reranked results with a `source_collection` label. Use when the user's question could be answered by data from multiple collections.
>
> Params: `query` (string, required), `limit` (int, default 10).

### `list_collections`
> List all collections in the connected database, with document counts and configuration status. Use to discover what data is available before running a search or query.

### `list_configured_collections`
> List only collections that are set up for semantic search, with their configured fields and active embedding model. Use when you want to know what's actually searchable.

### `inspect_fields`
> For a given collection, list all fields found in a sample of documents, with types, coverage, and suitability scores for semantic search. Use to recommend which field a user should configure for search.
>
> Params: `collection` (string, required), `sample_size` (int, default 100).

### `get_sample_docs`
> Return N random documents from a collection. Use to understand the data shape before composing a query.
>
> Params: `collection` (string, required), `limit` (int, default 3).

### `get_embedding_status`
> Return embedding coverage, pending job count, failed job count, and worker health for one or all collections. Use to diagnose why search isn't returning expected results.

### `get_schema_context`
> Return a compact JSON summary of all configured collections, their fields, types, and relationships (if known). Use before composing an aggregation pipeline.

### `execute_safe_aggregation`
> Run a read-only MongoDB aggregation pipeline. Blocked stages: `$out`, `$merge`, `$function`, `$accumulator`, any write op. 10-second timeout. 100-document limit. Use for structured queries where semantic search isn't the right tool (counts, groupings, date-range filters).
>
> Params: `collection` (string, required), `pipeline` (array of stages, required).

**Safety for `execute_safe_aggregation`:** recursive stage-allowlist parser. Rejects disallowed stage names at any nesting depth (including inside `$lookup.pipeline`, `$facet`). Executes with `readConcern: "local"` and explicit `maxTimeMS`.

---

## 9. LLM-facing prompts

### 9.1 MCP tool-choice system prompt (injected when client supports it)

```
You have access to a MongoDB database through the mongosemantic tools.
Prefer semantic_search for open-ended, meaning-based questions.
Prefer hybrid_search when the user mentions exact names, SKUs, or rare terms.
Use execute_safe_aggregation only for structured queries (counts, groupings, filters).
Always check get_schema_context before writing aggregation pipelines.
Never claim a document exists unless it appears in a tool result.
Cite results by their source collection and _id.
```

Where clients don't allow system-prompt injection, the same guidance lives in individual tool descriptions.

### 9.2 Cluster-labeling prompt (Visualize page)

```
Given the following top-20 TF-IDF terms for a cluster of documents, produce a 2-to-4-word label describing the shared theme. Be concrete, not abstract. Do not start with "the" or "a". Return only the label.

Terms: <comma-separated list>
```

### 9.3 Onboarding empty-state copy (shown in UI, not sent to an LLM)

```
Connect a MongoDB database to begin. Once connected, we'll walk you through:
1. Picking a collection.
2. Choosing a field to make searchable.
3. Indexing your existing data.

Most users are set up in under a minute.
```

---

## 10. Security

**Mongo-specific:**
- Connection URI held server-side only; never echoed to browser.
- All aggregation tools use `readConcern: "local"` and `maxTimeMS: 10000`.
- `execute_safe_aggregation` parses and rejects disallowed stages recursively.
- Atlas Admin API key (for vector-index creation) stored in `.env`; recommend scoped keys.
- URI scheme allowlist: `mongodb://`, `mongodb+srv://`. No arbitrary URL fetching anywhere.

**Web surface:**
- CSRF tokens on all POST.
- Rate limiting: 120 req/min/IP.
- Security headers: CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy.
- `.env` written with mode 0600.
- Identifier validation via strict regex (`^[A-Za-z_][A-Za-z0-9_.\[\]]*$`) before interpolation.
- Default bind: localhost only.

**MCP surface:**
- All tools have bounded inputs (limits, timeouts).
- No tool mutates data.
- Errors return sanitized messages; stack traces go to logs, not responses.

---

## 11. Testing

**Unit tests (~150 target):**
- Embedding provider abstraction + one test per provider (mocked HTTP).
- Chunking: boundary cases, unicode, very long input, overlap correctness.
- DB layer: topology detection (3 cases), schema walker on synthetic docs, nested/array path parsing.
- Sync: change-stream event handlers (simulated), polling watermark logic, hash-based skip, resume-token persistence.
- Search: pipeline construction for all 3 strategies, RRF math.
- Migrate: state machine transitions on simulated failures.
- State: atomic claim correctness under simulated contention.

**Integration tests (docker fixtures):**
- `docker-compose.yml` brings up: 3-node replica set, 1 standalone, different ports.
- End-to-end apply → index → search on both topologies.
- Change-stream resumption: kill worker mid-indexing, restart, verify no data lost.
- Polling: insert without `updated_at`, verify `_id`-tail catches it.
- Model migration: dual-write → backfill → swap → rollback.
- Cross-collection search.

**E2E tests:**
- CLI: run each command against docker cluster, assert output.
- Web: FastAPI TestClient for routes; validate JSON + HTML.
- MCP: FastMCP test client, all 10 tools, valid + invalid inputs.

**CI:** GitHub Actions, matrix over Python 3.10/3.11/3.12 × Mongo 7.0/8.0.

---

## 12. File tree

```
mongosemantic/
├── .env.example
├── .gitignore
├── LICENSE
├── README.md
├── docker-compose.yml
├── pyproject.toml
├── docs/
│   ├── screenshots/
│   └── superpowers/
│       └── specs/
│           └── 2026-04-22-mongosemantic-design.md
├── mongosemantic/
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py
│   ├── config.py
│   ├── exceptions.py
│   ├── commands/
│   │   ├── inspect.py
│   │   ├── apply.py
│   │   ├── index.py
│   │   ├── search.py
│   │   ├── migrate.py
│   │   ├── worker.py
│   │   ├── serve.py
│   │   ├── status.py
│   │   ├── retry.py
│   │   └── reindex.py
│   ├── db/
│   │   ├── client.py
│   │   ├── schema.py
│   │   ├── indexes.py
│   │   └── queries.py
│   ├── embeddings/
│   │   ├── provider.py
│   │   ├── local.py
│   │   ├── openai.py
│   │   └── ollama.py
│   ├── chunking/
│   │   └── splitter.py
│   ├── sync/
│   │   ├── change_stream.py
│   │   └── polling.py
│   ├── worker/
│   │   └── runner.py
│   ├── state/
│   │   ├── config_store.py
│   │   ├── job_queue.py
│   │   └── resume_tokens.py
│   ├── search/
│   │   ├── atlas.py
│   │   ├── atlas_hybrid.py
│   │   ├── brute_force.py
│   │   └── cross_collection.py
│   ├── migrate/
│   │   └── state_machine.py
│   ├── mcp_server/
│   │   ├── server.py
│   │   └── tools.py
│   └── web/
│       ├── app.py
│       ├── routes.py
│       ├── static/
│       │   ├── index.html
│       │   ├── app.js
│       │   └── style.css
│       └── security.py
└── tests/
    ├── unit/
    ├── integration/
    │   └── conftest.py
    └── e2e/
```

---

## 13. Release plan

| Version | Scope |
|---|---|
| v0.1.0 | MVP: connect, inspect, apply (shadow only), index, search (Atlas native + brute-force), CLI. No web UI, no MCP. |
| v0.2.0 | Web UI parity with this spec. |
| v0.3.0 | MCP server, all 10 tools. |
| v0.4.0 | Atlas hybrid, nested-field, array-of-subdocs, visualization. |
| v0.5.0 | Zero-downtime model migration. |
| v1.0.0 | Polish, perf, docs complete. |

Every release ships something useful on its own. Atlas-native path stays healthy throughout.

---

## 14. Decisions considered and rejected

- **Shared `semantic-core` package with pgsemantic.** Deferred to v2. v1 copies code from pgsemantic. Reason: abstraction locked in before real pain points surface would be premature.
- **Sidecar ANN store (sqlite-vec).** Rejected. Adds infra and breaks the "your data lives in your DB" story. Brute-force is the honest v1 answer for self-hosted.
- **One giant `embeddings` collection across all source collections.** Rejected. Atlas vector indexes are dim-specific; mixing breaks index semantics.
- **BinData storage for vectors.** Rejected. Atlas `$vectorSearch` requires array-of-double.
- **User-customizable shadow collection name.** Rejected for v1. Deterministic names prevent footguns.
- **One change-stream listener per collection.** Rejected. Pays connection cost N times; one per database is strictly better.
- **Redis as job queue.** Rejected. Adds infra. Mongo is a fine queue at this scale.
- **Leader election for multi-worker setups.** Deferred to v2. Atomic claim handles correctness.
- **Token-aware chunking using the real tokenizer.** Rejected. `len/4` heuristic is within 15%; tokenize-twice isn't worth the perf cost.
- **Per-field Atlas Search text indexes.** Rejected. Atlas's `default` index on all text fields is the standard.
- **RRF constant `k=0`.** Rejected. `k=60` from Cormack et al. is the standard; empirically better.
- **Learned rerankers (cross-encoder) in v1.** Deferred. Lexical + dense fusion only for v1.
- **Automatic migration cleanup.** Rejected. Manual is safer; rollback remains trivial.
- **Auth on the web UI itself.** Rejected. Bind to localhost; users put their own auth proxy in front for remote access.

---

## 15. Deferred to v2

- Shared `semantic-core` package with pgsemantic.
- GridFS text extraction.
- Time-series collection support.
- Multi-cluster federation.
- Sidecar ANN index (sqlite-vec).
- Combined array-fanout + chunking.
- Cross-encoder rerankers.
- Leader election for multi-worker.
- User-customizable shadow collection names.
- Automatic migration cleanup.
