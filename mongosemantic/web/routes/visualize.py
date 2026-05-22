"""Visualize endpoint: PCA-project the embeddings of a configured collection
to 2D and return scatter-plot points the UI can render with canvas.

Server-side PCA keeps the heavy numpy work close to the database and avoids
shipping raw embedding vectors over the wire. Sample size is capped at
5,000 — past that we're well into "use the CLI" territory anyway.
"""
from __future__ import annotations

import numpy as np
from fastapi import APIRouter, HTTPException, Path, Query

from mongosemantic.config import Settings
from mongosemantic.db.client import MongoConnection
from mongosemantic.db.queries import INLINE_ROOT, inline_field_key
from mongosemantic.state import load_config
from mongosemantic.web.identifiers import IdentifierError, validate_identifier

router = APIRouter()

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


def _pca_2d(matrix: np.ndarray) -> np.ndarray:
    """Manual PCA via eigendecomposition of the covariance matrix. Returns
    an (n, 2) array of projected coordinates. Falls back to the first two
    raw dimensions if the input is rank-deficient."""
    centered = matrix - matrix.mean(axis=0, keepdims=True)
    cov = np.cov(centered, rowvar=False)
    try:
        vals, vecs = np.linalg.eigh(cov)
    except np.linalg.LinAlgError:
        return centered[:, :2]
    top2 = vecs[:, -2:][:, ::-1]
    return centered @ top2


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
        coords = _normalize_01(_pca_2d(matrix))
        points = [
            {
                "id": rows[i][0],
                "x": float(coords[i, 0]),
                "y": float(coords[i, 1]),
                "text": (rows[i][2] or "")[:160],
            }
            for i in range(len(rows))
        ]
        return {
            "collection": name,
            "field": field_path,
            "available_fields": [f.path for f in cfg.fields],
            "points": points,
            "sample_size": len(rows),
            "embedding_dim": int(matrix.shape[1]),
        }
    finally:
        conn.close()
