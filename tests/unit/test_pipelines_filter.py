"""tests/unit/test_pipelines_filter.py"""
from mongosemantic.search.atlas import build_atlas_pipeline
from mongosemantic.search.hybrid import build_hybrid_pipeline
from mongosemantic.search.inline import build_inline_atlas_pipeline

QV = [0.1, 0.2]
FLT = {"year": {"$gte": 1960}}


def _stages(pipeline, name):
    return [s for s in pipeline if name in s]


def test_atlas_pipeline_overfetches_and_postmatches():
    p = build_atlas_pipeline("movies", "plot", QV, limit=10,
                             index_name="ix", source_filter=FLT)
    vs = p[0]["$vectorSearch"]
    assert vs["limit"] == 50
    assert vs["numCandidates"] == 500
    matches = [m["$match"] for m in _stages(p, "$match")]
    assert {"source_doc.year": {"$gte": 1960}} in matches
    limits = [s["$limit"] for s in _stages(p, "$limit")]
    assert limits[-1] == 10
    # filter match must come after the $lookup (source_doc exists only then)
    lookup_idx = next(i for i, s in enumerate(p) if "$lookup" in s)
    match_idx = next(i for i, s in enumerate(p)
                     if "$match" in s and "source_doc.year" in str(s))
    assert match_idx > lookup_idx


def test_atlas_pipeline_no_filter_unchanged():
    p = build_atlas_pipeline("movies", "plot", QV, limit=10, index_name="ix")
    assert p[0]["$vectorSearch"]["limit"] == 10
    assert not any("source_doc.year" in str(s) for s in p)
    assert not any("$limit" in s for s in p)


def test_inline_atlas_pipeline_matches_unprefixed():
    p = build_inline_atlas_pipeline("plot", QV, limit=10, index_name="ix",
                                    source_filter=FLT)
    assert p[0]["$vectorSearch"]["limit"] == 50
    assert {"year": {"$gte": 1960}} in [m["$match"] for m in _stages(p, "$match")]
    assert [s["$limit"] for s in _stages(p, "$limit")][-1] == 10


def test_inline_atlas_pipeline_no_filter_unchanged():
    p = build_inline_atlas_pipeline("plot", QV, limit=10, index_name="ix")
    assert p[0]["$vectorSearch"]["limit"] == 10


def test_hybrid_pipeline_filter():
    p = build_hybrid_pipeline("movies", "plot", "q", QV, limit=10,
                              vector_index_name="v", search_index_name="s",
                              source_filter=FLT)
    sub = p[0]["$rankFusion"]["input"]["pipelines"]
    assert sub["vector"][0]["$vectorSearch"]["limit"] == 50
    assert {"source_doc.year": {"$gte": 1960}} in [m["$match"] for m in _stages(p, "$match")]
    assert [s["$limit"] for s in _stages(p, "$limit")][-1] == 10


def test_hybrid_pipeline_no_filter_unchanged():
    p = build_hybrid_pipeline("movies", "plot", "q", QV, limit=10,
                              vector_index_name="v", search_index_name="s")
    sub = p[0]["$rankFusion"]["input"]["pipelines"]
    assert sub["vector"][0]["$vectorSearch"]["limit"] == 10


# --- numCandidates clamp (Atlas rejects numCandidates > 10k) ------------------
# limit=300 + source_filter -> fetch_limit 1500 -> raw 15000 -> clamped 10000.

def test_atlas_pipeline_clamps_num_candidates_at_10k():
    p = build_atlas_pipeline("movies", "plot", QV, limit=300,
                             index_name="ix", source_filter=FLT)
    vs = p[0]["$vectorSearch"]
    assert vs["limit"] == 1500
    assert vs["numCandidates"] == 10_000


def test_inline_atlas_pipeline_clamps_num_candidates_at_10k():
    p = build_inline_atlas_pipeline("plot", QV, limit=300, index_name="ix",
                                    source_filter=FLT)
    vs = p[0]["$vectorSearch"]
    assert vs["limit"] == 1500
    assert vs["numCandidates"] == 10_000


def test_hybrid_pipeline_clamps_num_candidates_at_10k():
    p = build_hybrid_pipeline("movies", "plot", "q", QV, limit=300,
                              vector_index_name="v", search_index_name="s",
                              source_filter=FLT)
    vs = p[0]["$rankFusion"]["input"]["pipelines"]["vector"][0]["$vectorSearch"]
    assert vs["limit"] == 1500
    assert vs["numCandidates"] == 10_000
