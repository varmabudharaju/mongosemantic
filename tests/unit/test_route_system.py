from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from mongosemantic import connection_store
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


@pytest.fixture
def isolated_xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path


def _csrf(client):
    """Seed a session and return (token, headers)."""
    r = client.get("/healthz")
    token = r.cookies.get("csrftoken")
    return token, {CSRF_HEADER: token}


def _client_no_env(monkeypatch):
    """A TestClient with no MONGOSEMANTIC_* env vars set."""
    for k in ("MONGOSEMANTIC_URI", "MONGOSEMANTIC_DB", "MONGOSEMANTIC_MODEL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("MONGOSEMANTIC_MODEL", "local-fast")
    return TestClient(create_app())


def test_connection_state_not_connected(monkeypatch, isolated_xdg):
    client = _client_no_env(monkeypatch)
    r = client.get("/api/connection")
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "not_connected"
    assert body["env_overrides"]["uri"] is False


def test_connection_state_connected_env(monkeypatch, isolated_xdg):
    client = _client(monkeypatch)
    from mongosemantic.db.client import Topology
    fake_conn = MagicMock()
    fake_conn.topology = Topology.ATLAS
    fake_conn.client.server_info.return_value = {"version": "8.0.23"}
    fake_conn.db = MagicMock()
    with patch(
        "mongosemantic.web.routes.system.MongoConnection.open",
        return_value=fake_conn,
    ), patch(
        "mongosemantic.web.routes.system.list_configured", return_value=[]
    ):
        r = client.get("/api/connection")
    body = r.json()
    assert body["state"] == "connected_env"
    assert body["env_overrides"]["uri"] is True
    # _client sets MONGOSEMANTIC_URI=mongodb://x (no @, so no redaction needed)
    assert body["database"] == "d"


def test_connection_state_connected_ui(monkeypatch, isolated_xdg):
    client = _client_no_env(monkeypatch)
    connection_store.save("mongodb+srv://u:p@cluster.mongodb.net/", "filedb")
    from mongosemantic.db.client import Topology
    fake_conn = MagicMock()
    fake_conn.topology = Topology.ATLAS
    fake_conn.client.server_info.return_value = {"version": "8.0.23"}
    with patch(
        "mongosemantic.web.routes.system.MongoConnection.open",
        return_value=fake_conn,
    ), patch(
        "mongosemantic.web.routes.system.list_configured", return_value=[]
    ):
        r = client.get("/api/connection")
    body = r.json()
    assert body["state"] == "connected_ui"
    assert body["database"] == "filedb"
    assert "<redacted>" in body["uri_redacted"]


def test_save_writes_config_on_success(monkeypatch, isolated_xdg):
    client = _client_no_env(monkeypatch)
    token, headers = _csrf(client)
    from mongosemantic.db.client import Topology
    fake_conn = MagicMock()
    fake_conn.topology = Topology.ATLAS
    fake_conn.client.server_info.return_value = {"version": "8.0.23"}
    with patch(
        "mongosemantic.web.routes.system.MongoConnection.open",
        return_value=fake_conn,
    ):
        r = client.post(
            "/api/connection/save",
            json={"uri": "mongodb+srv://u:p@cluster.mongodb.net/", "database": "newdb"},
            headers=headers,
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["restart_required"] is True
    saved = connection_store.load()
    assert saved is not None
    assert saved.database == "newdb"


def test_save_does_not_write_on_failure(monkeypatch, isolated_xdg):
    client = _client_no_env(monkeypatch)
    token, headers = _csrf(client)
    from pymongo.errors import OperationFailure

    def fake_open(uri, db):
        raise OperationFailure("auth failed", code=18)

    with patch(
        "mongosemantic.web.routes.system.MongoConnection.open",
        side_effect=fake_open,
    ):
        r = client.post(
            "/api/connection/save",
            json={"uri": "mongodb+srv://u:p@cluster.mongodb.net/", "database": "newdb"},
            headers=headers,
        )
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "auth_failed"
    assert connection_store.load() is None


def test_save_rejects_bad_scheme(monkeypatch, isolated_xdg):
    client = _client_no_env(monkeypatch)
    token, headers = _csrf(client)
    r = client.post(
        "/api/connection/save",
        json={"uri": "http://x", "database": "db"},
        headers=headers,
    )
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "bad_scheme"


def test_save_rejects_empty_database(monkeypatch, isolated_xdg):
    client = _client_no_env(monkeypatch)
    token, headers = _csrf(client)
    r = client.post(
        "/api/connection/save",
        json={"uri": "mongodb+srv://u:p@cluster.mongodb.net/", "database": ""},
        headers=headers,
    )
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "missing_database"


def test_delete_removes_config(monkeypatch, isolated_xdg):
    client = _client_no_env(monkeypatch)
    token, headers = _csrf(client)
    connection_store.save("mongodb+srv://u:p@c.mongodb.net/", "db")
    r = client.delete("/api/connection", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["restart_required"] is True
    assert connection_store.load() is None


def test_test_connection_pings_active(monkeypatch, isolated_xdg):
    client = _client(monkeypatch)
    token, headers = _csrf(client)
    from mongosemantic.db.client import Topology
    fake_conn = MagicMock()
    fake_conn.topology = Topology.ATLAS
    fake_conn.client.server_info.return_value = {"version": "8.0.23"}
    with patch(
        "mongosemantic.web.routes.system.MongoConnection.open",
        return_value=fake_conn,
    ):
        r = client.post("/api/connection/test", headers=headers)
    body = r.json()
    assert body["ok"] is True
    assert "latency_ms" in body
    assert body["mongo_version"] == "8.0.23"


def test_test_connection_returns_error_when_unreachable(monkeypatch, isolated_xdg):
    client = _client(monkeypatch)
    token, headers = _csrf(client)
    from pymongo.errors import ServerSelectionTimeoutError

    def fake_open(uri, db):
        raise ServerSelectionTimeoutError("No servers found yet")

    with patch(
        "mongosemantic.web.routes.system.MongoConnection.open",
        side_effect=fake_open,
    ):
        r = client.post("/api/connection/test", headers=headers)
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "timeout"


def test_connection_config_path(monkeypatch, isolated_xdg):
    client = _client_no_env(monkeypatch)
    r = client.get("/api/connection/config-path")
    body = r.json()
    assert body["path"].endswith("mongosemantic/config.json")
    assert str(isolated_xdg) in body["path"]


def test_save_failure_scrubs_uri_from_details(monkeypatch, isolated_xdg):
    client = _client_no_env(monkeypatch)
    token, headers = _csrf(client)
    from pymongo.errors import OperationFailure

    # Construct an exception whose repr embeds the URI (defensive worst-case).
    leaky_uri = "mongodb+srv://leakuser:leakpass@evil.example.net/"

    def fake_open(uri, db):
        raise OperationFailure(f"auth failed against {uri}", code=18)

    with patch(
        "mongosemantic.web.routes.system.MongoConnection.open",
        side_effect=fake_open,
    ):
        r = client.post(
            "/api/connection/save",
            json={"uri": leaky_uri, "database": "x"},
            headers=headers,
        )
    body = r.json()
    assert body["ok"] is False
    # Raw URI (with password) must not appear anywhere in the response.
    assert "leakpass" not in r.text
    assert "leakuser:leakpass" not in r.text
