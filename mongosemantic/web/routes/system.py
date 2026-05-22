from __future__ import annotations

import os
import time
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from mongosemantic import connection_store
from mongosemantic.config import Settings
from mongosemantic.db.client import MongoConnection
from mongosemantic.state import list_configured
from mongosemantic.web.connection_errors import (
    ConnectionError,
    map_exception,
    validate_uri_prefix,
)

router = APIRouter()


# -- Existing endpoint kept for backward compatibility --

@router.get("/api/topology")
def topology() -> dict:
    settings = Settings.from_environment()
    conn = MongoConnection.open(settings.uri, settings.database)
    try:
        return {"topology": conn.topology.value}
    finally:
        conn.close()


# -- New connection endpoints --

class SaveRequest(BaseModel):
    uri: str
    database: str


class UriRequest(BaseModel):
    uri: str


def _redact(uri: str) -> str:
    """Mask credentials. mongodb+srv://user:pass@host -> mongodb+srv://<redacted>@host."""
    if "@" not in uri:
        return uri
    scheme, rest = uri.split("://", 1)
    creds, host = rest.rsplit("@", 1)
    if ":" in creds:
        return f"{scheme}://<redacted>@{host}"
    return uri  # no creds to redact


def _scrub(details: str, uri: str) -> str:
    """Replace a known URI inside arbitrary exception text with its redacted form.

    Defends against PyMongo exception reprs that may echo back the URI we
    passed in. Empirically the current PyMongo versions don't do this for the
    error paths we map, but the cost of the scrub is trivial and the cost of
    a credentials leak is high.
    """
    if not uri or uri not in details:
        return details
    return details.replace(uri, _redact(uri))


def _env_overrides() -> dict:
    return {
        "uri": bool(os.environ.get("MONGOSEMANTIC_URI")),
        "db": bool(os.environ.get("MONGOSEMANTIC_DB")),
        "model": bool(os.environ.get("MONGOSEMANTIC_MODEL")),
    }


def _err(err: ConnectionError, uri: str = "") -> dict:
    return {
        "ok": False,
        "error": {
            "code": err.code,
            "message": err.message,
            "hint": err.hint,
            "details": _scrub(err.details, uri),
        },
    }


def _missing_database_err() -> dict:
    return {
        "ok": False,
        "error": {
            "code": "missing_database",
            "message": "Database name is required.",
            "hint": "Enter the database name to use after connecting.",
            "details": "",
        },
    }


@router.get("/api/connection")
def get_connection() -> dict:
    settings = Settings.try_from_environment()
    env_overrides = _env_overrides()
    if settings is None:
        return {
            "state": "not_connected",
            "uri_redacted": "",
            "database": "",
            "topology": None,
            "mongo_version": None,
            "model": os.environ.get("MONGOSEMANTIC_MODEL", "local-fast"),
            "configured_count": 0,
            "env_overrides": env_overrides,
        }

    state: Literal["connected_ui", "connected_env"] = (
        "connected_env" if settings.source == "env" else "connected_ui"
    )

    try:
        conn = MongoConnection.open(settings.uri, settings.database)
    except Exception as exc:  # noqa: BLE001
        err = map_exception(exc)
        return {
            "state": state,
            "uri_redacted": _redact(settings.uri),
            "database": settings.database,
            "topology": None,
            "mongo_version": None,
            "model": settings.model,
            "configured_count": 0,
            "env_overrides": env_overrides,
            "warning": {"code": err.code, "message": err.message, "hint": err.hint},
        }

    try:
        info = conn.client.server_info()
        configured_count = sum(1 for _ in list_configured(conn.db))
        return {
            "state": state,
            "uri_redacted": _redact(settings.uri),
            "database": settings.database,
            "topology": conn.topology.value,
            "mongo_version": info.get("version", ""),
            "model": settings.model,
            "configured_count": configured_count,
            "env_overrides": env_overrides,
        }
    finally:
        conn.close()


@router.post("/api/connection/save")
def save_connection(req: SaveRequest) -> dict:
    prefix_err = validate_uri_prefix(req.uri)
    if prefix_err is not None:
        return _err(prefix_err)
    if not req.database.strip():
        return _missing_database_err()

    try:
        conn = MongoConnection.open(req.uri, req.database)
    except Exception as exc:  # noqa: BLE001
        return _err(map_exception(exc), uri=req.uri)

    try:
        info = conn.client.server_info()
        topology = conn.topology.value
        mongo_version = info.get("version", "")
    finally:
        conn.close()

    connection_store.save(req.uri, req.database)
    return {
        "ok": True,
        "topology": topology,
        "mongo_version": mongo_version,
        "restart_required": True,
    }


@router.post("/api/connection/test")
def test_connection() -> dict:
    settings = Settings.try_from_environment()
    if settings is None:
        return {
            "ok": False,
            "error": {
                "code": "not_connected",
                "message": "No connection configured.",
                "hint": "Save a connection first.",
                "details": "",
            },
        }
    start = time.monotonic()
    try:
        conn = MongoConnection.open(settings.uri, settings.database)
    except Exception as exc:  # noqa: BLE001
        return _err(map_exception(exc), uri=settings.uri)
    try:
        info = conn.client.server_info()
    finally:
        conn.close()
    latency_ms = int((time.monotonic() - start) * 1000)
    return {
        "ok": True,
        "latency_ms": latency_ms,
        "mongo_version": info.get("version", ""),
    }


@router.delete("/api/connection")
def delete_connection() -> dict:
    connection_store.delete()
    return {"ok": True, "restart_required": True}


@router.get("/api/connection/config-path")
def connection_config_path() -> dict:
    return {"path": str(connection_store.config_path())}


# System databases excluded from the picker by default — users rarely want them.
_HIDDEN_DBS = {"admin", "local", "config"}


@router.post("/api/connection/list-databases")
def list_databases(req: UriRequest) -> dict:
    """Open a temp connection and return the cluster's user-visible databases.

    Used by the Connection page to populate a database picker after the user
    pastes a URI. The URI is NOT saved here — this is a probe.
    """
    prefix_err = validate_uri_prefix(req.uri)
    if prefix_err is not None:
        return _err(prefix_err)

    try:
        conn = MongoConnection.open(req.uri, "admin")
    except Exception as exc:  # noqa: BLE001
        return _err(map_exception(exc), uri=req.uri)

    try:
        result = conn.client.admin.command("listDatabases", nameOnly=True)
        names = [
            d["name"] for d in result.get("databases", [])
            if d.get("name") not in _HIDDEN_DBS
        ]
        return {
            "ok": True,
            "databases": sorted(names),
            "default": connection_store.extract_path_database(req.uri),
        }
    except Exception as exc:  # noqa: BLE001
        # Some users have connect access but no listDatabases privilege; let
        # the client fall back to a text input gracefully.
        return _err(map_exception(exc), uri=req.uri)
    finally:
        conn.close()
