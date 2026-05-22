from __future__ import annotations

import json
import stat

import pytest

from mongosemantic.connection_store import (
    SavedConnection,
    config_path,
    delete,
    extract_path_database,
    load,
    save,
)


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Redirect XDG_CONFIG_HOME so the test never touches the real ~/.config."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return tmp_path


def test_config_path_uses_xdg(isolated_home):
    p = config_path()
    assert p == isolated_home / "mongosemantic" / "config.json"


def test_config_path_falls_back_to_home(tmp_path, monkeypatch):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    p = config_path()
    assert p == tmp_path / ".config" / "mongosemantic" / "config.json"


def test_load_missing_returns_none(isolated_home):
    assert load() is None


def test_save_writes_file_with_0600(isolated_home):
    save("mongodb+srv://u:p@cluster.mongodb.net/", "mydb")
    p = config_path()
    assert p.exists()
    mode = stat.S_IMODE(p.stat().st_mode)
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"


def test_save_creates_parent_dir_with_0700(isolated_home):
    save("mongodb+srv://u:p@cluster.mongodb.net/", "mydb")
    parent = config_path().parent
    mode = stat.S_IMODE(parent.stat().st_mode)
    assert mode == 0o700, f"expected 0700, got {oct(mode)}"


def test_roundtrip(isolated_home):
    save("mongodb+srv://u:p@cluster.mongodb.net/", "mydb")
    sc = load()
    assert isinstance(sc, SavedConnection)
    assert sc.uri == "mongodb+srv://u:p@cluster.mongodb.net/"
    assert sc.database == "mydb"
    assert sc.saved_at  # ISO 8601 string, non-empty


def test_overwrite(isolated_home):
    save("mongodb+srv://u1:p@c.mongodb.net/", "db1")
    save("mongodb+srv://u2:p@c.mongodb.net/", "db2")
    sc = load()
    assert sc.uri == "mongodb+srv://u2:p@c.mongodb.net/"
    assert sc.database == "db2"


def test_delete_removes_file(isolated_home):
    save("mongodb+srv://u:p@cluster.mongodb.net/", "mydb")
    delete()
    assert not config_path().exists()
    assert load() is None


def test_delete_is_idempotent(isolated_home):
    delete()  # nothing exists yet
    delete()  # still nothing
    assert load() is None


def test_load_malformed_returns_none(isolated_home):
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{ not json")
    p.chmod(0o600)
    assert load() is None


def test_load_partial_returns_none(isolated_home):
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"uri": "mongodb://x"}))  # missing database
    p.chmod(0o600)
    assert load() is None


def test_save_overwrite_preserves_0600(isolated_home):
    save("mongodb+srv://a:b@c.mongodb.net/", "db1")
    save("mongodb+srv://x:y@z.mongodb.net/", "db2")  # overwrite
    mode = stat.S_IMODE(config_path().stat().st_mode)
    assert mode == 0o600


def test_extract_path_db_with_database():
    assert extract_path_database(
        "mongodb+srv://u:p@cluster.mongodb.net/sample_mflix"
    ) == "sample_mflix"


def test_extract_path_db_with_query_string():
    assert extract_path_database(
        "mongodb+srv://u:p@cluster.mongodb.net/sample_mflix?tls=true&retryWrites=true"
    ) == "sample_mflix"


def test_extract_path_db_trailing_slash():
    assert extract_path_database("mongodb+srv://u:p@cluster.mongodb.net/") is None


def test_extract_path_db_no_path():
    assert extract_path_database("mongodb+srv://u:p@cluster.mongodb.net") is None


def test_extract_path_db_no_creds():
    assert extract_path_database("mongodb://localhost:27017/mydb") == "mydb"


def test_extract_path_db_garbage_returns_none():
    assert extract_path_database("not a uri") is None
    assert extract_path_database("") is None
