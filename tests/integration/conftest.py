import os
import time
import pytest
from pymongo import MongoClient

REPLICA_URI = "mongodb://localhost:27117,localhost:27118,localhost:27119/?replicaSet=rs0"
STANDALONE_URI = "mongodb://localhost:27219"

def _wait_for(uri: str, timeout: float = 60.0) -> MongoClient:
    deadline = time.time() + timeout
    last_err = None
    while time.time() < deadline:
        try:
            client = MongoClient(uri, serverSelectionTimeoutMS=2000)
            client.admin.command("hello")
            return client
        except Exception as e:
            last_err = e
            time.sleep(1)
    raise RuntimeError(f"Mongo at {uri} not reachable within {timeout}s: {last_err}")

@pytest.fixture(scope="session")
def replica_set_client() -> MongoClient:
    client = _wait_for(REPLICA_URI)
    yield client
    client.close()

@pytest.fixture(scope="session")
def standalone_client() -> MongoClient:
    client = _wait_for(STANDALONE_URI)
    yield client
    client.close()

@pytest.fixture
def clean_db(replica_set_client):
    dbname = f"test_{int(time.time() * 1000)}"
    yield replica_set_client[dbname]
    replica_set_client.drop_database(dbname)

@pytest.fixture
def clean_standalone_db(standalone_client):
    dbname = f"test_{int(time.time() * 1000)}"
    yield standalone_client[dbname]
    standalone_client.drop_database(dbname)
