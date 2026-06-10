"""tests/unit/test_local_hybrid.py"""
from mongosemantic.search.local_hybrid import rrf_fuse


def _row(sid, score, text="t"):
    return {
        "source_id": sid, "source_collection": "c", "field_path": "body",
        "chunk_index": 0, "chunk_text": text, "source_doc": {"_id": sid},
        "score": score,
    }


def test_rrf_doc_in_both_lists_outranks_single_list():
    vec = [_row("a", 0.9), _row("b", 0.8)]
    txt = [_row("b", 5.0), _row("c", 3.0)]
    fused = rrf_fuse([vec, txt], weights=[0.6, 0.4], limit=10)
    assert fused[0]["source_id"] == "b"  # rank 2 in vec + rank 1 in text
    ids = [r["source_id"] for r in fused]
    assert set(ids) == {"a", "b", "c"}
    # RRF formula: b = 0.6/(60+2) + 0.4/(60+1); a = 0.6/(60+1)
    assert abs(fused[0]["score"] - (0.6 / 62 + 0.4 / 61)) < 1e-9


def test_rrf_respects_limit_and_sorts_desc():
    vec = [_row(i, 1.0 - i / 10) for i in range(5)]
    fused = rrf_fuse([vec, []], weights=[0.6, 0.4], limit=3)
    assert len(fused) == 3
    assert fused[0]["score"] >= fused[1]["score"] >= fused[2]["score"]


def test_rrf_dedup_key_includes_chunk():
    a0 = _row("a", 0.9)
    a1 = dict(_row("a", 0.8), chunk_index=1)
    fused = rrf_fuse([[a0, a1], []], limit=10)
    assert len(fused) == 2
