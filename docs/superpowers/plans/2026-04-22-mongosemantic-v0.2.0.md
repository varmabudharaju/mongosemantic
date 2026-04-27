# mongosemantic v0.2.0 Implementation Plan — Web UI

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a working web dashboard for the v0.1.0 CLI feature set — connect, inspect, apply, index, search, status, retry, reindex — plus a safe read-only aggregation runner. Localhost-bound, no auth, vanilla HTML/JS/CSS frontend, FastAPI backend. Visualize and MCP-integration pages are scaffolded as placeholders for v0.3.0/v0.4.0 but ship visible in the nav.

**Architecture:** A `mongosemantic/web/` package adds a FastAPI app exposed via a new `mongosemantic ui` CLI command. The backend is a thin layer over the v0.1.0 modules — every route delegates to `db/`, `state/`, `embeddings/`, `search/`, etc. The frontend is a single-page vanilla-JS app (`index.html` + `app.js` + `style.css`) that fetches JSON. **All user-facing strings live in `mongosemantic/web/content.py`** so the visual layer can be redesigned without touching backend code. Security: CSRF (double-submit cookie), rate limit (120 req/min/IP), security headers, identifier validation, safe-aggregation stage allowlist.

**Tech stack:** Python 3.10+, FastAPI 0.110+, uvicorn 0.30+, starlette (transitive), pydantic 2.x (already in deps), python-multipart (form uploads if needed). Frontend: vanilla HTML5/ES2020+/CSS, no build step, no external runtime deps.

---

## File structure

```
mongosemantic/
├── pyproject.toml                              # Task 1 — add fastapi, uvicorn deps
├── mongosemantic/
│   ├── cli.py                                  # Task 3 — register `ui` command
│   ├── commands/
│   │   └── ui.py                               # Task 3 — new: launches FastAPI
│   ├── web/
│   │   ├── __init__.py                         # Task 2
│   │   ├── app.py                              # Task 2 — FastAPI app factory
│   │   ├── content.py                          # Task 2 — ALL UI strings live here
│   │   ├── security.py                         # Task 4 — CSRF, rate limit, headers
│   │   ├── identifiers.py                      # Task 4 — name regex validators
│   │   ├── routes/
│   │   │   ├── __init__.py
│   │   │   ├── system.py                       # Task 5 — /topology, /connect, /healthz
│   │   │   ├── collections.py                  # Task 6 — list + inspect
│   │   │   ├── apply.py                        # Task 7 — POST /apply
│   │   │   ├── index.py                        # Task 8 — POST /index, GET /index/{coll}/progress
│   │   │   ├── search.py                       # Task 9 — GET /search
│   │   │   ├── aggregation.py                  # Task 10 — POST /aggregation (safe)
│   │   │   ├── dashboard.py                    # Task 11 — GET /dashboard, /jobs, retry, reindex
│   │   │   └── ui.py                           # Task 12 — serve index.html + static files
│   │   ├── safe_pipeline.py                    # Task 10 — pipeline allowlist parser
│   │   ├── progress.py                         # Task 8 — in-memory progress registry
│   │   └── static/
│   │       ├── index.html                      # Task 13 — SPA shell
│   │       ├── app.js                          # Task 14 — client-side routing + fetch
│   │       └── style.css                       # Task 14 — minimal utilitarian styles
└── tests/
    ├── unit/
    │   ├── test_web_security.py                # Task 4
    │   ├── test_web_identifiers.py             # Task 4
    │   ├── test_route_system.py                # Task 5
    │   ├── test_route_collections.py           # Task 6
    │   ├── test_route_apply.py                 # Task 7
    │   ├── test_route_index.py                 # Task 8
    │   ├── test_route_search.py                # Task 9
    │   ├── test_safe_pipeline.py               # Task 10
    │   ├── test_route_aggregation.py           # Task 10
    │   ├── test_route_dashboard.py             # Task 11
    │   └── test_route_ui.py                    # Task 12
    └── integration/
        └── test_web_e2e.py                     # Task 17
```

**Total new files: ~20 source + ~12 test + 3 static.** Modifies `pyproject.toml`, `cli.py`, `mongosemantic/__init__.py` (version bump), `README.md`, `CHANGELOG.md`.

---

## Task 1: Add web dependencies + version bump

**Files:**
- Modify: `pyproject.toml`
- Modify: `mongosemantic/__init__.py`

- [ ] **Step 1: Edit `pyproject.toml` — add web deps**

In the `dependencies = [...]` array, add:

```
  "fastapi>=0.110",
  "uvicorn[standard]>=0.30",
  "python-multipart>=0.0.9",
  "itsdangerous>=2.2",
```

(`python-multipart` enables form parsing if we later need multipart uploads; `itsdangerous` is used for CSRF token signing.)

- [ ] **Step 2: Bump version**

Edit `mongosemantic/__init__.py`:
```python
__version__ = "0.2.0-dev"
```

(Will become `"0.2.0"` in Task 19's release commit.)

Edit `pyproject.toml` `version = "0.2.0"` (drop the `-dev` for the wheel — version-strings on the wheel are PEP 440-strict; we mark dev internally only).

- [ ] **Step 3: Reinstall**

```bash
cd /Users/varma/mongosemantic && python3 -m pip install -e ".[dev,openai]"
```

Expected: pulls fastapi + uvicorn + python-multipart + itsdangerous; everything else cached.

- [ ] **Step 4: Verify imports work**

```bash
python3 -c "import fastapi, uvicorn, itsdangerous, multipart; print('ok')"
```

Expected: `ok`.

- [ ] **Step 5: Verify existing tests still pass**

```bash
python3 -m pytest tests/unit -v
```

Expected: 76 pass (no regressions from dependency bump).

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml mongosemantic/__init__.py
git commit -m "chore: add web deps (fastapi, uvicorn, multipart, itsdangerous) + bump to 0.2.0"
```

---

## Task 2: Web package skeleton + content constants module

**Files:**
- Create: `mongosemantic/web/__init__.py`
- Create: `mongosemantic/web/app.py`
- Create: `mongosemantic/web/content.py`
- Create: `mongosemantic/web/routes/__init__.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_web_app.py`:

```python
from fastapi.testclient import TestClient
from mongosemantic.web.app import create_app

def test_app_creates_with_default_settings(monkeypatch):
    monkeypatch.setenv("MONGOSEMANTIC_URI", "mongodb://fake")
    monkeypatch.setenv("MONGOSEMANTIC_DB", "d")
    monkeypatch.setenv("MONGOSEMANTIC_MODEL", "local-fast")
    app = create_app()
    assert app is not None
    client = TestClient(app)
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True}
```

- [ ] **Step 2: Run — expect failure (module missing)**

```bash
python3 -m pytest tests/unit/test_web_app.py -v
```

- [ ] **Step 3: Create `mongosemantic/web/__init__.py`**

```python
from mongosemantic.web.app import create_app

__all__ = ["create_app"]
```

- [ ] **Step 4: Create `mongosemantic/web/content.py`**

```python
"""All user-facing strings used in the web UI.

This is the single source of truth for copy. The frontend (HTML/JS/CSS) reads
these via the /api/content endpoint, so editing copy here flows directly to the
UI without touching templates.
"""
from __future__ import annotations

CONTENT: dict[str, dict[str, str]] = {
    "connection": {
        "title": "Connect to MongoDB",
        "subtitle": "Paste a connection string. We'll detect your deployment and set things up from there.",
        "uri_label": "Connection URI",
        "uri_placeholder": "mongodb+srv://user:pass@cluster.mongodb.net/your_db",
        "db_label": "Database",
        "db_helper": "Leave blank to use the database in the URI.",
        "test_button": "Test connection",
        "footer_note": "Your connection string is stored in .env with chmod 600. It's never sent to the browser.",
        "state_connecting": "Testing connection…",
        "state_atlas": 'Connected to Atlas cluster "{name}" — native $vectorSearch available.',
        "state_replica": 'Connected to replica set "{name}" — change streams enabled, brute-force search until $vectorSearch is configured.',
        "state_standalone": "Connected to standalone MongoDB — polling mode will be used (check collection has an updated_at field for best results).",
        "error_auth": "Authentication failed. Check username, password, and that your IP is allowed in Atlas > Network Access.",
        "error_network": "Couldn't reach that host. Check the URI and try again.",
        "error_version": "MongoDB {version} is below the minimum supported version (7.0). Please upgrade or use a newer cluster.",
    },
    "collections": {
        "title": "Collections",
        "subtitle": "Pick a collection to inspect. We'll score each field for how well it fits semantic search.",
        "col_collection": "Collection",
        "col_documents": "Documents",
        "col_avg_size": "Avg size",
        "col_status": "Status",
        "row_action": "Inspect →",
        "status_not_configured": "Not configured",
        "status_configured": "Configured ({n} fields)",
        "status_indexing": "Indexing… ({n}/{total})",
        "status_ready": "Ready",
        "status_migrating": "Migrating",
        "status_failed": "Failed",
        "empty_title": "No collections yet",
        "empty_body": "This database doesn't have any collections. Add some data, then come back.",
    },
    "inspect": {
        "title": "Inspect {collection}",
        "subtitle": "We sampled {n} documents. Here's what we found.",
        "col_field": "Field path",
        "col_type": "Type",
        "col_coverage": "Coverage",
        "col_avg_length": "Avg length",
        "col_suitability": "Suitability",
        "col_action": "Action",
        "band_great": "Great",
        "band_good": "Good",
        "band_usable": "Usable",
        "band_not_recommended": "Not recommended",
        "tooltip_great": "Text field, well populated, varied content. Embed this.",
        "tooltip_good": "Usable for search. Try it.",
        "tooltip_usable": "Short or sparse. Combining with another field may help.",
        "tooltip_not_recommended": "This looks like a label or ID, not content.",
        "tooltip_array_subdoc": "Each array element gets its own embedding.",
        "action_embed": "Embed",
        "action_combine": "Combine with…",
    },
    "apply": {
        "title": "Configure semantic search",
        "subtitle": "Pick fields, a mode, and a model. You can change any of this later.",
        "section_fields": "Fields",
        "section_mode": "Mode",
        "mode_shadow": "Shadow collection (recommended)",
        "mode_shadow_helper": 'Embeddings live in "{collection}_embeddings". Your original documents are never modified.',
        "mode_inline": "Inline field",
        "mode_inline_helper": 'Embeddings live in an "_embedding" field on each source document. Faster on Atlas, mutates your documents.',
        "mode_chunk_notice": "Chunking requires shadow mode. We'll use shadow for this collection.",
        "section_chunking": "Chunking",
        "chunking_toggle": "Split long text into overlapping chunks",
        "chunking_help": "Chunking finds the best paragraph, not just the best document. Enable for text longer than ~1000 characters.",
        "chunking_size": "Chunk size",
        "chunking_overlap": "Overlap",
        "section_model": "Model",
        "model_local_fast": "Local Fast (MiniLM, 384d)",
        "model_local_fast_helper": "Free. Runs on your machine. Good for most use cases.",
        "model_local_better": "Local Better (MPNet, 768d)",
        "model_local_better_helper": "Free. More accurate, slower. Good for nuanced content.",
        "model_openai_small": "OpenAI Small (text-embedding-3-small, 1536d)",
        "model_openai_small_helper": "~$0.02/1M tokens. Requires OPENAI_API_KEY. Multilingual.",
        "model_openai_large": "OpenAI Large (text-embedding-3-large, 3072d)",
        "model_openai_large_helper": "~$0.13/1M tokens. Maximum accuracy.",
        "model_ollama_nomic": "Ollama (nomic-embed-text, 768d)",
        "model_ollama_nomic_helper": "Self-hosted via Ollama. Requires OLLAMA_HOST.",
        "cta_apply": "Apply configuration →",
        "notice_atlas": 'We\'ll create a $vectorSearch index named "mongosemantic_{collection}_{field}". This takes ~1 minute.',
        "notice_self_hosted": "No vector index will be created. Search uses brute-force aggregation — fine up to ~100k documents.",
    },
    "indexing": {
        "title": "Indexing {collection}",
        "subtitle": "Embedding existing documents. You can close this page — it runs in the background.",
        "metric_progress": "{processed} / {total} documents",
        "metric_rate": "{rate} docs/sec",
        "metric_eta": "ETA {duration}",
        "btn_pause": "Pause",
        "btn_cancel": "Cancel",
        "toast_started": "Indexing started.",
        "toast_paused": "Indexing paused at {n}/{total}.",
        "toast_resumed": "Indexing resumed.",
        "toast_complete": "Indexing complete — {n} documents embedded.",
        "toast_failed": "Indexing failed on {n} documents. Run retry from the dashboard.",
    },
    "search": {
        "placeholder": 'Search by meaning — "budget travel", "unhappy customers", "legal risk"',
        "toggle_hybrid": "Hybrid",
        "tooltip_hybrid": "Combines semantic similarity with keyword matching. Requires Atlas Search index.",
        "toggle_filter": "Filter…",
        "selector_all": "All configured collections",
        "result_score": "Score",
        "result_view_full": "View full document ↗",
        "empty_no_query": "Type a query above to search by meaning.",
        "empty_no_results": "No matches. Try a broader phrase, or switch to hybrid search.",
        "empty_not_configured": "No collections are configured yet. Go to Collections to set one up.",
    },
    "visualize": {
        "title": "Explore {collection}",
        "subtitle": "Documents laid out by meaning. Clusters are grouped by similarity, labeled by their top keywords.",
        "control_collection": "Collection",
        "control_clusters": "Clusters",
        "control_refresh": "Refresh",
        "empty_too_few": "Not enough embeddings to visualize. Index at least 50 documents first.",
        "tooltip_point": "Cluster: {label} · Score: {score} · Click for details",
        "coming_in": "Visualization arrives in v0.4.0.",
    },
    "aggregation": {
        "title": "Aggregation query",
        "subtitle": "Run read-only aggregation pipelines. Read-only, 10-second timeout, 100-document limit.",
        "editor_label": "Pipeline (JSON array of stages)",
        "default_pipeline": '[{ "$match": {} }, { "$limit": 20 }]',
        "safety_banner": "Blocked stages: $out, $merge, $function, any write operation. We parse before running.",
        "btn_run": "Run",
        "error_rejected": "Pipeline rejected: {reason}",
    },
    "dashboard": {
        "card_collections": "Configured collections",
        "card_total_embeddings": "Total embeddings",
        "card_coverage": "Coverage",
        "card_pending": "Pending jobs",
        "card_failed": "Failed jobs",
        "card_worker": "Worker status",
        "card_last_event": "Last change-stream event",
        "action_retry_all": "Retry all →",
        "topology_atlas": "Atlas cluster · $vectorSearch enabled",
        "topology_replica": "Replica set · change streams · brute-force search",
        "topology_standalone": "Standalone · polling (every {n}s)",
    },
    "mcp": {
        "title": "AI agent integration",
        "subtitle": "Connect Claude Desktop, Cursor, or any MCP-compatible AI agent to your MongoDB.",
        "claude_block_label": "Claude Desktop config",
        "coming_in": "MCP integration arrives in v0.3.0.",
    },
    "global": {
        "toast_saved": "Saved.",
        "toast_config_updated": "Configuration updated.",
        "toast_mongo_unreachable": "Couldn't reach MongoDB. Retrying…",
        "toast_provider_error": "Provider error: {summary}. We'll retry automatically.",
        "toast_jobs_healthy": "Job queue is healthy.",
        "toast_jobs_lagging": "Job queue is lagging by {n} items.",
        "toast_rate_limited": "Rate limited. Retrying in {n}s.",
        "onboarding_intro": "Connect a MongoDB database to begin. Once connected, we'll walk you through:",
        "onboarding_step1": "Picking a collection.",
        "onboarding_step2": "Choosing a field to make searchable.",
        "onboarding_step3": "Indexing your existing data.",
        "onboarding_summary": "Most users are set up in under a minute.",
        "nav_connection": "Connection",
        "nav_collections": "Collections",
        "nav_search": "Search",
        "nav_query": "Query",
        "nav_dashboard": "Dashboard",
        "nav_visualize": "Visualize",
        "nav_mcp": "MCP",
    },
}
```

- [ ] **Step 5: Create `mongosemantic/web/app.py`**

```python
from __future__ import annotations
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from mongosemantic import __version__
from mongosemantic.web.content import CONTENT

