import mongomock

from mongosemantic.state.resume_tokens import (
    load_polling_watermark,
    load_resume_token,
    save_polling_watermark,
    save_resume_token,
)


def _db():
    return mongomock.MongoClient()["test"]

def test_resume_token_round_trip():
    db = _db()
    assert load_resume_token(db) is None
    save_resume_token(db, {"_data": "token-v1"})
    assert load_resume_token(db) == {"_data": "token-v1"}

def test_polling_watermark_per_collection():
    db = _db()
    assert load_polling_watermark(db, "articles") is None
    save_polling_watermark(db, "articles", 1000)
    save_polling_watermark(db, "products", 2000)
    assert load_polling_watermark(db, "articles") == 1000
    assert load_polling_watermark(db, "products") == 2000
