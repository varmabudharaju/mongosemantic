"""Hybrid search pipeline + index name tests.

We can't execute `$rankFusion` against mongomock — these tests verify the
pipeline *shape* is what Atlas expects. End-to-end execution is verified
against a real Atlas cluster when one is available.
"""
import pytest

from mongosemantic.search.hybrid import (
    build_hybrid_pipeline,
    search_index_name,
)


def test_pipeline_uses_rank_fusion_over_named_vector_and_text_inputs():
    p = build_hybrid_pipeline(
        source_collection="articles",
        field_path="body",
        query_text="budget travel",
        query_vector=[0.1, 0.2, 0.3],
        limit=5,
        vector_index_name="mongosemantic_articles_abc",
        search_index_name="mongosemantic_search_articles_abc",
    )
    assert "$rankFusion" in p[0]
    rf = p[0]["$rankFusion"]
    pipelines = rf["input"]["pipelines"]
    assert set(pipelines) == {"vector", "text"}

    vec = pipelines["vector"][0]["$vectorSearch"]
    assert vec["index"] == "mongosemantic_articles_abc"
    assert vec["queryVector"] == [0.1, 0.2, 0.3]
    assert vec["path"] == "embedding"
    assert vec["limit"] == 5

    txt = pipelines["text"][0]["$search"]
    assert txt["index"] == "mongosemantic_search_articles_abc"
    assert txt["text"]["query"] == "budget travel"
    assert txt["text"]["path"] == "chunk_text"


def test_pipeline_applies_field_path_filter():
    """We embed many fields into one shadow collection — the result must
    still be scoped to the field_path the user asked for."""
    p = build_hybrid_pipeline(
        source_collection="articles",
        field_path="title",
        query_text="q",
        query_vector=[0.0, 0.0, 0.0],
        limit=10,
        vector_index_name="vidx",
        search_index_name="sidx",
    )
    match = next(s for s in p if "$match" in s)
    assert match["$match"]["field_path"] == "title"


def test_pipeline_includes_source_lookup_and_score():
    p = build_hybrid_pipeline(
        source_collection="articles",
        field_path="body",
        query_text="q",
        query_vector=[0.0, 0.0, 0.0],
        limit=10,
        vector_index_name="v",
        search_index_name="s",
    )
    # Joins back to the source collection so callers get source_doc, like the
    # vector-only pipelines do.
    lookup = next(s for s in p if "$lookup" in s)
    assert lookup["$lookup"]["from"] == "articles"
    proj = next(s for s in p if "$project" in s)
    assert "score" in proj["$project"]


def test_pipeline_weights_default_to_balanced():
    p = build_hybrid_pipeline(
        source_collection="x", field_path="b", query_text="q",
        query_vector=[0.0], limit=10,
        vector_index_name="v", search_index_name="s",
    )
    weights = p[0]["$rankFusion"]["combination"]["weights"]
    assert weights["vector"] > 0 and weights["text"] > 0


def test_pipeline_weights_customizable():
    p = build_hybrid_pipeline(
        source_collection="x", field_path="b", query_text="q",
        query_vector=[0.0], limit=10,
        vector_index_name="v", search_index_name="s",
        vector_weight=0.8, text_weight=0.2,
    )
    weights = p[0]["$rankFusion"]["combination"]["weights"]
    assert weights == {"vector": 0.8, "text": 0.2}


def test_search_index_name_is_stable_and_distinct_from_vector_index():
    from mongosemantic.db.indexes import vector_index_name
    vname = vector_index_name("articles", "body")
    sname = search_index_name("articles", "body")
    assert sname != vname
    assert sname.startswith("mongosemantic_search_articles_")
    # Stable across calls
    assert sname == search_index_name("articles", "body")


def test_zero_or_negative_weight_rejected():
    with pytest.raises(ValueError):
        build_hybrid_pipeline(
            source_collection="x", field_path="b", query_text="q",
            query_vector=[0.0], limit=10,
            vector_index_name="v", search_index_name="s",
            vector_weight=0.0, text_weight=1.0,
        )