def create_app() -> FastAPI:
    app = FastAPI(
        title="mongosemantic",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/healthz")
    def _healthz() -> JSONResponse:
        return JSONResponse({"ok": True})

    @app.get("/api/content")
    def _content() -> JSONResponse:
        return JSONResponse(CONTENT)

    return app
```

- [ ] **Step 6: Create empty `mongosemantic/web/routes/__init__.py`**

```python
```

(Empty file — directory marker only. Real route modules added in later tasks.)

- [ ] **Step 7: Run test — expect pass**

```bash
python3 -m pytest tests/unit/test_web_app.py -v
```

Expected: 1 PASS.

- [ ] **Step 8: Verify `/api/content` returns the expected shape**

```bash
python3 -c "
from fastapi.testclient import TestClient
from mongosemantic.web.app import create_app
import os
os.environ['MONGOSEMANTIC_URI'] = 'mongodb://x'
os.environ['MONGOSEMANTIC_DB'] = 'd'
c = TestClient(create_app())
r = c.get('/api/content')
assert r.status_code == 200
data = r.json()
assert 'connection' in data
assert 'global' in data
assert data['connection']['title'] == 'Connect to MongoDB'
print('ok')
"
```

Expected: `ok`.

- [ ] **Step 9: Commit**

```bash
git add mongosemantic/web/ tests/unit/test_web_app.py
git commit -m "feat(web): FastAPI app skeleton + centralized content strings"
```

---

## Task 3: `mongosemantic ui` CLI command

**Files:**
- Create: `mongosemantic/commands/ui.py`
- Modify: `mongosemantic/cli.py`
- Create: `tests/unit/test_cmd_ui.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_cmd_ui.py`:

```python
from unittest.mock import patch
from typer.testing import CliRunner
from mongosemantic.cli import app

runner = CliRunner()

def test_ui_command_invokes_uvicorn(monkeypatch):
    monkeypatch.setenv("MONGOSEMANTIC_URI", "mongodb://x")
    monkeypatch.setenv("MONGOSEMANTIC_DB", "d")
    monkeypatch.setenv("MONGOSEMANTIC_MODEL", "local-fast")
    with patch("mongosemantic.commands.ui.uvicorn.run") as fake_run:
        r = runner.invoke(app, ["ui", "--port", "9999"])
        assert r.exit_code == 0, r.output
        fake_run.assert_called_once()
        kwargs = fake_run.call_args.kwargs
        assert kwargs["host"] == "127.0.0.1"
        assert kwargs["port"] == 9999
```

- [ ] **Step 2: Verify fail**

```bash
python3 -m pytest tests/unit/test_cmd_ui.py -v
```

Expected: FAIL — `ui` command not registered.

- [ ] **Step 3: Create `mongosemantic/commands/ui.py`**

```python
from __future__ import annotations
import typer
import uvicorn
from rich.console import Console
from mongosemantic.web.app import create_app

console = Console()

def ui_cmd(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host. Default localhost-only."),
    port: int = typer.Option(8080, "--port"),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code changes (dev only)."),
) -> None:
    """Launch the web dashboard."""
    if host != "127.0.0.1":
        console.print(
            f"[yellow]Binding to {host} (not localhost). "
            f"This UI has no built-in auth — put it behind your own auth proxy.[/yellow]"
        )
    console.print(f"[green]mongosemantic UI → http://{host}:{port}[/green]")
    if reload:
        uvicorn.run(
            "mongosemantic.web.app:create_app",
            host=host,
            port=port,
            reload=True,
            factory=True,
        )
    else:
        app = create_app()
        uvicorn.run(app, host=host, port=port)
```

- [ ] **Step 4: Register the command in `mongosemantic/cli.py`**

Add to the imports block at the top of `cli.py`:

```python
from mongosemantic.commands import ui as _ui_mod         # noqa: E402
```

Then add to the command registrations near the other `app.command` calls:

```python
app.command("ui")(_ui_mod.ui_cmd)
```

- [ ] **Step 5: Run test — expect pass**

```bash
python3 -m pytest tests/unit/test_cmd_ui.py -v
```

Expected: 1 PASS.

- [ ] **Step 6: Verify CLI shows new command**

```bash
python3 -m mongosemantic --help | grep ui
```

Expected: a line containing `ui  Launch the web dashboard.`

- [ ] **Step 7: Smoke-test the server (briefly)**

```bash
cd /Users/varma/mongosemantic && (
  MONGOSEMANTIC_URI="mongodb://localhost:27117/?replicaSet=rs0" \
  MONGOSEMANTIC_DB="demo" \
  MONGOSEMANTIC_MODEL="local-fast" \
  python3 -m mongosemantic ui --port 18080 &
)
SERVER_PID=$!
sleep 2
curl -s http://127.0.0.1:18080/healthz
kill "$SERVER_PID" 2>/dev/null
```

Expected: `{"ok":true}`.

- [ ] **Step 8: Commit**

```bash
git add mongosemantic/commands/ui.py mongosemantic/cli.py tests/unit/test_cmd_ui.py
git commit -m "feat(cli): ui command launches the FastAPI dashboard via uvicorn"
```

---

## Task 4: Security middleware (CSRF, rate limit, headers) + identifier validators

**Files:**
- Create: `mongosemantic/web/security.py`
- Create: `mongosemantic/web/identifiers.py`
- Modify: `mongosemantic/web/app.py`
- Create: `tests/unit/test_web_security.py`
- Create: `tests/unit/test_web_identifiers.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_web_identifiers.py`:

```python
import pytest
from mongosemantic.web.identifiers import validate_identifier, IdentifierError

def test_accepts_simple_name():
    assert validate_identifier("articles") == "articles"

def test_accepts_dotted_path():
    assert validate_identifier("user.profile.bio") == "user.profile.bio"

def test_accepts_array_subdoc_path():
    assert validate_identifier("comments[].body") == "comments[].body"

def test_rejects_dollar_sign():
    with pytest.raises(IdentifierError):
        validate_identifier("$where")

def test_rejects_null_byte():
    with pytest.raises(IdentifierError):
        validate_identifier("articles\x00body")

def test_rejects_empty():
    with pytest.raises(IdentifierError):
        validate_identifier("")

def test_rejects_long():
    with pytest.raises(IdentifierError):
        validate_identifier("a" * 200)
```

Create `tests/unit/test_web_security.py`:

```python
import time
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mongosemantic.web.security import (
    install_security_headers, install_csrf, install_rate_limit, CSRF_HEADER,
)

def _csrf_app():
    app = FastAPI()
    install_csrf(app)
    @app.get("/r")
    def _r(): return {"ok": True}
    @app.post("/w")
    def _w(): return {"ok": True}
    return app

def test_get_emits_csrf_cookie():
    client = TestClient(_csrf_app())
    r = client.get("/r")
    assert r.status_code == 200
    assert "csrftoken" in r.cookies

def test_post_without_csrf_token_is_forbidden():
    client = TestClient(_csrf_app())
    client.get("/r")  # populate cookie
    r = client.post("/w")
    assert r.status_code == 403

def test_post_with_matching_token_succeeds():
    client = TestClient(_csrf_app())
    g = client.get("/r")
    token = g.cookies.get("csrftoken")
    r = client.post("/w", headers={CSRF_HEADER: token})
    assert r.status_code == 200

def test_security_headers_installed():
    app = FastAPI()
    install_security_headers(app)
    @app.get("/x")
    def _x(): return {}
    client = TestClient(app)
    r = client.get("/x")
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert "Content-Security-Policy" in r.headers
    assert "Referrer-Policy" in r.headers

def test_rate_limit_blocks_excess():
    app = FastAPI()
    install_rate_limit(app, limit=3, window_seconds=60)
    @app.get("/x")
    def _x(): return {}
    client = TestClient(app)
    for _ in range(3):
        assert client.get("/x").status_code == 200
    assert client.get("/x").status_code == 429
```

- [ ] **Step 2: Run — expect fail**

```bash
python3 -m pytest tests/unit/test_web_security.py tests/unit/test_web_identifiers.py -v
```

Expected: import errors.

- [ ] **Step 3: Create `mongosemantic/web/identifiers.py`**

```python
from __future__ import annotations
import re

# Allows: letters, digits, underscore, dot, hyphen, brackets for array notation.
# No $ (which would let users name a field "$where" and trip Mongo operators).
# No quote characters, no null, no whitespace.
_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_\-.\[\]]{0,127}$")

class IdentifierError(ValueError):
    pass

