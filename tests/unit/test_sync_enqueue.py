from datetime import datetime, timezone

import mongomock

from mongosemantic.state import count_by_status
from mongosemantic.state.config_store import CollectionConfig, FieldSpec, save_config
from mongosemantic.state.job_queue import JOBS_COLLECTION
from mongosemantic.sync.change_stream import hash_text
from mongosemantic.sync.enqueue import enqueue_for_doc


def _db():
    return mongomock.MongoClient()["test"]


def _cfg(db, fields):
    save_config(
        db,
        CollectionConfig(
            collection="articles",
            mode="shadow",
            shadow_collection="articles_embeddings",
            fields=fields,
            embedding_model="local-fast",
            embedding_dim=384,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        ),
    )


def _long_text(n_sentences: int = 30) -> str:
    return ". ".join(f"Sentence number {i} talks about something interesting"
                     for i in range(n_sentences)) + "."


def test_non_chunked_enqueues_one_job_per_field():
    db = _db()
    _cfg(db, [FieldSpec(path="title"), FieldSpec(path="body")])
    cfg = _cfg.__self__ if False else None  # noqa
    from mongosemantic.state import load_config
    cfg = load_config(db, "articles")
    n = enqueue_for_doc(db, cfg, source_id="doc1",
                       doc={"_id": "doc1", "title": "A", "body": "B"})
    assert n == 2
    jobs = list(db[JOBS_COLLECTION].find({}))
    assert sorted(j["field_path"] for j in jobs) == ["body", "title"]
    assert all(j["chunk_index"] is None for j in jobs)


def test_chunked_field_produces_one_job_per_chunk():
    db = _db()
    _cfg(db, [FieldSpec(path="body", chunked=True,
                        chunk_size=20, chunk_overlap=0)])
    from mongosemantic.state import load_config
    cfg = load_config(db, "articles")
    text = _long_text(30)
    n = enqueue_for_doc(db, cfg, source_id="doc1",
                       doc={"_id": "doc1", "body": text})
    jobs = list(db[JOBS_COLLECTION].find({}).sort("chunk_index", 1))
    # Many chunks (>1) and distinct chunk_index 0..N-1
    assert n == len(jobs) > 1
    assert [j["chunk_index"] for j in jobs] == list(range(len(jobs)))
    # Each job has distinct text
    assert len({j["input_text"] for j in jobs}) == len(jobs)


def test_chunked_skips_unchanged_chunks():
    db = _db()
    _cfg(db, [FieldSpec(path="body", chunked=True,
                        chunk_size=20, chunk_overlap=0)])
    from mongosemantic.state import load_config
    cfg = load_config(db, "articles")
    text = _long_text(20)
    # First call — embed everything
    enqueue_for_doc(db, cfg, source_id="doc1", doc={"_id": "doc1", "body": text})
    # Simulate worker completing every job by mirroring jobs into shadow
    for j in db[JOBS_COLLECTION].find({}):
        db["articles_embeddings"].insert_one({
            "source_id": j["source_id"],
            "field_path": j["field_path"],
            "chunk_index": j["chunk_index"],
            "embedding_model": "local-fast",
            "embedding_hash": j["input_hash"],
        })
    db[JOBS_COLLECTION].delete_many({})
    # Same text again — nothing new should be enqueued
    n = enqueue_for_doc(db, cfg, source_id="doc1", doc={"_id": "doc1", "body": text})
    assert n == 0
    assert count_by_status(db) == {}


def test_chunked_cleans_up_stale_chunks_on_shrink():
    db = _db()
    _cfg(db, [FieldSpec(path="body", chunked=True,
                        chunk_size=20, chunk_overlap=0)])
    from mongosemantic.state import load_config
    cfg = load_config(db, "articles")
    # Pre-seed shadow with 5 stale chunks for this doc/field
    for i in range(5):
        db["articles_embeddings"].insert_one({
            "source_id": "doc1",
            "field_path": "body",
            "chunk_index": i,
            "embedding_model": "local-fast",
            "embedding_hash": "sha1:stale",
        })
    # Replace text with something that splits into ≤ 2 chunks
    short_text = "Short sentence one. Short sentence two."
    enqueue_for_doc(db, cfg, source_id="doc1",
                    doc={"_id": "doc1", "body": short_text})
    remaining = list(db["articles_embeddings"].find({"source_id": "doc1"}))
    assert all(r["chunk_index"] < 2 for r in remaining)


def test_empty_text_enqueues_nothing():
    db = _db()
    _cfg(db, [FieldSpec(path="body", chunked=True,
                        chunk_size=20, chunk_overlap=0)])
    from mongosemantic.state import load_config
    cfg = load_config(db, "articles")
    n = enqueue_for_doc(db, cfg, source_id="doc1",
                       doc={"_id": "doc1", "body": ""})
    assert n == 0


def test_non_chunked_skips_when_hash_matches():
    db = _db()
    _cfg(db, [FieldSpec(path="body")])
    from mongosemantic.state import load_config
    cfg = load_config(db, "articles")
    db["articles_embeddings"].insert_one({
        "source_id": "doc1",
        "field_path": "body",
        "chunk_index": 0,
        "embedding_model": "local-fast",
        "embedding_hash": hash_text("local-fast", "same text"),
    })
    n = enqueue_for_doc(db, cfg, source_id="doc1",
                       doc={"_id": "doc1", "body": "same text"})
    assert n == 0
