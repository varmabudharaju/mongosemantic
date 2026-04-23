import numpy as np

from mongosemantic.search.atlas import build_atlas_pipeline
from mongosemantic.search.brute_force import build_brute_pipeline


def test_atlas_pipeline_shape():
    q = np.zeros(384, dtype=np.float32).tolist()
    pipeline = build_atlas_pipeline(
        source_collection="articles",
        field_path="body",
        query_vector=q,
        limit=10,
        index_name="mongosemantic_articles_abc",
    )
    assert pipeline[0] == {
        "$vectorSearch": {
            "index": "mongosemantic_articles_abc",
            "path": "embedding",
            "queryVector": q,
            "numCandidates": 100,
            "limit": 10,
        }
    }
    lookup = next(s for s in pipeline if "$lookup" in s)
    assert lookup["$lookup"]["from"] == "articles"
    proj = next(s for s in pipeline if "$project" in s)
    assert proj["$project"]["score"] == {"$meta": "vectorSearchScore"}

def test_atlas_pipeline_limit_drives_num_candidates():
    q = [0.0] * 384
    pipeline = build_atlas_pipeline(
        source_collection="c", field_path="body", query_vector=q, limit=50, index_name="i"
    )
    assert pipeline[0]["$vectorSearch"]["numCandidates"] == 500

def test_brute_pipeline_shape():
    q = [0.0] * 384
    pipeline = build_brute_pipeline(
        source_collection="articles",
        field_path="body",
        query_vector=q,
        limit=10,
    )
    match = next(s for s in pipeline if "$match" in s)
    assert match["$match"]["field_path"] == "body"
    sort = next(s for s in pipeline if "$sort" in s)
    assert sort["$sort"] == {"similarity": -1}
    limit = next(s for s in pipeline if "$limit" in s)
    assert limit["$limit"] == 10