def validate_identifier(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise IdentifierError("identifier must be non-empty string")
    if "\x00" in value or "$" in value:
        raise IdentifierError("identifier contains forbidden character")
    if len(value) > 128:
        raise IdentifierError("identifier exceeds 128 chars")
    if not _NAME_RE.match(value):
        raise IdentifierError(f"identifier {value!r} does not match required shape")
    return value
```

- [ ] **Step 4: Create `mongosemantic/web/security.py`**

```python
from __future__ import annotations
import secrets
import time
from collections import defaultdict, deque
from typing import Callable
from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

CSRF_COOKIE = "csrftoken"
CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

class _CSRFMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        cookie_token = request.cookies.get(CSRF_COOKIE)
        if request.method not in SAFE_METHODS:
            header_token = request.headers.get(CSRF_HEADER, "")
            if not cookie_token or not header_token or not secrets.compare_digest(
                cookie_token, header_token
            ):
                return Response("CSRF token missing or mismatched", status_code=403)
        response = await call_next(request)
        if cookie_token is None:
            new_token = secrets.token_urlsafe(32)
            response.set_cookie(
                CSRF_COOKIE,
                new_token,
                httponly=False,  # JS needs to read it to echo into header
                samesite="strict",
                secure=False,    # local-only by default; set true behind HTTPS
                path="/",
            )
        return response

class _SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'"
        )
        return response

class _RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, limit: int = 120, window_seconds: int = 60) -> None:
        super().__init__(app)
        self.limit = limit
        self.window = window_seconds
        self.history: dict[str, deque[float]] = defaultdict(deque)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        ip = request.client.host if request.client else "unknown"
        now = time.monotonic()
        cutoff = now - self.window
        q = self.history[ip]
        while q and q[0] < cutoff:
            q.popleft()
        if len(q) >= self.limit:
            return Response("rate limit exceeded", status_code=429)
        q.append(now)
        return await call_next(request)

def install_csrf(app: FastAPI) -> None:
    app.add_middleware(_CSRFMiddleware)

def install_security_headers(app: FastAPI) -> None:
    app.add_middleware(_SecurityHeadersMiddleware)

def install_rate_limit(app: FastAPI, limit: int = 120, window_seconds: int = 60) -> None:
    app.add_middleware(_RateLimitMiddleware, limit=limit, window_seconds=window_seconds)
```

- [ ] **Step 5: Wire middleware into `mongosemantic/web/app.py`**

Replace the existing `create_app()` body with:

```python
def create_app() -> FastAPI:
    app = FastAPI(
        title="mongosemantic",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    install_security_headers(app)
    install_rate_limit(app, limit=120, window_seconds=60)
    install_csrf(app)

    @app.get("/healthz")
    def _healthz() -> JSONResponse:
        return JSONResponse({"ok": True})

    @app.get("/api/content")
    def _content() -> JSONResponse:
        return JSONResponse(CONTENT)

    return app
```

Add at top of file:
```python
from mongosemantic.web.security import (
    install_csrf, install_rate_limit, install_security_headers,
)
```

- [ ] **Step 6: Run all tests — expect pass**

```bash
python3 -m pytest tests/unit/test_web_security.py tests/unit/test_web_identifiers.py tests/unit/test_web_app.py -v
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add mongosemantic/web/security.py mongosemantic/web/identifiers.py mongosemantic/web/app.py tests/unit/test_web_security.py tests/unit/test_web_identifiers.py
git commit -m "feat(web): security middleware (CSRF, rate limit, headers) + identifier validators"
```

---

## Task 5: System routes — `/api/topology`, `/api/connect`

**Files:**
- Create: `mongosemantic/web/routes/system.py`
- Modify: `mongosemantic/web/app.py`
- Create: `tests/unit/test_route_system.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_route_system.py`:

```python
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from mongosemantic.web.app import create_app
from mongosemantic.web.security import CSRF_HEADER

def _client(monkeypatch):
    monkeypatch.setenv("MONGOSEMANTIC_URI", "mongodb://x")
    monkeypatch.setenv("MONGOSEMANTIC_DB", "d")
    monkeypatch.setenv("MONGOSEMANTIC_MODEL", "local-fast")
    return TestClient(create_app())

def test_topology_returns_atlas_for_atlas_uri(monkeypatch):
    client = _client(monkeypatch)
    fake_conn = MagicMock()
    from mongosemantic.db.client import Topology
    fake_conn.topology = Topology.ATLAS
    fake_conn.close = MagicMock()
    with patch(
        "mongosemantic.web.routes.system.MongoConnection.open",
        return_value=fake_conn,
    ):
        r = client.get("/api/topology")
        assert r.status_code == 200
        assert r.json() == {"topology": "atlas"}

def test_connect_post_returns_topology_when_ok(monkeypatch):
    client = _client(monkeypatch)
    fake_conn = MagicMock()
    from mongosemantic.db.client import Topology
    fake_conn.topology = Topology.STANDALONE
    fake_conn.close = MagicMock()
    seed = client.get("/api/topology")  # populate CSRF cookie
    token = seed.cookies.get("csrftoken")
    with patch(
        "mongosemantic.web.routes.system.MongoConnection.open",
        return_value=fake_conn,
    ):
        r = client.post(
            "/api/connect",
            json={"uri": "mongodb://localhost", "database": "x"},
            headers={CSRF_HEADER: token},
        )
        assert r.status_code == 200, r.text
        assert r.json()["topology"] == "standalone"

def test_connect_post_rejects_bad_scheme(monkeypatch):
    client = _client(monkeypatch)
    seed = client.get("/api/topology")
    token = seed.cookies.get("csrftoken")
    r = client.post(
        "/api/connect",
        json={"uri": "postgres://nope", "database": "x"},
        headers={CSRF_HEADER: token},
    )
    assert r.status_code == 400
    assert "mongodb" in r.json()["detail"].lower()
```

- [ ] **Step 2: Verify fail**

```bash
python3 -m pytest tests/unit/test_route_system.py -v
```

- [ ] **Step 3: Create `mongosemantic/web/routes/system.py`**

```python
from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from mongosemantic.config import Settings
from mongosemantic.db.client import MongoConnection

router = APIRouter()

class ConnectRequest(BaseModel):
    uri: str
    database: str

@router.get("/api/topology")
def topology() -> dict:
    settings = Settings()
    conn = MongoConnection.open(settings.uri, settings.database)
    try:
        return {"topology": conn.topology.value}
    finally:
        conn.close()

@router.post("/api/connect")
def connect(req: ConnectRequest) -> dict:
    if not (req.uri.startswith("mongodb://") or req.uri.startswith("mongodb+srv://")):
        raise HTTPException(
            status_code=400,
            detail="URI must start with mongodb:// or mongodb+srv://",
        )
    if not req.database:
        raise HTTPException(status_code=400, detail="database is required")
    try:
        conn = MongoConnection.open(req.uri, req.database)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"could not connect: {e}") from e
    try:
        return {"topology": conn.topology.value}
    finally:
        conn.close()
```

- [ ] **Step 4: Wire into `mongosemantic/web/app.py`**

Add at top:
```python
from mongosemantic.web.routes import system as _system_routes
```

Then inside `create_app()` after the `/healthz` endpoint:
```python
    app.include_router(_system_routes.router)
```

- [ ] **Step 5: Run tests — expect pass**

```bash
python3 -m pytest tests/unit/test_route_system.py -v
```

- [ ] **Step 6: Commit**

```bash
git add mongosemantic/web/routes/system.py mongosemantic/web/app.py tests/unit/test_route_system.py
git commit -m "feat(web): /api/topology + /api/connect routes"
```

---

## Task 6: Collections list + inspect

**Files:**
- Create: `mongosemantic/web/routes/collections.py`
- Modify: `mongosemantic/web/app.py`
- Create: `tests/unit/test_route_collections.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_route_collections.py`:

```python
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock
import mongomock
from fastapi.testclient import TestClient
from mongosemantic.web.app import create_app
from mongosemantic.state.config_store import CollectionConfig, FieldSpec, save_config

def _client_and_db(monkeypatch):
    monkeypatch.setenv("MONGOSEMANTIC_URI", "mongodb://x")
    monkeypatch.setenv("MONGOSEMANTIC_DB", "d")
    monkeypatch.setenv("MONGOSEMANTIC_MODEL", "local-fast")
    db = mongomock.MongoClient()["d"]
    return TestClient(create_app()), db

def _patch_conn(db):
    fake_conn = MagicMock()
    fake_conn.db = db
    from mongosemantic.db.client import Topology
    fake_conn.topology = Topology.STANDALONE
    fake_conn.close = MagicMock()
    return fake_conn

def test_collections_list_includes_all_user_collections(monkeypatch):
    client, db = _client_and_db(monkeypatch)
    db["articles"].insert_many([{"_id": i, "body": f"b{i}"} for i in range(3)])
    db["products"].insert_one({"_id": 1, "name": "x"})
    save_config(db, CollectionConfig(
        collection="articles", mode="shadow", shadow_collection="articles_embeddings",
        fields=[FieldSpec(path="body")], embedding_model="local-fast", embedding_dim=384,
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    ))
    with patch(
        "mongosemantic.web.routes.collections.MongoConnection.open",
        return_value=_patch_conn(db),
    ):
        r = client.get("/api/collections")
        assert r.status_code == 200
        rows = {row["name"]: row for row in r.json()["collections"]}
        assert "articles" in rows and "products" in rows
        assert rows["articles"]["status"] == "configured"
        assert rows["articles"]["fields_count"] == 1
        assert rows["products"]["status"] == "not_configured"

def test_inspect_returns_field_stats(monkeypatch):
    client, db = _client_and_db(monkeypatch)
    db["articles"].insert_many([
        {"title": "a", "body": "lorem ipsum dolor sit amet" * 20} for _ in range(20)
    ])
    with patch(
        "mongosemantic.web.routes.collections.MongoConnection.open",
        return_value=_patch_conn(db),
    ):
        r = client.get("/api/collections/articles/inspect?sample=20")
        assert r.status_code == 200
        body = r.json()
        paths = {f["path"] for f in body["fields"]}
        assert "title" in paths and "body" in paths

def test_inspect_rejects_bad_collection_name(monkeypatch):
    client, db = _client_and_db(monkeypatch)
    with patch(
        "mongosemantic.web.routes.collections.MongoConnection.open",
        return_value=_patch_conn(db),
    ):
        r = client.get("/api/collections/$evil/inspect")
        assert r.status_code == 400
```

- [ ] **Step 2: Verify fail**

- [ ] **Step 3: Create `mongosemantic/web/routes/collections.py`**

```python
from __future__ import annotations
from fastapi import APIRouter, HTTPException, Path, Query
from mongosemantic.config import Settings
from mongosemantic.db.client import MongoConnection
from mongosemantic.db.schema import inspect_collection, score_field
from mongosemantic.state import list_configured, count_by_status
from mongosemantic.web.identifiers import IdentifierError, validate_identifier

router = APIRouter()

def _band(score: int) -> str:
    if score >= 80: return "great"
    if score >= 60: return "good"
    if score >= 40: return "usable"
    return "not_recommended"

@router.get("/api/collections")
def list_collections() -> dict:
    settings = Settings()
    conn = MongoConnection.open(settings.uri, settings.database)
    try:
        configured = {c.collection: c for c in list_configured(conn.db)}
        rows = []
        for name in conn.db.list_collection_names():
            if name.startswith("mongosemantic_") or name.endswith("_embeddings"):
                continue
            cfg = configured.get(name)
            rows.append({
                "name": name,
                "status": "configured" if cfg else "not_configured",
                "fields_count": len(cfg.fields) if cfg else 0,
                "embedding_model": cfg.embedding_model if cfg else None,
            })
        return {"collections": rows, "topology": conn.topology.value}
    finally:
        conn.close()

@router.get("/api/collections/{name}/inspect")
def inspect(
    name: str = Path(...),
    sample: int = Query(500, ge=1, le=10000),
) -> dict:
    try:
        validate_identifier(name)
    except IdentifierError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    settings = Settings()
    conn = MongoConnection.open(settings.uri, settings.database)
    try:
        stats = inspect_collection(conn.db[name], sample_size=sample)
        fields = []
        for path, fs in stats.items():
            score = score_field(fs)
            coverage = 1 - (fs.null_count / max(1, fs.count))
            fields.append({
                "path": path,
                "type": fs.type_name,
                "count": fs.count,
                "null_count": fs.null_count,
                "avg_len": round(fs.avg_len, 1),
                "coverage": round(coverage, 3),
                "score": score,
                "band": _band(score),
            })
        fields.sort(key=lambda f: -f["score"])
        return {"collection": name, "sample_size": sample, "fields": fields}
    finally:
        conn.close()
```

- [ ] **Step 4: Wire into app.py**

Add to imports:
```python
from mongosemantic.web.routes import collections as _collections_routes
```

Add to `create_app()`:
```python
    app.include_router(_collections_routes.router)
