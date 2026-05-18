from fastapi import FastAPI
from fastapi.testclient import TestClient

from mongosemantic.web.security import (
    CSRF_HEADER,
    install_csrf,
    install_rate_limit,
    install_security_headers,
)


def _csrf_app():
    app = FastAPI()
    install_csrf(app)

    @app.get("/r")
    def _r():
        return {"ok": True}

    @app.post("/w")
    def _w():
        return {"ok": True}

    return app


def test_get_emits_csrf_cookie():
    client = TestClient(_csrf_app())
    r = client.get("/r")
    assert r.status_code == 200
    assert "csrftoken" in r.cookies


def test_post_without_csrf_token_is_forbidden():
    client = TestClient(_csrf_app())
    client.get("/r")
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
    def _x():
        return {}

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
    def _x():
        return {}

    client = TestClient(app)
    for _ in range(3):
        assert client.get("/x").status_code == 200
    assert client.get("/x").status_code == 429
