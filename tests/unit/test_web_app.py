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


def test_content_endpoint_returns_full_dict(monkeypatch):
    monkeypatch.setenv("MONGOSEMANTIC_URI", "mongodb://fake")
    monkeypatch.setenv("MONGOSEMANTIC_DB", "d")
    client = TestClient(create_app())
    r = client.get("/api/content")
    assert r.status_code == 200
    data = r.json()
    assert "connection" in data
    assert "global" in data
    assert data["connection"]["title"] == "Connection"
