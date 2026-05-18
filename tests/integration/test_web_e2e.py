"""End-to-end web flow: HTTP server → index route → worker → search route.

Spins up uvicorn on a free port in a background thread, hits the routes the
same way a browser would, runs one worker batch in-process to drain the queue,
and then asserts the semantic-search result includes the seeded document.
"""
import threading
import time
from datetime import datetime, timezone

import httpx
import pytest
import uvicorn

from mongosemantic.embeddings.provider import get_provider
from mongosemantic.state.config_store import CollectionConfig, FieldSpec, save_config
from mongosemantic.web.app import create_app
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
    monkeypatch.setenv("MONGOSEMANTIC_URI", "mongodb://localhost:27117/?replicaSet=rs0")
    monkeypatch.setenv("MONGOSEMANTIC_DB", db.name)
    monkeypatch.setenv("MONGOSEMANTIC_MODEL", "local-fast")
    config = uvicorn.Config(create_app(), host="127.0.0.1", port=18091, log_level="error")
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(40):
        try:
            r = httpx.get("http://127.0.0.1:18091/healthz", timeout=0.5)
            if r.status_code == 200:
                break
        except Exception:
            time.sleep(0.25)
    else:
        raise RuntimeError("server failed to start")
    try:
        with httpx.Client(base_url="http://127.0.0.1:18091") as c:
            assert c.get("/healthz").json()["ok"] is True
            assert "connection" in c.get("/api/content").json()
            r = c.get("/api/collections")
            assert r.status_code == 200
            assert any(row["name"] == "articles" for row in r.json()["collections"])
            r = c.post(
                "/api/collections/articles/index",
                headers={"X-CSRF-Token": c.cookies.get("csrftoken", "")},
            )
            assert r.status_code == 200, r.text
        process_batch(db, get_provider("local-fast"), "t", 32)
        with httpx.Client(base_url="http://127.0.0.1:18091") as c:
            r = c.get("/api/search", params={"q": "vector database", "collection": "articles"})
            assert r.status_code == 200
            rows = r.json()["rows"]
            assert any("semantic" in row.get("chunk_text", "") for row in rows)
    finally:
        server.should_exit = True
        t.join(timeout=5)
