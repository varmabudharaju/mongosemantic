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
