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
