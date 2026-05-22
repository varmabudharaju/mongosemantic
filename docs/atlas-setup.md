# Atlas setup and live testing

This walks you through pointing mongosemantic at a free-tier MongoDB Atlas
cluster so you can validate the Atlas-only paths. Each path is now codified
as a pytest under `tests/integration/atlas/` and was live-tested against an
M0 cluster as of v0.7.5 — see the "Verified automatically" section below.

The Atlas-only paths:

- `$vectorSearch` (replaces the brute-force aggregation)
- `$search` (BM25)
- `$rankFusion` hybrid search
- Migration with Atlas Search index rename carry-over (M10+ on Atlas;
  M0/M2/M5 can't fit the temporary 4-index swap window)

---

## 1. Create the cluster (≈ 5 min)

1. Go to <https://www.mongodb.com/cloud/atlas/register>. The free M0 tier
   is enough for everything below except online migration — which needs
   M10+ (see "Known caveats" at the bottom).
2. Pick any cloud / region. Any cluster name is fine.
3. Wait ~3 minutes for the cluster to spin up.

## 2. Database user + IP allowlist

In the Atlas console under **SECURITY** → **Database & Network Access**:

1. **Database Users** tab → **Add New Database User**.
   Username: `mongosemantic`. Choose a strong password and save it.
   Built-in role: **Atlas admin** (we'll be creating search indexes).
2. **IP Access List** tab → **Add IP Address** → **Add Current IP Address**.
   If your IP changes, repeat. (Or `0.0.0.0/0` for testing only — never
   for production.)

## 3. Load the sample dataset

In the Atlas console: **Database** → your cluster → **"..."** menu →
**Load Sample Dataset**. Wait ~2 minutes. This populates several sample
databases including `sample_mflix`, which is what the rest of this runbook
uses.

The collection we'll exercise is `sample_mflix.embedded_movies` (~3,483
docs, curated by Atlas as their Vector Search demo dataset). It has rich
text fields (`title`, `plot`, `fullplot`) and nested arrays (`genres`,
`cast`).

## 4. Grab the connection string

Atlas → **Database** → **Connect** → **Drivers**. Copy the URI:

    mongodb+srv://mongosemantic:<password>@<cluster>.xxxxx.mongodb.net/?retryWrites=true&w=majority

Replace `<password>` with the password you set in step 2.

## 5. Point mongosemantic at Atlas

Export the URI in your shell:

```bash
export MONGOSEMANTIC_URI="mongodb+srv://mongosemantic:<password>@…mongodb.net/"
export MONGOSEMANTIC_DB="sample_mflix"
export MONGOSEMANTIC_MODEL="local-fast"
```

Confirm the topology detector recognizes it as Atlas:

```bash
mongosemantic status
# → Topology: atlas
```

## 6. Apply + index

```bash
mongosemantic apply  -c embedded_movies -f title --mode shadow
mongosemantic index  -c embedded_movies
mongosemantic worker --once
```

On Atlas, `apply` automatically creates two index types on each
shadow collection:

- `mongosemantic_<coll>_<digest>` — the **vectorSearch** index used by `$vectorSearch`.
- `mongosemantic_search_<coll>_<digest>` — the **search** index used by `$search` and hybrid.

Both indexes take **30–90 seconds** to come online. The CLI returns
immediately; the indexes finish building in the background. You can
watch progress in Atlas → **Database** → cluster → **Search** tab.

> **Note on M0/M2/M5:** Atlas free and shared tiers cap **search indexes
> at 3 per cluster**. Each shadow-mode field needs 2 indexes (vector + BM25),
> so two-field `apply` on those tiers needs 4 and will fail. `apply` now
> exits non-zero with a clear M0 hint if you hit it. Use a single field or
> upgrade to M10+.

## 7. Verify each Atlas-only path

### $vectorSearch (replaces brute-force aggregation)

Once the vector index is queryable, every search you run is using
`$vectorSearch` under the hood. To confirm:

```bash
mongosemantic search "heist gone wrong" -c embedded_movies --limit 5
```

Scores will be in the 0.5–0.8 range (cosine similarity from Atlas).
Compared to brute-force fallback (which would show raw dot-product values
above 1), these fractional scores are the signal Atlas-side aggregation is
running.

### Hybrid search ($rankFusion)

```bash
mongosemantic search "gangster crime" -c embedded_movies --hybrid --limit 5
```

The hybrid result should include both semantic neighbors and keyword
anchors. Scores are RRF-fused (typically `0.005`–`0.05`).

If you're on a MongoDB version that doesn't support `$rankFusion`, the CLI
prints an explicit fallback notice.

### $search BM25 index

The BM25 index is created automatically alongside the vector index. You
can query it directly through the pymongo aggregation pipeline (see
`tests/integration/atlas/test_search_bm25.py`), or just rely on it being
used by the hybrid path above.

### Chunked indexing

```bash
mongosemantic apply  -c embedded_movies -f fullplot --mode shadow \
                     --chunked --chunk-size 60 --chunk-overlap 10
mongosemantic index  -c embedded_movies
mongosemantic worker --once
```

Long-`fullplot` docs will produce >1 chunk in `embedded_movies_embeddings`.
Search returns chunk-level excerpts.

### Inline mode

```bash
mongosemantic teardown -c embedded_movies --yes
mongosemantic apply    -c embedded_movies -f plot --mode inline
mongosemantic index    -c embedded_movies
mongosemantic worker   --once
```

Source documents now have the embedding written directly under
`_msem.plot.embedding`. No shadow collection.

### Migration with index name carry-over (**M10+ only on Atlas**)

```bash
mongosemantic migrate -c embedded_movies -m local-better
```

What to verify:

1. The CLI shows a progress bar that reaches 100%.
2. After the rename, the same control query returns the same top hit (the
   model changed, so absolute scores differ, but the nearest neighbour
   shouldn't move for a clear query).
3. Atlas → cluster → **Search** tab shows the migration-renamed
   vector + search indexes attached to `embedded_movies_embeddings`
   (names include `_mig_<timestamp>`).
4. `embedded_movies_embeddings_archive_<ts>` still exists with the
   old 384-d embeddings until you drop it manually.

**On M0/M2/M5 this step will fail** with `OperationFailure: maximum number
of FTS indexes`. Online migration temporarily needs 4 indexes during the
swap window (old vector + old BM25 + new vector + new BM25); the free /
shared tiers cap at 3. Either upgrade to M10+ for this step or use the
local replica-set runbook to exercise migration.

### Web dashboard against Atlas

```bash
mongosemantic ui --port 8081
# Open http://127.0.0.1:8081
```

Things to check visually:

- Connection page reports **Atlas cluster**.
- Search page works at Atlas latencies (typically 50–150 ms vs. the
  local 5–20 ms).
- Hybrid toggle returns RRF-fused results (small fractional scores).
- Visualize page shows points laid out by the same Atlas embeddings.
- Migrate modal works end-to-end (M10+).

## 7a. Verified automatically

The verification above is also codified as a pytest suite under
`tests/integration/atlas/`. Re-run it against any Atlas cluster with:

```bash
export MONGOSEMANTIC_RUN_ATLAS_INTEGRATION=1
export MONGOSEMANTIC_ATLAS_URI="mongodb+srv://..."
python3 -m pytest tests/integration/atlas -v
```

Each of tiers 1–6 from this runbook has a corresponding `test_*.py`.
Tier 7 (UI) is manual. Tier 6 (migration) self-skips on M0/M2/M5.

## 8. Tear down

When you're done testing:

```bash
# In Atlas console: Database → cluster → ... → Terminate.
```

Or keep the cluster around. M0 is free.

---

## Known caveats

- **`$rankFusion` may print a fallback notice** on MongoDB versions that
  don't support it. Atlas's M0 ran 8.0 during our verification and `$rankFusion`
  worked anyway (Atlas appears to backport), but the fallback path is
  documented in the CLI.
- **Index build time.** The first `apply` against Atlas blocks user
  queries on that collection until both indexes finish building
  (~30–90 s). Subsequent applies on other collections build in
  parallel.
- **M0 storage cap.** 512 MB. embedded_movies + 384-d vectors uses about
  20 MB; a real workload at scale needs M10+.
- **M0/M2/M5 FTS-index cap.** Three search indexes per cluster. Each
  shadow-mode field needs 2 (vector + BM25). Migration needs 4 briefly.
  Single-field shadow + hybrid fits; multi-field or online migration
  doesn't. Apply now exits non-zero with a clear hint.
- **TLS CA bundle.** macOS Python from python.org / Apple Python often
  doesn't have a discoverable CA bundle and Atlas connections fail with
  `CERTIFICATE_VERIFY_FAILED`. As of v0.7.2 mongosemantic passes
  `certifi.where()` to pymongo by default, so this is fixed without
  manual `SSL_CERT_FILE` plumbing.
