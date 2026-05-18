from unittest.mock import MagicMock, patch

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
    seed = client.get("/healthz")
    token = seed.cookies.get("csrftoken")
    with patch("mongosemantic.web.routes.apply.MongoConnection.open", return_value=_conn(db)):
        r = client.post(
            "/api/collections/articles/apply",
            json={"fields": ["body"], "mode": "shadow", "chunked": False,
                  "model": "local-fast"},
            headers={CSRF_HEADER: token},
        )
        assert r.status_code == 200, r.text
    from mongosemantic.state import load_config
    cfg = load_config(db, "articles")
    assert cfg is not None
    assert cfg.fields[0].path == "body"
    assert cfg.mode == "shadow"


def test_apply_inline_saves_inline_config(monkeypatch):
    client, db = _client_db(monkeypatch)
    seed = client.get("/healthz")
    token = seed.cookies.get("csrftoken")
    with patch("mongosemantic.web.routes.apply.MongoConnection.open", return_value=_conn(db)):
        r = client.post(
            "/api/collections/products/apply",
            json={"fields": ["description"], "mode": "inline", "chunked": False,
                  "model": "local-fast"},
            headers={CSRF_HEADER: token},
        )
        assert r.status_code == 200, r.text
    from mongosemantic.state import load_config
    cfg = load_config(db, "products")
    assert cfg.mode == "inline"
    assert cfg.shadow_collection is None


def test_apply_rejects_inline_with_chunked(monkeypatch):
    client, db = _client_db(monkeypatch)
    seed = client.get("/healthz")
    token = seed.cookies.get("csrftoken")
    with patch("mongosemantic.web.routes.apply.MongoConnection.open", return_value=_conn(db)):
        r = client.post(
            "/api/collections/articles/apply",
            json={"fields": ["body"], "mode": "inline", "chunked": True,
                  "model": "local-fast"},
            headers={CSRF_HEADER: token},
        )
        assert r.status_code == 400
        assert "chunk" in r.json()["detail"].lower()


def test_apply_rejects_bad_collection(monkeypatch):
    client, db = _client_db(monkeypatch)
    seed = client.get("/healthz")
    token = seed.cookies.get("csrftoken")
    r = client.post(
        "/api/collections/$evil/apply",
        json={"fields": ["body"], "mode": "shadow", "chunked": False,
              "model": "local-fast"},
        headers={CSRF_HEADER: token},
    )
    assert r.status_code == 400


def test_apply_rejects_unknown_model(monkeypatch):
    client, db = _client_db(monkeypatch)
    seed = client.get("/healthz")
    token = seed.cookies.get("csrftoken")
    with patch("mongosemantic.web.routes.apply.MongoConnection.open", return_value=_conn(db)):
        r = client.post(
            "/api/collections/articles/apply",
            json={"fields": ["body"], "mode": "shadow", "chunked": False,
                  "model": "bogus"},
            headers={CSRF_HEADER: token},
        )
        assert r.status_code == 400
