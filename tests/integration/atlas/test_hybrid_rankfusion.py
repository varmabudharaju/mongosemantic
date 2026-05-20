"""Tier 4 — $rankFusion hybrid path on Atlas.

Reuses Tier 2's state (no teardown / apply). Verifies hybrid search returns
results without raising the score-projection TypeError that v0.7.3 fixed.

Atlas notes:
- The original plan branched on MongoDB version (8.1+ for $rankFusion).
  Empirically Atlas appears to support $rankFusion on what `buildInfo`
  reports as 8.0.x — likely a backport on Atlas's side. We don't trust
  the version branch; instead we just assert the call succeeds and
  returns results.
- The fallback-notice path (when hybrid is genuinely unsupported, e.g.
  inline mode) is unit-tested under tests/unit/.
"""
from __future__ import annotations

import pytest
from typer.testing import CliRunner

from mongosemantic.cli import app


@pytest.mark.atlas
def test_hybrid_search_returns_results_without_error(
    atlas_client, env_pointing_at_atlas, atlas_collection_name,
):
    runner = CliRunner()
    r = runner.invoke(app, [
        "search", "gangster crime",
        "--collection", atlas_collection_name,
        "--hybrid",
        "--limit", "5",
    ])
    assert r.exit_code == 0, r.output
    if r.exception:
        raise AssertionError(
            f"hybrid search raised: {r.exception!r}\noutput:\n{r.output}"
        )

    # Score-projection bug fixed in v0.7.3 raised TypeError on any 2+ result
    # set. Assert we got a populated result table now.
    assert "Score" in r.output and "Collection" in r.output, (
        f"expected result table header, got:\n{r.output}"
    )
    # At least one fractional score row (RRF fused score, typically < 0.05).
    has_score_row = any(
        f"0.{i:03d}" in r.output for i in range(1, 1000)
    )
    assert has_score_row, (
        f"expected at least one fractional score in result table, got:\n{r.output}"
    )
