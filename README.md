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
```

## Status (v0.1.0 MVP)

- [x] Connect to Atlas / replica set / standalone
- [x] Inspect a collection, score fields for suitability
- [x] Configure shadow-mode semantic search on a field
- [x] Bulk-embed existing documents
- [x] Sync in real time (change streams) or on a schedule (polling)
- [x] Search via native Atlas `$vectorSearch` or brute-force aggregation
- [x] CLI: inspect / apply / index / search / worker / status / retry / reindex
- [ ] Web UI _(v0.2.0)_
- [ ] MCP server for AI agents _(v0.3.0)_
- [ ] Atlas hybrid search (semantic + keyword) _(v0.4.0)_
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
