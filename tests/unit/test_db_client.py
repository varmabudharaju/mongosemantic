from unittest.mock import MagicMock

from mongosemantic.db.client import Topology, detect_topology


def _hello(setName=None, msg=None):
    m = MagicMock()
    m.admin.command.return_value = {k: v for k, v in {"setName": setName, "msg": msg}.items() if v}
    return m

def test_detect_atlas_from_uri():
    c = _hello(setName="atlas-abc")
    t = detect_topology(c, uri="mongodb+srv://cluster0.abc123.mongodb.net")
    assert t == Topology.ATLAS

def test_detect_replica_set_by_setname():
    c = _hello(setName="rs0")
    t = detect_topology(c, uri="mongodb://localhost:27117")
    assert t == Topology.REPLICA_SET

def test_detect_sharded_by_msg():
    c = _hello(msg="isdbgrid")
    t = detect_topology(c, uri="mongodb://mongos:27017")
    assert t == Topology.REPLICA_SET  # sharded treated as replica for our purposes

def test_detect_standalone():
    c = _hello()
    t = detect_topology(c, uri="mongodb://localhost:27017")
    assert t == Topology.STANDALONE

def test_open_propagates_connection_failure(monkeypatch):
    """If the initial hello fails, MongoConnection.open() must propagate, not swallow."""
    from unittest.mock import MagicMock, patch

    from pymongo.errors import ServerSelectionTimeoutError

    from mongosemantic.db.client import MongoConnection

    fake_client = MagicMock()
    fake_client.admin.command.side_effect = ServerSelectionTimeoutError("cluster unreachable")
    with patch("mongosemantic.db.client.MongoClient", return_value=fake_client):
        import pytest
        with pytest.raises(ServerSelectionTimeoutError):
            MongoConnection.open("mongodb://bogus:27017", "db")


def test_open_passes_certifi_ca_bundle_to_mongo_client():
    """Regression: MongoConnection.open must pass tlsCAFile=certifi.where() to
    MongoClient so TLS connections (mongodb+srv://, Atlas) work on systems whose
    Python install lacks a discoverable system CA bundle — notably macOS Python
    from python.org without 'Install Certificates.command' run, or Apple's
    system Python."""
    from unittest.mock import MagicMock, patch

    import certifi

    from mongosemantic.db.client import MongoConnection

    fake_client = MagicMock()
    fake_client.admin.command.return_value = {}
    with patch("mongosemantic.db.client.MongoClient", return_value=fake_client) as mc:
        MongoConnection.open("mongodb+srv://user:pw@example.mongodb.net/", "db")
    _, kwargs = mc.call_args
    assert kwargs.get("tlsCAFile") == certifi.where(), (
        f"MongoClient must be invoked with tlsCAFile=certifi.where(); got kwargs={kwargs}"
    )
