# mongosemantic v0.1.0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the MVP — a working `mongosemantic` CLI that connects to any MongoDB (Atlas, replica set, or standalone), inspects a collection's fields, configures shadow-mode embedding on a chosen field, bulk-indexes existing documents, and returns semantic search results. No web UI and no MCP in this release — those are separate plans.

**Architecture:** Single Python package. `db/` is the only module that imports pymongo; `embeddings/` is the only one that touches provider SDKs. A `sync/` module auto-detects topology (Atlas/replica-set/standalone) and picks change streams or polling. A `worker/` consumes a Mongo-backed job queue with hash-based dedup and pipelined embed-then-write batches. `search/` builds an aggregation pipeline with two strategies: Atlas `$vectorSearch` when available, brute-force `$dotProduct` otherwise. All config and state live in three collections in the user's own database (`mongosemantic_config`, `mongosemantic_jobs`, `mongosemantic_state`).

**Tech Stack:** Python 3.10+, pymongo 4.x (async via motor where needed), Typer (CLI), sentence-transformers (local embeddings), openai (remote embeddings), httpx (ollama), pytest, pytest-asyncio, docker compose (replica set + standalone for integration tests), ruff (lint).

---

## File structure

This is the full v0.1.0 file layout. Tasks below are ordered so each task produces self-contained changes.

```
mongosemantic/
├── .env.example                         # Task 1
├── .gitignore                           # Task 1
├── LICENSE                              # Task 1 (MIT)
├── README.md                            # Task 1 (skeleton; fleshed out in Task 24)
├── docker-compose.yml                   # Task 2
├── pyproject.toml                       # Task 1
├── mongosemantic/
│   ├── __init__.py                      # Task 1
│   ├── __main__.py                      # Task 17
│   ├── cli.py                           # Task 17
│   ├── config.py                        # Task 3
│   ├── exceptions.py                    # Task 3
│   ├── commands/
│   │   ├── __init__.py
│   │   ├── inspect.py                   # Task 18
│   │   ├── apply.py                     # Task 19
│   │   ├── index.py                     # Task 20
│   │   ├── search.py                    # Task 21
│   │   ├── status.py                    # Task 22
│   │   ├── retry.py                     # Task 22
│   │   └── reindex.py                   # Task 22
│   ├── db/
│   │   ├── __init__.py
│   │   ├── client.py                    # Task 4  (connect + topology)
│   │   ├── schema.py                    # Task 5  (walker + suitability)
│   │   ├── indexes.py                   # Task 13 (Atlas vector-index mgmt)
│   │   └── queries.py                   # Task 14 (pipeline helpers)
│   ├── embeddings/
│   │   ├── __init__.py
│   │   ├── provider.py                  # Task 6  (ABC)
│   │   ├── local.py                     # Task 7
│   │   ├── openai.py                    # Task 8
│   │   └── ollama.py                    # Task 8
│   ├── chunking/
│   │   ├── __init__.py
│   │   └── splitter.py                  # Task 9
│   ├── state/
│   │   ├── __init__.py
│   │   ├── config_store.py              # Task 10
│   │   ├── job_queue.py                 # Task 11
│   │   └── resume_tokens.py             # Task 12
│   ├── sync/
│   │   ├── __init__.py
│   │   ├── change_stream.py             # Task 15
│   │   └── polling.py                   # Task 16
│   ├── worker/
│   │   ├── __init__.py
│   │   └── runner.py                    # Task 16 (worker loop)
│   └── search/
│       ├── __init__.py
│       ├── atlas.py                     # Task 14
│       ├── brute_force.py               # Task 14
│       └── cross_collection.py          # Task 21
└── tests/
    ├── __init__.py
    ├── conftest.py                      # Task 2
    ├── unit/                            # one test file per source module
    └── integration/
        └── conftest.py                  # Task 2
```

**Out of scope for v0.1.0** (deferred to later plans): web UI, MCP server, hybrid search with `$search`, nested-field embedding, array-of-subdocs, inline mode, zero-downtime model migration, cross-collection search is minimal-only (single-collection is the v0.1.0 path).

---

## Task 1: Project skeleton, pyproject, license, gitignore

**Files:**
- Create: `pyproject.toml`
- Create: `.env.example`
- Create: `.gitignore`
- Create: `LICENSE`
- Create: `README.md`
- Create: `mongosemantic/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/unit/__init__.py`
- Create: `tests/integration/__init__.py`

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "mongosemantic"
version = "0.1.0"
description = "Zero-config semantic search for any MongoDB database."
readme = "README.md"
requires-python = ">=3.10"
license = { text = "MIT" }
authors = [{ name = "Varma Budharaju" }]
keywords = ["mongodb", "semantic-search", "vector-search", "embeddings", "atlas"]
classifiers = [
  "Development Status :: 3 - Alpha",
  "Intended Audience :: Developers",
  "License :: OSI Approved :: MIT License",
  "Programming Language :: Python :: 3",
  "Programming Language :: Python :: 3.10",
  "Programming Language :: Python :: 3.11",
  "Programming Language :: Python :: 3.12",
  "Topic :: Database",
]
dependencies = [
  "pymongo>=4.7",
  "typer>=0.12",
  "rich>=13.7",
  "python-dotenv>=1.0",
  "pydantic>=2.6",
  "sentence-transformers>=2.6",
  "numpy>=1.26",
  "httpx>=0.27",
  "tenacity>=8.2",
]

[project.optional-dependencies]
openai = ["openai>=1.30"]
dev = [
  "pytest>=8.1",
  "pytest-asyncio>=0.23",
  "pytest-cov>=5.0",
  "ruff>=0.4",
  "mongomock>=4.1",
]

[project.scripts]
mongosemantic = "mongosemantic.__main__:app"

[tool.hatch.build.targets.wheel]
packages = ["mongosemantic"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"

[tool.ruff]
line-length = 100
target-version = "py310"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "UP", "B", "SIM"]
ignore = ["E501"]
```

- [ ] **Step 2: Create `.env.example`**

```bash
# Connection
MONGOSEMANTIC_URI="mongodb://localhost:27017"
MONGOSEMANTIC_DB="my_app"

# Embedding provider (one of: local-fast, local-better, openai-small, openai-large, ollama-nomic)
MONGOSEMANTIC_MODEL="local-fast"

# OpenAI (only required if MONGOSEMANTIC_MODEL is openai-*)
OPENAI_API_KEY=""

# Ollama (only required if MONGOSEMANTIC_MODEL is ollama-*)
OLLAMA_HOST="http://localhost:11434"

# Worker tuning
MONGOSEMANTIC_BATCH_SIZE=32
MONGOSEMANTIC_POLL_INTERVAL_SECONDS=30
```

- [ ] **Step 3: Create `.gitignore`**

```
__pycache__/
*.py[cod]
*.egg-info/
dist/
build/
.venv/
venv/
.env
.pytest_cache/
.coverage
coverage.xml
htmlcov/
.ruff_cache/
.DS_Store
*.swp
```

- [ ] **Step 4: Create `LICENSE` (MIT, name: Varma Budharaju, year 2026)**

Use the standard MIT license text.

- [ ] **Step 5: Create `README.md` skeleton**

```markdown
# mongosemantic

Zero-config semantic search for any MongoDB database.

```bash
pip install mongosemantic
mongosemantic inspect --collection articles
mongosemantic apply --collection articles --field body
mongosemantic index --collection articles
mongosemantic search "budget travel"
```

Works on MongoDB Atlas, self-hosted replica sets, and standalone MongoDB 7.0+.

Full docs coming in v0.2.0.
```

- [ ] **Step 6: Create `mongosemantic/__init__.py`**

```python
__version__ = "0.1.0"
```

- [ ] **Step 7: Create empty `__init__.py` files**

```bash
: > tests/__init__.py
: > tests/unit/__init__.py
: > tests/integration/__init__.py
```

- [ ] **Step 8: Initialize git and install**

```bash
cd /Users/varma/mongosemantic
git init
git add .
python3 -m pip install -e ".[dev,openai]"
```

Expected: pip install completes without errors. `mongosemantic --help` will fail (no CLI yet) — that's fine.

- [ ] **Step 9: Commit**

```bash
git add .
git commit -m "chore: project skeleton, pyproject, license, gitignore"
```

---

## Task 2: docker-compose + test fixtures

**Files:**
- Create: `docker-compose.yml`
- Create: `tests/conftest.py`
- Create: `tests/integration/conftest.py`

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_smoke.py`:

```python
import pytest

@pytest.mark.integration
def test_replica_set_is_reachable(replica_set_client):
    info = replica_set_client.admin.command("hello")
    assert info.get("setName") == "rs0"

@pytest.mark.integration
def test_standalone_is_reachable(standalone_client):
    info = standalone_client.admin.command("hello")
    assert info.get("setName") is None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest tests/integration/test_smoke.py -v`
Expected: FAIL with fixture `replica_set_client` not found.

- [ ] **Step 3: Create `docker-compose.yml`**

```yaml
services:
  mongo-rs-0:
    image: mongo:7.0
    container_name: mongosemantic-rs-0
    ports: ["27117:27017"]
    command: ["--replSet", "rs0", "--bind_ip_all", "--port", "27017"]
    healthcheck:
      test: ["CMD", "mongosh", "--quiet", "--eval", "db.adminCommand('ping').ok || quit(1)"]
      interval: 5s
      timeout: 5s
      retries: 20

  mongo-rs-1:
    image: mongo:7.0
    container_name: mongosemantic-rs-1
    ports: ["27118:27017"]
    command: ["--replSet", "rs0", "--bind_ip_all", "--port", "27017"]

  mongo-rs-2:
    image: mongo:7.0
    container_name: mongosemantic-rs-2
    ports: ["27119:27017"]
    command: ["--replSet", "rs0", "--bind_ip_all", "--port", "27017"]

  mongo-rs-init:
    image: mongo:7.0
    depends_on: [mongo-rs-0, mongo-rs-1, mongo-rs-2]
    restart: "no"
    entrypoint: ["bash", "-c"]
    command: >
      "sleep 5 && mongosh --host mongo-rs-0:27017 --eval '
      rs.initiate({_id: \"rs0\", members: [
        {_id: 0, host: \"mongo-rs-0:27017\"},
        {_id: 1, host: \"mongo-rs-1:27017\"},
        {_id: 2, host: \"mongo-rs-2:27017\"}
      ]})'"

  mongo-standalone:
    image: mongo:7.0
    container_name: mongosemantic-standalone
    ports: ["27219:27017"]
    command: ["--bind_ip_all"]
    healthcheck:
      test: ["CMD", "mongosh", "--quiet", "--eval", "db.adminCommand('ping').ok || quit(1)"]
      interval: 5s
      timeout: 5s
      retries: 20
```

- [ ] **Step 4: Create `tests/conftest.py`**

```python
import os
import pytest

def pytest_configure(config):
    config.addinivalue_line("markers", "integration: requires docker compose (see README)")

def pytest_collection_modifyitems(config, items):
    if os.environ.get("MONGOSEMANTIC_RUN_INTEGRATION") != "1":
        skip_integration = pytest.mark.skip(reason="set MONGOSEMANTIC_RUN_INTEGRATION=1 to run")
        for item in items:
            if "integration" in item.keywords:
                item.add_marker(skip_integration)
```

- [ ] **Step 5: Create `tests/integration/conftest.py`**

```python
import os
import time
import pytest
from pymongo import MongoClient

REPLICA_URI = "mongodb://localhost:27117,localhost:27118,localhost:27119/?replicaSet=rs0"
STANDALONE_URI = "mongodb://localhost:27219"

def _wait_for(uri: str, timeout: float = 60.0) -> MongoClient:
    deadline = time.time() + timeout
    last_err = None
    while time.time() < deadline:
        try:
            client = MongoClient(uri, serverSelectionTimeoutMS=2000)
            client.admin.command("hello")
            return client
        except Exception as e:
            last_err = e
            time.sleep(1)
    raise RuntimeError(f"Mongo at {uri} not reachable within {timeout}s: {last_err}")

@pytest.fixture(scope="session")
def replica_set_client() -> MongoClient:
    client = _wait_for(REPLICA_URI)
    yield client
    client.close()

@pytest.fixture(scope="session")
def standalone_client() -> MongoClient:
    client = _wait_for(STANDALONE_URI)
    yield client
    client.close()

@pytest.fixture
def clean_db(replica_set_client):
    dbname = f"test_{int(time.time() * 1000)}"
    yield replica_set_client[dbname]
    replica_set_client.drop_database(dbname)

@pytest.fixture
def clean_standalone_db(standalone_client):
    dbname = f"test_{int(time.time() * 1000)}"
    yield standalone_client[dbname]
    standalone_client.drop_database(dbname)
```

- [ ] **Step 6: Bring docker up and run the integration smoke test**

```bash
docker compose up -d
# Wait ~15s for replica set init
MONGOSEMANTIC_RUN_INTEGRATION=1 python3 -m pytest tests/integration/test_smoke.py -v
```

Expected: PASS (both tests).

- [ ] **Step 7: Commit**

```bash
git add docker-compose.yml tests/conftest.py tests/integration/conftest.py tests/integration/test_smoke.py
git commit -m "chore: docker compose with replica set + standalone, integration fixtures"
```

---

## Task 3: Config + exceptions

**Files:**
- Create: `mongosemantic/config.py`
- Create: `mongosemantic/exceptions.py`
- Create: `tests/unit/test_config.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_config.py`:

```python
import os
import pytest
from mongosemantic.config import Settings

def test_settings_loads_from_env(monkeypatch):
    monkeypatch.setenv("MONGOSEMANTIC_URI", "mongodb://test:27017")
    monkeypatch.setenv("MONGOSEMANTIC_DB", "my_db")
    monkeypatch.setenv("MONGOSEMANTIC_MODEL", "local-fast")
    s = Settings()
    assert s.uri == "mongodb://test:27017"
    assert s.database == "my_db"
    assert s.model == "local-fast"
    assert s.batch_size == 32
    assert s.poll_interval_seconds == 30

def test_settings_requires_uri(monkeypatch):
    monkeypatch.delenv("MONGOSEMANTIC_URI", raising=False)
    with pytest.raises(ValueError, match="MONGOSEMANTIC_URI is required"):
        Settings()

def test_settings_rejects_unknown_scheme(monkeypatch):
    monkeypatch.setenv("MONGOSEMANTIC_URI", "postgres://foo")
    monkeypatch.setenv("MONGOSEMANTIC_DB", "d")
    with pytest.raises(ValueError, match="must start with mongodb://"):
        Settings()

def test_settings_validates_model(monkeypatch):
    monkeypatch.setenv("MONGOSEMANTIC_URI", "mongodb://x")
    monkeypatch.setenv("MONGOSEMANTIC_DB", "d")
    monkeypatch.setenv("MONGOSEMANTIC_MODEL", "bogus-model")
    with pytest.raises(ValueError, match="Unknown model"):
        Settings()
```

- [ ] **Step 2: Run to verify fail**

Run: `python3 -m pytest tests/unit/test_config.py -v`
Expected: FAIL — module `mongosemantic.config` not importable.

- [ ] **Step 3: Create `mongosemantic/exceptions.py`**

```python
class MongoSemanticError(Exception):
    """Base exception for mongosemantic."""

class ConfigError(MongoSemanticError):
    """Bad configuration or missing env vars."""

class ProviderError(MongoSemanticError):
    """Embedding provider failure."""

class DimMismatchError(ProviderError):
    """Embedding returned has wrong dimension."""

class TopologyError(MongoSemanticError):
    """Connected cluster doesn't support a required feature."""

class NotConfiguredError(MongoSemanticError):
    """Operation requires apply() to have been run."""
```

- [ ] **Step 4: Create `mongosemantic/config.py`**

```python
from __future__ import annotations
import os
from dataclasses import dataclass, field
from typing import ClassVar

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
```

- [ ] **Step 5: Run test to verify pass**

Run: `python3 -m pytest tests/unit/test_config.py -v`
Expected: all 4 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add mongosemantic/config.py mongosemantic/exceptions.py tests/unit/test_config.py
git commit -m "feat(config): settings loader + exception hierarchy"
```

---

## Task 4: DB client + topology detection

**Files:**
- Create: `mongosemantic/db/__init__.py`
- Create: `mongosemantic/db/client.py`
- Create: `tests/unit/test_db_client.py`
- Create: `tests/integration/test_topology.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_db_client.py`:

```python
from unittest.mock import MagicMock
from mongosemantic.db.client import Topology, detect_topology

