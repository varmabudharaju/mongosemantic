# Atlas setup and live testing

This walks you through pointing mongosemantic at a free-tier MongoDB Atlas
cluster so you can validate the paths that don't run against self-hosted:

- Atlas `$vectorSearch` (replaces the brute-force aggregation)
- `$search` (BM25)
- `$rankFusion` hybrid search
- Migration with Atlas Search index rename carry-over

All four paths are logically reviewed and unit-tested but have not been
live-tested against a real Atlas cluster as of v0.6.0.

---

## 1. Create the cluster (≈ 5 min)

1. Go to <https://www.mongodb.com/cloud/atlas/register>. The free M0 tier
   is enough — it supports both Atlas Search and Atlas Vector Search.
2. Pick any cloud / region. Name the cluster `mongosemantic-test`.
3. Wait ~3 minutes for the cluster to spin up.

## 2. Database user + IP allowlist

1. Atlas → **Database Access** → **Add New Database User**.
   Username: `mongosemantic`. Choose a strong password and save it.
   Built-in role: **Atlas admin** (we'll be creating search indexes).
2. Atlas → **Network Access** → **Add IP Address** → **Add Current IP**.
   If your IP changes, repeat. (Or `0.0.0.0/0` for testing only — never
   for production.)

## 3. Grab the connection string

Atlas → **Database** → **Connect** → **Drivers**. Copy the URI:

    mongodb+srv://mongosemantic:<password>@mongosemantic-test.xxxxx.mongodb.net/?retryWrites=true&w=majority

Replace `<password>` with the password you set in step 2.

## 4. Point mongosemantic at Atlas

Export the URI in your shell (or write a `.env`):

```bash
export MONGOSEMANTIC_URI="mongodb+srv://mongosemantic:<password>@…mongodb.net/"
export MONGOSEMANTIC_DB="demo"
export MONGOSEMANTIC_MODEL="local-fast"
```

Confirm the topology detector recognizes it as Atlas:

```bash
mongosemantic status
# → Topology: atlas
```

## 5. Seed the demo data into Atlas

```bash
python3 scripts/seed_demo.py
# → Seeded demo@mongodb+srv://…mongodb.net/
```

## 6. Apply + index

```bash
mongosemantic apply  -c articles -f title -f body
mongosemantic apply  -c products -f description --mode inline
mongosemantic apply  -c recipes  -f body --chunked --chunk-size 60 --chunk-overlap 10

mongosemantic index  -c articles
mongosemantic index  -c products
mongosemantic index  -c recipes

mongosemantic worker --once
```

On Atlas, `apply` automatically creates two index types on each
shadow collection:

- `mongosemantic_<coll>_<digest>` — the **vectorSearch** index used by `$vectorSearch`.
- `mongosemantic_search_<coll>_<digest>` — the **search** index used by `$search` and hybrid.

Both indexes take **30–90 seconds** to come online. The CLI returns
immediately; the indexes finish building in the background. You can
watch progress in Atlas → **Database** → cluster → **Search** tab.

## 7. Verify each Atlas-only path

### $vectorSearch (replaces brute-force aggregation)

Once the vector indexes are queryable, every search you run is using
`$vectorSearch` under the hood. To confirm:

```bash
mongosemantic search "budget travel" -c articles --limit 3
```

Scores will be in the 0.6–0.8 range (cosine similarity from Atlas).
Compare to running the same query against the local replica set —
results should be similar but scores are computed differently.

### Hybrid search ($rankFusion)

```bash
mongosemantic search "MongoDB 7.0 replica set issues" -c articles --hybrid --limit 3
```

The hybrid result should include both semantic neighbors (programming
articles about MongoDB) and keyword anchors (anything literally
mentioning "7.0"). If hybrid silently fell back to pure semantic,
check the cluster version — `$rankFusion` requires **MongoDB 8.1+**.
Atlas typically runs the latest stable, but verify in
**Database** → cluster → **Overview** → MongoDB version.

If you're on 8.0, the hybrid path will run but return only the
`$vectorSearch` half. You'll see a warning in the CLI:

    Hybrid search requires Atlas + shadow-mode collections. …

### Migration with index name carry-over

```bash
mongosemantic migrate -c recipes -m local-better
```

What to verify:

1. The CLI shows a 10/10 progress bar; total time is roughly
   `2 × (seed time of recipes)` because we're rebuilding 768-d vectors.
2. After the rename, `mongosemantic search "how to make crusty bread" -c recipes`
   continues to return the baguette chunk at the top.
3. Atlas → cluster → **Search** tab now shows the migration-renamed
   vector + search indexes attached to `recipes_embeddings` (i.e. the
   names include `_mig_<timestamp>` — that's the post-rename position).
4. The archive collection `recipes_embeddings_archive_<ts>` still
   exists with the old 384-d embeddings until you drop it manually
   or re-run with `--drop-archive`.

### Web dashboard against Atlas

```bash
mongosemantic ui
# Open http://127.0.0.1:8080
```

Things to check visually:

- Connection page reports **Atlas cluster**.
- Search page works at Atlas latencies (typically 50–150 ms vs. the
  local 5–20 ms).
- Hybrid toggle does **not** show the amber fallback banner.
- Visualize page shows points laid out by the same Atlas embeddings.
- Migrate modal works end-to-end with the polled progress bar.

## 8. Tear down

When you're done testing:

```bash
# In Atlas console: Database → cluster → ... → Terminate.
```

Or keep the cluster around. M0 is free.

---

## Known caveats

- **`$rankFusion` requires MongoDB 8.1+.** Older Atlas tiers on 8.0 or
  earlier fall back to pure semantic. The fallback is silent in the
  pipeline but the CLI prints an explicit notice.
- **Index build time.** The first `apply` against Atlas blocks user
  queries on that collection until both indexes finish building
  (~30–90 s). Subsequent applies on other collections build in
  parallel.
- **M0 storage cap.** 512 MB. The 100-doc demo corpus uses about 8 MB
  including embeddings; a real workload at scale needs M10+.
