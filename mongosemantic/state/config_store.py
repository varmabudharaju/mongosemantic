from __future__ import annotations

from dataclasses import asdict, dataclass, field
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
    # v0.5.0: actual Atlas index names per field path. Optional for
    # back-compat with v0.4 and older configs — if empty, search code
    # falls back to computing the name from the field path.
    vector_index_names: dict[str, str] = field(default_factory=dict)
    search_index_names: dict[str, str] = field(default_factory=dict)
    migrated_at: datetime | None = None


# Fields added in later versions. When `load_config` reads an older config
# that's missing these, we substitute the dataclass default rather than crash.
_OPTIONAL_FIELDS = {
    "vector_index_names": dict,
    "search_index_names": dict,
    "migrated_at": type(None),
}


def save_config(db: Database, cfg: CollectionConfig) -> None:
    payload = asdict(cfg)
    payload["_id"] = cfg.collection
    db[CONFIG_COLLECTION].replace_one({"_id": cfg.collection}, payload, upsert=True)


def _hydrate(doc: dict) -> CollectionConfig:
    doc.pop("_id", None)
    fields = [FieldSpec(**f) for f in doc.pop("fields", [])]
    # Back-compat: drop any unknown keys; fill in any missing optional keys.
    known = set(CollectionConfig.__dataclass_fields__) - {"fields"}
    cleaned = {k: v for k, v in doc.items() if k in known}
    for optional in _OPTIONAL_FIELDS:
        cleaned.setdefault(optional, None)
        if cleaned[optional] is None and optional != "migrated_at":
            cleaned[optional] = {}
    return CollectionConfig(fields=fields, **cleaned)


def load_config(db: Database, collection: str) -> CollectionConfig | None:
    doc = db[CONFIG_COLLECTION].find_one({"_id": collection, "disabled": {"$ne": True}})
    if not doc:
        return None
    return _hydrate(doc)


def list_configured(db: Database) -> list[CollectionConfig]:
    return [_hydrate(doc) for doc in db[CONFIG_COLLECTION].find({"disabled": {"$ne": True}})]


def disable_config(db: Database, collection: str) -> None:
    db[CONFIG_COLLECTION].update_one(
        {"_id": collection}, {"$set": {"disabled": True, "updated_at": datetime.utcnow()}}
    )