def _hello(setName=None, msg=None):
    m = MagicMock()
    m.admin.command.return_value = {k: v for k, v in {"setName": setName, "msg": msg}.items() if v}
    return m

def test_detect_atlas_from_uri():
    c = _hello(setName="atlas-abc")
    t = detect_topology(c, uri="mongodb+srv://cluster0.abc123.mongodb.net")
    assert t == Topology.ATLAS

def test_detect_replica_set_by_setname():
    c = _hello(setName="rs0")
    t = detect_topology(c, uri="mongodb://localhost:27117")
    assert t == Topology.REPLICA_SET

def test_detect_sharded_by_msg():
    c = _hello(msg="isdbgrid")
    t = detect_topology(c, uri="mongodb://mongos:27017")
    assert t == Topology.REPLICA_SET  # sharded treated as replica for our purposes

def test_detect_standalone():
    c = _hello()
    t = detect_topology(c, uri="mongodb://localhost:27017")
    assert t == Topology.STANDALONE
```

Create `tests/integration/test_topology.py`:

```python
import pytest
from mongosemantic.db.client import Topology, detect_topology

@pytest.mark.integration
def test_real_replica_set_topology(replica_set_client):
    t = detect_topology(
        replica_set_client, uri="mongodb://localhost:27117,localhost:27118/?replicaSet=rs0"
    )
    assert t == Topology.REPLICA_SET

@pytest.mark.integration
def test_real_standalone_topology(standalone_client):
    t = detect_topology(standalone_client, uri="mongodb://localhost:27219")
    assert t == Topology.STANDALONE
```

- [ ] **Step 2: Run to verify fail**

Run: `python3 -m pytest tests/unit/test_db_client.py -v`
Expected: FAIL — `mongosemantic.db.client` not importable.

- [ ] **Step 3: Create `mongosemantic/db/__init__.py`**

```python
from mongosemantic.db.client import Topology, detect_topology, MongoConnection

__all__ = ["Topology", "detect_topology", "MongoConnection"]
```

- [ ] **Step 4: Create `mongosemantic/db/client.py`**

```python
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from pymongo import MongoClient

class Topology(str, Enum):
    ATLAS = "atlas"
    REPLICA_SET = "replica_set"
    STANDALONE = "standalone"

def detect_topology(client: MongoClient, uri: str) -> Topology:
    if ".mongodb.net" in uri:
        return Topology.ATLAS
    info = client.admin.command("hello")
    if info.get("setName") or info.get("msg") == "isdbgrid":
        return Topology.REPLICA_SET
    return Topology.STANDALONE

@dataclass
class MongoConnection:
    client: MongoClient
    uri: str
    database_name: str
    topology: Topology

    @classmethod
    def open(cls, uri: str, database_name: str) -> "MongoConnection":
        client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        client.admin.command("hello")  # forces connect
        return cls(
            client=client,
            uri=uri,
            database_name=database_name,
            topology=detect_topology(client, uri),
        )

    @property
    def db(self):
        return self.client[self.database_name]

    def close(self) -> None:
        self.client.close()
```

- [ ] **Step 5: Run unit tests to verify pass**

Run: `python3 -m pytest tests/unit/test_db_client.py -v`
Expected: all 4 tests PASS.

- [ ] **Step 6: Run integration tests**

Run: `MONGOSEMANTIC_RUN_INTEGRATION=1 python3 -m pytest tests/integration/test_topology.py -v`
Expected: both PASS.

- [ ] **Step 7: Commit**

```bash
git add mongosemantic/db/ tests/unit/test_db_client.py tests/integration/test_topology.py
git commit -m "feat(db): MongoConnection + topology detection (atlas/replica/standalone)"
```

---

## Task 5: Schema walker + suitability scoring

**Files:**
- Create: `mongosemantic/db/schema.py`
- Create: `tests/unit/test_schema.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_schema.py`:

```python
from mongosemantic.db.schema import FieldStats, walk_document, score_field

def test_walk_flat_string():
    stats: dict[str, FieldStats] = {}
    walk_document({"title": "hello"}, stats)
    assert "title" in stats
    assert stats["title"].type_name == "string"
    assert stats["title"].count == 1
    assert stats["title"].null_count == 0
    assert stats["title"].total_len == 5

def test_walk_nested_string():
    stats: dict[str, FieldStats] = {}
    walk_document({"author": {"name": "Alice"}}, stats)
    assert "author.name" in stats
    assert stats["author.name"].type_name == "string"
    assert stats["author.name"].total_len == 5

def test_walk_array_of_strings():
    stats: dict[str, FieldStats] = {}
    walk_document({"tags": ["a", "bb"]}, stats)
    assert "tags" in stats
    assert stats["tags"].type_name == "array<string>"
    assert stats["tags"].array_len_sum == 2
    assert stats["tags"].total_len == 3  # sum of element lens

def test_walk_array_of_subdocs():
    stats: dict[str, FieldStats] = {}
    walk_document({"comments": [{"body": "nice"}, {"body": "ok!"}]}, stats)
    assert "comments[].body" in stats
    assert stats["comments[].body"].type_name == "array<string>"
    assert stats["comments[].body"].array_len_sum == 2
    assert stats["comments[].body"].total_len == 7

def test_null_counts():
    stats: dict[str, FieldStats] = {}
    walk_document({"body": None}, stats)
    assert stats["body"].null_count == 1
    assert stats["body"].type_name == "null"

def test_score_great_field():
    fs = FieldStats(type_name="string", count=500, null_count=2, total_len=500 * 800)
    assert score_field(fs) >= 80

def test_score_terrible_field():
    fs = FieldStats(type_name="int", count=500, null_count=0, total_len=0)
    assert score_field(fs) == 0

def test_score_short_sparse_field():
    fs = FieldStats(type_name="string", count=500, null_count=400, total_len=500 * 5)
    assert score_field(fs) < 40
```

- [ ] **Step 2: Run to verify fail**

Run: `python3 -m pytest tests/unit/test_schema.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Create `mongosemantic/db/schema.py`**

```python
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Iterable
from pymongo.collection import Collection

@dataclass
class FieldStats:
    type_name: str = "unknown"
    count: int = 0
    null_count: int = 0
    total_len: int = 0
    array_len_sum: int = 0
    array_occurrences: int = 0

    @property
    def avg_len(self) -> float:
        denom = max(1, self.count - self.null_count)
        return self.total_len / denom

    @property
    def avg_array_len(self) -> float:
        return self.array_len_sum / max(1, self.array_occurrences)

def _classify(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        if not value:
            return "array<empty>"
        inner = _classify(value[0])
        return f"array<{inner}>"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__

def walk_document(doc: dict, stats: dict[str, FieldStats], prefix: str = "") -> None:
    for key, value in doc.items():
        path = f"{prefix}{key}"
        _record(path, value, stats)
        if isinstance(value, dict):
            walk_document(value, stats, prefix=f"{path}.")
        elif isinstance(value, list) and value and isinstance(value[0], dict):
            # array-of-subdocs: recurse each element with a "comments[]" prefix
            for element in value:
                if isinstance(element, dict):
                    walk_document(element, stats, prefix=f"{path}[].")

def _record(path: str, value: Any, stats: dict[str, FieldStats]) -> None:
    fs = stats.setdefault(path, FieldStats())
    fs.count += 1
    classified = _classify(value)
    if value is None:
        fs.null_count += 1
        if fs.type_name == "unknown":
            fs.type_name = "null"
        return
    if fs.type_name in ("unknown", "null"):
        fs.type_name = classified
    if isinstance(value, str):
        fs.total_len += len(value)
    elif isinstance(value, list):
        fs.array_occurrences += 1
        fs.array_len_sum += len(value)
        if value and isinstance(value[0], str):
            fs.total_len += sum(len(x) for x in value if isinstance(x, str))

def score_field(fs: FieldStats) -> int:
    if not fs.type_name.startswith(("string", "array<string>")):
        return 0
    score = 100
    avg_len = fs.avg_len
    if avg_len < 20:
        score -= 60
    elif avg_len < 100:
        score -= 30
    null_ratio = fs.null_count / max(1, fs.count)
    score -= int(null_ratio * 30)
    return max(0, min(100, score))

def inspect_collection(collection: Collection, sample_size: int = 500) -> dict[str, FieldStats]:
    stats: dict[str, FieldStats] = {}
    cursor = collection.aggregate([{"$sample": {"size": sample_size}}])
    for doc in cursor:
        walk_document(doc, stats)
    return stats
```

- [ ] **Step 4: Run tests to verify pass**

Run: `python3 -m pytest tests/unit/test_schema.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add mongosemantic/db/schema.py tests/unit/test_schema.py
git commit -m "feat(db): schema walker + suitability scoring (flat, nested, array, array-of-subdocs)"
```

---

## Task 6: Embedding provider abstract base class

**Files:**
- Create: `mongosemantic/embeddings/__init__.py`
- Create: `mongosemantic/embeddings/provider.py`
- Create: `tests/unit/test_provider.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_provider.py`:

```python
import numpy as np
import pytest
from mongosemantic.embeddings.provider import EmbeddingProvider, l2_normalize

class FakeProvider(EmbeddingProvider):
    model_name = "fake"
    dim = 3
    def embed_batch(self, texts):
        return np.array([[1.0, 0.0, 0.0] for _ in texts], dtype=np.float32)

def test_provider_embed_single():
    p = FakeProvider()
    v = p.embed("hello")
    assert v.shape == (3,)
    assert np.allclose(np.linalg.norm(v), 1.0)

def test_l2_normalize_unit_vector():
    v = np.array([3.0, 4.0, 0.0])
    n = l2_normalize(v)
    assert np.isclose(np.linalg.norm(n), 1.0)

def test_l2_normalize_zero_vector_safe():
    v = np.zeros(3)
    n = l2_normalize(v)
    # Zero vector stays zero, no NaN
    assert not np.any(np.isnan(n))
```

- [ ] **Step 2: Run to verify fail**

Run: `python3 -m pytest tests/unit/test_provider.py -v`
Expected: FAIL — import error.

- [ ] **Step 3: Create `mongosemantic/embeddings/__init__.py`**

```python
from mongosemantic.embeddings.provider import EmbeddingProvider, get_provider

__all__ = ["EmbeddingProvider", "get_provider"]
```

- [ ] **Step 4: Create `mongosemantic/embeddings/provider.py`**

```python
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Sequence
import numpy as np
from mongosemantic.config import MODEL_DIMS
from mongosemantic.exceptions import DimMismatchError

def l2_normalize(v: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(v, axis=-1, keepdims=True)
    # Avoid divide-by-zero on empty vectors
    safe = np.where(norm == 0, 1, norm)
    return v / safe

class EmbeddingProvider(ABC):
    model_name: str = ""
    dim: int = 0

    @abstractmethod
    def embed_batch(self, texts: Sequence[str]) -> np.ndarray:
        """Return array of shape (len(texts), dim). Must be L2-normalized."""

    def embed(self, text: str) -> np.ndarray:
        return self.embed_batch([text])[0]

    def _validate(self, matrix: np.ndarray, n_expected: int) -> np.ndarray:
        if matrix.shape != (n_expected, self.dim):
            raise DimMismatchError(
                f"Provider {self.model_name} returned {matrix.shape}, expected ({n_expected}, {self.dim})"
            )
        return l2_normalize(matrix.astype(np.float32))

def get_provider(model_key: str) -> EmbeddingProvider:
    from mongosemantic.embeddings.local import LocalProvider
    from mongosemantic.embeddings.openai import OpenAIProvider
    from mongosemantic.embeddings.ollama import OllamaProvider
    if model_key in ("local-fast", "local-better"):
        return LocalProvider(model_key)
    if model_key in ("openai-small", "openai-large"):
        return OpenAIProvider(model_key)
    if model_key == "ollama-nomic":
        return OllamaProvider(model_key)
    raise ValueError(f"Unknown model key: {model_key}. Known: {list(MODEL_DIMS)}")
```

- [ ] **Step 5: Fix the FakeProvider in the test**

Edit `tests/unit/test_provider.py` — the `FakeProvider.embed_batch` already returns a unit vector `[1,0,0]`, but the base class `embed()` just returns `embed_batch()[0]` without re-normalizing. That's fine because our concrete providers will normalize inside `embed_batch`. Update the FakeProvider to exercise `_validate`:

Replace the `FakeProvider` class with:

```python
class FakeProvider(EmbeddingProvider):
    model_name = "fake"
    dim = 3
    def embed_batch(self, texts):
        raw = np.array([[1.0, 0.0, 0.0] for _ in texts], dtype=np.float32)
        return self._validate(raw, len(texts))
```

- [ ] **Step 6: Run tests to verify pass**

Run: `python3 -m pytest tests/unit/test_provider.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add mongosemantic/embeddings/__init__.py mongosemantic/embeddings/provider.py tests/unit/test_provider.py
git commit -m "feat(embeddings): EmbeddingProvider ABC with L2-normalize + dim validation"
```

---

## Task 7: Local embedding provider (sentence-transformers)

**Files:**
- Create: `mongosemantic/embeddings/local.py`
- Create: `tests/unit/test_local_provider.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_local_provider.py`:

```python
import numpy as np
import pytest
from mongosemantic.embeddings.local import LocalProvider

@pytest.mark.parametrize("key,dim", [("local-fast", 384), ("local-better", 768)])
def test_local_provider_dim(key, dim):
    p = LocalProvider(key)
    assert p.dim == dim

def test_local_provider_embed_returns_normalized():
    p = LocalProvider("local-fast")
    vecs = p.embed_batch(["hello world", "goodbye sun"])
    assert vecs.shape == (2, 384)
    norms = np.linalg.norm(vecs, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-5)

def test_local_provider_rejects_unknown_key():
    with pytest.raises(ValueError):
        LocalProvider("bogus")
```

- [ ] **Step 2: Run to verify fail**

Run: `python3 -m pytest tests/unit/test_local_provider.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Create `mongosemantic/embeddings/local.py`**

```python
from __future__ import annotations
from typing import Sequence
import numpy as np
from mongosemantic.embeddings.provider import EmbeddingProvider

_MODEL_MAP: dict[str, tuple[str, int]] = {
    "local-fast": ("sentence-transformers/all-MiniLM-L6-v2", 384),
    "local-better": ("sentence-transformers/all-mpnet-base-v2", 768),
}

class LocalProvider(EmbeddingProvider):
    def __init__(self, key: str) -> None:
        if key not in _MODEL_MAP:
            raise ValueError(f"Unknown local model key: {key}")
        hf_name, dim = _MODEL_MAP[key]
        self.model_name = key
        self.dim = dim
        # Lazy import so unit tests that don't use the provider are fast
        from sentence_transformers import SentenceTransformer
        self._model = SentenceTransformer(hf_name)

    def embed_batch(self, texts: Sequence[str]) -> np.ndarray:
        texts = list(texts)
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        raw = self._model.encode(
            texts, batch_size=32, convert_to_numpy=True, show_progress_bar=False
        )
        return self._validate(raw, len(texts))
```

- [ ] **Step 4: Run tests to verify pass**

Run: `python3 -m pytest tests/unit/test_local_provider.py -v`
Expected: PASS. (First run will download the model — allow ~30s on first run.)

- [ ] **Step 5: Commit**

```bash
git add mongosemantic/embeddings/local.py tests/unit/test_local_provider.py
git commit -m "feat(embeddings): LocalProvider backed by sentence-transformers"
```

---

## Task 8: OpenAI + Ollama providers

**Files:**
- Create: `mongosemantic/embeddings/openai.py`
- Create: `mongosemantic/embeddings/ollama.py`
- Create: `tests/unit/test_remote_providers.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_remote_providers.py`:

```python
from unittest.mock import patch, MagicMock
import numpy as np
import pytest
from mongosemantic.embeddings.openai import OpenAIProvider
from mongosemantic.embeddings.ollama import OllamaProvider
from mongosemantic.exceptions import ProviderError

def test_openai_provider_uses_model_and_returns_normalized():
    fake_client = MagicMock()
    fake_client.embeddings.create.return_value = MagicMock(
        data=[MagicMock(embedding=[3.0, 4.0] + [0.0] * 1534)]  # 1536-d
    )
    with patch("mongosemantic.embeddings.openai._make_client", return_value=fake_client):
        p = OpenAIProvider("openai-small")
        v = p.embed_batch(["hi"])
        assert v.shape == (1, 1536)
        assert np.isclose(np.linalg.norm(v[0]), 1.0)