```

- [ ] **Step 5: Run tests — expect pass**

```bash
python3 -m pytest tests/unit/test_route_collections.py -v
```

- [ ] **Step 6: Commit**

```bash
git add mongosemantic/web/routes/collections.py mongosemantic/web/app.py tests/unit/test_route_collections.py
git commit -m "feat(web): collections list + inspect routes"
```

---

## Task 7: Apply route

**Files:**
- Create: `mongosemantic/web/routes/apply.py`
- Modify: `mongosemantic/web/app.py`
- Create: `tests/unit/test_route_apply.py`

- [ ] **Step 1: Failing test**

Create `tests/unit/test_route_apply.py`:

```python
from unittest.mock import patch, MagicMock
import mongomock
from fastapi.testclient import TestClient
from mongosemantic.web.app import create_app
from mongosemantic.web.security import CSRF_HEADER

def _client_db(monkeypatch):
    monkeypatch.setenv("MONGOSEMANTIC_URI", "mongodb://x")
    monkeypatch.setenv("MONGOSEMANTIC_DB", "d")
    monkeypatch.setenv("MONGOSEMANTIC_MODEL", "local-fast")
    db = mongomock.MongoClient()["d"]
    return TestClient(create_app()), db

def _conn(db):
    from mongosemantic.db.client import Topology
    fake = MagicMock()
    fake.db = db
    fake.topology = Topology.STANDALONE
    fake.close = MagicMock()
    return fake

def test_apply_saves_config(monkeypatch):
    client, db = _client_db(monkeypatch)
    seed = client.get("/api/topology")
    token = seed.cookies.get("csrftoken")
    with patch("mongosemantic.web.routes.apply.MongoConnection.open", return_value=_conn(db)), \
         patch("mongosemantic.web.routes.system.MongoConnection.open", return_value=_conn(db)):
        r = client.post(
            "/api/collections/articles/apply",
            json={
                "fields": ["body"],
                "mode": "shadow",
                "chunked": False,
                "model": "local-fast",
            },
            headers={CSRF_HEADER: token},
        )
        assert r.status_code == 200, r.text
    from mongosemantic.state import load_config
    cfg = load_config(db, "articles")
    assert cfg is not None
    assert cfg.fields[0].path == "body"

def test_apply_rejects_bad_collection(monkeypatch):
    client, db = _client_db(monkeypatch)
    seed = client.get("/api/topology")
    token = seed.cookies.get("csrftoken")
    with patch("mongosemantic.web.routes.system.MongoConnection.open", return_value=_conn(db)):
        r = client.post(
            "/api/collections/$evil/apply",
            json={"fields": ["body"], "mode": "shadow", "chunked": False, "model": "local-fast"},
            headers={CSRF_HEADER: token},
        )
        assert r.status_code == 400

def test_apply_rejects_unknown_model(monkeypatch):
    client, db = _client_db(monkeypatch)
    seed = client.get("/api/topology")
    token = seed.cookies.get("csrftoken")
    with patch("mongosemantic.web.routes.apply.MongoConnection.open", return_value=_conn(db)), \
         patch("mongosemantic.web.routes.system.MongoConnection.open", return_value=_conn(db)):
        r = client.post(
            "/api/collections/articles/apply",
            json={"fields": ["body"], "mode": "shadow", "chunked": False, "model": "bogus"},
            headers={CSRF_HEADER: token},
        )
        assert r.status_code == 400
```

- [ ] **Step 2: Verify fail**

- [ ] **Step 3: Create `mongosemantic/web/routes/apply.py`**

```python
from __future__ import annotations
from datetime import datetime, timezone
from typing import Literal
from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel, Field
from mongosemantic.config import MODEL_DIMS, Settings
from mongosemantic.db.client import MongoConnection, Topology
from mongosemantic.db.indexes import (
    create_atlas_vector_index,
    ensure_shadow_indexes,
    shadow_collection_name,
    suggested_atlas_command,
)
from mongosemantic.state import (
    CollectionConfig,
    FieldSpec,
    ensure_indexes,
    save_config,
)
from mongosemantic.web.identifiers import IdentifierError, validate_identifier

router = APIRouter()

class ApplyRequest(BaseModel):
    fields: list[str] = Field(..., min_length=1)
    mode: Literal["shadow", "inline"] = "shadow"
    chunked: bool = False
    chunk_size: int = Field(512, ge=64, le=2048)
    chunk_overlap: int = Field(64, ge=0, le=256)
    model: str = "local-fast"

@router.post("/api/collections/{name}/apply")
def apply(name: str = Path(...), req: ApplyRequest = ...) -> dict:
    try:
        validate_identifier(name)
        for f in req.fields:
            validate_identifier(f)
    except IdentifierError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    if req.model not in MODEL_DIMS:
        raise HTTPException(status_code=400, detail=f"unknown model: {req.model}")

    notices: list[str] = []
    mode = req.mode
    if req.chunked and mode != "shadow":
        notices.append("chunking_forces_shadow")
        mode = "shadow"
    if mode != "shadow":
        notices.append("inline_not_supported_in_v0_2")
        mode = "shadow"

    settings = Settings()
    conn = MongoConnection.open(settings.uri, settings.database)
    try:
        db = conn.db
        ensure_indexes(db)
        shadow_name = shadow_collection_name(name)
        ensure_shadow_indexes(db[shadow_name])
        now = datetime.now(timezone.utc)
        cfg = CollectionConfig(
            collection=name,
            mode="shadow",
            shadow_collection=shadow_name,
            fields=[
                FieldSpec(
                    path=p,
                    chunked=req.chunked,
                    chunk_size=req.chunk_size,
                    chunk_overlap=req.chunk_overlap,
                )
                for p in req.fields
            ],
            embedding_model=req.model,
            embedding_dim=MODEL_DIMS[req.model],
            created_at=now,
            updated_at=now,
        )
        save_config(db, cfg)
        atlas_action: dict | None = None
        if conn.topology == Topology.ATLAS:
            try:
                created = []
                for p in req.fields:
                    created.append(
                        create_atlas_vector_index(db[shadow_name], name, p, MODEL_DIMS[req.model])
                    )
                atlas_action = {"status": "created", "names": created}
            except Exception as e:
                atlas_action = {
                    "status": "manual_required",
                    "error": str(e),
                    "commands": [
                        suggested_atlas_command(name, p, shadow_name, MODEL_DIMS[req.model])
                        for p in req.fields
                    ],
                }
        return {
            "ok": True,
            "topology": conn.topology.value,
            "shadow_collection": shadow_name,
            "notices": notices,
            "atlas": atlas_action,
        }
    finally:
        conn.close()
```

- [ ] **Step 4: Wire into app.py** — add `_apply_routes` import + `app.include_router(_apply_routes.router)`.

- [ ] **Step 5: Run tests — expect pass.**

- [ ] **Step 6: Commit**

```bash
git add mongosemantic/web/routes/apply.py mongosemantic/web/app.py tests/unit/test_route_apply.py
git commit -m "feat(web): apply route with topology-aware index creation"
```

---

## Task 8: Index route + in-memory progress registry

**Files:**
- Create: `mongosemantic/web/progress.py`
- Create: `mongosemantic/web/routes/index.py`
- Modify: `mongosemantic/web/app.py`
- Create: `tests/unit/test_route_index.py`

- [ ] **Step 1: Failing test**

Create `tests/unit/test_route_index.py`:

```python
import time
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock
import mongomock
from fastapi.testclient import TestClient
from mongosemantic.web.app import create_app
from mongosemantic.web.security import CSRF_HEADER
from mongosemantic.state.config_store import CollectionConfig, FieldSpec, save_config

def _client_db(monkeypatch):
    monkeypatch.setenv("MONGOSEMANTIC_URI", "mongodb://x")
    monkeypatch.setenv("MONGOSEMANTIC_DB", "d")
    monkeypatch.setenv("MONGOSEMANTIC_MODEL", "local-fast")
    db = mongomock.MongoClient()["d"]
    return TestClient(create_app()), db

def _conn(db):
    from mongosemantic.db.client import Topology
    fake = MagicMock(); fake.db = db; fake.topology = Topology.STANDALONE; fake.close = MagicMock()
    return fake

def test_start_index_enqueues_jobs(monkeypatch):
    client, db = _client_db(monkeypatch)
    save_config(db, CollectionConfig(
        collection="articles", mode="shadow", shadow_collection="articles_embeddings",
        fields=[FieldSpec(path="body")], embedding_model="local-fast", embedding_dim=384,
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    ))
    db["articles"].insert_many([{"_id": i, "body": f"t{i}"} for i in range(4)])
    seed = client.get("/api/topology")
    token = seed.cookies.get("csrftoken")
    with patch("mongosemantic.web.routes.index.MongoConnection.open", return_value=_conn(db)), \
         patch("mongosemantic.web.routes.system.MongoConnection.open", return_value=_conn(db)):
        r = client.post("/api/collections/articles/index", headers={CSRF_HEADER: token})
        assert r.status_code == 200
        body = r.json()
        assert body["enqueued"] == 4
    # progress endpoint
    r2 = client.get("/api/collections/articles/index/progress")
    assert r2.status_code == 200
    assert r2.json()["enqueued"] == 4
```

- [ ] **Step 2: Verify fail**

- [ ] **Step 3: Create `mongosemantic/web/progress.py`**

```python
from __future__ import annotations
import threading
from dataclasses import dataclass, field
from time import time

@dataclass
class IndexProgress:
    collection: str
    total: int = 0
    enqueued: int = 0
    started_at: float = field(default_factory=time)
    finished_at: float | None = None

_LOCK = threading.Lock()
_REGISTRY: dict[str, IndexProgress] = {}

def start(collection: str, total: int) -> IndexProgress:
    with _LOCK:
        p = IndexProgress(collection=collection, total=total)
        _REGISTRY[collection] = p
        return p

def bump(collection: str, n: int = 1) -> None:
    with _LOCK:
        p = _REGISTRY.get(collection)
        if p is not None:
            p.enqueued += n

def finish(collection: str) -> None:
    with _LOCK:
        p = _REGISTRY.get(collection)
        if p is not None:
            p.finished_at = time()

def get(collection: str) -> IndexProgress | None:
    with _LOCK:
        return _REGISTRY.get(collection)
```

- [ ] **Step 4: Create `mongosemantic/web/routes/index.py`**

```python
from __future__ import annotations
from fastapi import APIRouter, HTTPException, Path
from mongosemantic.config import Settings
from mongosemantic.db.client import MongoConnection
from mongosemantic.exceptions import NotConfiguredError
from mongosemantic.state import enqueue_embed, ensure_indexes, load_config
from mongosemantic.sync.change_stream import _get_path, _resolve_text, hash_text
from mongosemantic.web import progress
from mongosemantic.web.identifiers import IdentifierError, validate_identifier

router = APIRouter()

@router.post("/api/collections/{name}/index")
def start_index(name: str = Path(...)) -> dict:
    try:
        validate_identifier(name)
    except IdentifierError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    settings = Settings()
    conn = MongoConnection.open(settings.uri, settings.database)
    try:
        db = conn.db
        ensure_indexes(db)
        cfg = load_config(db, name)
        if not cfg:
            raise HTTPException(status_code=400, detail=f"{name} is not configured")
        total = db[name].estimated_document_count()
        progress.start(name, total)
        shadow = db[cfg.shadow_collection]
        enqueued = 0
        for doc in db[name].find({}):
            key = doc.get("_id")
            for spec in cfg.fields:
                text = _resolve_text(_get_path(doc, spec.path))
                if not text:
                    continue
                new_hash = hash_text(cfg.embedding_model, text)
                existing = shadow.find_one(
                    {
                        "source_id": key, "field_path": spec.path,
                        "chunk_index": 0, "embedding_model": cfg.embedding_model,
                    },
                    {"embedding_hash": 1},
                )
                if existing and existing.get("embedding_hash") == new_hash:
                    continue
                enqueue_embed(
                    db, collection=name, source_id=key, field_path=spec.path,
                    chunk_index=None, input_text=text, input_hash=new_hash,
                    model=cfg.embedding_model,
                )
                enqueued += 1
            progress.bump(name)
        progress.finish(name)
        return {"ok": True, "enqueued": enqueued, "total": total}
    finally:
        conn.close()

@router.get("/api/collections/{name}/index/progress")
def get_progress(name: str = Path(...)) -> dict:
    try:
        validate_identifier(name)
    except IdentifierError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    p = progress.get(name)
    if p is None:
        return {"collection": name, "running": False, "enqueued": 0, "total": 0}
    return {
        "collection": name,
        "running": p.finished_at is None,
        "enqueued": p.enqueued,
        "total": p.total,
        "started_at": p.started_at,
        "finished_at": p.finished_at,
    }
