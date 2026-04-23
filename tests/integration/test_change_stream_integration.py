import threading
import time
from datetime import datetime, timezone

import pytest

from mongosemantic.state import count_by_status
from mongosemantic.state.config_store import CollectionConfig, FieldSpec, save_config
from mongosemantic.sync.change_stream import ChangeStreamListener


@pytest.mark.integration
def test_change_stream_picks_up_real_insert(clean_db):
    db = clean_db
    save_config(db, CollectionConfig(
        collection="articles", mode="shadow", shadow_collection="articles_embeddings",
        fields=[FieldSpec(path="body")], embedding_model="local-fast",
        embedding_dim=384, created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    ))
    listener = ChangeStreamListener(db, ["articles"])
    t = threading.Thread(target=listener.run, daemon=True)
    t.start()
    time.sleep(2.0)  # allow the stream to open
    db["articles"].insert_one({"_id": "doc1", "body": "semantic"})
    for _ in range(20):
        if count_by_status(db).get("pending", 0) >= 1:
            break
        time.sleep(0.5)
    listener.stop()
    assert count_by_status(db).get("pending", 0) == 1
