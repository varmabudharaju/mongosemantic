import mongomock

from mongosemantic.state.job_queue import (
    claim_batch,
    complete,
    count_by_status,
    enqueue_delete_all,
    enqueue_embed,
    fail,
    reset_failed,
)


def _db():
    return mongomock.MongoClient()["test"]


def test_enqueue_and_claim():
    db = _db()
    enqueue_embed(
        db, collection="articles", source_id="abc", field_path="body",
        chunk_index=0, input_text="hello", input_hash="sha1:abc", model="local-fast",
    )
    batch = claim_batch(db, worker_id="w1", limit=10)
    assert len(batch) == 1
    assert batch[0]["collection"] == "articles"
    assert batch[0]["status"] == "in_flight"


def test_complete_removes_from_pending_count():
    db = _db()
    enqueue_embed(db, "c", "id1", "body", 0, "t", "h", "local-fast")
    batch = claim_batch(db, "w1", 10)
    complete(db, batch[0]["_id"])
    counts = count_by_status(db)
    assert counts.get("completed", 0) == 1
    assert counts.get("in_flight", 0) == 0


def test_fail_records_error_and_attempts():
    db = _db()
    enqueue_embed(db, "c", "id1", "body", 0, "t", "h", "local-fast")
    batch = claim_batch(db, "w1", 10)
    fail(db, batch[0]["_id"], reason="provider 500")
    counts = count_by_status(db)
    assert counts.get("pending", 0) == 1


def test_fail_three_times_moves_to_failed():
    db = _db()
    enqueue_embed(db, "c", "id1", "body", 0, "t", "h", "local-fast")
    for _ in range(4):
        batch = claim_batch(db, "w1", 10)
        if not batch:
            break
        fail(db, batch[0]["_id"], reason="boom")
    counts = count_by_status(db)
    assert counts.get("failed", 0) == 1


def test_reset_failed_returns_them_to_pending():
    db = _db()
    enqueue_embed(db, "c", "id1", "body", 0, "t", "h", "local-fast")
    for _ in range(4):
        batch = claim_batch(db, "w1", 10)
        if not batch:
            break
        fail(db, batch[0]["_id"], reason="boom")
    reset_failed(db)
    counts = count_by_status(db)
    assert counts.get("pending", 0) == 1
    assert counts.get("failed", 0) == 0


def test_dedup_upsert_on_same_logical_job():
    db = _db()
    enqueue_embed(db, "c", "id1", "body", 0, "t", "h", "local-fast")
    enqueue_embed(db, "c", "id1", "body", 0, "t", "h", "local-fast")
    counts = count_by_status(db)
    assert counts.get("pending", 0) == 1


def test_enqueue_delete_all():
    db = _db()
    enqueue_delete_all(db, "articles", "doc1")
    batch = claim_batch(db, "w1", 10)
    assert batch[0]["kind"] == "delete"
