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


def test_aggregation_runs_safe_pipeline(monkeypatch):
    client, db = _client_db(monkeypatch)
    db["articles"].insert_many([{"x": 1}, {"x": 2}, {"x": 3}])
    seed = client.get("/healthz")
    token = seed.cookies.get("csrftoken")
    with patch("mongosemantic.web.routes.aggregation.MongoConnection.open", return_value=_conn(db)):
        r = client.post(
            "/api/collections/articles/aggregation",
            json={"pipeline": [{"$match": {"x": {"$gte": 2}}}, {"$count": "n"}]},
            headers={CSRF_HEADER: token},
        )
        assert r.status_code == 200, r.text
        assert r.json()["rows"] == [{"n": 2}]


def test_aggregation_rejects_out(monkeypatch):
    client, db = _client_db(monkeypatch)
    seed = client.get("/healthz")
    token = seed.cookies.get("csrftoken")
    with patch("mongosemantic.web.routes.aggregation.MongoConnection.open", return_value=_conn(db)):
        r = client.post(
            "/api/collections/articles/aggregation",
            json={"pipeline": [{"$out": "x"}]},
            headers={CSRF_HEADER: token},
        )
        assert r.status_code == 400
        assert "$out" in r.json()["detail"]
