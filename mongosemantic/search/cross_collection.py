from __future__ import annotations

from pymongo.database import Database

from mongosemantic.state import list_configured


def min_max_normalize(rows: list[dict], score_key: str = "score") -> list[dict]:
    if not rows:
        return rows
    scores = [r.get(score_key, 0.0) for r in rows]
    lo, hi = min(scores), max(scores)
    if hi == lo:
        return rows
    for r in rows:
        r[score_key] = (r.get(score_key, 0.0) - lo) / (hi - lo)
    return rows

def per_collection_targets(db: Database) -> list[str]:
    return [cfg.collection for cfg in list_configured(db)]
