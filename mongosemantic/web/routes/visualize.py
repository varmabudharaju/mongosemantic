"""Visualize endpoint: PCA-project the embeddings of a configured collection
to 2D, cluster them, and return scatter-plot points the UI can render with
canvas. Each cluster also gets auto-extracted keyword labels so the user
can read meaning off the legend instead of staring at unlabeled dots.

Server-side does:
  1. Sample embeddings + their source text
  2. PCA → 2D coordinates (returned for plotting)
  3. K-means on the full embeddings (better clusters than on PCA output)
  4. Per-cluster TF-IDF over the source text → top-N keyword labels
  5. Variance-explained for the first two PCA components

All numpy, no sklearn dep — K-means here is ~50 lines and fine up to
~20k points.
"""
from __future__ import annotations

import contextlib
import re
from collections import Counter

import numpy as np
from fastapi import APIRouter, HTTPException, Path, Query

from mongosemantic.config import Settings
from mongosemantic.db.client import MongoConnection
from mongosemantic.db.queries import INLINE_ROOT, inline_field_key
from mongosemantic.state import load_config
from mongosemantic.web.identifiers import IdentifierError, validate_identifier

router = APIRouter()

# Minimal English stopword list, enough for free-text in product/review
# datasets. Kept inline (not a dep) so this module stays self-contained.
_STOPWORDS = frozenset(["a", "about", "above", "after", "again", "against", "all", "am", "an", "and", "any", "are", "as", "at", "be", "because", "been", "before", "being", "below", "between", "both", "but", "by", "could", "did", "do", "does", "doing", "down", "during", "each", "few", "for", "from", "further", "had", "has", "have", "having", "he", "her", "here", "hers", "herself", "him", "himself", "his", "how", "i", "if", "in", "into", "is", "it", "its", "itself", "just", "me", "more", "most", "my", "myself", "no", "nor", "not", "now", "of", "off", "on", "once", "only", "or", "other", "our", "ours", "ourselves", "out", "over", "own", "same", "she", "should", "so", "some", "such", "than", "that", "the", "their", "theirs", "them", "themselves", "then", "there", "these", "they", "this", "those", "through", "to", "too", "under", "until", "up", "very", "was", "we", "were", "what", "when", "where", "which", "while", "who", "whom", "why", "will", "with", "would", "you", "your", "yours", "yourself", "yourselves", "can", "also", "like", "one", "two", "new", "well", "much", "many", "made", "via", "use", "using", "used", "get", "got", "really", "still", "maybe", "even", "though", "although", "however", "thus", "hence", "whilst"])

# Sampling cap. PCA itself scales fine; the limiter here is the JSON payload
# back to the browser (~200 bytes per point including the text snippet).
# 50 000 points is ~10 MB which loads fine on a local network; if you push
# this much higher consider streaming or downsampling client-side.
MAX_SAMPLE = 50_000
MIN_FOR_PCA = 3


def _sample_inline(db, cfg, field_path: str, sample: int) -> list[tuple]:
    """For inline mode the embeddings live at `_msem.{key}.embedding` on the
    source doc itself. Return (id, embedding, text) tuples."""
    key = inline_field_key(field_path)
    path = f"{INLINE_ROOT}.{key}"
    pipeline = [
        {"$match": {f"{path}.embedding": {"$exists": True}}},
        {"$sample": {"size": sample}},
        {"$project": {
            "_id": 1,
            "embedding": f"${path}.embedding",
            "text": f"${path}.text",
        }},
    ]
    return [
        (str(r["_id"]), r["embedding"], r.get("text") or "")
        for r in db[cfg.collection].aggregate(pipeline)
    ]


def _sample_shadow(db, cfg, field_path: str, sample: int) -> list[tuple]:
    pipeline = [
        {"$match": {"field_path": field_path}},
        {"$sample": {"size": sample}},
        {"$project": {"source_id": 1, "embedding": 1, "chunk_text": 1, "chunk_index": 1}},
    ]
    return [
        (str(r["source_id"]), r["embedding"], r.get("chunk_text") or "")
        for r in db[cfg.shadow_collection].aggregate(pipeline)
    ]


