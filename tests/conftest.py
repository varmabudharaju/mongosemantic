import os

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: requires docker compose (see README)")

def pytest_collection_modifyitems(config, items):
    if os.environ.get("MONGOSEMANTIC_RUN_INTEGRATION") != "1":
        skip_integration = pytest.mark.skip(reason="set MONGOSEMANTIC_RUN_INTEGRATION=1 to run")
        for item in items:
            own_marker_names = {m.name for m in item.own_markers}
            if "integration" in own_marker_names:
                item.add_marker(skip_integration)
