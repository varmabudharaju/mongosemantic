# mongosemantic

**Zero-config semantic search for any MongoDB database.**

`mongosemantic` connects to your existing MongoDB, picks a text field, and makes it searchable by meaning. No separate vector database. No ETL. Works on Atlas, self-hosted replica sets, and standalone MongoDB 7.0+.

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

## Hybrid search (Atlas)

Combine semantic similarity with BM25 keyword matching, fused via Atlas
`$rankFusion`. Useful when a query mixes meaning and specific terms — e.g.
*"MongoDB 7.0 replica set issues"* benefits from semantic (catches
"replica set" → "replication") plus keyword (anchors on "7.0").

```bash
mongosemantic search "MongoDB 7.0 replica set issues" --hybrid
```

CLI flag, web UI toggle, and `hybrid_search` MCP tool are all wired. Atlas
auto-creates both the vector index and the BM25 search index during
`apply`. Self-hosted topologies and inline-mode collections fall back to
pure semantic with a clear notice (no error).

## MCP — let Claude Desktop / Cursor query your MongoDB

```bash
mongosemantic integrate claude          # writes Claude Desktop config (restart Claude)
mongosemantic serve --transport sse     # or run as a standalone SSE server on :8090
```

Ten tools are exposed:

| Tool | What it does |
|---|---|
| `semantic_search` | Find rows in one collection by meaning |
| `hybrid_search` | Semantic + BM25 fused via Atlas `$rankFusion`; falls back to semantic with a notice elsewhere |
| `search_all_collections` | Cross-collection fanout, merged by score |
| `list_collections` | Every collection + its configured/not-configured status |
| `list_configured` | Just the ones with semantic search wired up |
| `inspect_collection` | Field-by-field suitability scoring |
| `get_sample_documents` | Real rows, embedding sub-doc stripped |
| `get_status` | Topology + total embeddings + job-queue counts |
| `safe_aggregation` | Read-only pipeline runner (10s, 100-row, no `$out`/`$merge`/`$function`) |
| `get_schema_context` | Compact schema summary for AI-generated aggregations |

## Status (v0.4.0)

- [x] Connect to Atlas / replica set / standalone
- [x] Inspect a collection, score fields for suitability
- [x] Configure shadow-mode **or inline-mode** semantic search on one or more fields
- [x] Real chunking — long documents split into overlapping chunks, search ranks per chunk
- [x] Bulk-embed existing documents
- [x] Sync in real time (change streams) or on a schedule (polling)
- [x] Search via native Atlas `$vectorSearch` or brute-force aggregation
- [x] CLI: inspect / apply / index / search / worker / status / retry / reindex / **ui** / **serve** / **integrate**
- [x] Web UI with seven pages and a safe aggregation runner
- [x] **MCP server** for Claude Desktop / Cursor / any MCP client (stdio + SSE)
- [x] **Atlas hybrid search** — semantic + keyword via `$rankFusion` (`--hybrid` / UI toggle / `hybrid_search` MCP tool)
- [ ] Zero-downtime model migration _(v0.5.0)_

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

| Topology | Sync | Search |
|---|---|---|
| **Atlas** | Change streams | `$vectorSearch` (native) |
| **Self-hosted replica set** | Change streams | Brute-force aggregation |
| **Self-hosted standalone** | Polling (`updated_at` watermark) | Brute-force aggregation |

Brute-force is fine up to ~100k chunks. For larger self-hosted collections, Atlas is recommended.

## Development

```bash
git clone https://github.com/varmabudharaju/mongosemantic
cd mongosemantic
pip install -e ".[dev,openai]"
docker compose up -d                          # replica set + standalone
MONGOSEMANTIC_RUN_INTEGRATION=1 python3 -m pytest -v
```

## License

MIT
