from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Literal

from pymongo.database import Database

CONFIG_COLLECTION = "mongosemantic_config"

@dataclass
class FieldSpec:
    path: str
    chunked: bool = False
    chunk_size: int = 512
    chunk_overlap: int = 64

@dataclass
class CollectionConfig:
    collection: str
    mode: Literal["shadow", "inline"]
    shadow_collection: str | None
    fields: list[FieldSpec]
    embedding_model: str
    embedding_dim: int
    created_at: datetime
    updated_at: datetime
    disabled: bool = False

def save_config(db: Database, cfg: CollectionConfig) -> None:
    payload = asdict(cfg)
    payload["_id"] = cfg.collection
    db[CONFIG_COLLECTION].replace_one({"_id": cfg.collection}, payload, upsert=True)

def load_config(db: Database, collection: str) -> CollectionConfig | None:
    doc = db[CONFIG_COLLECTION].find_one({"_id": collection, "disabled": {"$ne": True}})
    if not doc:
        return None
    doc.pop("_id", None)
    fields = [FieldSpec(**f) for f in doc.pop("fields", [])]
    return CollectionConfig(fields=fields, **doc)

def list_configured(db: Database) -> list[CollectionConfig]:
    out = []
    for doc in db[CONFIG_COLLECTION].find({"disabled": {"$ne": True}}):
        doc.pop("_id", None)
        fields = [FieldSpec(**f) for f in doc.pop("fields", [])]
        out.append(CollectionConfig(fields=fields, **doc))
    return out

def disable_config(db: Database, collection: str) -> None:
    db[CONFIG_COLLECTION].update_one(
        {"_id": collection}, {"$set": {"disabled": True, "updated_at": datetime.utcnow()}}
    )
