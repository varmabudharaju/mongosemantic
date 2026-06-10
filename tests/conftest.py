import os

import pytest


@pytest.fixture(autouse=True)
def _isolate_connection_store(tmp_path, monkeypatch):
    # CLI commands fall back to the saved connection file under
    # $XDG_CONFIG_HOME/mongosemantic/config.json. Point XDG at a per-test
    # directory so a developer's real saved connection never leaks into tests.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: requires docker compose (see README)")

def pytest_collection_modifyitems(config, items):
    if os.environ.get("MONGOSEMANTIC_RUN_INTEGRATION") != "1":
        skip_integration = pytest.mark.skip(reason="set MONGOSEMANTIC_RUN_INTEGRATION=1 to run")
        for item in items:
            own_marker_names = {m.name for m in item.own_markers}
            if "integration" in own_marker_names:
                item.add_marker(skip_integration)