def _pca_2d(matrix: np.ndarray) -> tuple[np.ndarray, float]:
    """Manual PCA via eigendecomposition of the covariance matrix. Returns
    (coords, variance_explained_pct) where variance_explained_pct is the
    fraction of total variance captured by the top two components, expressed
    as a percentage. Falls back to the first two raw dimensions if the
    input is rank-deficient.
    """
    centered = matrix - matrix.mean(axis=0, keepdims=True)
    cov = np.cov(centered, rowvar=False)
    try:
        vals, vecs = np.linalg.eigh(cov)
    except np.linalg.LinAlgError:
        return centered[:, :2], 0.0
    top2 = vecs[:, -2:][:, ::-1]
    total = float(vals.sum())
    # eigh returns eigenvalues sorted ascending; the top two are the last.
    var_pct = 0.0 if total <= 0 else float(vals[-2:].sum() / total * 100.0)
    return centered @ top2, var_pct


def _kmeans(X: np.ndarray, k: int, max_iter: int = 30, seed: int = 42) -> np.ndarray:
    """Plain Lloyd-style K-means in numpy. Returns the cluster label per row.

    Uses the ||x - c||^2 = ||x||^2 + ||c||^2 - 2 x·c trick to avoid the
    explicit (n, k, d) broadcast — fine for ~20k × 384 in well under a
    second. Empty clusters are reseeded from the data point farthest from
    its current centroid so we don't drift toward fewer-than-k clusters.
    """
    rng = np.random.default_rng(seed)
    n = X.shape[0]
    if n <= k:
        return np.arange(n, dtype=np.int32) % k
    idx = rng.choice(n, size=k, replace=False)
    centroids = X[idx].astype(np.float64).copy()
    labels = np.full(n, -1, dtype=np.int32)
    X_sq = (X.astype(np.float64) ** 2).sum(axis=1)
    for _ in range(max_iter):
        C_sq = (centroids ** 2).sum(axis=1)
        # Pairwise squared distances via the dot-product identity.
        dists = X_sq[:, None] + C_sq[None, :] - 2.0 * (X @ centroids.T)
        new_labels = dists.argmin(axis=1).astype(np.int32)
        if np.array_equal(new_labels, labels):
            break
        labels = new_labels
        for c in range(k):
            mask = labels == c
            if mask.any():
                centroids[c] = X[mask].mean(axis=0)
            else:
                # Empty cluster — reseed from the point that's worst-fit
                # under the current centroids. Keeps k effective.
                worst = int(dists.min(axis=1).argmax())
                centroids[c] = X[worst]
    return labels