```

- [ ] **Step 5: Wire + run + commit**

Wire into app.py. Run `pytest tests/unit/test_route_index.py -v`. Commit:

```bash
git add mongosemantic/web/progress.py mongosemantic/web/routes/index.py mongosemantic/web/app.py tests/unit/test_route_index.py
git commit -m "feat(web): index route + in-memory progress registry"
```

---

## Task 9: Search route

**Files:**
- Create: `mongosemantic/web/routes/search.py`
- Modify: `mongosemantic/web/app.py`
- Create: `tests/unit/test_route_search.py`

- [ ] **Step 1: Failing test**

Create `tests/unit/test_route_search.py`:

```python
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock
import mongomock
import numpy as np
from fastapi.testclient import TestClient
from mongosemantic.web.app import create_app
from mongosemantic.state.config_store import CollectionConfig, FieldSpec, save_config

def _client_db(monkeypatch):
    monkeypatch.setenv("MONGOSEMANTIC_URI", "mongodb://x")
    monkeypatch.setenv("MONGOSEMANTIC_DB", "d")
    monkeypatch.setenv("MONGOSEMANTIC_MODEL", "local-fast")
    db = mongomock.MongoClient()["d"]
    return TestClient(create_app()), db

def _conn(db):
    from mongosemantic.db.client import Topology
    fake = MagicMock(); fake.db = db; fake.topology = Topology.STANDALONE; fake.close = MagicMock()
    return fake

def test_search_returns_rows(monkeypatch):
    client, db = _client_db(monkeypatch)
    save_config(db, CollectionConfig(
        collection="articles", mode="shadow", shadow_collection="articles_embeddings",
        fields=[FieldSpec(path="body")], embedding_model="local-fast", embedding_dim=3,
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    ))
    fake_provider = MagicMock()
    fake_provider.embed_batch = lambda texts: np.array([[1.0, 0.0, 0.0]], dtype=np.float32)
    fake_rows = [
        {"source_id": "a", "source_collection": "articles", "field_path": "body",
         "chunk_index": 0, "chunk_text": "match me", "score": 0.97},
    ]
    with patch("mongosemantic.web.routes.search.MongoConnection.open", return_value=_conn(db)), \
         patch("mongosemantic.web.routes.search.get_provider", return_value=fake_provider), \
         patch("mongosemantic.web.routes.search._run_one", return_value=fake_rows):
        r = client.get("/api/search?q=hello&collection=articles&limit=10")
        assert r.status_code == 200
        body = r.json()
        assert body["query"] == "hello"
        assert len(body["rows"]) == 1
        assert body["rows"][0]["chunk_text"] == "match me"
```

- [ ] **Step 2: Verify fail.**

- [ ] **Step 3: Create `mongosemantic/web/routes/search.py`**

```python
from __future__ import annotations
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from mongosemantic.config import Settings
from mongosemantic.db.client import MongoConnection, Topology
from mongosemantic.db.indexes import atlas_vector_index_exists, vector_index_name
from mongosemantic.embeddings.provider import get_provider
from mongosemantic.search.atlas import build_atlas_pipeline
from mongosemantic.search.brute_force import build_brute_pipeline
from mongosemantic.search.cross_collection import min_max_normalize, per_collection_targets
from mongosemantic.state import load_config
from mongosemantic.web.identifiers import IdentifierError, validate_identifier

router = APIRouter()

def _run_one(db, cfg, collection: str, qvec: list[float], limit: int, topology: Topology):
    field_path = cfg.fields[0].path
    shadow = db[cfg.shadow_collection]
    if topology == Topology.ATLAS and atlas_vector_index_exists(shadow, collection, field_path):
        pipeline = build_atlas_pipeline(
            source_collection=collection, field_path=field_path,
            query_vector=qvec, limit=limit,
            index_name=vector_index_name(collection, field_path),
        )
    else:
        pipeline = build_brute_pipeline(
            source_collection=collection, field_path=field_path,
            query_vector=qvec, limit=limit,
        )
    rows = list(shadow.aggregate(pipeline))
    for r in rows:
        r["source_collection"] = collection
    return rows

def _serialize(row: dict) -> dict:
    out = {k: row[k] for k in ("source_id", "source_collection", "field_path", "chunk_index", "chunk_text", "score") if k in row}
    if "source_doc" in row and isinstance(row["source_doc"], dict):
        out["source_doc"] = {k: v for k, v in row["source_doc"].items() if not k.startswith("_")}
        out["source_doc"]["_id"] = str(row["source_doc"].get("_id"))
    return out

@router.get("/api/search")
def search(
    q: str = Query(..., min_length=1, max_length=2000),
    collection: Optional[str] = Query(None),
    limit: int = Query(10, ge=1, le=100),
) -> dict:
    if collection:
        try:
            validate_identifier(collection)
        except IdentifierError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
    settings = Settings()
    conn = MongoConnection.open(settings.uri, settings.database)
    try:
        db = conn.db
        provider = get_provider(settings.model)
        qvec = provider.embed(q).tolist()
        if collection:
            cfg = load_config(db, collection)
            if not cfg:
                raise HTTPException(status_code=400, detail=f"{collection} is not configured")
            rows = _run_one(db, cfg, collection, qvec, limit, conn.topology)
        else:
            targets = per_collection_targets(db)
            if not targets:
                raise HTTPException(status_code=400, detail="no collections configured")
            all_rows: list[dict] = []
            models: dict[str, str] = {}
            for name in targets:
                cfg = load_config(db, name)
                if cfg is None:
                    continue
                models[name] = cfg.embedding_model
                all_rows.extend(_run_one(db, cfg, name, qvec, limit, conn.topology))
            if len(set(models.values())) > 1:
                all_rows = min_max_normalize(all_rows, "score")
            all_rows.sort(key=lambda r: r.get("score", 0), reverse=True)
            rows = all_rows[:limit]
        return {"query": q, "rows": [_serialize(r) for r in rows]}
    finally:
        conn.close()
```

- [ ] **Step 4: Wire + run + commit**

```bash
git add mongosemantic/web/routes/search.py mongosemantic/web/app.py tests/unit/test_route_search.py
git commit -m "feat(web): search route with Atlas/brute auto-select + cross-collection fanout"
```

---

## Task 10: Safe pipeline parser + aggregation route

**Files:**
- Create: `mongosemantic/web/safe_pipeline.py`
- Create: `mongosemantic/web/routes/aggregation.py`
- Modify: `mongosemantic/web/app.py`
- Create: `tests/unit/test_safe_pipeline.py`
- Create: `tests/unit/test_route_aggregation.py`

### 10A: safe_pipeline parser

- [ ] **Step 1: Failing test**

Create `tests/unit/test_safe_pipeline.py`:

```python
import pytest
from mongosemantic.web.safe_pipeline import validate_pipeline, PipelineSafetyError

def test_simple_match_is_allowed():
    validate_pipeline([{"$match": {"x": 1}}, {"$limit": 5}])

def test_out_is_rejected():
    with pytest.raises(PipelineSafetyError):
        validate_pipeline([{"$match": {}}, {"$out": "x"}])

def test_merge_is_rejected():
    with pytest.raises(PipelineSafetyError):
        validate_pipeline([{"$merge": "x"}])

def test_function_is_rejected_anywhere():
    with pytest.raises(PipelineSafetyError):
        validate_pipeline([{"$match": {"$expr": {"$function": {"body": "...", "args": [], "lang": "js"}}}}])

def test_accumulator_is_rejected():
    with pytest.raises(PipelineSafetyError):
        validate_pipeline([{"$group": {"_id": "$x", "y": {"$accumulator": {}}}}])

def test_lookup_pipeline_is_recursed():
    with pytest.raises(PipelineSafetyError):
        validate_pipeline([
            {"$lookup": {
                "from": "x", "as": "y",
                "pipeline": [{"$out": "z"}],
            }}
        ])

def test_empty_pipeline_rejected():
    with pytest.raises(PipelineSafetyError):
        validate_pipeline([])

def test_pipeline_too_long_rejected():
    with pytest.raises(PipelineSafetyError):
        validate_pipeline([{"$match": {}}] * 101)

def test_facet_inner_pipelines_recursed():
    with pytest.raises(PipelineSafetyError):
        validate_pipeline([
            {"$facet": {"a": [{"$out": "z"}]}}
        ])
```

- [ ] **Step 2: Verify fail.**

- [ ] **Step 3: Create `mongosemantic/web/safe_pipeline.py`**

```python
from __future__ import annotations
from typing import Any

class PipelineSafetyError(ValueError):
    pass

DENIED_STAGES = frozenset({"$out", "$merge"})
DENIED_OPERATORS = frozenset({"$function", "$accumulator", "$where", "$jsonSchema"})

MAX_STAGES = 100
MAX_DEPTH = 10

def validate_pipeline(pipeline: list[dict]) -> None:
    if not isinstance(pipeline, list) or not pipeline:
        raise PipelineSafetyError("pipeline must be a non-empty array of stages")
    if len(pipeline) > MAX_STAGES:
        raise PipelineSafetyError(f"pipeline exceeds {MAX_STAGES} stages")
    for stage in pipeline:
        _validate_stage(stage, depth=0)

def _validate_stage(stage: Any, depth: int) -> None:
    if depth > MAX_DEPTH:
        raise PipelineSafetyError("pipeline nesting too deep")
    if not isinstance(stage, dict) or len(stage) != 1:
        raise PipelineSafetyError("each stage must be a single-key dict")
    name, body = next(iter(stage.items()))
    if not isinstance(name, str) or not name.startswith("$"):
        raise PipelineSafetyError(f"stage name {name!r} not allowed")
    if name in DENIED_STAGES:
        raise PipelineSafetyError(f"{name} is not allowed")
    _scan(body, depth + 1)
    if name == "$lookup" and isinstance(body, dict) and "pipeline" in body:
        inner = body.get("pipeline")
        if isinstance(inner, list):
            for s in inner:
                _validate_stage(s, depth + 1)
    if name == "$facet" and isinstance(body, dict):
        for inner in body.values():
            if isinstance(inner, list):
                for s in inner:
                    _validate_stage(s, depth + 1)

def _scan(value: Any, depth: int) -> None:
    if depth > MAX_DEPTH:
        raise PipelineSafetyError("expression nesting too deep")
    if isinstance(value, dict):
        for k, v in value.items():
            if isinstance(k, str) and k in DENIED_OPERATORS:
                raise PipelineSafetyError(f"operator {k} is not allowed")
            _scan(v, depth + 1)
    elif isinstance(value, list):
        for v in value:
            _scan(v, depth + 1)
```

- [ ] **Step 4: Run tests — expect pass.**

### 10B: aggregation route

- [ ] **Step 5: Failing route test**

Create `tests/unit/test_route_aggregation.py`:

```python
from unittest.mock import patch, MagicMock
import mongomock
from fastapi.testclient import TestClient
from mongosemantic.web.app import create_app
from mongosemantic.web.security import CSRF_HEADER

def _client_db(monkeypatch):
    monkeypatch.setenv("MONGOSEMANTIC_URI", "mongodb://x")
    monkeypatch.setenv("MONGOSEMANTIC_DB", "d")
    monkeypatch.setenv("MONGOSEMANTIC_MODEL", "local-fast")
    db = mongomock.MongoClient()["d"]
    return TestClient(create_app()), db

def _conn(db):
    from mongosemantic.db.client import Topology
    fake = MagicMock(); fake.db = db; fake.topology = Topology.STANDALONE; fake.close = MagicMock()
    return fake

def test_aggregation_runs_safe_pipeline(monkeypatch):
    client, db = _client_db(monkeypatch)
    db["articles"].insert_many([{"x": 1}, {"x": 2}, {"x": 3}])
    seed = client.get("/api/topology")
    token = seed.cookies.get("csrftoken")
    with patch("mongosemantic.web.routes.aggregation.MongoConnection.open", return_value=_conn(db)), \
         patch("mongosemantic.web.routes.system.MongoConnection.open", return_value=_conn(db)):
        r = client.post(
            "/api/collections/articles/aggregation",
            json={"pipeline": [{"$match": {"x": {"$gte": 2}}}, {"$count": "n"}]},
            headers={CSRF_HEADER: token},
        )
        assert r.status_code == 200, r.text
        rows = r.json()["rows"]
        assert rows == [{"n": 2}]

def test_aggregation_rejects_out(monkeypatch):
    client, db = _client_db(monkeypatch)
    seed = client.get("/api/topology")
    token = seed.cookies.get("csrftoken")
    with patch("mongosemantic.web.routes.aggregation.MongoConnection.open", return_value=_conn(db)), \
         patch("mongosemantic.web.routes.system.MongoConnection.open", return_value=_conn(db)):
        r = client.post(
            "/api/collections/articles/aggregation",
            json={"pipeline": [{"$out": "x"}]},
            headers={CSRF_HEADER: token},
        )
        assert r.status_code == 400
        assert "$out" in r.json()["detail"]
