# Connection page overhaul — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current Connection page with a three-state UI (not connected / connected via UI-saved config / connected via env override) backed by a persistent JSON config file at `~/.config/mongosemantic/config.json`, with explicit error mapping and dev help.

**Architecture:** A new pure `connection_store` module owns the JSON file. `Settings.from_environment()` adds a config-file fallback below the existing env-var layer. The web UI gains four new `/api/connection*` endpoints; existing `POST /api/connect` is removed. Frontend renders one of three states based on `GET /api/connection`. The running server keeps using its launch-time connection; all saves require a restart of `mongosemantic ui` (and `worker`) — clearly surfaced.

**Tech Stack:** Python 3.11, FastAPI, PyMongo, vanilla JS frontend (no build step). Tests use pytest + mongomock for unit, real Atlas for integration.

**Spec:** `docs/superpowers/specs/2026-05-20-connection-page-design.md`

---

## Task 0: Branch setup

**Files:** none

- [ ] **Step 1: Create feature branch off main**

```bash
git checkout main
git pull --ff-only
git checkout -b feat/connection-page
```

- [ ] **Step 2: Confirm clean baseline**

Run: `python3 -m pytest tests/unit -q && ruff check .`
Expected: all green, no warnings.

---

## Task 1: `connection_store` — load/save/delete config file

**Files:**
- Create: `mongosemantic/connection_store.py`
- Test: `tests/unit/test_connection_store.py`

(Distinct from the existing `mongosemantic/state/config_store.py` which holds per-collection MongoDB configs — that module is unrelated.)

- [ ] **Step 1: Write the failing tests**

Write to `tests/unit/test_connection_store.py`:

```python
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from mongosemantic.connection_store import (
    SavedConnection,
    config_path,
    delete,
    load,
    save,
)


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Redirect XDG_CONFIG_HOME so the test never touches the real ~/.config."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return tmp_path


def test_config_path_uses_xdg(isolated_home):
    p = config_path()
    assert p == isolated_home / "mongosemantic" / "config.json"


def test_config_path_falls_back_to_home(tmp_path, monkeypatch):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    p = config_path()
    assert p == tmp_path / ".config" / "mongosemantic" / "config.json"


def test_load_missing_returns_none(isolated_home):
    assert load() is None


def test_save_writes_file_with_0600(isolated_home):
    save("mongodb+srv://u:p@cluster.mongodb.net/", "mydb")
    p = config_path()
    assert p.exists()
    mode = stat.S_IMODE(p.stat().st_mode)
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"


def test_save_creates_parent_dir_with_0700(isolated_home):
    save("mongodb+srv://u:p@cluster.mongodb.net/", "mydb")
    parent = config_path().parent
    mode = stat.S_IMODE(parent.stat().st_mode)
    assert mode == 0o700, f"expected 0700, got {oct(mode)}"


def test_roundtrip(isolated_home):
    save("mongodb+srv://u:p@cluster.mongodb.net/", "mydb")
    sc = load()
    assert isinstance(sc, SavedConnection)
    assert sc.uri == "mongodb+srv://u:p@cluster.mongodb.net/"
    assert sc.database == "mydb"
    assert sc.saved_at  # ISO 8601 string, non-empty


def test_overwrite(isolated_home):
    save("mongodb+srv://u1:p@c.mongodb.net/", "db1")
    save("mongodb+srv://u2:p@c.mongodb.net/", "db2")
    sc = load()
    assert sc.uri == "mongodb+srv://u2:p@c.mongodb.net/"
    assert sc.database == "db2"


def test_delete_removes_file(isolated_home):
    save("mongodb+srv://u:p@cluster.mongodb.net/", "mydb")
    delete()
    assert not config_path().exists()
    assert load() is None


def test_delete_is_idempotent(isolated_home):
    delete()  # nothing exists yet
    delete()  # still nothing
    assert load() is None


def test_load_malformed_returns_none(isolated_home):
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{ not json")
    p.chmod(0o600)
    assert load() is None


def test_load_partial_returns_none(isolated_home):
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"uri": "mongodb://x"}))  # missing database
    p.chmod(0o600)
    assert load() is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/unit/test_connection_store.py -v`
