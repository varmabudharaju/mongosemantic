from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pymongo.database import Database

STATE_COLLECTION = "mongosemantic_state"

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)

def save_resume_token(db: Database, token: dict) -> None:
    db[STATE_COLLECTION].update_one(
        {"_id": "change_stream"},
        {"$set": {"token": token, "updated_at": _utcnow()}},
        upsert=True,
    )

def load_resume_token(db: Database) -> dict | None:
    doc = db[STATE_COLLECTION].find_one({"_id": "change_stream"})
    return doc.get("token") if doc else None

def save_polling_watermark(db: Database, collection: str, watermark: Any) -> None:
    db[STATE_COLLECTION].update_one(
        {"_id": f"polling:{collection}"},
        {"$set": {"watermark": watermark, "updated_at": _utcnow()}},
        upsert=True,
    )

def load_polling_watermark(db: Database, collection: str) -> Any | None:
    doc = db[STATE_COLLECTION].find_one({"_id": f"polling:{collection}"})
    return doc.get("watermark") if doc else None
