from unittest.mock import MagicMock

import pytest

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


@pytest.mark.parametrize(
    "uri",
    [
        "mongodb+srv://user:pw@example.mongodb.net/",  # SRV implies TLS
        "mongodb://host/?tls=true",                     # explicit tls
        "mongodb://host/?ssl=true",                     # legacy alias
    ],
)
def test_open_injects_tls_ca_file_when_tls_in_play(uri):
    """Regression (v0.7.2): when TLS is in play, MongoConnection.open must
    default tlsCAFile to certifi.where() so verification works on systems
    whose Python lacks a discoverable system CA bundle (macOS python.org)."""
    from unittest.mock import MagicMock, patch

    import certifi

    from mongosemantic.db.client import MongoConnection

    fake_client = MagicMock()
    fake_client.admin.command.return_value = {}
    with patch("mongosemantic.db.client.MongoClient", return_value=fake_client) as mc:
        MongoConnection.open(uri, "db")
    _, kwargs = mc.call_args
    assert kwargs.get("tlsCAFile") == certifi.where(), (
        f"MongoClient must default tlsCAFile to certifi.where(); got kwargs={kwargs}"
    )


@pytest.mark.parametrize(
    "uri",
    [
        "mongodb://localhost:27017/",
        "mongodb://localhost:27117/?replicaSet=rs0",
    ],
)
def test_open_does_not_inject_tls_for_non_tls_uri(uri):
    """Regression: plain mongodb:// URIs without tls=true must NOT have
    tlsCAFile injected, because pymongo treats that as an implicit TLS
    request and fails to handshake against non-TLS servers (e.g. local Docker)."""
    from unittest.mock import MagicMock, patch

    from mongosemantic.db.client import MongoConnection

    fake_client = MagicMock()
    fake_client.admin.command.return_value = {}
    with patch("mongosemantic.db.client.MongoClient", return_value=fake_client) as mc:
        MongoConnection.open(uri, "db")
    _, kwargs = mc.call_args
    assert "tlsCAFile" not in kwargs, (
        f"tlsCAFile must NOT be injected for non-TLS URI; got kwargs={kwargs}"
    )


def test_open_respects_user_tls_ca_file_in_uri():
    """Regression: if the user already specifies tlsCAFile in the URI (e.g. a
    corporate/private CA), MongoConnection.open must NOT silently override it
    with certifi's bundle."""
    from unittest.mock import MagicMock, patch

    from mongosemantic.db.client import MongoConnection

    fake_client = MagicMock()
    fake_client.admin.command.return_value = {}
    uri = "mongodb+srv://user:pw@example.mongodb.net/?tlsCAFile=/etc/corp/ca.pem"
    with patch("mongosemantic.db.client.MongoClient", return_value=fake_client) as mc:
        MongoConnection.open(uri, "db")
    _, kwargs = mc.call_args
    assert "tlsCAFile" not in kwargs, (
        f"tlsCAFile must not be injected when URI already specifies it; got kwargs={kwargs}"
    )