```

- [ ] **Step 6: Verify fail.**

- [ ] **Step 7: Create `mongosemantic/web/routes/aggregation.py`**

```python
from __future__ import annotations
from typing import Any
from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel
from mongosemantic.config import Settings
from mongosemantic.db.client import MongoConnection
from mongosemantic.web.identifiers import IdentifierError, validate_identifier
from mongosemantic.web.safe_pipeline import PipelineSafetyError, validate_pipeline

router = APIRouter()

class AggregationRequest(BaseModel):
    pipeline: list[dict]

MAX_DOCS = 100
MAX_TIME_MS = 10_000

def _stringify(value: Any) -> Any:
    """Return JSON-safe version of pymongo result. ObjectId, Decimal128, datetime → str."""
    if isinstance(value, dict):
        return {k: _stringify(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_stringify(v) for v in value]
    if hasattr(value, "binary"):  # bson types fall through to repr
        return str(value)
    return value if isinstance(value, (str, int, float, bool, type(None))) else str(value)

@router.post("/api/collections/{name}/aggregation")
def aggregation(name: str = Path(...), req: AggregationRequest = ...) -> dict:
    try:
        validate_identifier(name)
    except IdentifierError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    try:
        validate_pipeline(req.pipeline)
    except PipelineSafetyError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    settings = Settings()
    conn = MongoConnection.open(settings.uri, settings.database)
    try:
        cursor = conn.db[name].aggregate(req.pipeline, maxTimeMS=MAX_TIME_MS)
        rows = []
        for i, doc in enumerate(cursor):
            if i >= MAX_DOCS:
                break
            rows.append(_stringify(doc))
        return {"rows": rows, "limit": MAX_DOCS, "truncated": len(rows) >= MAX_DOCS}
    finally:
        conn.close()
```

- [ ] **Step 8: Wire + tests pass + commit**

```bash
git add mongosemantic/web/safe_pipeline.py mongosemantic/web/routes/aggregation.py mongosemantic/web/app.py tests/unit/test_safe_pipeline.py tests/unit/test_route_aggregation.py
git commit -m "feat(web): safe-aggregation runner with stage allowlist"
```

---

## Task 11: Dashboard, jobs, retry, reindex routes

**Files:**
- Create: `mongosemantic/web/routes/dashboard.py`
- Modify: `mongosemantic/web/app.py`
- Create: `tests/unit/test_route_dashboard.py`

- [ ] **Step 1: Failing test**

Create `tests/unit/test_route_dashboard.py`:

```python
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock
import mongomock
from fastapi.testclient import TestClient
from mongosemantic.web.app import create_app
from mongosemantic.web.security import CSRF_HEADER
from mongosemantic.state.config_store import CollectionConfig, FieldSpec, save_config
from mongosemantic.state import enqueue_embed, claim_batch, fail, count_by_status

def _client_db(monkeypatch):
    monkeypatch.setenv("MONGOSEMANTIC_URI", "mongodb://x")
    monkeypatch.setenv("MONGOSEMANTIC_DB", "d")
    monkeypatch.setenv("MONGOSEMANTIC_MODEL", "local-fast")
    db = mongomock.MongoClient()["d"]
    return TestClient(create_app()), db

def _conn(db):
    from mongosemantic.db.client import Topology
    fake = MagicMock(); fake.db = db; fake.topology = Topology.STANDALONE; fake.close = MagicMock()
    return fake

def _seed(db):
    save_config(db, CollectionConfig(
        collection="articles", mode="shadow", shadow_collection="articles_embeddings",
        fields=[FieldSpec(path="body")], embedding_model="local-fast", embedding_dim=384,
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    ))

def test_dashboard_returns_overview(monkeypatch):
    client, db = _client_db(monkeypatch)
    _seed(db)
    enqueue_embed(db, "articles", "id1", "body", None, "t", "h", "local-fast")
    with patch("mongosemantic.web.routes.dashboard.MongoConnection.open", return_value=_conn(db)):
        r = client.get("/api/dashboard")
        assert r.status_code == 200
        body = r.json()
        assert body["topology"] == "standalone"
        assert body["configured_count"] == 1
        assert body["jobs"]["pending"] == 1

def test_retry_resets_failed(monkeypatch):
    client, db = _client_db(monkeypatch)
    _seed(db)
    enqueue_embed(db, "articles", "id1", "body", None, "t", "h", "local-fast")
    for _ in range(3):
        b = claim_batch(db, "w", 10); fail(db, b[0]["_id"], "boom")
    seed = client.get("/api/topology")
    token = seed.cookies.get("csrftoken")
    with patch("mongosemantic.web.routes.dashboard.MongoConnection.open", return_value=_conn(db)), \
         patch("mongosemantic.web.routes.system.MongoConnection.open", return_value=_conn(db)):
        r = client.post("/api/jobs/retry", headers={CSRF_HEADER: token})
        assert r.status_code == 200
    assert count_by_status(db).get("pending", 0) == 1
```

- [ ] **Step 2: Verify fail.**

- [ ] **Step 3: Create `mongosemantic/web/routes/dashboard.py`**

```python
from __future__ import annotations
from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel
from mongosemantic.config import Settings
from mongosemantic.db.client import MongoConnection
from mongosemantic.exceptions import NotConfiguredError
from mongosemantic.state import (
    count_by_status,
    enqueue_embed,
    ensure_indexes,
    list_configured,
    load_config,
    reset_failed,
)
from mongosemantic.sync.change_stream import _get_path, _resolve_text, hash_text
from mongosemantic.web.identifiers import IdentifierError, validate_identifier

router = APIRouter()

@router.get("/api/dashboard")
def dashboard() -> dict:
    settings = Settings()
    conn = MongoConnection.open(settings.uri, settings.database)
    try:
        db = conn.db
        cfgs = list_configured(db)
        total_embeddings = 0
        for cfg in cfgs:
            total_embeddings += db[cfg.shadow_collection].count_documents({})
        return {
            "topology": conn.topology.value,
            "configured_count": len(cfgs),
            "configured": [
                {"collection": c.collection, "fields": [f.path for f in c.fields],
                 "embedding_model": c.embedding_model}
                for c in cfgs
            ],
            "total_embeddings": total_embeddings,
            "jobs": count_by_status(db),
        }
    finally:
        conn.close()

@router.get("/api/jobs/status")
def jobs_status() -> dict:
    settings = Settings()
    conn = MongoConnection.open(settings.uri, settings.database)
    try:
        return {"jobs": count_by_status(conn.db)}
    finally:
        conn.close()

@router.post("/api/jobs/retry")
def retry_failed() -> dict:
    settings = Settings()
    conn = MongoConnection.open(settings.uri, settings.database)
    try:
        n = reset_failed(conn.db)
        return {"reset": n}
    finally:
        conn.close()

class ReindexRequest(BaseModel):
    collection: str

@router.post("/api/reindex")
def reindex(req: ReindexRequest) -> dict:
    try:
        validate_identifier(req.collection)
    except IdentifierError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    settings = Settings()
    conn = MongoConnection.open(settings.uri, settings.database)
    try:
        db = conn.db
        ensure_indexes(db)
        cfg = load_config(db, req.collection)
        if not cfg:
            raise HTTPException(status_code=400, detail=f"{req.collection} not configured")
        db[cfg.shadow_collection].delete_many({"source_collection": req.collection})
        enqueued = 0
        for doc in db[req.collection].find({}):
            key = doc.get("_id")
            for spec in cfg.fields:
                text = _resolve_text(_get_path(doc, spec.path))
                if not text:
                    continue
                h = hash_text(cfg.embedding_model, text)
                enqueue_embed(
                    db, collection=req.collection, source_id=key, field_path=spec.path,
                    chunk_index=None, input_text=text, input_hash=h,
                    model=cfg.embedding_model,
                )
                enqueued += 1
        return {"enqueued": enqueued}
    finally:
        conn.close()
```

- [ ] **Step 4: Wire + run + commit**

```bash
git add mongosemantic/web/routes/dashboard.py mongosemantic/web/app.py tests/unit/test_route_dashboard.py
git commit -m "feat(web): dashboard, jobs status, retry, and reindex routes"
```

---

## Task 12: Static UI route + index.html serving

**Files:**
- Create: `mongosemantic/web/routes/ui.py`
- Modify: `mongosemantic/web/app.py`
- Create: `tests/unit/test_route_ui.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_route_ui.py`:

```python
from fastapi.testclient import TestClient
from mongosemantic.web.app import create_app

def test_ui_root_returns_html(monkeypatch):
    monkeypatch.setenv("MONGOSEMANTIC_URI", "mongodb://x")
    monkeypatch.setenv("MONGOSEMANTIC_DB", "d")
    monkeypatch.setenv("MONGOSEMANTIC_MODEL", "local-fast")
    client = TestClient(create_app())
    r = client.get("/")
    assert r.status_code == 200
    assert "<!doctype html>" in r.text.lower() or "<html" in r.text.lower()

def test_static_app_js_served(monkeypatch):
    monkeypatch.setenv("MONGOSEMANTIC_URI", "mongodb://x")
    monkeypatch.setenv("MONGOSEMANTIC_DB", "d")
    monkeypatch.setenv("MONGOSEMANTIC_MODEL", "local-fast")
    client = TestClient(create_app())
    r = client.get("/static/app.js")
    assert r.status_code == 200
```

- [ ] **Step 2: Verify fail.**

- [ ] **Step 3: Create `mongosemantic/web/routes/ui.py`**

```python
from __future__ import annotations
from pathlib import Path
from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

router = APIRouter()

@router.get("/", response_class=HTMLResponse)
def root() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")

def install(app) -> None:
    app.include_router(router)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
```

- [ ] **Step 4: Wire into `mongosemantic/web/app.py`**

Add at top:
```python
from mongosemantic.web.routes import ui as _ui_routes
```

In `create_app()` after the other `include_router` calls:
```python
    _ui_routes.install(app)
```

- [ ] **Step 5: Stub static files** so the tests can pass before Task 13/14 fills them in:

```bash
mkdir -p /Users/varma/mongosemantic/mongosemantic/web/static
printf '<!doctype html><title>mongosemantic</title>\n' > /Users/varma/mongosemantic/mongosemantic/web/static/index.html
printf '/* placeholder */\n' > /Users/varma/mongosemantic/mongosemantic/web/static/app.js
printf '/* placeholder */\n' > /Users/varma/mongosemantic/mongosemantic/web/static/style.css
```

- [ ] **Step 6: Run tests — expect pass.**

- [ ] **Step 7: Commit**

```bash
git add mongosemantic/web/routes/ui.py mongosemantic/web/app.py mongosemantic/web/static/ tests/unit/test_route_ui.py
git commit -m "feat(web): serve / and /static — stub HTML/JS/CSS for now"
```

---

## Task 13: Real `index.html` SPA shell

**Files:**
- Modify: `mongosemantic/web/static/index.html`

- [ ] **Step 1: Replace `index.html` with the real SPA shell**

The HTML is intentionally bare-bones — semantic structure, no styling. The user's separate design pass replaces classes/markup but keeps the IDs and `data-page` hooks that `app.js` uses.

Write `/Users/varma/mongosemantic/mongosemantic/web/static/index.html`:

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>mongosemantic</title>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
  <header id="app-header">
    <h1>mongosemantic</h1>
    <nav id="app-nav" aria-label="primary">
      <a href="#/connection"  data-page="connection">Connection</a>
      <a href="#/collections" data-page="collections">Collections</a>
      <a href="#/search"      data-page="search">Search</a>
      <a href="#/query"       data-page="query">Query</a>
      <a href="#/dashboard"   data-page="dashboard">Dashboard</a>
      <a href="#/visualize"   data-page="visualize">Visualize</a>
      <a href="#/mcp"         data-page="mcp">MCP</a>
    </nav>
  </header>

  <main id="app-main" aria-live="polite">
    <section id="page-connection" hidden>
      <h2 data-content="connection.title"></h2>
      <p  data-content="connection.subtitle"></p>
      <form id="form-connection">
        <label data-content="connection.uri_label" for="conn-uri"></label>
        <input id="conn-uri" name="uri" data-placeholder="connection.uri_placeholder">
        <label data-content="connection.db_label" for="conn-db"></label>
        <input id="conn-db" name="database">
        <small data-content="connection.db_helper"></small>
        <button type="submit" data-content="connection.test_button"></button>
      </form>
      <p id="conn-state"></p>
      <small data-content="connection.footer_note"></small>
    </section>

    <section id="page-collections" hidden>
      <h2 data-content="collections.title"></h2>
      <p  data-content="collections.subtitle"></p>
      <table id="collections-table"></table>
      <div id="collections-empty" hidden>
        <h3 data-content="collections.empty_title"></h3>
        <p  data-content="collections.empty_body"></p>
      </div>
    </section>

    <section id="page-inspect" hidden>
      <h2 id="inspect-title"></h2>
      <p  id="inspect-subtitle"></p>
      <table id="inspect-table"></table>
    </section>

    <section id="page-apply" hidden>
      <h2 data-content="apply.title"></h2>
      <p  data-content="apply.subtitle"></p>
      <form id="form-apply"></form>
      <p id="apply-notice"></p>
    </section>

    <section id="page-indexing" hidden>
      <h2 id="indexing-title"></h2>
      <p  data-content="indexing.subtitle"></p>
      <progress id="indexing-progress" value="0" max="0"></progress>
      <p id="indexing-metric"></p>
    </section>

    <section id="page-search" hidden>
      <input id="search-q" type="search" data-placeholder="search.placeholder">
      <select id="search-collection"></select>
      <ol id="search-results"></ol>
      <p id="search-empty"></p>
    </section>

    <section id="page-query" hidden>
      <h2 data-content="aggregation.title"></h2>
      <p  data-content="aggregation.subtitle"></p>
      <p  data-content="aggregation.safety_banner" id="query-banner"></p>
      <textarea id="query-pipeline" data-default="aggregation.default_pipeline" rows="10"></textarea>
      <button id="query-run" data-content="aggregation.btn_run"></button>
      <pre id="query-results"></pre>
    </section>

    <section id="page-dashboard" hidden>
      <h2>Overview</h2>
      <div id="dashboard-cards"></div>
    </section>

    <section id="page-visualize" hidden>
      <h2 data-content="visualize.title"></h2>
      <p  data-content="visualize.coming_in"></p>
    </section>

    <section id="page-mcp" hidden>
      <h2 data-content="mcp.title"></h2>
      <p  data-content="mcp.coming_in"></p>
    </section>
  </main>

  <div id="toast" role="status" aria-live="polite" hidden></div>
  <script src="/static/app.js"></script>
</body>
</html>
```

- [ ] **Step 2: Verify the page loads**

```bash
python3 -c "
import os; os.environ['MONGOSEMANTIC_URI']='mongodb://x'; os.environ['MONGOSEMANTIC_DB']='d'
from fastapi.testclient import TestClient
from mongosemantic.web.app import create_app
c = TestClient(create_app())
assert '<title>mongosemantic</title>' in c.get('/').text
print('ok')
"
```

Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add mongosemantic/web/static/index.html
git commit -m "feat(web): SPA shell with semantic markup + data-content hooks"
```

---

## Task 14: Real `app.js` — content hydration + page routing + fetch helpers

**Files:**
- Modify: `mongosemantic/web/static/app.js`
- Modify: `mongosemantic/web/static/style.css`

- [ ] **Step 1: Replace `app.js` with the real client**

Write `/Users/varma/mongosemantic/mongosemantic/web/static/app.js`:

```javascript
// mongosemantic web client. Vanilla ES2020+. No build step.
(() => {
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  let CONTENT = {};
  const PAGES = ["connection","collections","inspect","apply","indexing","search","query","dashboard","visualize","mcp"];

  const csrfFromCookie = () => {
    const m = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  };

  async function fetchJson(method, url, body) {
    const opts = {
      method,
      headers: {"Accept": "application/json"},
      credentials: "same-origin",
    };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    if (method !== "GET") {
      opts.headers["X-CSRF-Token"] = csrfFromCookie();
    }
    const r = await fetch(url, opts);
    const text = await r.text();
    let data;
    try { data = text ? JSON.parse(text) : null; } catch { data = {raw: text}; }
    if (!r.ok) {
      const msg = (data && data.detail) || `HTTP ${r.status}`;
      throw new Error(msg);
    }
    return data;
  }

  function get(content, path) {
    return path.split(".").reduce((acc, k) => acc && acc[k], content);
  }

  function hydrateContent(root = document) {
    $$("[data-content]", root).forEach(el => {
      const v = get(CONTENT, el.dataset.content);
      if (v != null) el.textContent = v;
    });
    $$("[data-placeholder]", root).forEach(el => {
      const v = get(CONTENT, el.dataset.placeholder);
      if (v != null) el.placeholder = v;
    });
    $$("[data-default]", root).forEach(el => {
      if (!el.value) el.value = get(CONTENT, el.dataset.default) || "";
    });
  }

  function showPage(name) {
    PAGES.forEach(p => {
      const el = document.getElementById(`page-${p}`);
      if (el) el.hidden = (p !== name);
    });
    $$("#app-nav a").forEach(a => a.toggleAttribute("aria-current", a.dataset.page === name));
  }

  function toast(msg) {
    const t = $("#toast");
    if (!t) return;
    t.textContent = msg;
    t.hidden = false;
    setTimeout(() => { t.hidden = true; }, 3000);
  }

  const route = () => {
    const hash = (location.hash || "#/connection").replace(/^#\//, "").split("/");
    const [page, ...args] = hash;
    if (!PAGES.includes(page)) { location.hash = "#/connection"; return; }
    showPage(page);
    handlers[page] && handlers[page](args);
  };

  const handlers = {
    connection() {},
    collections: async () => {
      const tbl = $("#collections-table");
      tbl.innerHTML = "";
      try {
        const data = await fetchJson("GET", "/api/collections");
        if (!data.collections.length) {
          $("#collections-empty").hidden = false; return;
        }
        const head = `<thead><tr>
          <th>${CONTENT.collections.col_collection}</th>
          <th>${CONTENT.collections.col_status}</th>
          <th></th>
        </tr></thead>`;
        const rows = data.collections.map(c => `<tr>
          <td>${c.name}</td>
          <td>${c.status === "configured"
              ? CONTENT.collections.status_configured.replace("{n}", c.fields_count)
              : CONTENT.collections.status_not_configured}</td>
          <td><a href="#/inspect/${encodeURIComponent(c.name)}">${CONTENT.collections.row_action}</a></td>
        </tr>`).join("");
        tbl.innerHTML = head + "<tbody>" + rows + "</tbody>";
      } catch (e) {
        toast(e.message);
      }
    },
    inspect: async ([name]) => {
      $("#inspect-title").textContent = CONTENT.inspect.title.replace("{collection}", name);
      try {
        const data = await fetchJson("GET", `/api/collections/${encodeURIComponent(name)}/inspect`);
        $("#inspect-subtitle").textContent =
          CONTENT.inspect.subtitle.replace("{n}", data.sample_size);
        const head = `<thead><tr>
          <th>${CONTENT.inspect.col_field}</th>
          <th>${CONTENT.inspect.col_type}</th>
          <th>${CONTENT.inspect.col_coverage}</th>
          <th>${CONTENT.inspect.col_avg_length}</th>
          <th>${CONTENT.inspect.col_suitability}</th>
        </tr></thead>`;
        const rows = data.fields.map(f => `<tr>
          <td>${f.path}</td>
          <td>${f.type}</td>
          <td>${(f.coverage*100).toFixed(0)}%</td>
          <td>${f.avg_len}</td>
          <td><span class="band band-${f.band}">${CONTENT.inspect["band_"+f.band]}</span></td>
        </tr>`).join("");
        $("#inspect-table").innerHTML = head + "<tbody>" + rows + "</tbody>";
      } catch (e) { toast(e.message); }
    },
    apply() {/* form rendering stub — full version in design layer */},
    indexing() {},
    search() {},
    query() {},
    dashboard: async () => {
      try {
        const d = await fetchJson("GET", "/api/dashboard");
        $("#dashboard-cards").innerHTML = `
          <div>${CONTENT.dashboard.card_collections}: ${d.configured_count}</div>
          <div>${CONTENT.dashboard.card_total_embeddings}: ${d.total_embeddings}</div>
          <div>${CONTENT.dashboard.card_pending}: ${d.jobs.pending || 0}</div>
          <div>${CONTENT.dashboard.card_failed}: ${d.jobs.failed || 0}</div>
        `;
      } catch (e) { toast(e.message); }
    },
    visualize() {},
    mcp() {},
  };

  // Bootstrap
  (async () => {
    try {
      CONTENT = await fetchJson("GET", "/api/content");
      hydrateContent();
      // populate nav text from CONTENT.global.nav_*
      $$("#app-nav a").forEach(a => {
        const k = "nav_" + a.dataset.page;
        if (CONTENT.global && CONTENT.global[k]) a.textContent = CONTENT.global[k];
      });
    } catch (e) {
      console.error("content load failed", e);
    }
    window.addEventListener("hashchange", route);
    route();

    // Connection form submit
    const cf = $("#form-connection");
    if (cf) cf.addEventListener("submit", async ev => {
      ev.preventDefault();
      const uri = $("#conn-uri").value.trim();
      const database = $("#conn-db").value.trim();
      $("#conn-state").textContent = CONTENT.connection.state_connecting;
      try {
        const r = await fetchJson("POST", "/api/connect", {uri, database});
        const stateKey = `state_${r.topology}`;
        $("#conn-state").textContent = CONTENT.connection[stateKey] || JSON.stringify(r);
      } catch (e) {
        $("#conn-state").textContent = e.message;
      }
    });
  })();
})();
```

- [ ] **Step 2: Replace `style.css` with minimal utilitarian styles**

Write `/Users/varma/mongosemantic/mongosemantic/web/static/style.css`:

```css
/* mongosemantic — minimal styles. Replace with your design pass. */
:root {
  --fg: #111;
  --bg: #fff;
  --muted: #666;
  --line: #ddd;
  --accent: #0a7;
  --warn: #c80;
  --bad: #c33;
  --ok: #0a7;
}
* { box-sizing: border-box; }
body { margin: 0; font: 14px/1.4 system-ui, sans-serif; color: var(--fg); background: var(--bg); }
header { display: flex; align-items: center; gap: 1rem; padding: .75rem 1rem; border-bottom: 1px solid var(--line); }
header h1 { font-size: 1rem; margin: 0; }
nav a { margin-right: 1rem; color: var(--fg); text-decoration: none; }
nav a[aria-current] { font-weight: 600; text-decoration: underline; }
main { padding: 1rem; max-width: 1100px; margin: 0 auto; }
table { border-collapse: collapse; width: 100%; }
th, td { text-align: left; padding: .35rem .6rem; border-bottom: 1px solid var(--line); }
input, select, textarea, button {
  font: inherit; padding: .35rem .6rem; border: 1px solid var(--line); border-radius: 4px;
  background: var(--bg); color: var(--fg);
}
button { cursor: pointer; }
.band { padding: 1px 8px; border-radius: 999px; font-size: 12px; }
.band-great { background: #d6f5e6; color: #036; }
.band-good { background: #e8f3ff; color: #036; }
.band-usable { background: #fff3cd; color: #553300; }
.band-not_recommended { background: #fde2e2; color: #800; }
#toast { position: fixed; bottom: 1rem; right: 1rem; background: #222; color: #fff;
  padding: .5rem .75rem; border-radius: 4px; }
[hidden] { display: none !important; }
```

- [ ] **Step 3: Smoke-test**

```bash
cd /Users/varma/mongosemantic && (
  MONGOSEMANTIC_URI="mongodb://localhost:27117/?replicaSet=rs0" \
  MONGOSEMANTIC_DB="demo" \
  MONGOSEMANTIC_MODEL="local-fast" \
  python3 -m mongosemantic ui --port 18080 &
)
SERVER_PID=$!
sleep 2
curl -s http://127.0.0.1:18080/static/app.js | head -3
curl -s http://127.0.0.1:18080/api/content | python3 -c "import sys, json; d=json.load(sys.stdin); print('connection.title =', d['connection']['title'])"
kill "$SERVER_PID" 2>/dev/null
```

Expected: `connection.title = Connect to MongoDB` and an app.js header.

- [ ] **Step 4: Commit**

```bash
git add mongosemantic/web/static/app.js mongosemantic/web/static/style.css
git commit -m "feat(web): client-side routing, content hydration, fetch helper, minimal styles"
```

---

## Task 15: Apply form rendering in `app.js`

**Files:**
- Modify: `mongosemantic/web/static/app.js`

This task fills out the `apply` page handler. Pure JS, no test file (covered by E2E in Task 17).

- [ ] **Step 1: Replace the `apply()` handler in `app.js`**

In `/Users/varma/mongosemantic/mongosemantic/web/static/app.js`, replace `apply() {/* form rendering stub — full version in design layer */}` with:

```javascript
    apply: async ([name]) => {
      const f = $("#form-apply");
      const c = CONTENT.apply;
      f.innerHTML = `
        <fieldset>
          <legend>${c.section_fields}</legend>
          <input id="apply-fields" placeholder="comma-separated paths, e.g. body, title">
        </fieldset>
        <fieldset>
          <legend>${c.section_mode}</legend>
          <label><input type="radio" name="mode" value="shadow" checked> ${c.mode_shadow}</label>
          <label><input type="radio" name="mode" value="inline"> ${c.mode_inline}</label>
        </fieldset>
        <fieldset>
          <legend>${c.section_chunking}</legend>
          <label><input id="apply-chunked" type="checkbox"> ${c.chunking_toggle}</label>
        </fieldset>
        <fieldset>
          <legend>${c.section_model}</legend>
          <select id="apply-model">
            <option value="local-fast">${c.model_local_fast}</option>
            <option value="local-better">${c.model_local_better}</option>
            <option value="openai-small">${c.model_openai_small}</option>
            <option value="openai-large">${c.model_openai_large}</option>
            <option value="ollama-nomic">${c.model_ollama_nomic}</option>
          </select>
        </fieldset>
        <button type="submit">${c.cta_apply}</button>
      `;
      f.onsubmit = async ev => {
        ev.preventDefault();
        const fields = $("#apply-fields").value.split(",").map(s => s.trim()).filter(Boolean);
        const mode = (f.querySelector('input[name="mode"]:checked') || {}).value || "shadow";
        const chunked = $("#apply-chunked").checked;
        const model = $("#apply-model").value;
        try {
          const r = await fetchJson("POST", `/api/collections/${encodeURIComponent(name)}/apply`,
            {fields, mode, chunked, model});
          toast(CONTENT.global.toast_config_updated);
          location.hash = `#/collections`;
        } catch (e) { toast(e.message); }
      };
    },
```

- [ ] **Step 2: Sanity check**

`python3 -m pytest tests/unit -v` — all unit tests still pass.

- [ ] **Step 3: Commit**

```bash
git add mongosemantic/web/static/app.js
git commit -m "feat(web): apply page form rendering"
```

---

## Task 16: Search + Query + Indexing handlers in `app.js`

**Files:**
- Modify: `mongosemantic/web/static/app.js`

- [ ] **Step 1: Replace the `search`, `query`, `indexing` handlers**

In `app.js`, replace the three stubs:

```javascript
    search: async () => {
      const c = CONTENT.search;
      const empty = $("#search-empty");
      const results = $("#search-results");
      empty.textContent = c.empty_no_query;
      results.innerHTML = "";
      const sel = $("#search-collection");
      try {
        const cols = await fetchJson("GET", "/api/collections");
        sel.innerHTML = `<option value="">${c.selector_all}</option>` +
          cols.collections.filter(c => c.status === "configured")
              .map(c => `<option value="${c.name}">${c.name}</option>`).join("");
      } catch (e) { /* fine to leave empty */ }
      const input = $("#search-q");
      input.placeholder = c.placeholder;
      let timer;
      input.oninput = () => {
        clearTimeout(timer);
        timer = setTimeout(async () => {
          const q = input.value.trim();
          if (!q) { results.innerHTML = ""; empty.textContent = c.empty_no_query; return; }
          const params = new URLSearchParams({q});
          if (sel.value) params.set("collection", sel.value);
          try {
            const r = await fetchJson("GET", "/api/search?" + params.toString());
            if (!r.rows.length) { empty.textContent = c.empty_no_results; results.innerHTML = ""; return; }
            empty.textContent = "";
            results.innerHTML = r.rows.map(row => `<li>
              <strong>${(row.score || 0).toFixed(3)}</strong>
              <span>${row.source_collection}</span>
              <span>${row.field_path}</span>
              <p>${(row.chunk_text || "").slice(0, 300)}</p>
            </li>`).join("");
          } catch (e) { toast(e.message); }
        }, 300);
      };
    },
    query: async () => {
      const ta = $("#query-pipeline");
      const out = $("#query-results");
      $("#query-run").onclick = async () => {
        let pipeline;
        try { pipeline = JSON.parse(ta.value); }
        catch { toast(CONTENT.aggregation.error_rejected.replace("{reason}", "invalid JSON")); return; }
        const collection = prompt("Collection name?", "articles");
        if (!collection) return;
        try {
          const r = await fetchJson(
            "POST",
            `/api/collections/${encodeURIComponent(collection)}/aggregation`,
            {pipeline},
          );
          out.textContent = JSON.stringify(r.rows, null, 2);
        } catch (e) {
          out.textContent = CONTENT.aggregation.error_rejected.replace("{reason}", e.message);
        }
      };
    },
    indexing: async ([name]) => {
      $("#indexing-title").textContent = CONTENT.indexing.title.replace("{collection}", name);
      try {
        const r = await fetchJson("POST", `/api/collections/${encodeURIComponent(name)}/index`);
        const bar = $("#indexing-progress");
        bar.max = r.total; bar.value = r.enqueued;
        $("#indexing-metric").textContent = CONTENT.indexing.metric_progress
          .replace("{processed}", r.enqueued).replace("{total}", r.total);
        toast(CONTENT.indexing.toast_complete.replace("{n}", r.enqueued));
      } catch (e) { toast(e.message); }
    },
```

- [ ] **Step 2: Tests still pass.**

- [ ] **Step 3: Commit**

```bash
git add mongosemantic/web/static/app.js
git commit -m "feat(web): search/query/indexing page handlers"
```

---

## Task 17: End-to-end web integration test

**Files:**
- Create: `tests/integration/test_web_e2e.py`

- [ ] **Step 1: Write the integration test**

```python
import threading
import time
from datetime import datetime, timezone
import pytest
import uvicorn
import httpx
from mongosemantic.web.app import create_app
from mongosemantic.state.config_store import CollectionConfig, FieldSpec, save_config
from mongosemantic.embeddings.provider import get_provider
from mongosemantic.worker.runner import process_batch

@pytest.mark.integration
def test_full_browser_like_flow(clean_db, monkeypatch):
    db = clean_db
    save_config(db, CollectionConfig(
        collection="articles", mode="shadow", shadow_collection="articles_embeddings",
        fields=[FieldSpec(path="body")], embedding_model="local-fast", embedding_dim=384,
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    ))
    db["articles"].insert_many([
        {"_id": "a", "body": "semantic vector search over mongodb"},
        {"_id": "b", "body": "completely unrelated: basketball scores"},
    ])
    monkeypatch.setenv("MONGOSEMANTIC_URI",
        "mongodb://localhost:27117/?replicaSet=rs0")
    monkeypatch.setenv("MONGOSEMANTIC_DB", db.name)
    monkeypatch.setenv("MONGOSEMANTIC_MODEL", "local-fast")
    config = uvicorn.Config(create_app(), host="127.0.0.1", port=18091, log_level="error")
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(40):
        try:
            r = httpx.get("http://127.0.0.1:18091/healthz", timeout=0.5)
            if r.status_code == 200: break
        except Exception:
            time.sleep(0.25)
    else:
        raise RuntimeError("server failed to start")
    try:
        with httpx.Client(base_url="http://127.0.0.1:18091") as c:
            assert c.get("/healthz").json()["ok"] is True
            r = c.get("/api/content"); assert "connection" in r.json()
            r = c.get("/api/collections")
            assert r.status_code == 200
            assert any(row["name"] == "articles" for row in r.json()["collections"])
            r = c.post(
                "/api/collections/articles/index",
                headers={"X-CSRF-Token": c.cookies.get("csrftoken", "")},
            )
            assert r.status_code == 200
        process_batch(db, get_provider("local-fast"), "t", 32)
        with httpx.Client(base_url="http://127.0.0.1:18091") as c:
            r = c.get("/api/search", params={"q": "vector database", "collection": "articles"})
            assert r.status_code == 200
            rows = r.json()["rows"]
            assert any("semantic" in row["chunk_text"] for row in rows)
    finally:
        server.should_exit = True
        t.join(timeout=5)
```

- [ ] **Step 2: Run**

```bash
cd /Users/varma/mongosemantic && MONGOSEMANTIC_RUN_INTEGRATION=1 python3 -m pytest tests/integration/test_web_e2e.py -v
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_web_e2e.py
git commit -m "test: end-to-end web flow (HTTP server → index → worker → search)"
```

---

## Task 18: Stub Visualize + MCP placeholder pages

**Files:**
- (handlers already in `app.js`; static section already in `index.html`)
- This task is verification-only.

- [ ] **Step 1: Verify the placeholders render**

```bash
cd /Users/varma/mongosemantic && (
  MONGOSEMANTIC_URI="mongodb://localhost:27117/?replicaSet=rs0" \
  MONGOSEMANTIC_DB="demo" \
  MONGOSEMANTIC_MODEL="local-fast" \
  python3 -m mongosemantic ui --port 18080 &
)
SERVER_PID=$!
sleep 2
curl -s http://127.0.0.1:18080/api/content | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert 'visualize' in d and d['visualize']['coming_in']
assert 'mcp' in d and d['mcp']['coming_in']
print('ok')
"
kill "$SERVER_PID" 2>/dev/null
```

Expected: `ok`.

- [ ] **Step 2: No commit needed** (placeholders shipped in earlier tasks). Skip if there's nothing to add.

---

## Task 19: README update + version bump + tag v0.2.0

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `mongosemantic/__init__.py`

- [ ] **Step 1: Update README**

In `README.md`, change the v0.1.0 status checklist to mark `[x] Web UI` and add a new "Web dashboard" section before the development section:

```markdown
## Web dashboard

```bash
mongosemantic ui                          # http://127.0.0.1:8080
```

Localhost-bound by default with CSRF protection, rate limiting, and security headers. Bind to a non-loopback address only behind your own auth proxy.

The dashboard provides:
- Connection setup with topology detection
- Collections browser with per-field suitability scoring
- One-click semantic-search configuration (Atlas index auto-creation)
- Bulk indexing with progress
- Live-search across one or all configured collections
- Read-only aggregation runner (10s timeout, 100-doc limit)
- Job queue dashboard with retry / reindex
```

- [ ] **Step 2: Update CHANGELOG**

Prepend to `CHANGELOG.md`:

```markdown
## 0.2.0 — <today's date in YYYY-MM-DD format>

- New `mongosemantic ui` command — boots a FastAPI dashboard on `127.0.0.1:8080`.
- Web pages: connection, collections browser, inspect, apply, indexing progress, search, aggregation runner, dashboard.
- Visualize and MCP-integration pages stubbed as placeholders for v0.4.0 and v0.3.0 respectively.
- Safe-aggregation API: stage allowlist, 10s `maxTimeMS`, 100-doc limit.
- All UI strings centralized in `mongosemantic/web/content.py` for design-layer separation.
- Security: CSRF (double-submit cookie), rate limit 120 req/min/IP, security headers, identifier validation.

```

- [ ] **Step 3: Version bump**

In `mongosemantic/__init__.py` change `"0.2.0-dev"` → `"0.2.0"`.

- [ ] **Step 4: Final test sweep**

```bash
cd /Users/varma/mongosemantic && python3 -m pytest tests/unit -v
cd /Users/varma/mongosemantic && MONGOSEMANTIC_RUN_INTEGRATION=1 python3 -m pytest tests/integration -v
cd /Users/varma/mongosemantic && ruff check .
```

All must be green.

- [ ] **Step 5: Commit + tag (locally)**

```bash
git add README.md CHANGELOG.md mongosemantic/__init__.py
git commit -m "docs: v0.2.0 README + changelog + version bump"
git tag v0.2.0
```

Do NOT push — that's a user decision (matches the v0.1.0 release flow).

- [ ] **Step 6: Final summary**

Run:
```bash
git log --oneline v0.1.0..v0.2.0 | wc -l
git log --oneline v0.1.0..v0.2.0
```

Report the commit count and chain.

---

## Done

At this point v0.2.0 is shippable. To verify by hand:

```bash
MONGOSEMANTIC_URI="mongodb+srv://your-cluster/your-db" \
MONGOSEMANTIC_DB="your-db" \
python3 -m mongosemantic ui
# Open http://127.0.0.1:8080 in a browser
```

User should design the visual layer separately by editing `static/index.html` + `static/style.css` (or replacing them entirely). Backend API + content strings stay stable.

**Next plans:**
- v0.3.0 — MCP server with 10 tools
- v0.4.0 — Atlas hybrid + nested-field embedding + array-of-subdocs + visualize
- v0.5.0 — Zero-downtime model migration
