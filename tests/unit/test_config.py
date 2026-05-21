import pytest

from mongosemantic import connection_store
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


# ---------------------------------------------------------------------------
# Task 2: Settings.from_environment() — env var > config file precedence
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_env(monkeypatch):
    for k in ("MONGOSEMANTIC_URI", "MONGOSEMANTIC_DB", "MONGOSEMANTIC_MODEL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("MONGOSEMANTIC_MODEL", "local-fast")
    yield monkeypatch


@pytest.fixture
def isolated_xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    yield tmp_path


def test_from_environment_uses_env_var(clean_env, isolated_xdg):
    clean_env.setenv("MONGOSEMANTIC_URI", "mongodb://env-host/")
    clean_env.setenv("MONGOSEMANTIC_DB", "env_db")
    s = Settings.from_environment()
    assert s.uri == "mongodb://env-host/"
    assert s.database == "env_db"
    assert s.source == "env"


def test_from_environment_falls_back_to_file(clean_env, isolated_xdg):
    connection_store.save("mongodb://file-host/", "file_db")
    s = Settings.from_environment()
    assert s.uri == "mongodb://file-host/"
    assert s.database == "file_db"
    assert s.source == "file"


def test_from_environment_env_wins_over_file(clean_env, isolated_xdg):
    connection_store.save("mongodb://file-host/", "file_db")
    clean_env.setenv("MONGOSEMANTIC_URI", "mongodb://env-host/")
    clean_env.setenv("MONGOSEMANTIC_DB", "env_db")
    s = Settings.from_environment()
    assert s.uri == "mongodb://env-host/"
    assert s.source == "env"


def test_from_environment_raises_when_neither(clean_env, isolated_xdg):
    with pytest.raises(ValueError, match="MONGOSEMANTIC_URI is required"):
        Settings.from_environment()


def test_try_from_environment_returns_none_when_neither(clean_env, isolated_xdg):
    assert Settings.try_from_environment() is None


def test_try_from_environment_returns_settings_when_file(clean_env, isolated_xdg):
    connection_store.save("mongodb://file-host/", "file_db")
    s = Settings.try_from_environment()
    assert s is not None
    assert s.source == "file"


def test_legacy_settings_constructor_still_works(clean_env, isolated_xdg):
    # Existing call-sites construct Settings() directly. That path must keep
    # working with just env vars (no file fallback).
    clean_env.setenv("MONGOSEMANTIC_URI", "mongodb://env-host/")
    clean_env.setenv("MONGOSEMANTIC_DB", "env_db")
    s = Settings()
    assert s.uri == "mongodb://env-host/"
    assert s.source == "env"


def test_partial_env_uri_only_raises(clean_env, isolated_xdg):
    clean_env.setenv("MONGOSEMANTIC_URI", "mongodb://x/")
    # MONGOSEMANTIC_DB intentionally unset
    with pytest.raises(ValueError, match="MONGOSEMANTIC_DB"):
        Settings.from_environment()


def test_partial_env_db_only_raises(clean_env, isolated_xdg):
    clean_env.setenv("MONGOSEMANTIC_DB", "mydb")
    # MONGOSEMANTIC_URI intentionally unset
    with pytest.raises(ValueError, match="MONGOSEMANTIC_URI"):
        Settings.from_environment()


def test_partial_env_does_not_silently_use_file(clean_env, isolated_xdg):
    # Even if a file is saved, a partial env var must raise — not mix sources.
    connection_store.save("mongodb://file-host/", "file_db")
    clean_env.setenv("MONGOSEMANTIC_URI", "mongodb://env-host/")
    # MONGOSEMANTIC_DB intentionally unset
    with pytest.raises(ValueError):
        Settings.from_environment()
