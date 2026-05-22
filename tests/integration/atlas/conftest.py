from __future__ import annotations

import os
import time

import certifi
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
    # Mirror the tlsCAFile default we apply in MongoConnection.open so the
    # fixture works on systems without a discoverable system CA bundle.
    kwargs: dict = {"serverSelectionTimeoutMS": 10000}
    if "tlsCAFile" not in atlas_uri:
        kwargs["tlsCAFile"] = certifi.where()
    client = MongoClient(atlas_uri, **kwargs)
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
    return "sample_mflix"


@pytest.fixture(scope="session")
def atlas_collection_name() -> str:
    # embedded_movies (~3,483 docs) over movies (~21k): Atlas per-doc latency
    # makes indexing the full movies corpus impractical for a verification
    # suite (~1 hour vs ~5 min). embedded_movies is Atlas's curated
    # vector-search demo subset and has the same field shape.
    return "embedded_movies"


@pytest.fixture(scope="session")
def atlas_dataset_loaded(
    atlas_client: MongoClient, atlas_db_name: str, atlas_collection_name: str
) -> Collection:
    coll = atlas_client[atlas_db_name][atlas_collection_name]
    count = coll.estimated_document_count()
    if count < 3000:
        pytest.fail(
            f"{atlas_db_name}.{atlas_collection_name} has {count} docs (need >= 3000).\n"
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


def wait_for_no_mongosemantic_search_indexes(
    db, collections: list[str], timeout: float = 120.0, poll: float = 3.0
) -> None:
    """Poll until all mongosemantic_* search indexes across `collections`
    are fully deleted on Atlas.

    Atlas `dropSearchIndex` is asynchronous: the index disappears from
    `listSearchIndexes` immediately but still counts against the
    per-cluster FTS-index cap (3 on M0/M2/M5) until cleanup completes.
    Without this wait, a teardown -> apply sequence in the same test
    can spuriously hit the cap.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        remaining = []
        for cname in collections:
            try:
                for idx in db[cname].list_search_indexes():
                    if idx.get("name", "").startswith("mongosemantic"):
                        remaining.append(f"{cname}:{idx['name']}")
            except Exception:
                pass
        if not remaining:
            # Extra grace: Atlas's cap accounting can lag past list visibility.
            time.sleep(5)
            return
        time.sleep(poll)
    raise TimeoutError(
        f"mongosemantic_* indexes still present after {timeout}s: {remaining}"
    )


@pytest.fixture
def env_pointing_at_atlas(monkeypatch, atlas_uri: str, atlas_db_name: str):
    """Sets MONGOSEMANTIC_* env vars pointing CliRunner invocations at Atlas."""
    monkeypatch.setenv("MONGOSEMANTIC_URI", atlas_uri)
    monkeypatch.setenv("MONGOSEMANTIC_DB", atlas_db_name)
    monkeypatch.setenv("MONGOSEMANTIC_MODEL", "local-fast")
    return None