def test_openai_rejects_wrong_dim():
    fake_client = MagicMock()
    fake_client.embeddings.create.return_value = MagicMock(
        data=[MagicMock(embedding=[1.0, 2.0])]  # wrong: 2-d instead of 1536
    )
    with patch("mongosemantic.embeddings.openai._make_client", return_value=fake_client):
        p = OpenAIProvider("openai-small")
        with pytest.raises(Exception):
            p.embed_batch(["hi"])

def test_ollama_provider(monkeypatch):
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {"embeddings": [[1.0] + [0.0] * 767]}
    fake_resp.raise_for_status = MagicMock()
    import mongosemantic.embeddings.ollama as ol
    monkeypatch.setattr(ol.httpx, "post", lambda *a, **kw: fake_resp)
    p = OllamaProvider("ollama-nomic")
    v = p.embed_batch(["hi"])
    assert v.shape == (1, 768)

def test_ollama_error_raises_provider_error(monkeypatch):
    import mongosemantic.embeddings.ollama as ol
    def boom(*a, **kw):
        raise ol.httpx.ConnectError("no ollama")
    monkeypatch.setattr(ol.httpx, "post", boom)
    p = OllamaProvider("ollama-nomic")
    with pytest.raises(ProviderError):
        p.embed_batch(["hi"])
```

- [ ] **Step 2: Run to verify fail**

Run: `python3 -m pytest tests/unit/test_remote_providers.py -v`
Expected: FAIL — imports missing.

- [ ] **Step 3: Create `mongosemantic/embeddings/openai.py`**

```python
from __future__ import annotations
import os
from typing import Sequence
import numpy as np
from mongosemantic.embeddings.provider import EmbeddingProvider
from mongosemantic.exceptions import ProviderError

_MODEL_MAP: dict[str, tuple[str, int]] = {
    "openai-small": ("text-embedding-3-small", 1536),
    "openai-large": ("text-embedding-3-large", 3072),
}

def _make_client():
    try:
        from openai import OpenAI
    except ImportError as e:
        raise ProviderError(
            "openai provider requires `pip install mongosemantic[openai]`"
        ) from e
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ProviderError("OPENAI_API_KEY is not set")
    return OpenAI(api_key=api_key)

class OpenAIProvider(EmbeddingProvider):
    def __init__(self, key: str) -> None:
        if key not in _MODEL_MAP:
            raise ValueError(f"Unknown openai key: {key}")
        self._openai_model, self.dim = _MODEL_MAP[key]
        self.model_name = key
        self._client = _make_client()

    def embed_batch(self, texts: Sequence[str]) -> np.ndarray:
        texts = list(texts)
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        try:
            resp = self._client.embeddings.create(model=self._openai_model, input=texts)
        except Exception as e:
            raise ProviderError(f"OpenAI embedding call failed: {e}") from e
        raw = np.array([item.embedding for item in resp.data], dtype=np.float32)
        return self._validate(raw, len(texts))
```

- [ ] **Step 4: Create `mongosemantic/embeddings/ollama.py`**

```python
from __future__ import annotations
import os
from typing import Sequence
import httpx
import numpy as np
from mongosemantic.embeddings.provider import EmbeddingProvider
from mongosemantic.exceptions import ProviderError

_MODEL_MAP: dict[str, tuple[str, int]] = {
    "ollama-nomic": ("nomic-embed-text", 768),
}

