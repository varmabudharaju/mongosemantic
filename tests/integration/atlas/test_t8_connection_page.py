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
from mongosemantic.web.security import CSRF_HEADER

pytestmark = pytest.mark.skipif(
    os.environ.get("MONGOSEMANTIC_RUN_ATLAS_INTEGRATION") != "1",
    reason="Atlas integration disabled (set MONGOSEMANTIC_RUN_ATLAS_INTEGRATION=1)",
)


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    # Strip env vars so we exercise the file-fallback path.
    for k in ("MONGOSEMANTIC_URI", "MONGOSEMANTIC_DB"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("MONGOSEMANTIC_MODEL", "local-fast")
    yield


@pytest.fixture
def client(isolated_config):
    return TestClient(create_app())


def _csrf(client):
    r = client.get("/healthz")
    return {CSRF_HEADER: r.cookies.get("csrftoken")}


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
        headers=_csrf(client),
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

    r = client.post("/api/connection/test", headers=_csrf(client))
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
        headers=_csrf(client),
    )
    body = r.json()
    assert body["ok"] is False
    # Tolerate slow auth-vs-timeout depending on Atlas response speed.
    assert body["error"]["code"] in {"auth_failed", "timeout"}
    assert connection_store.load() is None


def test_delete_clears_config(client):
    atlas_uri = os.environ["MONGOSEMANTIC_ATLAS_URI"]
    connection_store.save(atlas_uri, "sample_mflix")

    r = client.delete("/api/connection", headers=_csrf(client))
    body = r.json()
    assert body["ok"] is True

    r = client.get("/api/connection")
    assert r.json()["state"] == "not_connected"
