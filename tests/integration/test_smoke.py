import pytest


@pytest.mark.integration
def test_replica_set_is_reachable(replica_set_client):
    info = replica_set_client.admin.command("hello")
    assert info.get("setName") == "rs0"

@pytest.mark.integration
def test_standalone_is_reachable(standalone_client):
    info = standalone_client.admin.command("hello")
    assert info.get("setName") is None