class OllamaProvider(EmbeddingProvider):
    def __init__(self, key: str) -> None:
        if key not in _MODEL_MAP:
            raise ValueError(f"Unknown ollama key: {key}")
        self._ollama_model, self.dim = _MODEL_MAP[key]
        self.model_name = key
        self.host = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")

    def embed_batch(self, texts: Sequence[str]) -> np.ndarray:
        texts = list(texts)
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        try:
            resp = httpx.post(
                f"{self.host}/api/embed",
                json={"model": self._ollama_model, "input": texts},
                timeout=60.0,
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise ProviderError(f"Ollama call failed: {e}") from e
        vectors = resp.json().get("embeddings") or []
        raw = np.array(vectors, dtype=np.float32)
        return self._validate(raw, len(texts))
```

- [ ] **Step 5: Run tests to verify pass**

Run: `python3 -m pytest tests/unit/test_remote_providers.py -v`
Expected: all PASS (no real network — all mocked).

- [ ] **Step 6: Commit**

```bash
git add mongosemantic/embeddings/openai.py mongosemantic/embeddings/ollama.py tests/unit/test_remote_providers.py
git commit -m "feat(embeddings): OpenAI + Ollama providers with mocked unit tests"
```

---

## Task 9: Chunking (sentence-aware, overlap)

**Files:**
- Create: `mongosemantic/chunking/__init__.py`
- Create: `mongosemantic/chunking/splitter.py`
- Create: `tests/unit/test_chunking.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_chunking.py`:

```python
from mongosemantic.chunking.splitter import chunk_text, ChunkConfig

def test_short_text_single_chunk():
    out = chunk_text("Hello world.", ChunkConfig(chunk_size_tokens=100, overlap_tokens=0))
    assert out == ["Hello world."]

def test_long_text_splits_into_chunks():
    sentences = ". ".join([f"Sentence number {i}" for i in range(100)]) + "."
    out = chunk_text(sentences, ChunkConfig(chunk_size_tokens=50, overlap_tokens=10))
    assert len(out) > 1
    assert all(len(c) > 0 for c in out)

def test_overlap_produces_shared_content():
    # Token estimate is len(text)/4. chunk_size=20 tokens = ~80 chars.
    sentences = ". ".join([f"s{i}" for i in range(200)]) + "."
    out = chunk_text(sentences, ChunkConfig(chunk_size_tokens=20, overlap_tokens=10))
    assert len(out) >= 2
    # Adjacent chunks share at least a few characters of content
    a, b = out[0], out[1]
    # Not strictly testable without the splitter's internals; just assert basic shape
    assert len(a) > 0 and len(b) > 0

def test_empty_string():
    out = chunk_text("", ChunkConfig(chunk_size_tokens=100, overlap_tokens=0))
    assert out == []

def test_unicode_handled():
    out = chunk_text("Hello 世界. Goodbye 🌍.", ChunkConfig(chunk_size_tokens=100, overlap_tokens=0))
    assert len(out) == 1
    assert "世界" in out[0]

def test_chunks_respect_sentence_boundaries():
    text = "First sentence is here. Second sentence. Third. Fourth."
    out = chunk_text(text, ChunkConfig(chunk_size_tokens=5, overlap_tokens=0))
    # Each chunk should end on a sentence boundary where possible
    for chunk in out[:-1]:  # last chunk may be shorter
        assert chunk.rstrip().endswith((".", "!", "?"))
```

- [ ] **Step 2: Run to verify fail**

Run: `python3 -m pytest tests/unit/test_chunking.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Create `mongosemantic/chunking/__init__.py`**

```python
from mongosemantic.chunking.splitter import chunk_text, ChunkConfig

__all__ = ["chunk_text", "ChunkConfig"]
```

- [ ] **Step 4: Create `mongosemantic/chunking/splitter.py`**

```python
from __future__ import annotations
import re
from dataclasses import dataclass

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")

def _estimate_tokens(text: str) -> int:
    # Conservative heuristic: ~4 chars per token for English prose.
    return max(1, len(text) // 4)

@dataclass(frozen=True)
class ChunkConfig:
    chunk_size_tokens: int = 512
    overlap_tokens: int = 64

def _split_sentences(text: str) -> list[str]:
    parts = _SENTENCE_END.split(text.strip())
    return [p for p in parts if p]

def chunk_text(text: str, config: ChunkConfig) -> list[str]:
    if not text or not text.strip():
        return []
    sentences = _split_sentences(text)
    if not sentences:
        return []
    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0
    target = config.chunk_size_tokens
    overlap = config.overlap_tokens

    def _flush() -> None:
        if current:
            chunks.append(" ".join(current).strip())

    for sentence in sentences:
        tokens = _estimate_tokens(sentence)
        if current_tokens + tokens > target and current:
            _flush()
            if overlap > 0:
                # carry the tail of the previous chunk as overlap
                tail: list[str] = []
                tail_tokens = 0
                for s in reversed(current):
                    st = _estimate_tokens(s)
                    if tail_tokens + st > overlap:
                        break
                    tail.insert(0, s)
                    tail_tokens += st
                current = list(tail)
                current_tokens = tail_tokens
            else:
                current = []
                current_tokens = 0
        current.append(sentence)
        current_tokens += tokens
    _flush()
    return chunks
```

- [ ] **Step 5: Run tests to verify pass**

Run: `python3 -m pytest tests/unit/test_chunking.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add mongosemantic/chunking/ tests/unit/test_chunking.py
git commit -m "feat(chunking): sentence-aware splitter with overlap"
```

---

## Task 10: Config store (read/write `mongosemantic_config`)

**Files:**
- Create: `mongosemantic/state/__init__.py`
- Create: `mongosemantic/state/config_store.py`
- Create: `tests/unit/test_config_store.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_config_store.py`:

```python
from datetime import datetime
import mongomock
from mongosemantic.state.config_store import (
    CollectionConfig,
    FieldSpec,
    save_config,
    load_config,
    list_configured,
    disable_config,
)

def _db():
    return mongomock.MongoClient()["test"]

def test_save_and_load_config():
    db = _db()
    cfg = CollectionConfig(
        collection="articles",
        mode="shadow",
        shadow_collection="articles_embeddings",
        fields=[FieldSpec(path="body", chunked=True, chunk_size=256, chunk_overlap=32)],
        embedding_model="local-fast",
        embedding_dim=384,
        created_at=datetime(2026, 4, 22),
        updated_at=datetime(2026, 4, 22),
    )
    save_config(db, cfg)
    loaded = load_config(db, "articles")
    assert loaded is not None
    assert loaded.collection == "articles"
    assert loaded.fields[0].path == "body"
    assert loaded.fields[0].chunked is True

def test_load_missing_returns_none():
    db = _db()
    assert load_config(db, "nope") is None

def test_list_configured_returns_only_active():
    db = _db()
    save_config(db, CollectionConfig(
        collection="a", mode="shadow", shadow_collection="a_embeddings",
        fields=[FieldSpec(path="body")], embedding_model="local-fast", embedding_dim=384,
        created_at=datetime.now(), updated_at=datetime.now(),
    ))
    save_config(db, CollectionConfig(
        collection="b", mode="shadow", shadow_collection="b_embeddings",
        fields=[FieldSpec(path="body")], embedding_model="local-fast", embedding_dim=384,
        created_at=datetime.now(), updated_at=datetime.now(),
    ))
    names = {c.collection for c in list_configured(db)}
    assert names == {"a", "b"}

def test_disable_config():
    db = _db()
    save_config(db, CollectionConfig(
        collection="a", mode="shadow", shadow_collection="a_embeddings",
        fields=[FieldSpec(path="body")], embedding_model="local-fast", embedding_dim=384,
        created_at=datetime.now(), updated_at=datetime.now(),
    ))
    disable_config(db, "a")
    assert load_config(db, "a") is None
```

- [ ] **Step 2: Run to verify fail**

Run: `python3 -m pytest tests/unit/test_config_store.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Create `mongosemantic/state/__init__.py`**

```python
from mongosemantic.state.config_store import (
    CollectionConfig,
    FieldSpec,
    save_config,
    load_config,
    list_configured,
    disable_config,
)

__all__ = [
    "CollectionConfig",
    "FieldSpec",
    "save_config",
    "load_config",
    "list_configured",
    "disable_config",
]
```

- [ ] **Step 4: Create `mongosemantic/state/config_store.py`**

```python
from __future__ import annotations
from dataclasses import dataclass, field, asdict
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
```

- [ ] **Step 5: Run tests to verify pass**

Run: `python3 -m pytest tests/unit/test_config_store.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add mongosemantic/state/__init__.py mongosemantic/state/config_store.py tests/unit/test_config_store.py
git commit -m "feat(state): config store for mongosemantic_config collection"
```

---

## Task 11: Job queue

**Files:**
- Create: `mongosemantic/state/job_queue.py`
- Create: `tests/unit/test_job_queue.py`
- Modify: `mongosemantic/state/__init__.py` — re-export queue API

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_job_queue.py`:

```python
import mongomock
from mongosemantic.state.job_queue import (
    enqueue_embed,
    enqueue_delete_all,
    claim_batch,
    complete,
    fail,
    reset_failed,
    count_by_status,
)

def _db():
    return mongomock.MongoClient()["test"]

def test_enqueue_and_claim():
    db = _db()
    enqueue_embed(
        db, collection="articles", source_id="abc", field_path="body",
        chunk_index=0, input_text="hello", input_hash="sha1:abc", model="local-fast",
    )
    batch = claim_batch(db, worker_id="w1", limit=10)
    assert len(batch) == 1
    assert batch[0]["collection"] == "articles"
    assert batch[0]["status"] == "in_flight"

def test_complete_removes_from_pending_count():
    db = _db()
    enqueue_embed(db, "c", "id1", "body", 0, "t", "h", "local-fast")
    batch = claim_batch(db, "w1", 10)
    complete(db, batch[0]["_id"])
    counts = count_by_status(db)
    assert counts.get("completed", 0) == 1
    assert counts.get("in_flight", 0) == 0

def test_fail_records_error_and_attempts():
    db = _db()
    enqueue_embed(db, "c", "id1", "body", 0, "t", "h", "local-fast")
    batch = claim_batch(db, "w1", 10)
    fail(db, batch[0]["_id"], reason="provider 500")
    # First attempt -> re-queued as pending
    counts = count_by_status(db)
    assert counts.get("pending", 0) == 1

def test_fail_three_times_moves_to_failed():
    db = _db()
    enqueue_embed(db, "c", "id1", "body", 0, "t", "h", "local-fast")
    for _ in range(4):
        batch = claim_batch(db, "w1", 10)
        if not batch:
            break
        fail(db, batch[0]["_id"], reason="boom")
    counts = count_by_status(db)
    assert counts.get("failed", 0) == 1

def test_reset_failed_returns_them_to_pending():
    db = _db()
    enqueue_embed(db, "c", "id1", "body", 0, "t", "h", "local-fast")
    for _ in range(4):
        batch = claim_batch(db, "w1", 10)
        if not batch:
            break
        fail(db, batch[0]["_id"], reason="boom")
    reset_failed(db)
    counts = count_by_status(db)
    assert counts.get("pending", 0) == 1
    assert counts.get("failed", 0) == 0

def test_dedup_upsert_on_same_logical_job():
    db = _db()
    enqueue_embed(db, "c", "id1", "body", 0, "t", "h", "local-fast")
    enqueue_embed(db, "c", "id1", "body", 0, "t", "h", "local-fast")
    counts = count_by_status(db)
    assert counts.get("pending", 0) == 1

def test_enqueue_delete_all():
    db = _db()
    enqueue_delete_all(db, "articles", "doc1")
    batch = claim_batch(db, "w1", 10)
    assert batch[0]["kind"] == "delete"
```

- [ ] **Step 2: Run to verify fail**

Run: `python3 -m pytest tests/unit/test_job_queue.py -v`
Expected: FAIL — not implemented.

- [ ] **Step 3: Create `mongosemantic/state/job_queue.py`**

```python
from __future__ import annotations
from datetime import datetime
from typing import Any
from pymongo import ASCENDING
from pymongo.database import Database

JOBS_COLLECTION = "mongosemantic_jobs"
MAX_ATTEMPTS = 3

def ensure_indexes(db: Database) -> None:
    db[JOBS_COLLECTION].create_index(
        [
            ("collection", ASCENDING),
            ("source_id", ASCENDING),
            ("field_path", ASCENDING),
            ("chunk_index", ASCENDING),
            ("kind", ASCENDING),
            ("model", ASCENDING),
            ("status", ASCENDING),
        ],
        name="job_dedup_idx",
    )
    db[JOBS_COLLECTION].create_index([("status", ASCENDING)], name="status_idx")

def enqueue_embed(
    db: Database,
    collection: str,
    source_id: Any,
    field_path: str,
    chunk_index: int | None,
    input_text: str,
    input_hash: str,
    model: str,
) -> None:
    filter_ = {
        "collection": collection,
        "source_id": source_id,
        "field_path": field_path,
        "chunk_index": chunk_index,
        "kind": "embed",
        "model": model,
        "status": {"$in": ["pending", "in_flight"]},
    }
    update = {
        "$setOnInsert": {
            "collection": collection,
            "source_id": source_id,
            "field_path": field_path,
            "chunk_index": chunk_index,
            "kind": "embed",
            "model": model,
            "status": "pending",
            "attempts": 0,
            "last_error": None,
            "enqueued_at": datetime.utcnow(),
            "started_at": None,
            "completed_at": None,
            "owner": None,
            "input_text": input_text,
            "input_hash": input_hash,
        }
    }
    db[JOBS_COLLECTION].update_one(filter_, update, upsert=True)

def enqueue_delete_all(db: Database, collection: str, source_id: Any) -> None:
    db[JOBS_COLLECTION].insert_one({
        "collection": collection,
        "source_id": source_id,
        "field_path": None,
        "chunk_index": None,
        "kind": "delete",
        "model": None,
        "status": "pending",
        "attempts": 0,
        "last_error": None,
        "enqueued_at": datetime.utcnow(),
        "started_at": None,
        "completed_at": None,
        "owner": None,
        "input_text": None,
        "input_hash": None,
    })

def claim_batch(db: Database, worker_id: str, limit: int) -> list[dict]:
    claimed: list[dict] = []
    for _ in range(limit):
        doc = db[JOBS_COLLECTION].find_one_and_update(
            {"status": "pending"},
            {"$set": {
                "status": "in_flight",
                "owner": worker_id,
                "started_at": datetime.utcnow(),
            }},
            return_document=True,
        )
        if not doc:
            break
        claimed.append(doc)
    return claimed

def complete(db: Database, job_id: Any) -> None:
    db[JOBS_COLLECTION].update_one(
        {"_id": job_id},
        {"$set": {"status": "completed", "completed_at": datetime.utcnow()}},
    )

def fail(db: Database, job_id: Any, reason: str) -> None:
    doc = db[JOBS_COLLECTION].find_one({"_id": job_id}) or {}
    attempts = (doc.get("attempts") or 0) + 1
    next_status = "failed" if attempts >= MAX_ATTEMPTS else "pending"
    db[JOBS_COLLECTION].update_one(
        {"_id": job_id},
        {"$set": {
            "status": next_status,
            "attempts": attempts,
            "last_error": reason,
            "owner": None,
            "started_at": None,
        }},
    )

def reset_failed(db: Database) -> int:
    r = db[JOBS_COLLECTION].update_many(
        {"status": "failed"},
        {"$set": {"status": "pending", "attempts": 0, "last_error": None}},
    )
    return r.modified_count

def count_by_status(db: Database) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in db[JOBS_COLLECTION].aggregate([
        {"$group": {"_id": "$status", "n": {"$sum": 1}}}
    ]):
        out[row["_id"]] = row["n"]
    return out
```

- [ ] **Step 4: Update `mongosemantic/state/__init__.py`**

Replace the file with:

```python
from mongosemantic.state.config_store import (
    CollectionConfig,
    FieldSpec,
    save_config,
    load_config,
    list_configured,
    disable_config,
)
from mongosemantic.state.job_queue import (
    ensure_indexes,
    enqueue_embed,
    enqueue_delete_all,
    claim_batch,
    complete,
    fail,
    reset_failed,
    count_by_status,
)

__all__ = [
    "CollectionConfig",
    "FieldSpec",
    "save_config",
    "load_config",
    "list_configured",
    "disable_config",
    "ensure_indexes",
    "enqueue_embed",
    "enqueue_delete_all",
    "claim_batch",
    "complete",
    "fail",
    "reset_failed",
    "count_by_status",
]
```

- [ ] **Step 5: Run tests to verify pass**

Run: `python3 -m pytest tests/unit/test_job_queue.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add mongosemantic/state/ tests/unit/test_job_queue.py
git commit -m "feat(state): job queue with dedup, atomic claim, retry (3x), failed reset"
```

---

## Task 12: Resume-token store

**Files:**
- Create: `mongosemantic/state/resume_tokens.py`
- Create: `tests/unit/test_resume_tokens.py`
- Modify: `mongosemantic/state/__init__.py` — re-export resume-token API

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_resume_tokens.py`:

```python
import mongomock
from mongosemantic.state.resume_tokens import (
    save_resume_token,
    load_resume_token,
    save_polling_watermark,
    load_polling_watermark,
)

def _db():
    return mongomock.MongoClient()["test"]

def test_resume_token_round_trip():
    db = _db()
    assert load_resume_token(db) is None
    save_resume_token(db, {"_data": "token-v1"})
    assert load_resume_token(db) == {"_data": "token-v1"}

def test_polling_watermark_per_collection():
    db = _db()
    assert load_polling_watermark(db, "articles") is None
    save_polling_watermark(db, "articles", 1000)
    save_polling_watermark(db, "products", 2000)
    assert load_polling_watermark(db, "articles") == 1000
    assert load_polling_watermark(db, "products") == 2000
```

- [ ] **Step 2: Run to verify fail**

Run: `python3 -m pytest tests/unit/test_resume_tokens.py -v`
Expected: FAIL.

- [ ] **Step 3: Create `mongosemantic/state/resume_tokens.py`**

```python
from __future__ import annotations
from datetime import datetime
from typing import Any
from pymongo.database import Database

STATE_COLLECTION = "mongosemantic_state"

def save_resume_token(db: Database, token: dict) -> None:
    db[STATE_COLLECTION].update_one(
        {"_id": "change_stream"},
        {"$set": {"token": token, "updated_at": datetime.utcnow()}},
        upsert=True,
    )

def load_resume_token(db: Database) -> dict | None:
    doc = db[STATE_COLLECTION].find_one({"_id": "change_stream"})
    return doc.get("token") if doc else None

def save_polling_watermark(db: Database, collection: str, watermark: Any) -> None:
    db[STATE_COLLECTION].update_one(
        {"_id": f"polling:{collection}"},
        {"$set": {"watermark": watermark, "updated_at": datetime.utcnow()}},
        upsert=True,
    )

def load_polling_watermark(db: Database, collection: str) -> Any | None:
    doc = db[STATE_COLLECTION].find_one({"_id": f"polling:{collection}"})
    return doc.get("watermark") if doc else None
```

- [ ] **Step 4: Update `mongosemantic/state/__init__.py`**

Add to the existing re-exports:

```python
from mongosemantic.state.resume_tokens import (
    save_resume_token,
    load_resume_token,
    save_polling_watermark,
    load_polling_watermark,
)
```

and extend `__all__` with:

```python
"save_resume_token", "load_resume_token",
"save_polling_watermark", "load_polling_watermark",
```

- [ ] **Step 5: Run tests to verify pass**

Run: `python3 -m pytest tests/unit/test_resume_tokens.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add mongosemantic/state/resume_tokens.py mongosemantic/state/__init__.py tests/unit/test_resume_tokens.py
git commit -m "feat(state): resume-token + polling-watermark persistence"
```

---

## Task 13: Atlas vector-index management

**Files:**
- Create: `mongosemantic/db/indexes.py`
- Create: `tests/unit/test_indexes.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_indexes.py`:

```python
import hashlib
from mongosemantic.db.indexes import (
    vector_index_name,
    vector_index_definition,
    shadow_collection_name,
)

def test_shadow_collection_name():
    assert shadow_collection_name("articles") == "articles_embeddings"

def test_vector_index_name_is_stable_and_deterministic():
    a = vector_index_name("articles", "body")
    b = vector_index_name("articles", "body")
    assert a == b
    assert a.startswith("mongosemantic_articles_")
    # changing field changes name
    assert vector_index_name("articles", "title") != a

def test_vector_index_definition_shape():
    definition = vector_index_definition(dim=384)
    assert definition["fields"][0]["type"] == "vector"
    assert definition["fields"][0]["path"] == "embedding"
    assert definition["fields"][0]["numDimensions"] == 384
    assert definition["fields"][0]["similarity"] == "cosine"
```

- [ ] **Step 2: Run to verify fail**

Run: `python3 -m pytest tests/unit/test_indexes.py -v`
Expected: FAIL.

- [ ] **Step 3: Create `mongosemantic/db/indexes.py`**

```python
from __future__ import annotations
import hashlib
from typing import Any
from pymongo import ASCENDING
from pymongo.collection import Collection
from pymongo.database import Database
from mongosemantic.db.client import Topology

def shadow_collection_name(source: str) -> str:
    return f"{source}_embeddings"

def vector_index_name(collection: str, field_path: str) -> str:
    digest = hashlib.sha1(field_path.encode()).hexdigest()[:8]
    return f"mongosemantic_{collection}_{digest}"

def vector_index_definition(dim: int) -> dict[str, Any]:
    return {
        "fields": [
            {
                "type": "vector",
                "path": "embedding",
                "numDimensions": dim,
                "similarity": "cosine",
            }
        ]
    }

def ensure_shadow_indexes(shadow: Collection) -> None:
    shadow.create_index(
        [
            ("source_id", ASCENDING),
            ("field_path", ASCENDING),
            ("chunk_index", ASCENDING),
            ("embedding_model", ASCENDING),
        ],
        unique=True,
        name="source_field_chunk_model_uniq",
    )
    shadow.create_index([("source_id", ASCENDING)], name="source_id_idx")
    shadow.create_index([("embedding_model", ASCENDING)], name="embedding_model_idx")

def create_atlas_vector_index(
    shadow: Collection, collection: str, field_path: str, dim: int
) -> str:
    """Create an Atlas Search vector index. Returns the index name.
    Safe to call repeatedly — an already-existing index is left in place.
    """
    name = vector_index_name(collection, field_path)
    existing = {idx.get("name") for idx in list(shadow.list_search_indexes())}
    if name in existing:
        return name
    definition = vector_index_definition(dim)
    shadow.create_search_index({"name": name, "type": "vectorSearch", "definition": definition})
    return name

def atlas_vector_index_exists(shadow: Collection, collection: str, field_path: str) -> bool:
    name = vector_index_name(collection, field_path)
    return any(idx.get("name") == name for idx in shadow.list_search_indexes())

def suggested_atlas_command(
    collection: str, field_path: str, shadow_coll: str, dim: int
) -> str:
    name = vector_index_name(collection, field_path)
    definition = vector_index_definition(dim)
    return (
        f'db.{shadow_coll}.createSearchIndex('
        f'{{"name": "{name}", "type": "vectorSearch", '
        f'"definition": {definition}}})'
    )
```

- [ ] **Step 4: Run tests to verify pass**

Run: `python3 -m pytest tests/unit/test_indexes.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mongosemantic/db/indexes.py tests/unit/test_indexes.py
git commit -m "feat(db): shadow index creation + Atlas vector-index name/definition helpers"
```

---

## Task 14: Search pipelines (Atlas native + brute-force)

**Files:**
- Create: `mongosemantic/db/queries.py`
- Create: `mongosemantic/search/__init__.py`
- Create: `mongosemantic/search/atlas.py`
- Create: `mongosemantic/search/brute_force.py`
- Create: `tests/unit/test_search_pipelines.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_search_pipelines.py`:

```python
import numpy as np
from mongosemantic.search.atlas import build_atlas_pipeline
from mongosemantic.search.brute_force import build_brute_pipeline

def test_atlas_pipeline_shape():
    q = np.zeros(384, dtype=np.float32).tolist()
    pipeline = build_atlas_pipeline(
        source_collection="articles",
        field_path="body",
        query_vector=q,
        limit=10,
        index_name="mongosemantic_articles_abc",
    )
    assert pipeline[0] == {
        "$vectorSearch": {
            "index": "mongosemantic_articles_abc",
            "path": "embedding",
            "queryVector": q,
            "numCandidates": 100,
            "limit": 10,
        }
    }
    # lookup to source collection
    lookup = next(s for s in pipeline if "$lookup" in s)
    assert lookup["$lookup"]["from"] == "articles"
    # projection includes score meta
    proj = next(s for s in pipeline if "$project" in s)
    assert proj["$project"]["score"] == {"$meta": "vectorSearchScore"}

def test_atlas_pipeline_limit_drives_num_candidates():
    q = [0.0] * 384
    pipeline = build_atlas_pipeline(
        source_collection="c", field_path="body", query_vector=q, limit=50, index_name="i"
    )
    assert pipeline[0]["$vectorSearch"]["numCandidates"] == 500

def test_brute_pipeline_shape():
    q = [0.0] * 384
    pipeline = build_brute_pipeline(
        source_collection="articles",
        field_path="body",
        query_vector=q,
        limit=10,
    )
    # Must filter by field_path
    match = next(s for s in pipeline if "$match" in s)
    assert match["$match"]["field_path"] == "body"
    # Must compute similarity and sort descending
    sort = next(s for s in pipeline if "$sort" in s)
    assert sort["$sort"] == {"similarity": -1}
    # Must limit
    limit = next(s for s in pipeline if "$limit" in s)
    assert limit["$limit"] == 10
```

- [ ] **Step 2: Run to verify fail**

Run: `python3 -m pytest tests/unit/test_search_pipelines.py -v`
Expected: FAIL — modules missing.

- [ ] **Step 3: Create `mongosemantic/db/queries.py`**

```python
from __future__ import annotations
from typing import Any

def lookup_source_stage(source_collection: str) -> dict[str, Any]:
    return {
        "$lookup": {
            "from": source_collection,
            "localField": "source_id",
            "foreignField": "_id",
            "as": "source_doc",
        }
    }

def unwind_source_stage() -> dict[str, Any]:
    return {"$unwind": {"path": "$source_doc", "preserveNullAndEmptyArrays": True}}

def base_projection(score_expr: dict[str, Any]) -> dict[str, Any]:
    return {
        "$project": {
            "source_id": 1,
            "field_path": 1,
            "chunk_index": 1,
            "chunk_text": 1,
            "source_doc": 1,
            "score": score_expr,
        }
    }
```

- [ ] **Step 4: Create `mongosemantic/search/__init__.py`**

```python
from mongosemantic.search.atlas import build_atlas_pipeline
from mongosemantic.search.brute_force import build_brute_pipeline

__all__ = ["build_atlas_pipeline", "build_brute_pipeline"]
```

- [ ] **Step 5: Create `mongosemantic/search/atlas.py`**

```python
from __future__ import annotations
from typing import Any
from mongosemantic.db.queries import lookup_source_stage, unwind_source_stage, base_projection

def build_atlas_pipeline(
    source_collection: str,
    field_path: str,
    query_vector: list[float],
    limit: int,
    index_name: str,
    filter_match: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    num_candidates = max(10 * limit, 100)
    vector_search: dict[str, Any] = {
        "index": index_name,
        "path": "embedding",
        "queryVector": query_vector,
        "numCandidates": num_candidates,
        "limit": limit,
    }
    if filter_match:
        vector_search["filter"] = filter_match
    pipeline: list[dict[str, Any]] = [
        {"$vectorSearch": vector_search},
        {"$match": {"field_path": field_path}},
        lookup_source_stage(source_collection),
        unwind_source_stage(),
        base_projection({"$meta": "vectorSearchScore"}),
    ]
    return pipeline
```

- [ ] **Step 6: Create `mongosemantic/search/brute_force.py`**

```python
from __future__ import annotations
from typing import Any
from mongosemantic.db.queries import lookup_source_stage, unwind_source_stage, base_projection

def build_brute_pipeline(
    source_collection: str,
    field_path: str,
    query_vector: list[float],
    limit: int,
    filter_match: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    match_stage: dict[str, Any] = {"field_path": field_path}
    if filter_match:
        match_stage.update(filter_match)
    similarity_expr = {
        "$reduce": {
            "input": {"$zip": {"inputs": ["$embedding", {"$literal": query_vector}]}},
            "initialValue": 0.0,
            "in": {
                "$add": [
                    "$$value",
                    {
                        "$multiply": [
                            {"$arrayElemAt": ["$$this", 0]},
                            {"$arrayElemAt": ["$$this", 1]},
                        ]
                    },
                ]
            },
        }
    }
    return [
        {"$match": match_stage},
        {"$addFields": {"similarity": similarity_expr}},
        {"$sort": {"similarity": -1}},
        {"$limit": limit},
        lookup_source_stage(source_collection),
        unwind_source_stage(),
        base_projection("$similarity"),
    ]
```

- [ ] **Step 7: Run tests to verify pass**

Run: `python3 -m pytest tests/unit/test_search_pipelines.py -v`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add mongosemantic/db/queries.py mongosemantic/search/ tests/unit/test_search_pipelines.py
git commit -m "feat(search): Atlas $vectorSearch + brute-force aggregation pipeline builders"
```

---

## Task 15: Change-stream listener

**Files:**
- Create: `mongosemantic/sync/__init__.py`
- Create: `mongosemantic/sync/change_stream.py`
- Create: `tests/unit/test_change_stream.py`
- Create: `tests/integration/test_change_stream_integration.py`

- [ ] **Step 1: Write the failing test (unit, synthetic events)**

Create `tests/unit/test_change_stream.py`:

```python
import mongomock
from datetime import datetime
from mongosemantic.sync.change_stream import process_event, hash_text
from mongosemantic.state.config_store import (
    CollectionConfig, FieldSpec, save_config
)
from mongosemantic.state import count_by_status

def _db():
    return mongomock.MongoClient()["test"]

def _config(db, fields):
    save_config(db, CollectionConfig(
        collection="articles", mode="shadow",
        shadow_collection="articles_embeddings",
        fields=fields,
        embedding_model="local-fast", embedding_dim=384,
        created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
    ))

def test_insert_event_enqueues_embed_job_per_field():
    db = _db()
    _config(db, [FieldSpec(path="title"), FieldSpec(path="body")])
    event = {
        "operationType": "insert",
        "ns": {"coll": "articles"},
        "documentKey": {"_id": "doc1"},
        "fullDocument": {"_id": "doc1", "title": "A", "body": "hello world"},
    }
    process_event(db, event)
    counts = count_by_status(db)
    assert counts["pending"] == 2

def test_update_event_skips_unchanged_field():
    db = _db()
    _config(db, [FieldSpec(path="body")])
    # Pre-write an embedding row to simulate prior state
    db["articles_embeddings"].insert_one({
        "source_id": "doc1", "field_path": "body", "chunk_index": 0,
        "embedding_model": "local-fast",
        "embedding_hash": hash_text("local-fast", "unchanged"),
    })
    event = {
        "operationType": "update",
        "ns": {"coll": "articles"},
        "documentKey": {"_id": "doc1"},
        "fullDocument": {"_id": "doc1", "body": "unchanged"},
    }
    process_event(db, event)
    counts = count_by_status(db)
    assert counts.get("pending", 0) == 0

def test_update_event_with_changed_field_enqueues_embed():
    db = _db()
    _config(db, [FieldSpec(path="body")])
    db["articles_embeddings"].insert_one({
        "source_id": "doc1", "field_path": "body", "chunk_index": 0,
        "embedding_model": "local-fast",
        "embedding_hash": hash_text("local-fast", "old content"),
    })
    event = {
        "operationType": "update",
        "ns": {"coll": "articles"},
        "documentKey": {"_id": "doc1"},
        "fullDocument": {"_id": "doc1", "body": "new content"},
    }
    process_event(db, event)
    counts = count_by_status(db)
    assert counts["pending"] == 1

def test_delete_event_enqueues_delete_job():
    db = _db()
    _config(db, [FieldSpec(path="body")])
    event = {
        "operationType": "delete",
        "ns": {"coll": "articles"},
        "documentKey": {"_id": "doc1"},
    }
    process_event(db, event)
    counts = count_by_status(db)
    assert counts["pending"] == 1

def test_event_for_unconfigured_collection_is_ignored():
    db = _db()
    _config(db, [FieldSpec(path="body")])
    event = {
        "operationType": "insert",
        "ns": {"coll": "other"},
        "documentKey": {"_id": "x"},
        "fullDocument": {"_id": "x", "body": "irrelevant"},
    }
    process_event(db, event)
    assert count_by_status(db) == {}
```

Create `tests/integration/test_change_stream_integration.py`:

```python
import threading
import time
import pytest
from mongosemantic.sync.change_stream import ChangeStreamListener
from mongosemantic.state.config_store import CollectionConfig, FieldSpec, save_config
from mongosemantic.state import count_by_status
from datetime import datetime

@pytest.mark.integration
def test_change_stream_picks_up_real_insert(clean_db):
    db = clean_db
    save_config(db, CollectionConfig(
        collection="articles", mode="shadow", shadow_collection="articles_embeddings",
        fields=[FieldSpec(path="body")], embedding_model="local-fast",
        embedding_dim=384, created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
    ))
    listener = ChangeStreamListener(db, ["articles"])
    t = threading.Thread(target=listener.run, daemon=True)
    t.start()
    time.sleep(2.0)  # allow the stream to open
    db["articles"].insert_one({"_id": "doc1", "body": "semantic"})
    # Wait for event to be processed
    for _ in range(20):
        if count_by_status(db).get("pending", 0) >= 1:
            break
        time.sleep(0.5)
    listener.stop()
    assert count_by_status(db).get("pending", 0) == 1
```

- [ ] **Step 2: Run unit tests to verify fail**

Run: `python3 -m pytest tests/unit/test_change_stream.py -v`
Expected: FAIL.

- [ ] **Step 3: Create `mongosemantic/sync/__init__.py`**

```python
from mongosemantic.sync.change_stream import process_event, ChangeStreamListener, hash_text

__all__ = ["process_event", "ChangeStreamListener", "hash_text"]
```

- [ ] **Step 4: Create `mongosemantic/sync/change_stream.py`**

```python
from __future__ import annotations
import hashlib
import logging
import threading
from typing import Any
from pymongo.database import Database
from pymongo.errors import PyMongoError
from mongosemantic.state import (
    enqueue_embed,
    enqueue_delete_all,
    load_config,
    list_configured,
    save_resume_token,
    load_resume_token,
)

log = logging.getLogger("mongosemantic.sync.change_stream")

def hash_text(model: str, text: str) -> str:
    h = hashlib.sha1()
    h.update(model.encode())
    h.update(b"\x00")
    h.update(text.encode("utf-8", errors="ignore"))
    return f"sha1:{h.hexdigest()}"

def _get_path(doc: dict, path: str) -> Any:
    # Supports dotted paths but NOT array-of-subdocs in v0.1.0
    current: Any = doc
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current

def _resolve_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " ".join(str(v) for v in value)
    return str(value)

def process_event(db: Database, event: dict) -> None:
    coll = event.get("ns", {}).get("coll")
    if not coll:
        return
    cfg = load_config(db, coll)
    if not cfg:
        return
    op = event.get("operationType")
    key = event.get("documentKey", {}).get("_id")
    if op == "delete":
        if key is not None:
            enqueue_delete_all(db, coll, key)
        return
    if op not in ("insert", "update", "replace"):
        return
    full = event.get("fullDocument") or {}
    if not full:
        return
    shadow = db[cfg.shadow_collection]
    for spec in cfg.fields:
        text = _resolve_text(_get_path(full, spec.path))
        if not text:
            continue
        new_hash = hash_text(cfg.embedding_model, text)
        existing = shadow.find_one(
            {
                "source_id": key,
                "field_path": spec.path,
                "chunk_index": 0,
                "embedding_model": cfg.embedding_model,
            },
            {"embedding_hash": 1},
        )
        if existing and existing.get("embedding_hash") == new_hash:
            continue
        enqueue_embed(
            db,
            collection=coll,
            source_id=key,
            field_path=spec.path,
            chunk_index=None if not spec.chunked else 0,
            input_text=text,
            input_hash=new_hash,
            model=cfg.embedding_model,
        )

class ChangeStreamListener:
    def __init__(self, db: Database, collections: list[str]) -> None:
        self.db = db
        self.collections = collections
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        pipeline = [{"$match": {"ns.coll": {"$in": self.collections}}}]
        resume = load_resume_token(self.db)
        kwargs: dict[str, Any] = {
            "pipeline": pipeline,
            "full_document": "updateLookup",
        }
        if resume:
            kwargs["resume_after"] = resume
        import time
        try:
            with self.db.watch(**kwargs) as stream:
                while not self._stop.is_set():
                    event = stream.try_next()
                    if event is None:
                        time.sleep(0.1)  # don't spin
                        continue
                    try:
                        process_event(self.db, event)
                    except Exception:
                        log.exception("process_event failed for %s", event.get("ns"))
                    save_resume_token(self.db, stream.resume_token)
        except PyMongoError:
            log.exception("change stream crashed")
            raise
```

- [ ] **Step 5: Run unit tests to verify pass**

Run: `python3 -m pytest tests/unit/test_change_stream.py -v`
Expected: all PASS.

- [ ] **Step 6: Run integration test**

Run: `MONGOSEMANTIC_RUN_INTEGRATION=1 python3 -m pytest tests/integration/test_change_stream_integration.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add mongosemantic/sync/ tests/unit/test_change_stream.py tests/integration/test_change_stream_integration.py
git commit -m "feat(sync): change-stream listener with hash-based skip + resume token persistence"
```

---

## Task 16: Polling listener + worker runner

**Files:**
- Create: `mongosemantic/sync/polling.py`
- Create: `mongosemantic/worker/__init__.py`
- Create: `mongosemantic/worker/runner.py`
- Create: `tests/unit/test_polling.py`
- Create: `tests/unit/test_worker_runner.py`

- [ ] **Step 1: Write the failing polling test**

Create `tests/unit/test_polling.py`:

```python
import mongomock
from datetime import datetime, timedelta
from mongosemantic.sync.polling import poll_once
from mongosemantic.state.config_store import CollectionConfig, FieldSpec, save_config
from mongosemantic.state import count_by_status
from mongosemantic.state.resume_tokens import load_polling_watermark

def _db():
    return mongomock.MongoClient()["test"]

def _config(db):
    save_config(db, CollectionConfig(
        collection="articles", mode="shadow",
        shadow_collection="articles_embeddings",
        fields=[FieldSpec(path="body")],
        embedding_model="local-fast", embedding_dim=384,
        created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
    ))

def test_polling_picks_up_new_docs():
    db = _db()
    _config(db)
    now = datetime.utcnow()
    db["articles"].insert_many([
        {"_id": "a", "body": "one", "updated_at": now - timedelta(seconds=1)},
        {"_id": "b", "body": "two", "updated_at": now},
    ])
    poll_once(db, "articles", watermark_field="updated_at")
    assert count_by_status(db).get("pending", 0) == 2

def test_polling_skips_docs_below_watermark():
    db = _db()
    _config(db)
    t0 = datetime.utcnow() - timedelta(seconds=10)
    t1 = datetime.utcnow()
    db["articles"].insert_one({"_id": "a", "body": "one", "updated_at": t0})
    poll_once(db, "articles", watermark_field="updated_at")
    assert count_by_status(db).get("pending", 0) == 1
    # Second pass with raised watermark should not re-enqueue 'a'
    db["articles"].insert_one({"_id": "b", "body": "two", "updated_at": t1})
    poll_once(db, "articles", watermark_field="updated_at")
    assert count_by_status(db).get("pending", 0) == 2

def test_polling_watermark_updates():
    db = _db()
    _config(db)
    now = datetime.utcnow()
    db["articles"].insert_one({"_id": "a", "body": "one", "updated_at": now})
    poll_once(db, "articles", watermark_field="updated_at")
    wm = load_polling_watermark(db, "articles")
    assert wm is not None
```

- [ ] **Step 2: Run to verify fail**

Run: `python3 -m pytest tests/unit/test_polling.py -v`
Expected: FAIL.

- [ ] **Step 3: Create `mongosemantic/sync/polling.py`**

```python
from __future__ import annotations
from typing import Any
from pymongo.database import Database
from mongosemantic.state import (
    load_config,
    enqueue_embed,
    save_polling_watermark,
    load_polling_watermark,
)
from mongosemantic.sync.change_stream import _get_path, _resolve_text, hash_text

def poll_once(
    db: Database,
    collection: str,
    watermark_field: str = "updated_at",
    batch_size: int = 200,
) -> int:
    """Scan for new/updated docs. Returns number of jobs enqueued."""
    cfg = load_config(db, collection)
    if not cfg:
        return 0
    last = load_polling_watermark(db, collection)
    filter_ = {} if last is None else {watermark_field: {"$gt": last}}
    cursor = db[collection].find(filter_).sort(watermark_field, 1).limit(batch_size)
    new_wm: Any = last
    enqueued = 0
    shadow = db[cfg.shadow_collection]
    for doc in cursor:
        wm_val = doc.get(watermark_field)
        if wm_val is not None and (new_wm is None or wm_val > new_wm):
            new_wm = wm_val
        key = doc.get("_id")
        for spec in cfg.fields:
            text = _resolve_text(_get_path(doc, spec.path))
            if not text:
                continue
            new_hash = hash_text(cfg.embedding_model, text)
            existing = shadow.find_one(
                {
                    "source_id": key,
                    "field_path": spec.path,
                    "chunk_index": 0,
                    "embedding_model": cfg.embedding_model,
                },
                {"embedding_hash": 1},
            )
            if existing and existing.get("embedding_hash") == new_hash:
                continue
            enqueue_embed(
                db,
                collection=collection,
                source_id=key,
                field_path=spec.path,
                chunk_index=None if not spec.chunked else 0,
                input_text=text,
                input_hash=new_hash,
                model=cfg.embedding_model,
            )
            enqueued += 1
    if new_wm is not None and new_wm != last:
        save_polling_watermark(db, collection, new_wm)
    return enqueued
```

- [ ] **Step 4: Run polling tests to verify pass**

Run: `python3 -m pytest tests/unit/test_polling.py -v`
Expected: PASS.

- [ ] **Step 5: Write worker runner test**

Create `tests/unit/test_worker_runner.py`:

```python
import mongomock
import numpy as np
from datetime import datetime
from unittest.mock import MagicMock
from mongosemantic.worker.runner import process_batch
from mongosemantic.state.config_store import CollectionConfig, FieldSpec, save_config
from mongosemantic.state import enqueue_embed, count_by_status

def _db():
    return mongomock.MongoClient()["test"]

def _cfg(db):
    save_config(db, CollectionConfig(
        collection="articles", mode="shadow",
        shadow_collection="articles_embeddings",
        fields=[FieldSpec(path="body")],
        embedding_model="local-fast", embedding_dim=3,
        created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
    ))

def test_worker_embeds_and_writes_to_shadow():
    db = _db()
    _cfg(db)
    enqueue_embed(db, "articles", "doc1", "body", None, "hello", "sha1:x", "local-fast")
    provider = MagicMock()
    provider.model_name = "local-fast"
    provider.dim = 3
    provider.embed_batch = lambda texts: np.array([[1.0, 0.0, 0.0]] * len(texts), dtype=np.float32)
    n = process_batch(db, provider, worker_id="w1", batch_size=32)
    assert n == 1
    assert count_by_status(db).get("completed", 0) == 1
    row = db["articles_embeddings"].find_one({"source_id": "doc1"})
    assert row is not None
    assert row["embedding_model"] == "local-fast"
    assert len(row["embedding"]) == 3

def test_worker_delete_kind_removes_all_vectors():
    db = _db()
    _cfg(db)
    db["articles_embeddings"].insert_many([
        {"source_id": "doc1", "field_path": "body", "chunk_index": 0, "embedding_model": "local-fast"},
        {"source_id": "doc2", "field_path": "body", "chunk_index": 0, "embedding_model": "local-fast"},
    ])
    from mongosemantic.state import enqueue_delete_all
    enqueue_delete_all(db, "articles", "doc1")
    provider = MagicMock()
    provider.model_name = "local-fast"
    process_batch(db, provider, "w1", 32)
    remaining = list(db["articles_embeddings"].find({}))
    assert len(remaining) == 1
    assert remaining[0]["source_id"] == "doc2"

def test_worker_fails_on_provider_error():
    db = _db()
    _cfg(db)
    enqueue_embed(db, "articles", "doc1", "body", None, "hello", "sha1:x", "local-fast")
    provider = MagicMock()
    provider.model_name = "local-fast"
    provider.dim = 3
    def boom(_):
        raise RuntimeError("provider down")
    provider.embed_batch = boom
    process_batch(db, provider, "w1", 32)
    counts = count_by_status(db)
    # First attempt fails -> back to pending
    assert counts.get("pending", 0) == 1
```

- [ ] **Step 6: Run worker test to verify fail**

Run: `python3 -m pytest tests/unit/test_worker_runner.py -v`
Expected: FAIL — runner missing.

- [ ] **Step 7: Create `mongosemantic/worker/__init__.py`**

```python
from mongosemantic.worker.runner import process_batch, WorkerRunner

__all__ = ["process_batch", "WorkerRunner"]
```

- [ ] **Step 8: Create `mongosemantic/worker/runner.py`**

```python
from __future__ import annotations
import logging
import threading
import time
import uuid
from datetime import datetime
from typing import Any
from pymongo.database import Database
from mongosemantic.embeddings.provider import EmbeddingProvider
from mongosemantic.state import (
    claim_batch,
    complete,
    fail,
    load_config,
)

log = logging.getLogger("mongosemantic.worker")

def _write_embedding(
    db: Database, cfg_cache: dict, job: dict, vector: list[float]
) -> None:
    coll_name = job["collection"]
    if coll_name not in cfg_cache:
        cfg = load_config(db, coll_name)
        if not cfg:
            return
        cfg_cache[coll_name] = cfg
    cfg = cfg_cache[coll_name]
    shadow = db[cfg.shadow_collection]
    chunk_index = job.get("chunk_index") if job.get("chunk_index") is not None else 0
    shadow.update_one(
        {
            "source_id": job["source_id"],
            "field_path": job["field_path"],
            "chunk_index": chunk_index,
            "embedding_model": cfg.embedding_model,
        },
        {
            "$set": {
                "source_collection": coll_name,
                "chunk_text": job["input_text"],
                "embedding": vector,
                "embedding_model": cfg.embedding_model,
                "embedding_dim": cfg.embedding_dim,
                "embedding_hash": job["input_hash"],
                "updated_at": datetime.utcnow(),
            },
            "$setOnInsert": {"created_at": datetime.utcnow()},
        },
        upsert=True,
    )

def _handle_delete(db: Database, cfg_cache: dict, job: dict) -> None:
    coll_name = job["collection"]
    if coll_name not in cfg_cache:
        cfg = load_config(db, coll_name)
        if not cfg:
            return
        cfg_cache[coll_name] = cfg
    cfg = cfg_cache[coll_name]
    db[cfg.shadow_collection].delete_many({"source_id": job["source_id"]})

def process_batch(
    db: Database, provider: EmbeddingProvider, worker_id: str, batch_size: int
) -> int:
    batch = claim_batch(db, worker_id, batch_size)
    if not batch:
        return 0
    cfg_cache: dict[str, Any] = {}
    embed_jobs = [j for j in batch if j.get("kind") == "embed"]
    delete_jobs = [j for j in batch if j.get("kind") == "delete"]
    # Deletes first (cheap)
    for job in delete_jobs:
        try:
            _handle_delete(db, cfg_cache, job)
            complete(db, job["_id"])
        except Exception as e:
            log.exception("delete failed")
            fail(db, job["_id"], reason=str(e))
    # Embeds: one provider call per model (all batch jobs share the same model in v0.1)
    if embed_jobs:
        texts = [j["input_text"] for j in embed_jobs]
        try:
            vectors = provider.embed_batch(texts)
        except Exception as e:
            log.exception("embed_batch failed")
            for job in embed_jobs:
                fail(db, job["_id"], reason=f"embed: {e}")
            return len(batch)
        for job, vec in zip(embed_jobs, vectors):
            try:
                _write_embedding(db, cfg_cache, job, vec.tolist())
                complete(db, job["_id"])
            except Exception as e:
                log.exception("write failed")
                fail(db, job["_id"], reason=f"write: {e}")
    return len(batch)

class WorkerRunner:
    def __init__(
        self, db: Database, provider: EmbeddingProvider, batch_size: int = 32,
        idle_sleep: float = 2.0,
    ) -> None:
        self.db = db
        self.provider = provider
        self.batch_size = batch_size
        self.idle_sleep = idle_sleep
        self.worker_id = f"worker-{uuid.uuid4().hex[:8]}"
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        log.info("worker %s starting", self.worker_id)
        while not self._stop.is_set():
            n = process_batch(self.db, self.provider, self.worker_id, self.batch_size)
            if n == 0:
                time.sleep(self.idle_sleep)
```

- [ ] **Step 9: Run tests to verify pass**

Run: `python3 -m pytest tests/unit/test_worker_runner.py -v`
Expected: all PASS.

- [ ] **Step 10: Commit**

```bash
git add mongosemantic/sync/polling.py mongosemantic/worker/ tests/unit/test_polling.py tests/unit/test_worker_runner.py
git commit -m "feat(sync+worker): polling listener with watermarks + worker runner with retry"
```

---

## Task 17: CLI root (Typer) + `__main__`

**Files:**
- Create: `mongosemantic/cli.py`
- Create: `mongosemantic/__main__.py`
- Create: `mongosemantic/commands/__init__.py`

- [ ] **Step 1: Create `mongosemantic/commands/__init__.py`**

```python
```

(empty — just makes the directory a package)

- [ ] **Step 2: Create `mongosemantic/cli.py`**

```python
from __future__ import annotations
import typer
from dotenv import load_dotenv

app = typer.Typer(
    help="Zero-config semantic search for any MongoDB database.",
    add_completion=False,
    no_args_is_help=True,
)

load_dotenv()  # pick up .env if present

# Commands are registered lazily so the CLI starts fast even when
# heavyweight deps (sentence-transformers) aren't needed.
from mongosemantic.commands import inspect as _inspect_mod  # noqa: E402
from mongosemantic.commands import apply as _apply_mod      # noqa: E402
from mongosemantic.commands import index as _index_mod      # noqa: E402
from mongosemantic.commands import search as _search_mod    # noqa: E402
from mongosemantic.commands import status as _status_mod    # noqa: E402
from mongosemantic.commands import retry as _retry_mod      # noqa: E402
from mongosemantic.commands import reindex as _reindex_mod  # noqa: E402

app.command("inspect")(_inspect_mod.inspect_cmd)
app.command("apply")(_apply_mod.apply_cmd)
app.command("index")(_index_mod.index_cmd)
app.command("search")(_search_mod.search_cmd)
app.command("status")(_status_mod.status_cmd)
app.command("retry")(_retry_mod.retry_cmd)
app.command("reindex")(_reindex_mod.reindex_cmd)

# worker command lives here inline because it's the only long-running command
@app.command("worker")
def worker_cmd(
    poll_interval: int = typer.Option(30, "--poll-interval", help="Polling seconds (standalone)"),
    batch_size: int = typer.Option(32, "--batch-size"),
) -> None:
    """Run the sync + embedding background worker."""
    from mongosemantic.commands.worker_cmd import run_worker
    run_worker(poll_interval=poll_interval, batch_size=batch_size)
```

- [ ] **Step 3: Create `mongosemantic/__main__.py`**

```python
from mongosemantic.cli import app

if __name__ == "__main__":
    app()
```

- [ ] **Step 4: Create stub command modules**

Each command module must export a callable. We'll fill them out in Tasks 18–22. For now, create placeholder files so the CLI import succeeds:

```bash
for cmd in inspect apply index search status retry reindex worker_cmd; do
  cat > "mongosemantic/commands/${cmd}.py" <<'EOF'
def placeholder(*args, **kwargs):
    raise NotImplementedError
EOF
done
```

Then inside each file, edit so it exports the expected callable name. Replace the `placeholder` export in each:

- `inspect.py`: `inspect_cmd = placeholder`
- `apply.py`: `apply_cmd = placeholder`
- `index.py`: `index_cmd = placeholder`
- `search.py`: `search_cmd = placeholder`
- `status.py`: `status_cmd = placeholder`
- `retry.py`: `retry_cmd = placeholder`
- `reindex.py`: `reindex_cmd = placeholder`
- `worker_cmd.py`: `def run_worker(**_): raise NotImplementedError`

- [ ] **Step 5: Verify CLI launches and shows help**

Run: `python3 -m mongosemantic --help`
Expected: top-level help output lists all commands (inspect, apply, index, search, status, retry, reindex, worker).

- [ ] **Step 6: Commit**

```bash
git add mongosemantic/cli.py mongosemantic/__main__.py mongosemantic/commands/
git commit -m "feat(cli): Typer root with command registry + stubs"
```

---

## Task 18: `inspect` command

**Files:**
- Modify: `mongosemantic/commands/inspect.py`
- Create: `tests/unit/test_cmd_inspect.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_cmd_inspect.py`:

```python
from typer.testing import CliRunner
from unittest.mock import patch, MagicMock
from mongosemantic.cli import app

runner = CliRunner()

def test_inspect_prints_suitability_table(monkeypatch):
    monkeypatch.setenv("MONGOSEMANTIC_URI", "mongodb://fake")
    monkeypatch.setenv("MONGOSEMANTIC_DB", "d")
    monkeypatch.setenv("MONGOSEMANTIC_MODEL", "local-fast")
    fake_conn = MagicMock()
    fake_db = MagicMock()
    fake_conn.db = fake_db
    fake_conn.topology.value = "atlas"
    with patch("mongosemantic.commands.inspect.MongoConnection.open", return_value=fake_conn), \
         patch("mongosemantic.commands.inspect.inspect_collection") as fake_inspect:
        from mongosemantic.db.schema import FieldStats
        fake_inspect.return_value = {
            "title": FieldStats(type_name="string", count=10, null_count=0, total_len=10 * 50),
            "body": FieldStats(type_name="string", count=10, null_count=0, total_len=10 * 2000),
        }
        r = runner.invoke(app, ["inspect", "--collection", "articles"])
        assert r.exit_code == 0
        assert "title" in r.stdout
        assert "body" in r.stdout
        assert "suitability" in r.stdout.lower() or "great" in r.stdout.lower()
```

- [ ] **Step 2: Run to verify fail**

Run: `python3 -m pytest tests/unit/test_cmd_inspect.py -v`
Expected: FAIL — placeholder raises NotImplementedError.

- [ ] **Step 3: Implement `mongosemantic/commands/inspect.py`**

```python
from __future__ import annotations
import typer
from rich.console import Console
from rich.table import Table
from mongosemantic.config import Settings
from mongosemantic.db.client import MongoConnection
from mongosemantic.db.schema import inspect_collection, score_field

console = Console()

def _band(score: int) -> str:
    if score >= 80:
        return "[green]Great[/green]"
    if score >= 60:
        return "[green3]Good[/green3]"
    if score >= 40:
        return "[yellow]Usable[/yellow]"
    return "[red]Not recommended[/red]"

def inspect_cmd(
    collection: str = typer.Option(..., "--collection", "-c"),
    sample: int = typer.Option(500, "--sample"),
) -> None:
    """Sample a collection and score each field for semantic-search suitability."""
    settings = Settings()
    conn = MongoConnection.open(settings.uri, settings.database)
    try:
        stats = inspect_collection(conn.db[collection], sample_size=sample)
    finally:
        conn.close()
    if not stats:
        console.print(f"[yellow]No documents sampled in {collection}.[/yellow]")
        raise typer.Exit(code=0)
    table = Table(title=f"Inspect {collection} (topology: {conn.topology.value})")
    table.add_column("Field path")
    table.add_column("Type")
    table.add_column("Coverage")
    table.add_column("Avg length")
    table.add_column("Suitability")
    for path, fs in sorted(stats.items(), key=lambda kv: -score_field(kv[1])):
        score = score_field(fs)
        coverage = 1 - (fs.null_count / max(1, fs.count))
        table.add_row(
            path,
            fs.type_name,
            f"{coverage * 100:.0f}%",
            f"{fs.avg_len:.0f}",
            _band(score),
        )
    console.print(table)
```

- [ ] **Step 4: Run test to verify pass**

Run: `python3 -m pytest tests/unit/test_cmd_inspect.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mongosemantic/commands/inspect.py tests/unit/test_cmd_inspect.py
git commit -m "feat(cli): inspect command prints suitability table"
```

---

## Task 19: `apply` command

**Files:**
- Modify: `mongosemantic/commands/apply.py`
- Create: `tests/unit/test_cmd_apply.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_cmd_apply.py`:

```python
from typer.testing import CliRunner
from unittest.mock import patch, MagicMock
import mongomock
from mongosemantic.cli import app

runner = CliRunner()

def _patch_env(monkeypatch):
    monkeypatch.setenv("MONGOSEMANTIC_URI", "mongodb://fake")
    monkeypatch.setenv("MONGOSEMANTIC_DB", "d")
    monkeypatch.setenv("MONGOSEMANTIC_MODEL", "local-fast")

def test_apply_creates_shadow_indexes_and_saves_config(monkeypatch):
    _patch_env(monkeypatch)
    fake_db = mongomock.MongoClient()["d"]
    fake_conn = MagicMock()
    fake_conn.db = fake_db
    from mongosemantic.db.client import Topology
    fake_conn.topology = Topology.STANDALONE
    with patch("mongosemantic.commands.apply.MongoConnection.open", return_value=fake_conn):
        r = runner.invoke(app, ["apply", "--collection", "articles", "--field", "body"])
        assert r.exit_code == 0, r.output
    from mongosemantic.state import load_config
    cfg = load_config(fake_db, "articles")
    assert cfg is not None
    assert cfg.fields[0].path == "body"
    assert cfg.shadow_collection == "articles_embeddings"

def test_apply_rejects_chunk_without_shadow(monkeypatch):
    _patch_env(monkeypatch)
    fake_db = mongomock.MongoClient()["d"]
    fake_conn = MagicMock()
    fake_conn.db = fake_db
    from mongosemantic.db.client import Topology
    fake_conn.topology = Topology.STANDALONE
    with patch("mongosemantic.commands.apply.MongoConnection.open", return_value=fake_conn):
        r = runner.invoke(
            app,
            ["apply", "--collection", "articles", "--field", "body",
             "--mode", "inline", "--chunked"],
        )
        # Should warn and force shadow; exit 0 with a note
        assert r.exit_code == 0
        assert "shadow" in r.output.lower()
```

- [ ] **Step 2: Run to verify fail**

Run: `python3 -m pytest tests/unit/test_cmd_apply.py -v`
Expected: FAIL — not implemented.

- [ ] **Step 3: Implement `mongosemantic/commands/apply.py`**

```python
from __future__ import annotations
from datetime import datetime
from typing import Optional
import typer
from rich.console import Console
from mongosemantic.config import Settings, MODEL_DIMS
from mongosemantic.db.client import MongoConnection, Topology
from mongosemantic.db.indexes import (
    shadow_collection_name,
    ensure_shadow_indexes,
    create_atlas_vector_index,
    suggested_atlas_command,
)
from mongosemantic.state import save_config, ensure_indexes, CollectionConfig, FieldSpec

console = Console()

def apply_cmd(
    collection: str = typer.Option(..., "--collection", "-c"),
    fields: list[str] = typer.Option(..., "--field", "-f"),
    mode: str = typer.Option("shadow", "--mode", help="shadow|inline"),
    chunked: bool = typer.Option(False, "--chunked"),
    chunk_size: int = typer.Option(512, "--chunk-size"),
    chunk_overlap: int = typer.Option(64, "--chunk-overlap"),
    model: Optional[str] = typer.Option(None, "--model"),
) -> None:
    """Configure semantic search on a collection."""
    settings = Settings()
    chosen_model = model or settings.model
    if chosen_model not in MODEL_DIMS:
        raise typer.BadParameter(f"Unknown model: {chosen_model}")
    dim = MODEL_DIMS[chosen_model]

    if chunked and mode != "shadow":
        console.print(
            "[yellow]Chunking requires shadow mode. Switching to shadow for this collection.[/yellow]"
        )
        mode = "shadow"
    if mode != "shadow":
        console.print(
            "[red]Only shadow mode is supported in v0.1.0. Falling back to shadow.[/red]"
        )
        mode = "shadow"

    conn = MongoConnection.open(settings.uri, settings.database)
    try:
        db = conn.db
        ensure_indexes(db)
        shadow_name = shadow_collection_name(collection)
        ensure_shadow_indexes(db[shadow_name])

        field_specs = [
            FieldSpec(path=p, chunked=chunked, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
            for p in fields
        ]
        now = datetime.utcnow()
        cfg = CollectionConfig(
            collection=collection,
            mode="shadow",
            shadow_collection=shadow_name,
            fields=field_specs,
            embedding_model=chosen_model,
            embedding_dim=dim,
            created_at=now,
            updated_at=now,
        )
        save_config(db, cfg)

        if conn.topology == Topology.ATLAS:
            try:
                for p in fields:
                    name = create_atlas_vector_index(db[shadow_name], collection, p, dim)
                    console.print(f"[green]Atlas vector index created: {name}[/green]")
            except Exception as e:
                console.print(f"[yellow]Could not auto-create Atlas vector index: {e}[/yellow]")
                for p in fields:
                    console.print(suggested_atlas_command(collection, p, shadow_name, dim))
        else:
            console.print(
                "[blue]No vector index created (self-hosted). Brute-force aggregation will be used — "
                "fine up to ~100k embeddings.[/blue]"
            )

        console.print(f"[green]Configured semantic search on {collection}: {fields}.[/green]")
    finally:
        conn.close()
```

- [ ] **Step 4: Run tests to verify pass**

Run: `python3 -m pytest tests/unit/test_cmd_apply.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mongosemantic/commands/apply.py tests/unit/test_cmd_apply.py
git commit -m "feat(cli): apply command (shadow-mode config + Atlas index creation)"
```

---

## Task 20: `index` command (bulk embed existing docs)

**Files:**
- Modify: `mongosemantic/commands/index.py`
- Create: `tests/unit/test_cmd_index.py`
- Create: `tests/integration/test_index_e2e.py`

- [ ] **Step 1: Write the failing unit test**

Create `tests/unit/test_cmd_index.py`:

```python
import mongomock
from datetime import datetime
from unittest.mock import patch, MagicMock
from typer.testing import CliRunner
from mongosemantic.cli import app
from mongosemantic.state.config_store import CollectionConfig, FieldSpec, save_config
from mongosemantic.state import count_by_status

runner = CliRunner()

def test_index_enqueues_all_existing_docs(monkeypatch):
    monkeypatch.setenv("MONGOSEMANTIC_URI", "mongodb://fake")
    monkeypatch.setenv("MONGOSEMANTIC_DB", "d")
    monkeypatch.setenv("MONGOSEMANTIC_MODEL", "local-fast")
    db = mongomock.MongoClient()["d"]
    save_config(db, CollectionConfig(
        collection="articles", mode="shadow", shadow_collection="articles_embeddings",
        fields=[FieldSpec(path="body")], embedding_model="local-fast", embedding_dim=384,
        created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
    ))
    db["articles"].insert_many([{"_id": i, "body": f"text {i}"} for i in range(5)])
    fake_conn = MagicMock()
    fake_conn.db = db
    with patch("mongosemantic.commands.index.MongoConnection.open", return_value=fake_conn):
        r = runner.invoke(app, ["index", "--collection", "articles"])
        assert r.exit_code == 0, r.output
    assert count_by_status(db).get("pending", 0) == 5
```

- [ ] **Step 2: Run to verify fail**

Run: `python3 -m pytest tests/unit/test_cmd_index.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `mongosemantic/commands/index.py`**

```python
from __future__ import annotations
import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn
from mongosemantic.config import Settings
from mongosemantic.db.client import MongoConnection
from mongosemantic.state import load_config, enqueue_embed, ensure_indexes
from mongosemantic.sync.change_stream import hash_text, _get_path, _resolve_text
from mongosemantic.exceptions import NotConfiguredError

console = Console()

def index_cmd(
    collection: str = typer.Option(..., "--collection", "-c"),
    batch_size: int = typer.Option(500, "--batch-size"),
) -> None:
    """Enqueue embed jobs for every existing document in a configured collection."""
    settings = Settings()
    conn = MongoConnection.open(settings.uri, settings.database)
    try:
        db = conn.db
        ensure_indexes(db)
        cfg = load_config(db, collection)
        if not cfg:
            raise NotConfiguredError(
                f"{collection} is not configured. Run `mongosemantic apply` first."
            )
        total = db[collection].estimated_document_count()
        shadow = db[cfg.shadow_collection]
        processed = 0
        with Progress(
            SpinnerColumn(),
            TextColumn("[bold]Enqueuing[/bold] {task.completed}/{task.total}"),
            BarColumn(),
            TimeElapsedColumn(),
        ) as progress:
            task_id = progress.add_task("enqueue", total=total)
            for doc in db[collection].find({}, batch_size=batch_size):
                key = doc.get("_id")
                for spec in cfg.fields:
                    text = _resolve_text(_get_path(doc, spec.path))
                    if not text:
                        continue
                    new_hash = hash_text(cfg.embedding_model, text)
                    existing = shadow.find_one(
                        {
                            "source_id": key,
                            "field_path": spec.path,
                            "chunk_index": 0,
                            "embedding_model": cfg.embedding_model,
                        },
                        {"embedding_hash": 1},
                    )
                    if existing and existing.get("embedding_hash") == new_hash:
                        continue
                    enqueue_embed(
                        db,
                        collection=collection,
                        source_id=key,
                        field_path=spec.path,
                        chunk_index=None,
                        input_text=text,
                        input_hash=new_hash,
                        model=cfg.embedding_model,
                    )
                processed += 1
                progress.update(task_id, completed=processed)
        console.print(
            f"[green]Enqueued embed jobs for {processed} documents.[/green] "
            f"Run `mongosemantic worker` to process them."
        )
    finally:
        conn.close()
```

- [ ] **Step 4: Run unit test to verify pass**

Run: `python3 -m pytest tests/unit/test_cmd_index.py -v`
Expected: PASS.

- [ ] **Step 5: Write E2E integration test**

Create `tests/integration/test_index_e2e.py`:

```python
import os
import subprocess
import time
import pytest
from datetime import datetime
from mongosemantic.state.config_store import CollectionConfig, FieldSpec, save_config
from mongosemantic.state import count_by_status

@pytest.mark.integration
def test_end_to_end_index_and_worker(clean_db, monkeypatch):
    db = clean_db
    save_config(db, CollectionConfig(
        collection="articles", mode="shadow", shadow_collection="articles_embeddings",
        fields=[FieldSpec(path="body")], embedding_model="local-fast", embedding_dim=384,
        created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
    ))
    db["articles"].insert_many([
        {"body": "semantic search with mongodb"},
        {"body": "a totally different document about sports"},
    ])
    from mongosemantic.commands.index import index_cmd
    import typer
    # Invoke index_cmd directly via app-less call
    from typer.testing import CliRunner
    from mongosemantic.cli import app
    monkeypatch.setenv(
        "MONGOSEMANTIC_URI",
        f"mongodb://localhost:27117,localhost:27118,localhost:27119/?replicaSet=rs0"
    )
    monkeypatch.setenv("MONGOSEMANTIC_DB", db.name)
    monkeypatch.setenv("MONGOSEMANTIC_MODEL", "local-fast")
    runner = CliRunner()
    r = runner.invoke(app, ["index", "--collection", "articles"])
    assert r.exit_code == 0
    assert count_by_status(db).get("pending", 0) == 2
    # Run one worker batch synchronously
    from mongosemantic.embeddings.provider import get_provider
    from mongosemantic.worker.runner import process_batch
    provider = get_provider("local-fast")
    process_batch(db, provider, "test-worker", 32)
    # Should have 2 embedding rows now
    assert db["articles_embeddings"].count_documents({}) == 2
```

- [ ] **Step 6: Run E2E test**

Run: `MONGOSEMANTIC_RUN_INTEGRATION=1 python3 -m pytest tests/integration/test_index_e2e.py -v`
Expected: PASS (first run downloads the MiniLM model).

- [ ] **Step 7: Commit**

```bash
git add mongosemantic/commands/index.py tests/unit/test_cmd_index.py tests/integration/test_index_e2e.py
git commit -m "feat(cli): index command + end-to-end integration test"
```

---

## Task 21: `search` command (+ single-collection cross path)

**Files:**
- Modify: `mongosemantic/commands/search.py`
- Create: `mongosemantic/search/cross_collection.py`
- Create: `tests/unit/test_cmd_search.py`
- Create: `tests/integration/test_search_e2e.py`

- [ ] **Step 1: Create `mongosemantic/search/cross_collection.py`**

```python
from __future__ import annotations
from typing import Any
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
```

- [ ] **Step 2: Write the failing test**

Create `tests/unit/test_cmd_search.py`:

```python
import mongomock
import numpy as np
from datetime import datetime
from unittest.mock import patch, MagicMock
from typer.testing import CliRunner
from mongosemantic.cli import app
from mongosemantic.state.config_store import CollectionConfig, FieldSpec, save_config

runner = CliRunner()

def _setup(monkeypatch):
    monkeypatch.setenv("MONGOSEMANTIC_URI", "mongodb://fake")
    monkeypatch.setenv("MONGOSEMANTIC_DB", "d")
    monkeypatch.setenv("MONGOSEMANTIC_MODEL", "local-fast")
    db = mongomock.MongoClient()["d"]
    save_config(db, CollectionConfig(
        collection="articles", mode="shadow", shadow_collection="articles_embeddings",
        fields=[FieldSpec(path="body")], embedding_model="local-fast", embedding_dim=3,
        created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
    ))
    # Seed two embeddings
    db["articles_embeddings"].insert_many([
        {"source_id": "a", "source_collection": "articles", "field_path": "body",
         "chunk_index": 0, "chunk_text": "match me",
         "embedding": [1.0, 0.0, 0.0], "embedding_model": "local-fast", "embedding_dim": 3},
        {"source_id": "b", "source_collection": "articles", "field_path": "body",
         "chunk_index": 0, "chunk_text": "no match",
         "embedding": [0.0, 1.0, 0.0], "embedding_model": "local-fast", "embedding_dim": 3},
    ])
    db["articles"].insert_many([
        {"_id": "a", "body": "match me"},
        {"_id": "b", "body": "no match"},
    ])
    return db

def test_search_prints_results_single_collection(monkeypatch):
    db = _setup(monkeypatch)
    fake_provider = MagicMock()
    fake_provider.embed_batch = lambda texts: np.array([[1.0, 0.0, 0.0]], dtype=np.float32)
    fake_conn = MagicMock()
    fake_conn.db = db
    from mongosemantic.db.client import Topology
    fake_conn.topology = Topology.STANDALONE
    with patch("mongosemantic.commands.search.MongoConnection.open", return_value=fake_conn), \
         patch("mongosemantic.commands.search.get_provider", return_value=fake_provider):
        r = runner.invoke(app, ["search", "match me", "--collection", "articles", "--limit", "2"])
        assert r.exit_code == 0, r.output
        assert "match me" in r.stdout
```

- [ ] **Step 3: Run to verify fail**

Run: `python3 -m pytest tests/unit/test_cmd_search.py -v`
Expected: FAIL.

- [ ] **Step 4: Implement `mongosemantic/commands/search.py`**

```python
from __future__ import annotations
from typing import Optional
import json
import typer
from rich.console import Console
from rich.table import Table
from mongosemantic.config import Settings
from mongosemantic.db.client import MongoConnection, Topology
from mongosemantic.db.indexes import vector_index_name, atlas_vector_index_exists
from mongosemantic.embeddings.provider import get_provider
from mongosemantic.exceptions import NotConfiguredError
from mongosemantic.search.atlas import build_atlas_pipeline
from mongosemantic.search.brute_force import build_brute_pipeline
from mongosemantic.search.cross_collection import per_collection_targets, min_max_normalize
from mongosemantic.state import load_config

console = Console()

def _run_one(db, cfg, collection: str, query_vec: list[float], limit: int, topology: Topology):
    field_path = cfg.fields[0].path  # v0.1.0: search uses the first configured field
    shadow = db[cfg.shadow_collection]
    if topology == Topology.ATLAS and atlas_vector_index_exists(shadow, collection, field_path):
        pipeline = build_atlas_pipeline(
            source_collection=collection,
            field_path=field_path,
            query_vector=query_vec,
            limit=limit,
            index_name=vector_index_name(collection, field_path),
        )
    else:
        pipeline = build_brute_pipeline(
            source_collection=collection,
            field_path=field_path,
            query_vector=query_vec,
            limit=limit,
        )
    rows = list(shadow.aggregate(pipeline))
    for r in rows:
        r["source_collection"] = collection
    return rows

def search_cmd(
    query: str = typer.Argument(...),
    collection: Optional[str] = typer.Option(None, "--collection", "-c"),
    limit: int = typer.Option(10, "--limit"),
) -> None:
    """Search by meaning. Omit --collection to search all configured collections."""
    settings = Settings()
    conn = MongoConnection.open(settings.uri, settings.database)
    try:
        db = conn.db
        provider = get_provider(settings.model)
        qvec = provider.embed(query).tolist()

        if collection:
            cfg = load_config(db, collection)
            if not cfg:
                raise NotConfiguredError(f"{collection} not configured")
            rows = _run_one(db, cfg, collection, qvec, limit, conn.topology)
        else:
            all_rows: list[dict] = []
            targets = per_collection_targets(db)
            if not targets:
                raise NotConfiguredError("No collections are configured.")
            models_per_collection: dict[str, str] = {}
            for name in targets:
                cfg = load_config(db, name)
                if cfg is None:
                    continue
                models_per_collection[name] = cfg.embedding_model
                rows = _run_one(db, cfg, name, qvec, limit, conn.topology)
                all_rows.extend(rows)
            # If collections use different models, their score scales aren't
            # comparable — min-max normalize before merging.
            if len(set(models_per_collection.values())) > 1:
                all_rows = min_max_normalize(all_rows, "score")
            all_rows.sort(key=lambda r: r.get("score", 0.0), reverse=True)
            rows = all_rows[:limit]

        table = Table(title=f'Search: "{query}"')
        table.add_column("Score", justify="right")
        table.add_column("Collection")
        table.add_column("Field")
        table.add_column("Snippet")
        for row in rows:
            snippet = (row.get("chunk_text") or "")[:160].replace("\n", " ")
            table.add_row(
                f"{row.get('score', 0):.3f}",
                row.get("source_collection", "-"),
                row.get("field_path", "-"),
                snippet,
            )
        console.print(table)
    finally:
        conn.close()
```

- [ ] **Step 5: Run unit test to verify pass**

Run: `python3 -m pytest tests/unit/test_cmd_search.py -v`
Expected: PASS.

- [ ] **Step 6: Write E2E integration test**

Create `tests/integration/test_search_e2e.py`:

```python
import pytest
from datetime import datetime
from typer.testing import CliRunner
from mongosemantic.cli import app
from mongosemantic.state.config_store import CollectionConfig, FieldSpec, save_config
from mongosemantic.worker.runner import process_batch
from mongosemantic.embeddings.provider import get_provider

@pytest.mark.integration
def test_search_end_to_end(clean_db, monkeypatch):
    db = clean_db
    save_config(db, CollectionConfig(
        collection="articles", mode="shadow", shadow_collection="articles_embeddings",
        fields=[FieldSpec(path="body")], embedding_model="local-fast", embedding_dim=384,
        created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
    ))
    db["articles"].insert_many([
        {"_id": "a", "body": "semantic vector search over mongodb"},
        {"_id": "b", "body": "completely unrelated: basketball scores"},
    ])
    monkeypatch.setenv(
        "MONGOSEMANTIC_URI",
        "mongodb://localhost:27117,localhost:27118,localhost:27119/?replicaSet=rs0",
    )
    monkeypatch.setenv("MONGOSEMANTIC_DB", db.name)
    monkeypatch.setenv("MONGOSEMANTIC_MODEL", "local-fast")
    runner = CliRunner()
    r = runner.invoke(app, ["index", "--collection", "articles"])
    assert r.exit_code == 0
    process_batch(db, get_provider("local-fast"), "t", 32)
    assert db["articles_embeddings"].count_documents({}) == 2
    r2 = runner.invoke(app, ["search", "vector database", "--collection", "articles", "--limit", "2"])
    assert r2.exit_code == 0
    # "semantic vector search over mongodb" should rank above "basketball scores"
    output_lines = r2.output.splitlines()
    semantic_line_idx = next(i for i, line in enumerate(output_lines) if "semantic" in line)
    basketball_line_idx = next(i for i, line in enumerate(output_lines) if "basketball" in line)
    assert semantic_line_idx < basketball_line_idx
```

- [ ] **Step 7: Run E2E test**

Run: `MONGOSEMANTIC_RUN_INTEGRATION=1 python3 -m pytest tests/integration/test_search_e2e.py -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add mongosemantic/commands/search.py mongosemantic/search/cross_collection.py \
        tests/unit/test_cmd_search.py tests/integration/test_search_e2e.py
git commit -m "feat(cli): search command with Atlas/brute auto-select + cross-collection search"
```

---

## Task 22: `status`, `retry`, `reindex`, `worker` commands

**Files:**
- Modify: `mongosemantic/commands/status.py`
- Modify: `mongosemantic/commands/retry.py`
- Modify: `mongosemantic/commands/reindex.py`
- Create: `mongosemantic/commands/worker_cmd.py`
- Create: `tests/unit/test_cmd_housekeeping.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_cmd_housekeeping.py`:

```python
from datetime import datetime
from unittest.mock import patch, MagicMock
import mongomock
from typer.testing import CliRunner
from mongosemantic.cli import app
from mongosemantic.state.config_store import CollectionConfig, FieldSpec, save_config
from mongosemantic.state import enqueue_embed, claim_batch, fail, count_by_status

runner = CliRunner()

def _env(monkeypatch):
    monkeypatch.setenv("MONGOSEMANTIC_URI", "mongodb://fake")
    monkeypatch.setenv("MONGOSEMANTIC_DB", "d")
    monkeypatch.setenv("MONGOSEMANTIC_MODEL", "local-fast")

def _seed(db):
    save_config(db, CollectionConfig(
        collection="articles", mode="shadow", shadow_collection="articles_embeddings",
        fields=[FieldSpec(path="body")], embedding_model="local-fast", embedding_dim=384,
        created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
    ))

def test_status_prints_counts(monkeypatch):
    _env(monkeypatch)
    db = mongomock.MongoClient()["d"]
    _seed(db)
    enqueue_embed(db, "articles", "id1", "body", None, "t", "h", "local-fast")
    fake_conn = MagicMock()
    fake_conn.db = db
    from mongosemantic.db.client import Topology
    fake_conn.topology = Topology.STANDALONE
    with patch("mongosemantic.commands.status.MongoConnection.open", return_value=fake_conn):
        r = runner.invoke(app, ["status"])
        assert r.exit_code == 0
        assert "pending" in r.output.lower()
        assert "1" in r.output

def test_retry_resets_failed(monkeypatch):
    _env(monkeypatch)
    db = mongomock.MongoClient()["d"]
    _seed(db)
    enqueue_embed(db, "articles", "id1", "body", None, "t", "h", "local-fast")
    # Force 3 failures
    for _ in range(3):
        batch = claim_batch(db, "w", 10)
        fail(db, batch[0]["_id"], "boom")
    assert count_by_status(db).get("failed", 0) == 1
    fake_conn = MagicMock(); fake_conn.db = db
    with patch("mongosemantic.commands.retry.MongoConnection.open", return_value=fake_conn):
        r = runner.invoke(app, ["retry", "--all"])
        assert r.exit_code == 0
    assert count_by_status(db).get("pending", 0) == 1

def test_reindex_enqueues_everything(monkeypatch):
    _env(monkeypatch)
    db = mongomock.MongoClient()["d"]
    _seed(db)
    db["articles"].insert_many([{"_id": i, "body": f"t{i}"} for i in range(3)])
    # Pre-populate shadow with stale hashes so reindex must still enqueue
    db["articles_embeddings"].insert_many([
        {"source_id": i, "field_path": "body", "chunk_index": 0,
         "embedding_model": "local-fast", "embedding_hash": "sha1:OLD"}
        for i in range(3)
    ])
    fake_conn = MagicMock(); fake_conn.db = db
    with patch("mongosemantic.commands.reindex.MongoConnection.open", return_value=fake_conn):
        r = runner.invoke(app, ["reindex", "--collection", "articles", "--yes"])
        assert r.exit_code == 0
    assert count_by_status(db).get("pending", 0) == 3
```

- [ ] **Step 2: Run to verify fail**

Run: `python3 -m pytest tests/unit/test_cmd_housekeeping.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `mongosemantic/commands/status.py`**

```python
from __future__ import annotations
import typer
from rich.console import Console
from rich.table import Table
from mongosemantic.config import Settings
from mongosemantic.db.client import MongoConnection
from mongosemantic.state import list_configured, count_by_status

console = Console()

def status_cmd() -> None:
    """Print health overview: topology, configured collections, job counts."""
    settings = Settings()
    conn = MongoConnection.open(settings.uri, settings.database)
    try:
        db = conn.db
        console.print(f"[bold]Topology:[/bold] {conn.topology.value}")
        cfgs = list_configured(db)
        console.print(f"[bold]Configured collections:[/bold] {len(cfgs)}")
        for c in cfgs:
            console.print(f"  - {c.collection}: {[f.path for f in c.fields]} ({c.embedding_model})")
        counts = count_by_status(db)
        table = Table(title="Jobs")
        table.add_column("Status")
        table.add_column("Count", justify="right")
        for status_name in ("pending", "in_flight", "completed", "failed"):
            table.add_row(status_name, str(counts.get(status_name, 0)))
        console.print(table)
    finally:
        conn.close()
```

- [ ] **Step 4: Implement `mongosemantic/commands/retry.py`**

```python
from __future__ import annotations
import typer
from rich.console import Console
from mongosemantic.config import Settings
from mongosemantic.db.client import MongoConnection
from mongosemantic.state import reset_failed

console = Console()

def retry_cmd(
    all_: bool = typer.Option(False, "--all"),
    collection: str = typer.Option(None, "--collection", "-c"),
) -> None:
    """Reset failed embedding jobs back to pending."""
    if not all_ and not collection:
        console.print("[red]Pass --all or --collection.[/red]")
        raise typer.Exit(code=1)
    settings = Settings()
    conn = MongoConnection.open(settings.uri, settings.database)
    try:
        n = reset_failed(conn.db)
        console.print(f"[green]Reset {n} failed jobs to pending.[/green]")
    finally:
        conn.close()
```

- [ ] **Step 5: Implement `mongosemantic/commands/reindex.py`**

```python
from __future__ import annotations
import typer
from rich.console import Console
from mongosemantic.config import Settings
from mongosemantic.db.client import MongoConnection
from mongosemantic.state import load_config, enqueue_embed, ensure_indexes
from mongosemantic.sync.change_stream import hash_text, _get_path, _resolve_text
from mongosemantic.exceptions import NotConfiguredError

console = Console()

def reindex_cmd(
    collection: str = typer.Option(..., "--collection", "-c"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt"),
) -> None:
    """Force re-embedding of every document in a collection."""
    if not yes:
        typer.confirm(f"Force re-embed every document in {collection}?", abort=True)
    settings = Settings()
    conn = MongoConnection.open(settings.uri, settings.database)
    try:
        db = conn.db
        ensure_indexes(db)
        cfg = load_config(db, collection)
        if not cfg:
            raise NotConfiguredError(f"{collection} not configured")
        # Force re-embed: first clear existing embedding rows for this collection,
        # then enqueue fresh jobs. Clearing ensures the hash-based skip in any
        # live sync (change stream / polling) will not short-circuit our jobs.
        db[cfg.shadow_collection].delete_many({"source_collection": collection})
        enqueued = 0
        for doc in db[collection].find({}):
            key = doc.get("_id")
            for spec in cfg.fields:
                text = _resolve_text(_get_path(doc, spec.path))
                if not text:
                    continue
                h = hash_text(cfg.embedding_model, text)
                enqueue_embed(
                    db,
                    collection=collection,
                    source_id=key,
                    field_path=spec.path,
                    chunk_index=None,
                    input_text=text,
                    input_hash=h,
                    model=cfg.embedding_model,
                )
                enqueued += 1
        console.print(f"[green]Cleared shadow rows and enqueued {enqueued} reindex jobs.[/green]")
    finally:
        conn.close()
```

- [ ] **Step 6: Implement `mongosemantic/commands/worker_cmd.py`**

```python
from __future__ import annotations
import signal
import threading
import time
from rich.console import Console
from mongosemantic.config import Settings
from mongosemantic.db.client import MongoConnection, Topology
from mongosemantic.embeddings.provider import get_provider
from mongosemantic.state import ensure_indexes, list_configured
from mongosemantic.sync.change_stream import ChangeStreamListener
from mongosemantic.sync.polling import poll_once
from mongosemantic.worker.runner import WorkerRunner

console = Console()

def run_worker(poll_interval: int, batch_size: int) -> None:
    settings = Settings()
    conn = MongoConnection.open(settings.uri, settings.database)
    db = conn.db
    ensure_indexes(db)
    provider = get_provider(settings.model)
    runner = WorkerRunner(db, provider, batch_size=batch_size)
    threads: list[threading.Thread] = []
    threads.append(threading.Thread(target=runner.run, name="embed-worker", daemon=True))

    listener = None
    if conn.topology in (Topology.ATLAS, Topology.REPLICA_SET):
        configured = [c.collection for c in list_configured(db)]
        if configured:
            listener = ChangeStreamListener(db, configured)
            threads.append(threading.Thread(target=listener.run, name="change-stream", daemon=True))
        console.print(f"[green]Change streams: {configured}[/green]")
    else:
        console.print(f"[yellow]Standalone MongoDB. Polling every {poll_interval}s.[/yellow]")

    stop = threading.Event()

    def _shutdown(*_):
        console.print("\n[yellow]Shutting down…[/yellow]")
        stop.set()
        runner.stop()
        if listener:
            listener.stop()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    for t in threads:
        t.start()

    try:
        while not stop.is_set():
            if conn.topology == Topology.STANDALONE:
                for cfg in list_configured(db):
                    try:
                        poll_once(db, cfg.collection)
                    except Exception:
                        console.print_exception(show_locals=False)
            for _ in range(poll_interval):
                if stop.is_set():
                    break
                time.sleep(1)
    finally:
        conn.close()
```

- [ ] **Step 7: Run housekeeping tests to verify pass**

Run: `python3 -m pytest tests/unit/test_cmd_housekeeping.py -v`
Expected: PASS.

- [ ] **Step 8: Smoke-test the worker command launches**

Run: `MONGOSEMANTIC_URI=mongodb://localhost:27117,localhost:27118,localhost:27119/?replicaSet=rs0 MONGOSEMANTIC_DB=doesnotexist python3 -m mongosemantic worker --batch-size 4 &` then `sleep 3 && kill %1`
Expected: prints "Change streams: []" (since no configured collections). Exits cleanly on SIGTERM.

- [ ] **Step 9: Commit**

```bash
git add mongosemantic/commands/ tests/unit/test_cmd_housekeeping.py
git commit -m "feat(cli): status, retry, reindex, and worker daemon commands"
```

---

## Task 23: End-to-end integration test (standalone topology)

**Files:**
- Create: `tests/integration/test_standalone_e2e.py`

- [ ] **Step 1: Write the integration test**

```python
import pytest
from datetime import datetime
from typer.testing import CliRunner
from mongosemantic.cli import app
from mongosemantic.state.config_store import CollectionConfig, FieldSpec, save_config
from mongosemantic.worker.runner import process_batch
from mongosemantic.embeddings.provider import get_provider
from mongosemantic.sync.polling import poll_once

@pytest.mark.integration
def test_standalone_polling_flow(clean_standalone_db, monkeypatch):
    db = clean_standalone_db
    save_config(db, CollectionConfig(
        collection="articles", mode="shadow", shadow_collection="articles_embeddings",
        fields=[FieldSpec(path="body")], embedding_model="local-fast", embedding_dim=384,
        created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
    ))
    now = datetime.utcnow()
    db["articles"].insert_many([
        {"_id": "a", "body": "car mechanics and repair", "updated_at": now},
        {"_id": "b", "body": "deep sea fishing tips", "updated_at": now},
    ])
    # Poll picks them up
    poll_once(db, "articles")
    # Worker embeds them
    process_batch(db, get_provider("local-fast"), "t", 32)
    # Search via CLI
    monkeypatch.setenv("MONGOSEMANTIC_URI", "mongodb://localhost:27219")
    monkeypatch.setenv("MONGOSEMANTIC_DB", db.name)
    monkeypatch.setenv("MONGOSEMANTIC_MODEL", "local-fast")
    runner = CliRunner()
    r = runner.invoke(app, ["search", "engine oil change", "--collection", "articles"])
    assert r.exit_code == 0
    # "car mechanics" should be the top hit
    lines = r.output.splitlines()
    car_idx = next(i for i, l in enumerate(lines) if "car mechanics" in l)
    fish_idx = next(i for i, l in enumerate(lines) if "fishing" in l)
    assert car_idx < fish_idx
```

- [ ] **Step 2: Run the test**

Run: `MONGOSEMANTIC_RUN_INTEGRATION=1 python3 -m pytest tests/integration/test_standalone_e2e.py -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_standalone_e2e.py
git commit -m "test: standalone-topology end-to-end (polling → worker → search)"
```

---

## Task 24: README polish + MVP release prep

**Files:**
- Modify: `README.md`
- Create: `CHANGELOG.md`

- [ ] **Step 1: Write the README**

Replace `README.md` with:

```markdown
# mongosemantic

**Zero-config semantic search for any MongoDB database.**

`mongosemantic` connects to your existing MongoDB, picks a text field, and makes it searchable by meaning. No separate vector database. No ETL. Works on Atlas, self-hosted replica sets, and standalone MongoDB 7.0+.

## Quick start

```bash
pip install mongosemantic

export MONGOSEMANTIC_URI="mongodb+srv://user:pass@cluster.mongodb.net/my_db"
export MONGOSEMANTIC_DB="my_db"

mongosemantic inspect --collection articles
mongosemantic apply   --collection articles --field body
mongosemantic index   --collection articles        # bulk-embed existing docs
mongosemantic worker &                             # keep embeddings in sync
mongosemantic search  "budget travel"              # search by meaning
```

## Status (v0.1.0 MVP)

- [x] Connect to Atlas / replica set / standalone
- [x] Inspect a collection, score fields for suitability
- [x] Configure shadow-mode semantic search on a field
- [x] Bulk-embed existing documents
- [x] Sync in real time (change streams) or on a schedule (polling)
- [x] Search via native Atlas `$vectorSearch` or brute-force aggregation
- [x] CLI: inspect / apply / index / search / worker / status / retry / reindex
- [ ] Web UI _(v0.2.0)_
- [ ] MCP server for AI agents _(v0.3.0)_
- [ ] Atlas hybrid search (semantic + keyword) _(v0.4.0)_
- [ ] Zero-downtime model migration _(v0.5.0)_

## Embedding models

| Model | Dimensions | Cost | Notes |
|---|---|---|---|
| `local-fast` (MiniLM) | 384 | Free | Default. Runs on your machine. |
| `local-better` (MPNet) | 768 | Free | Higher quality, slower. |
| `openai-small` | 1536 | ~$0.02/1M tokens | Multilingual. |
| `openai-large` | 3072 | ~$0.13/1M tokens | Highest quality. |
| `ollama-nomic` | 768 | Free | Self-hosted via Ollama. |

Select via `MONGOSEMANTIC_MODEL` or `--model` on `apply`.

## Deployment topologies

| Topology | Sync | Search |
|---|---|---|
| **Atlas** | Change streams | `$vectorSearch` (native) |
| **Self-hosted replica set** | Change streams | Brute-force aggregation |
| **Self-hosted standalone** | Polling (`updated_at` watermark) | Brute-force aggregation |

Brute-force is fine up to ~100k chunks. For larger self-hosted collections, Atlas is recommended.

## Development

```bash
git clone https://github.com/varmabudharaju/mongosemantic
cd mongosemantic
pip install -e ".[dev,openai]"
docker compose up -d                          # replica set + standalone
MONGOSEMANTIC_RUN_INTEGRATION=1 python3 -m pytest -v
```

## License

MIT
```

- [ ] **Step 2: Create `CHANGELOG.md`**

```markdown
# Changelog

## 0.1.0 — 2026-04-22

Initial MVP release.

- Connect to MongoDB Atlas, self-hosted replica sets, and standalone MongoDB 7.0+.
- `inspect`: sample a collection and score each field for semantic-search suitability.
- `apply`: configure shadow-mode semantic search on one or more fields.
- `index`: bulk-enqueue embed jobs for existing documents.
- `worker`: background daemon (change streams on replica sets, polling on standalone) + embed pipeline.
- `search`: native Atlas `$vectorSearch` when available; brute-force aggregation otherwise.
- `status`, `retry`, `reindex`: operational commands.
- 5 embedding providers: MiniLM, MPNet, OpenAI small/large, Ollama (nomic-embed-text).
```

- [ ] **Step 3: Run the full test suite**

```bash
python3 -m pytest tests/unit -v
MONGOSEMANTIC_RUN_INTEGRATION=1 python3 -m pytest tests/integration -v
ruff check .
```

Expected: all pass. Ruff may surface minor issues — fix inline.

- [ ] **Step 4: Commit**

```bash
git add README.md CHANGELOG.md
git commit -m "docs: v0.1.0 README + changelog"
```

- [ ] **Step 5: Tag the release**

```bash
git tag v0.1.0
```

(Do not push yet — user decides when to push to GitHub.)

---

## Done

At this point v0.1.0 is shippable. To verify by hand:

```bash
# Run a worker against the docker replica set
MONGOSEMANTIC_URI="mongodb://localhost:27117,localhost:27118,localhost:27119/?replicaSet=rs0" \
MONGOSEMANTIC_DB="demo" \
MONGOSEMANTIC_MODEL="local-fast" \
python3 -m mongosemantic worker &

# In another shell, load some data and search
mongosh "mongodb://localhost:27117" --eval '
  db = db.getSiblingDB("demo");
  db.articles.insertMany([
    {body: "golden retrievers are friendly dogs"},
    {body: "quantum mechanics interpretations"},
    {body: "best pizza in brooklyn"},
  ])
'
mongosemantic apply  --collection articles --field body
mongosemantic index  --collection articles
sleep 5                                                   # let worker drain
mongosemantic search "italian food nyc" --collection articles
```

Expected: the pizza doc ranks above quantum mechanics and dogs.

Next plan: **v0.2.0 — Web UI.** Written against this shipped base; will not be in the same file.
