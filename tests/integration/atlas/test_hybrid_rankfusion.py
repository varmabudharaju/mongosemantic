"""Tier 4 — $rankFusion hybrid path (8.1+) or documented fallback (8.0-).

Reuses Tier 2's state (no teardown / apply). Detects the Atlas cluster's
MongoDB version and branches the assertion accordingly.
"""
from __future__ import annotations

import pytest
from typer.testing import CliRunner

from mongosemantic.cli import app


def _major_minor(client) -> tuple[int, int]:
    build = client.admin.command("buildInfo")
    parts = build.get("version", "0.0").split(".")
    major = int(parts[0])
    minor = int(parts[1]) if len(parts) > 1 else 0
    return major, minor


@pytest.mark.atlas
def test_hybrid_path_or_documented_fallback(
    atlas_client, env_pointing_at_atlas, atlas_collection_name,
):
    runner = CliRunner()
    major, minor = _major_minor(atlas_client)

    r = runner.invoke(app, [
        "search", "gangster crime",
        "--collection", atlas_collection_name,
        "--hybrid",
        "--limit", "5",
    ])
    assert r.exit_code == 0, r.output

    if (major, minor) >= (8, 1):
        # 8.1+ supports $rankFusion natively. No fallback banner.
        assert "fell back" not in r.output.lower(), (
            f"unexpected fallback notice on MongoDB {major}.{minor}: {r.output}"
        )
    else:
        # 8.0 or older: hybrid must emit the documented notice rather than
        # silently degrading.
        assert "fallback" in r.output.lower() or "notice" in r.output.lower(), (
            f"Expected hybrid fallback notice on MongoDB {major}.{minor}, got:\n{r.output}"
        )
