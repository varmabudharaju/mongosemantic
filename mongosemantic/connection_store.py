"""Persistent storage for the active MongoDB connection (URI + database).

Distinct from `mongosemantic.state.config_store` which stores per-collection
semantic-search configuration inside MongoDB itself. This module persists the
*connection identity* to disk so the web UI can manage it across restarts.

File location: $XDG_CONFIG_HOME/mongosemantic/config.json
                (or ~/.config/mongosemantic/config.json if XDG is unset).
File permissions: 0600 (only the owning user can read/write).
Directory permissions: 0700 (created if missing).
"""
from __future__ import annotations

import contextlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class SavedConnection:
    uri: str
    database: str
    saved_at: str  # ISO 8601


def config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path(os.environ.get("HOME", "")) / ".config"
    return root / "mongosemantic" / "config.json"


def load() -> SavedConnection | None:
    p = config_path()
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    uri = raw.get("uri")
    database = raw.get("database")
    saved_at = raw.get("saved_at", "")
    if not uri or not database:
        return None
    return SavedConnection(uri=uri, database=database, saved_at=saved_at)


def save(uri: str, database: str) -> None:
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.parent.chmod(0o700)
    payload = {
        "uri": uri,
        "database": database,
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }
    p.write_text(json.dumps(payload, indent=2))
    p.chmod(0o600)


def delete() -> None:
    """Remove the config file if it exists; silently ignore if absent."""
    with contextlib.suppress(FileNotFoundError):
        config_path().unlink()
