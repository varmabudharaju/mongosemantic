from unittest.mock import MagicMock, patch

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
    from mongosemantic.db.client import Topology
    fake_conn = MagicMock()
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
    from mongosemantic.db.client import Topology
    fake_conn = MagicMock()
    fake_conn.topology = Topology.STANDALONE
    fake_conn.close = MagicMock()
    seed = client.get("/healthz")
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
    seed = client.get("/healthz")
    token = seed.cookies.get("csrftoken")
    r = client.post(
        "/api/connect",
        json={"uri": "postgres://nope", "database": "x"},
        headers={CSRF_HEADER: token},
    )
    assert r.status_code == 400
    assert "mongodb" in r.json()["detail"].lower()