def _cluster_keywords(
    texts: list[str], labels: np.ndarray, k: int, top_n: int = 3
) -> list[list[str]]:
    """For each cluster, return top-N keywords by TF-IDF.

    Token rule: ASCII letters only, length ≥ 3, lowercased. We compute
    DF across *clusters* (not docs) — a word that appears in every cluster
    is uninformative; a word that appears in only one cluster is the
    cluster's signature.
    """
    word_re = re.compile(r"[A-Za-z]{3,}")
    cluster_freq: list[Counter[str]] = [Counter() for _ in range(k)]
    for text, lbl in zip(texts, labels, strict=False):
        tokens = (t.lower() for t in word_re.findall(text or ""))
        cluster_freq[int(lbl)].update(t for t in tokens if t not in _STOPWORDS)
    # DF across clusters
    df: Counter[str] = Counter()
    for c in range(k):
        for w in cluster_freq[c]:
            df[w] += 1
    # Domain stopwords: words that appear in more than half of all clusters
    # are too common to discriminate. Strip them entirely so the legend
    # surfaces what's *characteristic* of each cluster, not what's just
    # vocabulary-of-the-domain ("wine"/"flavor" on a wine corpus, etc).
    domain_stops = {w for w, n in df.items() if n > max(2, k // 2)}
    results: list[list[str]] = []
    for c in range(k):
        words = cluster_freq[c]
        if not words:
            results.append([])
            continue
        total = sum(words.values())
        scored = []
        for w, freq in words.items():
            if w in domain_stops:
                continue
            # Singletons in big clusters are usually typos / proper nouns.
            if freq < 2 and total >= 50:
                continue
            tf = freq / total
            # log(k / df) — 0 when a word is in every cluster, positive
            # otherwise. We've already filtered the high-df cases above.
            idf = float(np.log(k / max(df[w], 1)))
            scored.append((w, tf * idf, freq))
        scored.sort(key=lambda x: -x[1])
        results.append([w for w, _, _ in scored[:top_n]])
    return results


def _normalize_01(xy: np.ndarray) -> np.ndarray:
    """Squash each axis independently into [0, 1] so the canvas can draw
    without per-frame math."""
    out = xy.astype(np.float32)
    for col in range(out.shape[1]):
        lo, hi = float(out[:, col].min()), float(out[:, col].max())
        if hi - lo < 1e-12:
            out[:, col] = 0.5
        else:
            out[:, col] = (out[:, col] - lo) / (hi - lo)
    return out


@router.get("/api/collections/{name}/visualize")
def visualize(
    name: str = Path(...),
    field: str | None = Query(None, description="Which configured field to plot. "
                                                "Defaults to the first."),
    sample: int = Query(1000, ge=1, le=MAX_SAMPLE),
    clusters: int = Query(8, ge=2, le=20,
                          description="Target number of K-means clusters."),
) -> dict:
    try:
        validate_identifier(name)
        if field is not None:
            validate_identifier(field)
    except IdentifierError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    settings = Settings.from_environment()
    conn = MongoConnection.open(settings.uri, settings.database)
    try:
        cfg = load_config(conn.db, name)
        if not cfg:
            raise HTTPException(status_code=400, detail=f"{name} is not configured")
        field_path = field or cfg.fields[0].path
        if not any(f.path == field_path for f in cfg.fields):
            raise HTTPException(status_code=400, detail=f"{field_path!r} is not a configured field on {name}")
        if cfg.mode == "inline":
            rows = _sample_inline(conn.db, cfg, field_path, sample)
        else:
            rows = _sample_shadow(conn.db, cfg, field_path, sample)
        if len(rows) < MIN_FOR_PCA:
            return {
                "collection": name, "field": field_path, "points": [],
                "available_fields": [f.path for f in cfg.fields],
                "message": (
                    f"Need at least {MIN_FOR_PCA} embeddings to project; "
                    f"found {len(rows)}. Run `index` first."
                ),
            }
        matrix = np.array([r[1] for r in rows], dtype=np.float32)
        texts = [r[2] for r in rows]
        # PCA for layout (with variance %), K-means on the FULL embeddings
        # because the 2D projection throws away most of the signal that
        # makes good clusters.
        raw_coords, variance_pct = _pca_2d(matrix)
        coords = _normalize_01(raw_coords)
        k = min(clusters, len(rows))
        labels = _kmeans(matrix, k)
        keywords = _cluster_keywords(texts, labels, k)
        # Per-cluster point count — useful for the legend.
        cluster_sizes = Counter(int(c) for c in labels)
        clusters_meta = [
            {
                "id": c,
                "size": cluster_sizes.get(c, 0),
                "keywords": keywords[c] if c < len(keywords) else [],
            }
            for c in range(k)
        ]
        points = [
            {
                "id": rows[i][0],
                "x": float(coords[i, 0]),
                "y": float(coords[i, 1]),
                "cluster": int(labels[i]),
                "text": (rows[i][2] or "")[:160],
            }
            for i in range(len(rows))
        ]
        return {
            "collection": name,
            "field": field_path,
            "available_fields": [f.path for f in cfg.fields],
            "points": points,
            "clusters": clusters_meta,
            "stats": {
                "sample_size": len(rows),
                "embedding_dim": int(matrix.shape[1]),
                "k": k,
                "variance_explained_pct": round(variance_pct, 1),
            },
        }
    finally:
        conn.close()


@router.get("/api/collections/{name}/doc/{source_id}")
def get_doc(name: str = Path(...), source_id: str = Path(...)) -> dict:
    """Fetch a single source document by id. Used by the Visualize page to
    populate the slide-in detail panel on point click — same shape used
    by Inspect and Search.
    """
    try:
        validate_identifier(name)
    except IdentifierError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    settings = Settings.from_environment()
    conn = MongoConnection.open(settings.uri, settings.database)
    try:
        # source_id was stringified going out; try a few shapes coming back.
        # ObjectId is the common case; UUIDs and plain strings also valid.
        from bson import ObjectId
        candidates: list = [source_id]
        with contextlib.suppress(Exception):
            candidates.append(ObjectId(source_id))
        for cand in candidates:
            doc = conn.db[name].find_one({"_id": cand})
            if doc is not None:
                # bson.json_util-compatible: stringify _id for transport.
                if "_id" in doc:
                    doc["_id"] = str(doc["_id"])
                return {"doc": doc}
        raise HTTPException(status_code=404, detail="document not found")
    finally:
        conn.close()
