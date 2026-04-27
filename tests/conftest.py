import os

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: requires docker compose (see README)")

def pytest_collection_modifyitems(config, items):
    if os.environ.get("MONGOSEMANTIC_RUN_INTEGRATION") != "1":
        skip_integration = pytest.mark.skip(reason="set MONGOSEMANTIC_RUN_INTEGRATION=1 to run")
        for item in items:
            if "integration" in item.keywords:
                item.add_marker(skip_integration)
