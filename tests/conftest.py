import os

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: requires docker compose (see README)")
    config.addinivalue_line(
        "markers",
        "atlas: requires Atlas M0 cluster (set MONGOSEMANTIC_RUN_ATLAS_INTEGRATION=1 and MONGOSEMANTIC_ATLAS_URI)",
    )


def pytest_collection_modifyitems(config, items):
    if os.environ.get("MONGOSEMANTIC_RUN_INTEGRATION") != "1":
        skip_integration = pytest.mark.skip(reason="set MONGOSEMANTIC_RUN_INTEGRATION=1 to run")
        for item in items:
            if "integration" in item.keywords:
                item.add_marker(skip_integration)

    if os.environ.get("MONGOSEMANTIC_RUN_ATLAS_INTEGRATION") != "1":
        skip_atlas = pytest.mark.skip(
            reason="set MONGOSEMANTIC_RUN_ATLAS_INTEGRATION=1 and MONGOSEMANTIC_ATLAS_URI to run"
        )
        for item in items:
            if "atlas" in item.keywords:
                item.add_marker(skip_atlas)
    elif not os.environ.get("MONGOSEMANTIC_ATLAS_URI"):
        skip_atlas = pytest.mark.skip(reason="MONGOSEMANTIC_ATLAS_URI not set")
        for item in items:
            if "atlas" in item.keywords:
                item.add_marker(skip_atlas)
