import pytest

from mongosemantic.db.client import Topology, detect_topology


@pytest.mark.integration
def test_real_replica_set_topology(replica_set_client):
    t = detect_topology(
        replica_set_client, uri="mongodb://localhost:27117,localhost:27118/?replicaSet=rs0"
    )
    assert t == Topology.REPLICA_SET

@pytest.mark.integration
def test_real_standalone_topology(standalone_client):
    t = detect_topology(standalone_client, uri="mongodb://localhost:27219")
    assert t == Topology.STANDALONE
