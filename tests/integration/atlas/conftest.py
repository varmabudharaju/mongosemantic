from __future__ import annotations

import os
import time

import pytest
from pymongo import MongoClient
from pymongo.collection import Collection

from mongosemantic.db.client import Topology, detect_topology


@pytest.fixture(scope="session")
def atlas_uri() -> str:
    uri = os.environ.get("MONGOSEMANTIC_ATLAS_URI")
    if not uri:
        pytest.skip("MONGOSEMANTIC_ATLAS_URI not set")
    return uri


@pytest.fixture(scope="session")
def atlas_client(atlas_uri: str) -> MongoClient:
    client = MongoClient(atlas_uri, serverSelectionTimeoutMS=10000)
    client.admin.command("hello")  # surface auth/allowlist failures immediately
    yield client
    client.close()


@pytest.fixture(scope="session")
def atlas_topology(atlas_client: MongoClient, atlas_uri: str) -> Topology:
    topology = detect_topology(atlas_client, atlas_uri)
    if topology is not Topology.ATLAS:
        pytest.skip(f"URI did not detect as Atlas (got {topology}); skipping atlas suite")
    return topology


@pytest.fixture(scope="session")
def atlas_db_name() -> str:
    return "sample_airbnb"


@pytest.fixture(scope="session")
def atlas_collection_name() -> str:
    return "listingsAndReviews"


@pytest.fixture(scope="session")
def atlas_dataset_loaded(
    atlas_client: MongoClient, atlas_db_name: str, atlas_collection_name: str
) -> Collection:
    coll = atlas_client[atlas_db_name][atlas_collection_name]
    count = coll.estimated_document_count()
    if count < 5000:
        pytest.fail(
            f"{atlas_db_name}.{atlas_collection_name} has {count} docs (need >= 5000).\n"
            "In the Atlas console: Database -> '...' -> Load Sample Dataset."
        )
    return coll


def wait_for_search_index_queryable(
    coll: Collection, index_name: str, timeout: float = 180.0, poll: float = 3.0
) -> dict:
    """Poll listSearchIndexes until the index is queryable. Raises TimeoutError on miss."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        for idx in coll.list_search_indexes():
            if idx.get("name") == index_name:
                last = idx
                if idx.get("queryable") is True and idx.get("status") == "READY":
                    return idx
        time.sleep(poll)
    raise TimeoutError(
        f"Atlas search index {index_name!r} not queryable within {timeout}s. Last seen: {last}"
    )


@pytest.fixture
def env_pointing_at_atlas(monkeypatch, atlas_uri: str, atlas_db_name: str):
    """Sets MONGOSEMANTIC_* env vars pointing CliRunner invocations at Atlas."""
    monkeypatch.setenv("MONGOSEMANTIC_URI", atlas_uri)
    monkeypatch.setenv("MONGOSEMANTIC_DB", atlas_db_name)
    monkeypatch.setenv("MONGOSEMANTIC_MODEL", "local-fast")
    return None
