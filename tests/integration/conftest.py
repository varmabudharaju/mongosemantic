import contextlib
import time
import uuid

import pytest
from pymongo import MongoClient

REPLICA_URI = "mongodb://localhost:27117/?replicaSet=rs0"
STANDALONE_URI = "mongodb://localhost:27219"

def _wait_for(uri: str, timeout: float = 60.0) -> MongoClient:
    deadline = time.time() + timeout
    last_err = None
    while time.time() < deadline:
        client = None
        try:
            client = MongoClient(uri, serverSelectionTimeoutMS=2000)
            client.admin.command("hello")
            return client
        except Exception as e:
            if client is not None:
                with contextlib.suppress(Exception):
                    client.close()
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
    dbname = f"test_{uuid.uuid4().hex[:12]}"
    yield replica_set_client[dbname]
    replica_set_client.drop_database(dbname)

@pytest.fixture
def clean_standalone_db(standalone_client):
    dbname = f"test_{uuid.uuid4().hex[:12]}"
    yield standalone_client[dbname]
    standalone_client.drop_database(dbname)