Expected: `ImportError: cannot import name 'SavedConnection' from 'mongosemantic.connection_store'` (module doesn't exist yet).

- [ ] **Step 3: Implement the module**

Write to `mongosemantic/connection_store.py`:

```python
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
    if base:
        root = Path(base)
    else:
        root = Path(os.environ.get("HOME", "")) / ".config"
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
    p = config_path()
    try:
        p.unlink()
    except FileNotFoundError:
        pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/unit/test_connection_store.py -v`
Expected: 10 passed.

- [ ] **Step 5: Lint and commit**

```bash
ruff check mongosemantic/connection_store.py tests/unit/test_connection_store.py
git add mongosemantic/connection_store.py tests/unit/test_connection_store.py
git commit -m "feat(connection-store): persist URI+database to ~/.config"
```

---

## Task 2: `Settings.from_environment()` — env var > config file precedence

**Files:**
- Modify: `mongosemantic/config.py`
- Test: `tests/unit/test_config.py` (extend existing file)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_config.py` (or create if absent):

```python
import pytest

from mongosemantic.config import Settings
from mongosemantic import connection_store


@pytest.fixture
def clean_env(monkeypatch):
    for k in ("MONGOSEMANTIC_URI", "MONGOSEMANTIC_DB", "MONGOSEMANTIC_MODEL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("MONGOSEMANTIC_MODEL", "local-fast")
    yield monkeypatch


@pytest.fixture
def isolated_xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    yield tmp_path


def test_from_environment_uses_env_var(clean_env, isolated_xdg):
    clean_env.setenv("MONGOSEMANTIC_URI", "mongodb://env-host/")
    clean_env.setenv("MONGOSEMANTIC_DB", "env_db")
    s = Settings.from_environment()
    assert s.uri == "mongodb://env-host/"
    assert s.database == "env_db"
    assert s.source == "env"


def test_from_environment_falls_back_to_file(clean_env, isolated_xdg):
    connection_store.save("mongodb://file-host/", "file_db")
    s = Settings.from_environment()
    assert s.uri == "mongodb://file-host/"
    assert s.database == "file_db"
    assert s.source == "file"


def test_from_environment_env_wins_over_file(clean_env, isolated_xdg):
    connection_store.save("mongodb://file-host/", "file_db")
    clean_env.setenv("MONGOSEMANTIC_URI", "mongodb://env-host/")
    clean_env.setenv("MONGOSEMANTIC_DB", "env_db")
    s = Settings.from_environment()
    assert s.uri == "mongodb://env-host/"
    assert s.source == "env"


def test_from_environment_raises_when_neither(clean_env, isolated_xdg):
    with pytest.raises(ValueError, match="MONGOSEMANTIC_URI is required"):
        Settings.from_environment()


def test_try_from_environment_returns_none_when_neither(clean_env, isolated_xdg):
    assert Settings.try_from_environment() is None


def test_try_from_environment_returns_settings_when_file(clean_env, isolated_xdg):
    connection_store.save("mongodb://file-host/", "file_db")
    s = Settings.try_from_environment()
    assert s is not None
    assert s.source == "file"


def test_legacy_settings_constructor_still_works(clean_env, isolated_xdg):
    # Existing call-sites construct Settings() directly. That path must keep
    # working with just env vars (no file fallback).
    clean_env.setenv("MONGOSEMANTIC_URI", "mongodb://env-host/")
    clean_env.setenv("MONGOSEMANTIC_DB", "env_db")
    s = Settings()
    assert s.uri == "mongodb://env-host/"
    assert s.source == "env"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/unit/test_config.py -v -k from_environment`
Expected: AttributeError — `from_environment` / `try_from_environment` / `source` do not exist.

- [ ] **Step 3: Modify `mongosemantic/config.py`**

Replace the entire contents with:

```python
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal

from mongosemantic import connection_store

KNOWN_MODELS: tuple[str, ...] = (
    "local-fast",
    "local-better",
    "openai-small",
    "openai-large",
    "ollama-nomic",
)

MODEL_DIMS: dict[str, int] = {
    "local-fast": 384,
    "local-better": 768,
    "openai-small": 1536,
    "openai-large": 3072,
    "ollama-nomic": 768,
}

Source = Literal["env", "file", "none"]


@dataclass
class Settings:
    uri: str = field(default_factory=lambda: os.environ.get("MONGOSEMANTIC_URI", ""))
    database: str = field(default_factory=lambda: os.environ.get("MONGOSEMANTIC_DB", ""))
    model: str = field(default_factory=lambda: os.environ.get("MONGOSEMANTIC_MODEL", "local-fast"))
    batch_size: int = field(
        default_factory=lambda: int(os.environ.get("MONGOSEMANTIC_BATCH_SIZE", "32"))
    )
    poll_interval_seconds: int = field(
        default_factory=lambda: int(os.environ.get("MONGOSEMANTIC_POLL_INTERVAL_SECONDS", "30"))
    )
    openai_api_key: str = field(default_factory=lambda: os.environ.get("OPENAI_API_KEY", ""))
    ollama_host: str = field(
        default_factory=lambda: os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    )
    source: Source = "env"

    def __post_init__(self) -> None:
        if not self.uri:
            raise ValueError("MONGOSEMANTIC_URI is required")
        if not (self.uri.startswith("mongodb://") or self.uri.startswith("mongodb+srv://")):
            raise ValueError("MONGOSEMANTIC_URI must start with mongodb:// or mongodb+srv://")
        if self.model not in KNOWN_MODELS:
            raise ValueError(
                f"Unknown model '{self.model}'. Expected one of: {', '.join(KNOWN_MODELS)}"
            )
        if not self.database:
            raise ValueError("MONGOSEMANTIC_DB is required")

    @classmethod
    def from_environment(cls) -> Settings:
        """Layer env vars over the saved config file.

        Precedence (highest first):
          1. MONGOSEMANTIC_URI / MONGOSEMANTIC_DB env vars (source="env")
          2. ~/.config/mongosemantic/config.json                (source="file")
          3. raise ValueError                                   (no source available)
        """
        env_uri = os.environ.get("MONGOSEMANTIC_URI", "")
        env_db = os.environ.get("MONGOSEMANTIC_DB", "")
        if env_uri and env_db:
            return cls(uri=env_uri, database=env_db, source="env")

        saved = connection_store.load()
        if saved is not None:
            return cls(uri=saved.uri, database=saved.database, source="file")

        # Fall through: rely on Settings() to raise the canonical error.
        return cls()  # source defaults to "env"; will raise in __post_init__

    @classmethod
    def try_from_environment(cls) -> Settings | None:
        """Like from_environment but returns None instead of raising.

        Used by routes that need to detect "not connected" cleanly.
        """
        try:
            return cls.from_environment()
        except ValueError:
            return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/unit/test_config.py -v`
Expected: all tests pass (existing + new).

- [ ] **Step 5: Lint and commit**

```bash
ruff check mongosemantic/config.py tests/unit/test_config.py
git add mongosemantic/config.py tests/unit/test_config.py
git commit -m "feat(config): Settings.from_environment falls back to saved config file"
```

---

## Task 3: `connection_errors` — friendly error mapping

**Files:**
- Create: `mongosemantic/web/connection_errors.py`
- Test: `tests/unit/test_connection_errors.py`

- [ ] **Step 1: Write the failing tests**

Write to `tests/unit/test_connection_errors.py`:

```python
from __future__ import annotations

import socket

import pytest
from pymongo.errors import (
    ConfigurationError,
    OperationFailure,
    ServerSelectionTimeoutError,
)

from mongosemantic.web.connection_errors import map_exception, validate_uri_prefix


def test_validate_uri_prefix_accepts_mongodb():
    err = validate_uri_prefix("mongodb://x/")
    assert err is None


def test_validate_uri_prefix_accepts_srv():
    err = validate_uri_prefix("mongodb+srv://x.mongodb.net/")
    assert err is None


def test_validate_uri_prefix_rejects_other():
    err = validate_uri_prefix("http://example.com")
    assert err is not None
    assert err.code == "bad_scheme"
    assert "mongodb://" in err.message


def test_validate_uri_prefix_rejects_empty():
    err = validate_uri_prefix("")
    assert err is not None
    assert err.code == "bad_scheme"


def test_map_auth_failed():
    exc = OperationFailure("auth failed", code=18)
    err = map_exception(exc)
    assert err.code == "auth_failed"
    assert "rejected" in err.message.lower()
    assert "URL-encode" in err.hint


def test_map_malformed_uri():
    exc = ConfigurationError("Empty host (or empty string) is not allowed")
    err = map_exception(exc)
    assert err.code == "malformed_uri"
    assert "parse" in err.message.lower()


def test_map_dns_failure_via_gaierror():
    exc = socket.gaierror(-2, "Name or service not known")
    err = map_exception(exc)
    assert err.code == "dns_failure"


def test_map_dns_failure_via_configuration_srv():
    exc = ConfigurationError(
        "All nameservers failed to answer the SRV query for _mongodb._tcp.x.mongodb.net"
    )
    err = map_exception(exc)
    assert err.code == "dns_failure"


def test_map_ip_not_allowlisted():
    exc = ServerSelectionTimeoutError(
        "No replica set members available: connection refused; "
        "your IP that isn't whitelisted in atlas"
    )
    err = map_exception(exc)
    assert err.code == "ip_not_allowlisted"
    assert "Network Access" in err.hint


def test_map_generic_timeout():
    exc = ServerSelectionTimeoutError("No servers found yet")
    err = map_exception(exc)
    assert err.code == "timeout"
    assert "5 seconds" in err.message


def test_map_tls_failure():
    exc = ServerSelectionTimeoutError(
        "SSL handshake failed: certificate verify failed"
    )
    err = map_exception(exc)
    assert err.code == "tls_failure"
    assert "certifi" in err.hint


def test_map_db_not_readable():
    exc = OperationFailure("not authorized on mydb to execute command", code=13)
    err = map_exception(exc)
    assert err.code == "db_not_readable"
    assert "roles" in err.hint.lower()


def test_map_unknown_exception():
    exc = RuntimeError("totally unexpected")
    err = map_exception(exc)
    assert err.code == "unknown"
    assert "RuntimeError" in err.message
    assert "totally unexpected" in err.details


def test_details_contains_repr():
    exc = OperationFailure("auth failed", code=18)
    err = map_exception(exc)
    assert "OperationFailure" in err.details
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/unit/test_connection_errors.py -v`
Expected: ImportError — module doesn't exist.

- [ ] **Step 3: Implement the module**

Write to `mongosemantic/web/connection_errors.py`:

```python
"""Map low-level PyMongo / socket exceptions to user-facing connection errors.

The web UI's Connection page renders these with a message, a hint, and a
"Show technical details" disclosure containing the raw `repr(exc)`.
"""
from __future__ import annotations

import socket
from dataclasses import dataclass

from pymongo.errors import (
    ConfigurationError,
    OperationFailure,
    ServerSelectionTimeoutError,
)


@dataclass(frozen=True)
class ConnectionError:
    code: str
    message: str
    hint: str
    details: str


def validate_uri_prefix(uri: str) -> ConnectionError | None:
    if uri.startswith("mongodb://") or uri.startswith("mongodb+srv://"):
        return None
    return ConnectionError(
        code="bad_scheme",
        message="URI must start with mongodb:// or mongodb+srv://.",
        hint="Copy it from Atlas → Connect → Drivers.",
        details=f"got: {uri[:40]!r}",
    )


def map_exception(exc: BaseException) -> ConnectionError:
    msg = str(exc)
    details = repr(exc)

    # Authentication errors (OperationFailure code 18)
    if isinstance(exc, OperationFailure):
        if getattr(exc, "code", None) == 18 or "authentication failed" in msg.lower():
            return ConnectionError(
                code="auth_failed",
                message="Username or password rejected.",
                hint=(
                    "Atlas: check Database Access. URL-encode special "
                    "characters in the password (e.g. @ → %40)."
                ),
                details=details,
            )
        if "not authorized" in msg.lower():
            return ConnectionError(
                code="db_not_readable",
                message=(
                    "Connected to the cluster, but the database is not "
                    "readable with these credentials."
                ),
                hint="Check the database user's roles (needs read on this database).",
                details=details,
            )

    # DNS failures (raw gaierror or SRV lookup wrapped in ConfigurationError)
    if isinstance(exc, socket.gaierror):
        return ConnectionError(
            code="dns_failure",
            message="Can't resolve the cluster hostname.",
            hint="Check the URI for typos, or your network/DNS.",
            details=details,
        )
    if isinstance(exc, ConfigurationError):
        if "SRV" in msg or "nameservers" in msg.lower():
            return ConnectionError(
                code="dns_failure",
                message="Can't resolve the cluster hostname.",
                hint="Check the URI for typos, or your network/DNS.",
                details=details,
            )
        if "empty host" in msg.lower() or "parse" in msg.lower():
            return ConnectionError(
                code="malformed_uri",
                message="Couldn't parse the URI. Check for missing characters around @ or the host.",
                hint="Format: mongodb+srv://user:pass@cluster.mongodb.net/",
                details=details,
            )
        return ConnectionError(
            code="malformed_uri",
            message="Couldn't parse the URI.",
            hint="Format: mongodb+srv://user:pass@cluster.mongodb.net/",
            details=details,
        )

    # Server selection timeouts — narrow by sub-pattern
    if isinstance(exc, ServerSelectionTimeoutError):
        low = msg.lower()
        if "isn't whitelisted" in low or "ip not in whitelist" in low or "ip allowlist" in low:
            return ConnectionError(
                code="ip_not_allowlisted",
                message="Atlas refused the connection — your current IP isn't allowlisted.",
                hint="Add it under Atlas → Network Access, then try again.",
                details=details,
            )
        if "ssl" in low or "tls" in low or "certificate" in low:
            return ConnectionError(
                code="tls_failure",
                message="TLS handshake failed.",
                hint=(
                    "mongosemantic uses the certifi CA bundle by default. "
                    "If you're behind a corporate proxy, set SSL_CERT_FILE to your proxy's CA."
                ),
                details=details,
            )
        return ConnectionError(
            code="timeout",
            message="Couldn't reach the cluster within 5 seconds.",
            hint=(
                "Common causes: cluster paused, IP not in Atlas Network Access, "
                "firewall blocking port 27017."
            ),
            details=details,
        )

    return ConnectionError(
        code="unknown",
        message=f"{type(exc).__name__}: {msg}",
        hint="See technical details below.",
        details=details,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/unit/test_connection_errors.py -v`
Expected: 14 passed.

- [ ] **Step 5: Lint and commit**

```bash
ruff check mongosemantic/web/connection_errors.py tests/unit/test_connection_errors.py
git add mongosemantic/web/connection_errors.py tests/unit/test_connection_errors.py
git commit -m "feat(web): map PyMongo exceptions to friendly connection errors"
```

---

## Task 4: New `/api/connection*` routes

**Files:**
- Modify: `mongosemantic/web/routes/system.py`
- Test: `tests/unit/test_route_system.py` (extend)

- [ ] **Step 1: Read the existing test file to understand its patterns**

Run: `cat tests/unit/test_route_system.py | head -80`
This file already exists for the current `/api/topology` and `/api/connect` endpoints. Reuse its TestClient fixture.

- [ ] **Step 2: Write the failing tests**

Append to `tests/unit/test_route_system.py`:

```python
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from mongosemantic import connection_store
from mongosemantic.web.app import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    for k in ("MONGOSEMANTIC_URI", "MONGOSEMANTIC_DB"):
        monkeypatch.delenv(k, raising=False)
    return TestClient(create_app())


def test_connection_state_not_connected(client):
    r = client.get("/api/connection")
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "not_connected"
    assert body["env_overrides"]["uri"] is False


def test_connection_state_connected_env(client, monkeypatch):
    monkeypatch.setenv("MONGOSEMANTIC_URI", "mongodb+srv://u:p@cluster.mongodb.net/")
    monkeypatch.setenv("MONGOSEMANTIC_DB", "mydb")

    class FakeConn:
        topology = type("T", (), {"value": "atlas"})()
        @classmethod
        def open(cls, uri, db):
            inst = cls()
            inst.client = type("C", (), {
                "server_info": staticmethod(lambda: {"version": "8.0.23"}),
                "close": lambda self=None: None,
            })()
            return inst
        def close(self): pass

    with patch("mongosemantic.web.routes.system.MongoConnection", FakeConn):
        r = client.get("/api/connection")
    body = r.json()
    assert body["state"] == "connected_env"
    assert body["env_overrides"]["uri"] is True
    assert "<redacted>" in body["uri_redacted"]
    assert body["database"] == "mydb"


def test_connection_state_connected_ui(client):
    connection_store.save("mongodb+srv://u:p@cluster.mongodb.net/", "filedb")

    class FakeConn:
        topology = type("T", (), {"value": "atlas"})()
        @classmethod
        def open(cls, uri, db):
            inst = cls()
            inst.client = type("C", (), {
                "server_info": staticmethod(lambda: {"version": "8.0.23"}),
                "close": lambda self=None: None,
            })()
            return inst
        def close(self): pass

    with patch("mongosemantic.web.routes.system.MongoConnection", FakeConn):
        r = client.get("/api/connection")
    body = r.json()
    assert body["state"] == "connected_ui"
    assert body["database"] == "filedb"


def test_save_writes_config_on_success(client):
    class FakeConn:
        topology = type("T", (), {"value": "atlas"})()
        @classmethod
        def open(cls, uri, db):
            inst = cls()
            inst.client = type("C", (), {
                "server_info": staticmethod(lambda: {"version": "8.0.23"}),
                "close": lambda self=None: None,
            })()
            return inst
        def close(self): pass

    with patch("mongosemantic.web.routes.system.MongoConnection", FakeConn):
        r = client.post(
            "/api/connection/save",
            json={"uri": "mongodb+srv://u:p@cluster.mongodb.net/", "database": "newdb"},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["restart_required"] is True
    saved = connection_store.load()
    assert saved is not None
    assert saved.database == "newdb"


def test_save_does_not_write_on_failure(client):
    def fake_open(uri, db):
        from pymongo.errors import OperationFailure
        raise OperationFailure("auth failed", code=18)

    with patch(
        "mongosemantic.web.routes.system.MongoConnection.open",
        side_effect=fake_open,
    ):
        r = client.post(
            "/api/connection/save",
            json={"uri": "mongodb+srv://u:p@cluster.mongodb.net/", "database": "newdb"},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "auth_failed"
    assert connection_store.load() is None


def test_save_rejects_bad_scheme(client):
    r = client.post(
        "/api/connection/save",
        json={"uri": "http://x", "database": "db"},
    )
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "bad_scheme"


def test_save_rejects_empty_database(client):
    r = client.post(
        "/api/connection/save",
        json={"uri": "mongodb+srv://u:p@cluster.mongodb.net/", "database": ""},
    )
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "bad_scheme" or body["error"]["code"] == "malformed_uri"
    # actual code: we use a dedicated "missing_database" check, see impl


def test_delete_removes_config(client):
    connection_store.save("mongodb+srv://u:p@c.mongodb.net/", "db")
    r = client.delete("/api/connection")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["restart_required"] is True
    assert connection_store.load() is None


def test_test_connection_pings_active(client, monkeypatch):
    monkeypatch.setenv("MONGOSEMANTIC_URI", "mongodb+srv://u:p@cluster.mongodb.net/")
    monkeypatch.setenv("MONGOSEMANTIC_DB", "mydb")

    class FakeConn:
        topology = type("T", (), {"value": "atlas"})()
        @classmethod
        def open(cls, uri, db):
            inst = cls()
            inst.client = type("C", (), {
                "server_info": staticmethod(lambda: {"version": "8.0.23"}),
                "close": lambda self=None: None,
                "admin": type("A", (), {
                    "command": staticmethod(lambda cmd: {"ok": 1.0}),
                })(),
            })()
            return inst
        def close(self): pass

    with patch("mongosemantic.web.routes.system.MongoConnection", FakeConn):
        r = client.post("/api/connection/test")
    body = r.json()
    assert body["ok"] is True
    assert "latency_ms" in body
    assert body["mongo_version"] == "8.0.23"


def test_test_connection_returns_error_when_unreachable(client, monkeypatch):
    monkeypatch.setenv("MONGOSEMANTIC_URI", "mongodb+srv://u:p@cluster.mongodb.net/")
    monkeypatch.setenv("MONGOSEMANTIC_DB", "mydb")

    def fake_open(uri, db):
        from pymongo.errors import ServerSelectionTimeoutError
        raise ServerSelectionTimeoutError("No servers found yet")

    with patch(
        "mongosemantic.web.routes.system.MongoConnection.open",
        side_effect=fake_open,
    ):
        r = client.post("/api/connection/test")
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "timeout"
```

A missing-database check: we want a dedicated error code. Adjust `test_save_rejects_empty_database` to match the impl below — change the assertion to:

```python
    assert body["error"]["code"] == "missing_database"
```

Then add a `missing_database` branch to `connection_errors.py` if not present. Actually, simpler: validate it in the route and return a hand-rolled error dict without going through `connection_errors`.

- [ ] **Step 3: Run tests to verify they fail**

Run: `python3 -m pytest tests/unit/test_route_system.py -v -k "connection or save or delete or test_connection"`
Expected: route 404s or import errors.

- [ ] **Step 4: Replace `mongosemantic/web/routes/system.py`**

Write the full file contents:

```python
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


def _redact(uri: str) -> str:
    """Mask credentials. mongodb+srv://user:pass@host -> mongodb+srv://<redacted>@host."""
    if "@" not in uri:
        return uri
    scheme, rest = uri.split("://", 1)
    creds, host = rest.split("@", 1)
    if ":" in creds:
        return f"{scheme}://<redacted>@{host}"
    return uri  # no creds to redact


def _env_overrides() -> dict:
    return {
        "uri": bool(os.environ.get("MONGOSEMANTIC_URI")),
        "db": bool(os.environ.get("MONGOSEMANTIC_DB")),
        "model": bool(os.environ.get("MONGOSEMANTIC_MODEL")),
    }


def _err(err: ConnectionError) -> dict:
    return {
        "ok": False,
        "error": {
            "code": err.code,
            "message": err.message,
            "hint": err.hint,
            "details": err.details,
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
        return _err(map_exception(exc))

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
        return _err(map_exception(exc))
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
```

Note: the old `POST /api/connect` is deleted entirely.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/unit/test_route_system.py -v`
Expected: all green. If the original `test_connect_*` tests in this file referenced the removed endpoint, delete those tests.

- [ ] **Step 6: Lint and commit**

```bash
ruff check mongosemantic/web/routes/system.py tests/unit/test_route_system.py
git add mongosemantic/web/routes/system.py tests/unit/test_route_system.py
git commit -m "feat(api): /api/connection get|save|test|delete endpoints"
```

---

## Task 5: Route + worker callers — `Settings()` → `Settings.from_environment()`

**Files:**
- Modify: `mongosemantic/web/routes/aggregation.py`
- Modify: `mongosemantic/web/routes/collections.py`
- Modify: `mongosemantic/web/routes/dashboard.py`
- Modify: `mongosemantic/web/routes/index.py`
- Modify: `mongosemantic/web/routes/migrate.py`
- Modify: `mongosemantic/web/routes/search.py`
- Modify: `mongosemantic/web/routes/visualize.py`
- Modify: `mongosemantic/web/routes/apply.py`
- Modify: `mongosemantic/commands/worker_cmd.py`

These call-sites must read the file-saved config too, not just env vars.

CLI commands (`mongosemantic/commands/{apply,index,inspect,integrate,migrate,reindex,retry,search,status,teardown}.py`) keep `Settings()` — they're terminal-driven and env-var-based.

- [ ] **Step 1: Mechanical replacement in route files**

Each of the route files listed under "Files" above contains exactly one or more lines of the form `settings = Settings()`. Replace each with `settings = Settings.from_environment()`.

For each file, run:
```bash
sed -i '' 's/settings = Settings()/settings = Settings.from_environment()/g' <file>
```

Or use Edit per file.

- [ ] **Step 2: Same replacement in worker**

In `mongosemantic/commands/worker_cmd.py` line 32, change `settings = Settings()` to `settings = Settings.from_environment()`.

- [ ] **Step 3: Run full unit test suite**

Run: `python3 -m pytest tests/unit -q`
Expected: all pass. The mechanical change doesn't affect behavior when env vars are set (which the tests assume).

- [ ] **Step 4: Lint**

Run: `ruff check .`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add mongosemantic/web/routes/ mongosemantic/commands/worker_cmd.py
git commit -m "refactor: route+worker call-sites use Settings.from_environment"
```

---

## Task 6: Frontend copy in `content.py`

**Files:**
- Modify: `mongosemantic/web/content.py`

- [ ] **Step 1: Replace the `connection` block**

In `mongosemantic/web/content.py`, replace the existing `"connection": { ... }` dict (lines 10–26) with:

```python
    "connection": {
        # Page chrome
        "title": "Connection",
        "subtitle_disconnected": "Connect mongosemantic to a MongoDB database to get started.",
        "subtitle_connected_ui": "mongosemantic is connected to the database below.",
        "subtitle_connected_env": "mongosemantic is connected via the MONGOSEMANTIC_URI environment variable.",

        # First-run hero
        "hero_title": "Not connected yet",
        "hero_body": "Paste a MongoDB URI below to get started.",

        # Form
        "uri_label": "MongoDB URI",
        "uri_placeholder": "mongodb+srv://user:pass@cluster.mongodb.net/",
        "db_label": "Database",
        "db_placeholder": "e.g. sample_mflix",
        "connect_button": "Connect",
        "save_button": "Save & require restart",
        "cancel_button": "Cancel",

        # Connected state
        "status_label_uri": "URI",
        "status_label_database": "Database",
        "status_label_topology": "Topology",
        "status_label_mongo_version": "MongoDB",
        "status_label_model": "Embedding model",
        "status_label_configured": "Collections configured",
        "test_button": "Test connection",
        "change_button": "Change connection",
        "disconnect_button": "Disconnect",
        "disconnect_confirm_title": "Disconnect?",
        "disconnect_confirm_body": (
            "This removes the saved connection. mongosemantic will return to "
            "the \"Not connected\" state on next launch."
        ),
        "disconnect_confirm_ok": "Disconnect",

        # Banners
        "banner_env_override": (
            "Running from MONGOSEMANTIC_URI environment variable. To make "
            "changes, edit the env var and restart, or unset it and use this page."
        ),
        "banner_restart_required_save": (
            "Saved. Restart mongosemantic ui (and mongosemantic worker if you "
            "have one running) to start using this connection."
        ),
        "banner_restart_required_disconnect": (
            "Disconnected. Restart mongosemantic ui to return to first-run state."
        ),
        "banner_pending_restart": "Pending restart — current session still uses the old connection.",

        # Test result
        "test_success": "Connection alive — {latency_ms} ms · MongoDB {version}",
        "test_running": "Testing…",

        # Dev help
        "devhelp_title": "Help",
        "devhelp_env_state_title": "Environment variables",
        "devhelp_env_yes": "set",
        "devhelp_env_no": "not set",
        "devhelp_config_path_title": "Config file",
        "devhelp_quickref_title": "Quick reference",
        "devhelp_quickref_format": "URI format: mongodb+srv://user:pass@cluster.mongodb.net/",
        "devhelp_quickref_atlas": "Atlas: Network Access → add your IP; Database Access → user needs read on the database.",
        "devhelp_quickref_restart": "After saving, restart mongosemantic ui (and worker if running).",

        # Disabled-nav tooltip
        "nav_disabled_tooltip": "Connect to a database first.",
    },
```

- [ ] **Step 2: Verify the dict still loads**

Run: `python3 -c "from mongosemantic.web.content import CONTENT; print(len(CONTENT['connection']))"`
Expected: ~30 (just confirms no syntax errors).

- [ ] **Step 3: Commit**

```bash
git add mongosemantic/web/content.py
git commit -m "feat(content): copy for three-state connection page"
```

---

## Task 7: HTML — three-state structure for `#page-connection`

**Files:**
- Modify: `mongosemantic/web/static/index.html` (lines 76-98)

- [ ] **Step 1: Replace the section**

Replace lines 76-98 (`<section id="page-connection" ...> ... </section>`) with:

```html
    <section id="page-connection" hidden>
      <h2 data-content="connection.title"></h2>

      <!-- Banners (shown conditionally by app.js) -->
      <div id="conn-banner-env" class="banner banner-info" hidden data-content="connection.banner_env_override"></div>
      <div id="conn-banner-saved" class="banner banner-success" hidden></div>
      <div id="conn-banner-pending" class="banner banner-warning" hidden data-content="connection.banner_pending_restart"></div>

      <p id="conn-subtitle"></p>

      <!-- State 1: NOT CONNECTED -->
      <div id="conn-state-disconnected" class="conn-block" hidden>
        <div class="conn-hero">
          <h3 data-content="connection.hero_title"></h3>
          <p  data-content="connection.hero_body"></p>
        </div>
        <form id="conn-form-new">
          <label data-content="connection.uri_label" for="conn-form-new-uri"></label>
          <input id="conn-form-new-uri" name="uri" data-placeholder="connection.uri_placeholder">
          <label data-content="connection.db_label" for="conn-form-new-db"></label>
          <input id="conn-form-new-db" name="database" data-placeholder="connection.db_placeholder">
          <div style="margin-top:16px">
            <button type="submit" data-content="connection.connect_button"></button>
          </div>
          <div id="conn-form-new-error" class="conn-error" hidden></div>
        </form>
      </div>

      <!-- State 2/3: CONNECTED (status card) -->
      <div id="conn-state-connected" class="conn-block" hidden>
        <div class="conn-status-card">
          <div class="conn-status-dot"></div>
          <h3 id="conn-status-title"></h3>
          <dl id="conn-status-rows"></dl>
          <p id="conn-test-result" class="conn-test-result" hidden></p>
          <div class="conn-actions">
            <button id="conn-btn-test" type="button" data-content="connection.test_button"></button>
            <button id="conn-btn-change" type="button" data-content="connection.change_button"></button>
            <button id="conn-btn-disconnect" type="button" class="btn-danger" data-content="connection.disconnect_button"></button>
          </div>
        </div>

        <!-- Change form (hidden until "Change connection" clicked) -->
        <form id="conn-form-change" hidden>
          <label data-content="connection.uri_label" for="conn-form-change-uri"></label>
          <input id="conn-form-change-uri" name="uri">
          <label data-content="connection.db_label" for="conn-form-change-db"></label>
          <input id="conn-form-change-db" name="database">
          <div style="margin-top:16px">
            <button type="submit" data-content="connection.save_button"></button>
            <button type="button" id="conn-form-change-cancel" data-content="connection.cancel_button"></button>
          </div>
          <div id="conn-form-change-error" class="conn-error" hidden></div>
        </form>
      </div>

      <!-- Dev help (always visible) -->
      <aside class="page-help conn-devhelp">
        <strong data-content="connection.devhelp_title"></strong>

        <p class="conn-devhelp-section-title" data-content="connection.devhelp_env_state_title"></p>
        <dl id="conn-devhelp-env"></dl>

        <p class="conn-devhelp-section-title" data-content="connection.devhelp_config_path_title"></p>
        <code id="conn-devhelp-path"></code>

        <p class="conn-devhelp-section-title" data-content="connection.devhelp_quickref_title"></p>
        <ul>
          <li data-content="connection.devhelp_quickref_format"></li>
          <li data-content="connection.devhelp_quickref_atlas"></li>
          <li data-content="connection.devhelp_quickref_restart"></li>
        </ul>
      </aside>
    </section>
```

- [ ] **Step 2: Commit**

```bash
git add mongosemantic/web/static/index.html
git commit -m "feat(html): three-state structure for connection page"
```

---

## Task 8: Add `/api/connection/config-path` for dev help

**Files:**
- Modify: `mongosemantic/web/routes/system.py`
- Test: `tests/unit/test_route_system.py`

This is needed so the dev-help panel can display the actual config file path (XDG-aware).

- [ ] **Step 1: Add the test**

Append to `tests/unit/test_route_system.py`:

```python
def test_connection_config_path(client, tmp_path):
    r = client.get("/api/connection/config-path")
    body = r.json()
    assert body["path"].endswith("mongosemantic/config.json")
    # XDG override from fixture means tmp_path is in the path
    assert str(tmp_path) in body["path"]
```

- [ ] **Step 2: Add the route**

Append to `mongosemantic/web/routes/system.py`:

```python
@router.get("/api/connection/config-path")
def connection_config_path() -> dict:
    return {"path": str(connection_store.config_path())}
```

- [ ] **Step 3: Run tests**

Run: `python3 -m pytest tests/unit/test_route_system.py -v`
Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add mongosemantic/web/routes/system.py tests/unit/test_route_system.py
git commit -m "feat(api): /api/connection/config-path for dev help panel"
```

---

## Task 9: Frontend JS — connection-page state machine

**Files:**
- Modify: `mongosemantic/web/static/app.js`

The existing connection-page handler in `app.js` lives in the page-render block keyed off the `connection` route. Find it via:

```bash
grep -n "page-connection\|form-connection\|/api/connect" mongosemantic/web/static/app.js
```

Replace that handler block (and any helpers it uses that aren't referenced elsewhere) with the new state machine below.

- [ ] **Step 1: Append the new connection-page module**

Append to `mongosemantic/web/static/app.js` (or co-locate with the existing page handlers; keep one source of truth — if a `renderConnectionPage` function exists, replace its body):

```javascript
// ---- Connection page ----------------------------------------------------

async function renderConnectionPage(content) {
  const section = document.getElementById('page-connection');
  if (!section) return;

  // Hide all sub-blocks; the fetch will reveal the right one.
  document.getElementById('conn-state-disconnected').hidden = true;
  document.getElementById('conn-state-connected').hidden = true;
  document.getElementById('conn-banner-env').hidden = true;
  document.getElementById('conn-banner-saved').hidden = true;
  document.getElementById('conn-banner-pending').hidden = true;

  const [stateRes, pathRes] = await Promise.all([
    fetch('/api/connection').then(r => r.json()),
    fetch('/api/connection/config-path').then(r => r.json()),
  ]);

  // Subtitle
  const subtitleKeys = {
    not_connected: 'subtitle_disconnected',
    connected_ui:  'subtitle_connected_ui',
    connected_env: 'subtitle_connected_env',
  };
  document.getElementById('conn-subtitle').textContent =
    content.connection[subtitleKeys[stateRes.state] || 'subtitle_disconnected'];

  // Dev help (always)
  renderDevHelp(content, stateRes.env_overrides, pathRes.path);

  if (stateRes.state === 'not_connected') {
    document.getElementById('conn-state-disconnected').hidden = false;
    wireNewConnectionForm(content);
    setNavDisabled(true, content);
    return;
  }

  setNavDisabled(false, content);

  // Connected (either UI-saved or env-override)
  document.getElementById('conn-state-connected').hidden = false;
  renderStatusCard(content, stateRes);
  wireConnectedActions(content, stateRes);

  if (stateRes.state === 'connected_env') {
    document.getElementById('conn-banner-env').hidden = false;
    // Hide form/Change/Disconnect: env override is read-only.
    document.getElementById('conn-btn-change').hidden = true;
    document.getElementById('conn-btn-disconnect').hidden = true;
  }

  // Restart-pending banner: shown when sessionStorage has a "pending save" flag.
  if (sessionStorage.getItem('msem.connection.pending')) {
    document.getElementById('conn-banner-pending').hidden = false;
  }
}

function renderStatusCard(content, state) {
  const c = content.connection;
  document.getElementById('conn-status-title').textContent =
    `Connected to ${state.database}`;
  const rows = [
    [c.status_label_uri, state.uri_redacted],
    [c.status_label_database, state.database],
    [c.status_label_topology, state.topology || '—'],
    [c.status_label_mongo_version, state.mongo_version || '—'],
    [c.status_label_model, state.model],
    [c.status_label_configured, String(state.configured_count)],
  ];
  const dl = document.getElementById('conn-status-rows');
  dl.innerHTML = '';
  for (const [k, v] of rows) {
    const dt = document.createElement('dt'); dt.textContent = k;
    const dd = document.createElement('dd'); dd.textContent = v;
    dl.appendChild(dt); dl.appendChild(dd);
  }
}

function renderDevHelp(content, overrides, configPath) {
  const c = content.connection;
  const envDl = document.getElementById('conn-devhelp-env');
  envDl.innerHTML = '';
  const labelFor = (key) => ({uri: 'MONGOSEMANTIC_URI', db: 'MONGOSEMANTIC_DB', model: 'MONGOSEMANTIC_MODEL'})[key];
  for (const key of ['uri', 'db', 'model']) {
    const dt = document.createElement('dt'); dt.textContent = labelFor(key);
    const dd = document.createElement('dd');
    dd.textContent = overrides[key] ? c.devhelp_env_yes : c.devhelp_env_no;
    dd.className = overrides[key] ? 'env-set' : 'env-unset';
    envDl.appendChild(dt); envDl.appendChild(dd);
  }
  document.getElementById('conn-devhelp-path').textContent = configPath;
}

function wireNewConnectionForm(content) {
  const form = document.getElementById('conn-form-new');
  const errBox = document.getElementById('conn-form-new-error');
  form.onsubmit = async (e) => {
    e.preventDefault();
    errBox.hidden = true;
    const uri = document.getElementById('conn-form-new-uri').value;
    const database = document.getElementById('conn-form-new-db').value;
    let res;
    try { res = await fetchJson('POST', '/api/connection/save', {uri, database}); }
    catch (e) { res = {ok: false, error: {code: 'http_error', message: String(e), hint: '', details: ''}}; }
    if (!res.ok) {
      showConnError(errBox, res.error);
      return;
    }
    sessionStorage.setItem('msem.connection.pending', '1');
    showSavedBanner(content, content.connection.banner_restart_required_save);
    renderConnectionPage(content);
  };
}

function wireConnectedActions(content, state) {
  const c = content.connection;

  document.getElementById('conn-btn-test').onclick = async () => {
    const resBox = document.getElementById('conn-test-result');
    resBox.hidden = false;
    resBox.textContent = c.test_running;
    let res;
    try { res = await fetchJson('POST', '/api/connection/test', {}); }
    catch (e) { res = {ok: false, error: {code: 'http_error', message: String(e), hint: '', details: ''}}; }
    if (res.ok) {
      resBox.className = 'conn-test-result success';
      resBox.textContent = c.test_success
        .replace('{latency_ms}', res.latency_ms)
        .replace('{version}', res.mongo_version);
    } else {
      resBox.className = 'conn-test-result error';
      resBox.textContent = `${res.error.message} ${res.error.hint || ''}`;
    }
  };

  document.getElementById('conn-btn-change').onclick = () => {
    document.getElementById('conn-form-change-uri').value = state.uri_redacted.includes('<redacted>') ? '' : state.uri_redacted;
    document.getElementById('conn-form-change-db').value = state.database;
    document.getElementById('conn-form-change').hidden = false;
  };

  document.getElementById('conn-form-change-cancel').onclick = () => {
    document.getElementById('conn-form-change').hidden = true;
  };

  const changeForm = document.getElementById('conn-form-change');
  const changeErr = document.getElementById('conn-form-change-error');
  changeForm.onsubmit = async (e) => {
    e.preventDefault();
    changeErr.hidden = true;
    const uri = document.getElementById('conn-form-change-uri').value;
    const database = document.getElementById('conn-form-change-db').value;
    let res;
    try { res = await fetchJson('POST', '/api/connection/save', {uri, database}); }
    catch (e) { res = {ok: false, error: {code: 'http_error', message: String(e), hint: '', details: ''}}; }
    if (!res.ok) {
      showConnError(changeErr, res.error);
      return;
    }
    sessionStorage.setItem('msem.connection.pending', '1');
    showSavedBanner(content, content.connection.banner_restart_required_save);
    renderConnectionPage(content);
  };

  document.getElementById('conn-btn-disconnect').onclick = async () => {
    if (!confirm(c.disconnect_confirm_body)) return;
    let res;
    try { res = await fetchJson('DELETE', '/api/connection'); }
    catch (e) { res = {ok: false}; }
    if (res.ok) {
      sessionStorage.setItem('msem.connection.pending', '1');
      showSavedBanner(content, c.banner_restart_required_disconnect);
      renderConnectionPage(content);
    }
  };
}

function showConnError(box, err) {
  box.hidden = false;
  box.innerHTML = `<strong>${escapeHtml(err.message)}</strong>` +
    (err.hint ? `<br>${escapeHtml(err.hint)}` : '') +
    (err.details ? `<details><summary>Show technical details</summary><code>${escapeHtml(err.details)}</code></details>` : '');
}

function showSavedBanner(_content, message) {
  const b = document.getElementById('conn-banner-saved');
  b.textContent = message;
  b.hidden = false;
}

function setNavDisabled(disabled, content) {
  // Disable left-nav links except "Connection". Identify by data-route attr.
  const tooltip = content.connection.nav_disabled_tooltip;
  document.querySelectorAll('nav a[data-route]').forEach(a => {
    const route = a.getAttribute('data-route');
    if (route === 'connection') return;
    if (disabled) {
      a.classList.add('nav-disabled');
      a.setAttribute('aria-disabled', 'true');
      a.setAttribute('title', tooltip);
    } else {
      a.classList.remove('nav-disabled');
      a.removeAttribute('aria-disabled');
      a.removeAttribute('title');
    }
  });
}

// Note: `fetchJson(method, url, body)` is the existing IIFE-scoped helper in
// app.js (lines ~30-52). It handles CSRF, JSON parsing, and HTTP errors.
// All new functions above must live inside the same IIFE so they share scope.
// When fetchJson throws (HTTP non-2xx), catch and treat as a generic error.

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
```

- [ ] **Step 2: Wire the new function into the page router**

Find where existing pages dispatch (look for `case 'connection':` in the route switch, or a routing table). Replace the old connection rendering with `renderConnectionPage(content)`. If `csrfToken()` doesn't already exist in `app.js`, reuse the existing CSRF helper (find via `grep -n 'csrf' mongosemantic/web/static/app.js`).

- [ ] **Step 3: Remove dead handlers**

Delete the old `form-connection` submission handler and any helpers it called that are now unused.

- [ ] **Step 4: Commit**

```bash
git add mongosemantic/web/static/app.js
git commit -m "feat(js): connection page state machine + actions"
```

---

## Task 10: CSS — status card, banners, dev help, disabled nav

**Files:**
- Modify: `mongosemantic/web/static/style.css`

- [ ] **Step 1: Append the new styles**

Append to `mongosemantic/web/static/style.css`:

```css
/* ===== Connection page ================================================ */

.banner {
  padding: 12px 16px;
  border-radius: 8px;
  margin-bottom: 16px;
  font-size: 14px;
}
.banner-info    { background: #e8f4fd; color: #0b6cb1; border-left: 4px solid #1976d2; }
.banner-success { background: #e8f5e9; color: #13632b; border-left: 4px solid #13aa52; }
.banner-warning { background: #fff8e1; color: #6a4f00; border-left: 4px solid #f9a825; }

.conn-hero {
  text-align: center;
  padding: 24px;
  background: #f4f7f8;
  border-radius: 8px;
  margin-bottom: 24px;
}
.conn-hero h3 { margin: 0 0 8px; }
.conn-hero p  { margin: 0; color: var(--mdb-ink-muted); }

.conn-status-card {
  background: #fff;
  border: 1px solid var(--mdb-line);
  border-radius: 8px;
  padding: 20px;
  margin-bottom: 24px;
  position: relative;
}
.conn-status-dot {
  position: absolute;
  top: 22px; right: 20px;
  width: 10px; height: 10px;
  border-radius: 50%;
  background: #13aa52;
}
.conn-status-card h3 { margin: 0 0 16px; font-size: 18px; }
.conn-status-card dl {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 6px 16px;
  margin: 0;
}
.conn-status-card dt { color: var(--mdb-ink-muted); font-size: 13px; }
.conn-status-card dd {
  margin: 0;
  font-family: var(--font-mono);
  font-size: 13px;
}
.conn-actions {
  margin-top: 20px;
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.conn-actions button.btn-danger {
  background: #fff;
  color: #c0392b;
  border: 1px solid #c0392b;
}

.conn-test-result {
  margin-top: 14px;
  padding: 8px 12px;
  border-radius: 6px;
  font-size: 13px;
}
.conn-test-result.success { background: #e8f5e9; color: #13632b; }
.conn-test-result.error   { background: #fdecea; color: #c0392b; }

.conn-error {
  margin-top: 16px;
  padding: 12px;
  border: 1px solid #c0392b;
  border-radius: 6px;
  background: #fdecea;
  color: #c0392b;
  font-size: 13px;
}
.conn-error details { margin-top: 8px; }
.conn-error details code { display: block; padding: 8px; background: #fff; border-radius: 4px; }

.conn-devhelp {
  margin-top: 32px;
}
.conn-devhelp-section-title {
  margin: 12px 0 4px;
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: var(--mdb-ink-muted);
}
.conn-devhelp dl {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 2px 12px;
  margin: 0;
  font-size: 13px;
}
.conn-devhelp dd { margin: 0; }
.conn-devhelp dd.env-set   { color: #13632b; }
.conn-devhelp dd.env-unset { color: var(--mdb-ink-muted); }

nav a.nav-disabled {
  opacity: 0.4;
  pointer-events: none;
  cursor: not-allowed;
}
```

- [ ] **Step 2: Commit**

```bash
git add mongosemantic/web/static/style.css
git commit -m "feat(css): connection page status card, banners, dev help"
```

---

## Task 11: Atlas integration test (Tier 8)

**Files:**
- Create: `tests/integration/atlas/test_t8_connection_page.py`
- Modify: `docs/superpowers/specs/2026-05-19-atlas-verification-design.md` — add Tier 8 row

- [ ] **Step 1: Write the test**

Write to `tests/integration/atlas/test_t8_connection_page.py`:

```python
"""Tier 8 — Connection page end-to-end against real Atlas.

Run with .atlas.env sourced and MONGOSEMANTIC_RUN_ATLAS_INTEGRATION=1.

  set -a; source .atlas.env; set +a
  python3 -m pytest tests/integration/atlas/test_t8_connection_page.py -v
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from mongosemantic import connection_store
from mongosemantic.web.app import create_app


pytestmark = pytest.mark.skipif(
    os.environ.get("MONGOSEMANTIC_RUN_ATLAS_INTEGRATION") != "1",
    reason="Atlas integration disabled (set MONGOSEMANTIC_RUN_ATLAS_INTEGRATION=1)",
)


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    # Strip env vars so we exercise the file-fallback path
    for k in ("MONGOSEMANTIC_URI", "MONGOSEMANTIC_DB"):
        monkeypatch.delenv(k, raising=False)
    yield


@pytest.fixture
def client(isolated_config):
    return TestClient(create_app())


def test_not_connected_initial_state(client):
    r = client.get("/api/connection")
    body = r.json()
    assert body["state"] == "not_connected"


def test_save_then_load_against_real_atlas(client):
    atlas_uri = os.environ["MONGOSEMANTIC_ATLAS_URI"]
    db = "sample_mflix"

    r = client.post(
        "/api/connection/save",
        json={"uri": atlas_uri, "database": db},
    )
    body = r.json()
    assert body["ok"] is True, body
    assert body["restart_required"] is True
    assert body["topology"] == "atlas"
    assert body["mongo_version"]

    # Now GET /api/connection reflects the saved config (no env override).
    r = client.get("/api/connection")
    body = r.json()
    assert body["state"] == "connected_ui"
    assert body["database"] == db
    assert body["topology"] == "atlas"
    assert "<redacted>" in body["uri_redacted"]

    # And the file is on disk.
    saved = connection_store.load()
    assert saved is not None
    assert saved.database == db


def test_test_endpoint_pings_active(client):
    atlas_uri = os.environ["MONGOSEMANTIC_ATLAS_URI"]
    connection_store.save(atlas_uri, "sample_mflix")

    r = client.post("/api/connection/test")
    body = r.json()
    assert body["ok"] is True, body
    assert body["latency_ms"] >= 0
    assert body["mongo_version"]


def test_save_failure_does_not_write_config(client):
    # Use a deliberately wrong password to force auth failure.
    atlas_uri = os.environ["MONGOSEMANTIC_ATLAS_URI"]
    # Swap the password segment.
    scheme, rest = atlas_uri.split("://", 1)
    creds, host = rest.split("@", 1)
    user, _ = creds.split(":", 1)
    bad_uri = f"{scheme}://{user}:wrong-password-123@{host}"

    r = client.post(
        "/api/connection/save",
        json={"uri": bad_uri, "database": "sample_mflix"},
    )
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["code"] in {"auth_failed", "timeout"}  # tolerate slow auth-vs-timeout
    assert connection_store.load() is None


def test_delete_clears_config(client):
    atlas_uri = os.environ["MONGOSEMANTIC_ATLAS_URI"]
    connection_store.save(atlas_uri, "sample_mflix")

    r = client.delete("/api/connection")
    body = r.json()
    assert body["ok"] is True

    r = client.get("/api/connection")
    assert r.json()["state"] == "not_connected"
```

- [ ] **Step 2: Add Tier 8 row to the verification spec**

In `docs/superpowers/specs/2026-05-19-atlas-verification-design.md`, find the tier table (around the line containing "Tier 7" / "UI smoke") and add a new row:

```markdown
| 8 | connection page — save, test, error mapping, disconnect | (status) |
```

- [ ] **Step 3: Run the test (URI must be sourced)**

```bash
set -a; source .atlas.env; set +a
python3 -m pytest tests/integration/atlas/test_t8_connection_page.py -v 2>&1 | sed -E 's|mongodb\+srv://[^[:space:]"@]+@|mongodb+srv://<redacted>@|g'
```

Expected: 5 passed.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/atlas/test_t8_connection_page.py docs/superpowers/specs/2026-05-19-atlas-verification-design.md
git commit -m "test(atlas): tier 8 connection page end-to-end"
```

---

## Task 12: Full unit suite + lint + local UI smoke

**Files:** none

- [ ] **Step 1: Run full unit suite**

Run: `python3 -m pytest tests/unit -q`
Expected: all green. If anything regressed (e.g. an old test that called `POST /api/connect`), update or delete it as part of this task; do not leave stale tests behind.

- [ ] **Step 2: Run lint**

Run: `ruff check .`
Expected: clean.

- [ ] **Step 3: Manual UI smoke (browser)**

```bash
# A: First-run "Not connected" state (no env vars, clear any saved file)
rm -f ~/.config/mongosemantic/config.json
mongosemantic ui --port 8081
# Open http://localhost:8081 → land on Connection page.
# Verify: hero "Not connected yet" visible; URI/DB fields empty; left-nav items grayed out.

# B: Save a known-good URI (use Atlas from .atlas.env)
# In the UI: paste URI, paste "sample_mflix", click Connect.
# Expect: green banner "Saved. Restart..."; status card shows pending restart.
# Verify file: ls -l ~/.config/mongosemantic/config.json → mode 0600

# C: Restart and verify connected_ui state
# Kill the ui process, restart: mongosemantic ui --port 8081
# Open Connection page → "Connected to sample_mflix" status card, all nav enabled.
# Click Test connection → green "Connection alive — N ms · MongoDB 8.0.23"

# D: Trigger an error
# Click Change connection, paste a bad password URI, click Save.
# Expect: inline red error with friendly message + hint + technical details disclosure.

# E: Disconnect
# Click Disconnect → confirm. Green banner "Disconnected..."
# Restart ui → lands back on "Not connected" state.

# F: env-override state
# set -a; source .atlas.env; set +a
# MONGOSEMANTIC_URI=$MONGOSEMANTIC_ATLAS_URI MONGOSEMANTIC_DB=sample_mflix mongosemantic ui --port 8081
# Expect: blue banner "Running from MONGOSEMANTIC_URI..."; Change/Disconnect hidden.
# Test connection still works.
```

Mark each scenario passing/failing in your head; if anything fails, fix and re-run before continuing.

- [ ] **Step 4: Commit verification notes if anything was tweaked during smoke**

Only if a fix was needed:

```bash
git add -A
git commit -m "fix(connection-page): <what was fixed during smoke>"
```

---

## Task 13: Independent code review

**Files:** none (review only)

- [ ] **Step 1: Dispatch code-review agent**

Run a `general-purpose` Agent with this prompt:

> Review the diff between `main` and the current `feat/connection-page` branch for the connection-page overhaul. Spec: `docs/superpowers/specs/2026-05-20-connection-page-design.md`. Plan: `docs/superpowers/plans/2026-05-20-connection-page.md`.
>
> Focus on: (1) correctness — does the precedence logic in `Settings.from_environment` match the spec? Does `/api/connection/save` skip writing on failure as specified? (2) scope — any code that's outside the spec? Any feature creep? (3) regression risk — the mechanical `Settings()` → `Settings.from_environment()` sweep affected many route files; verify no behavioral change when env vars are set. (4) test coverage — every row in the error mapping table covered? (5) security — URI redaction never leaks credentials in API responses or logs.
>
> Report `must-fix` items with file:line and `nit` items separately. Under 400 words.

- [ ] **Step 2: Apply must-fix items**

For each `must-fix` returned by the reviewer, edit the relevant file and commit:

```bash
git add <file>
git commit -m "review: <short description>"
```

Skip `nit` items unless trivial.

- [ ] **Step 3: Re-run lint + unit tests**

```bash
python3 -m pytest tests/unit -q && ruff check .
```

---

## Task 14: PR, merge, version bump, tag

**Files:**
- Modify: `pyproject.toml` (version)

Per the user's `feedback_autonomy_on_established_workflow` memory: once a multi-task plan is approved, run git/PR/release operations autonomously.

- [ ] **Step 1: Push branch**

```bash
git push -u origin feat/connection-page
```

- [ ] **Step 2: Create PR**

```bash
gh pr create --title "feat: connection page overhaul (three states, persisted config)" --body-file - <<'EOF'
## Summary
- New Connection page with three states: not_connected / connected_ui / connected_env
- Persistent JSON config at `~/.config/mongosemantic/config.json` (chmod 0600); env var still wins
- Friendly error mapping for common Mongo connection failures (auth, DNS, TLS, IP-allowlist, timeout)
- Dev help panel surfaces env state + config path
- Save / Test / Change / Disconnect actions; restart-required surfaced explicitly

Spec: `docs/superpowers/specs/2026-05-20-connection-page-design.md`
Plan: `docs/superpowers/plans/2026-05-20-connection-page.md`

## Test plan
- [x] Unit suite green (`pytest tests/unit -q`)
- [x] Lint clean (`ruff check .`)
- [x] Atlas integration Tier 8 green (`pytest tests/integration/atlas/test_t8_connection_page.py -v`)
- [x] Manual UI smoke: first-run, save, restart, test, change, disconnect, env-override (see Task 12 in plan)
EOF
```

- [ ] **Step 3: Merge**

```bash
gh pr merge --squash --delete-branch
```

- [ ] **Step 4: Sync main**

```bash
git checkout main
git pull --ff-only
```

- [ ] **Step 5: Bump version**

In `pyproject.toml`, find the `version = "0.7.6"` line and bump to `version = "0.7.7"`. Commit:

```bash
git add pyproject.toml
git commit -m "chore(release): bump to 0.7.7 (connection page overhaul)"
git push
```

- [ ] **Step 6: Tag and push**

```bash
git tag v0.7.7
git push --tags
```

- [ ] **Step 7: Sync any other active feature branches**

If `feat/atlas-verification` (PR #6) is still open:

```bash
git checkout feat/atlas-verification
git merge main --no-edit
git push
git checkout main
```

---

## Notes on execution

- **No worktree needed** — feature branch off `main` per the established per-feature workflow.
- **Frontend has no test framework in this repo** — frontend correctness verified via the manual UI smoke in Task 12 plus the API tests in Tasks 4, 8, 11 which validate the contract the JS consumes.
- **CSP** is `default-src 'self'` — all new JS lives in `app.js`, no inline scripts. The `confirm()` modal in Disconnect is a browser native; if a custom modal pattern already exists in `app.js`, prefer it (look for `data-modal` or similar). If not, native `confirm()` is acceptable for v1.
- **`Settings()` strictness preserved** — CLI callers continue to fail loudly on missing env vars. Only web routes and worker were migrated to `Settings.from_environment()`.
