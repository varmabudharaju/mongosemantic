from __future__ import annotations

import typer
from rich.console import Console

from mongosemantic.config import Settings
from mongosemantic.db.client import MongoConnection
from mongosemantic.exceptions import NotConfiguredError
from mongosemantic.state import enqueue_embed, ensure_indexes, load_config
from mongosemantic.sync.change_stream import _get_path, _resolve_text, hash_text

console = Console()


def reindex_cmd(
    collection: str = typer.Option(..., "--collection", "-c"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt"),
) -> None:
    """Force re-embedding of every document in a collection."""
    if not yes:
        typer.confirm(f"Force re-embed every document in {collection}?", abort=True)
    settings = Settings()
    conn = MongoConnection.open(settings.uri, settings.database)
    try:
        db = conn.db
        ensure_indexes(db)
        cfg = load_config(db, collection)
        if not cfg:
            raise NotConfiguredError(f"{collection} not configured")
        # Clear any prior shadow rows for this source so the worker writes fresh ones
        # and downstream sync (change stream / polling) does not short-circuit on stale hashes.
        db[cfg.shadow_collection].delete_many({"source_collection": collection})
        enqueued = 0
        for doc in db[collection].find({}):
            key = doc.get("_id")
            for spec in cfg.fields:
                text = _resolve_text(_get_path(doc, spec.path))
                if not text:
                    continue
                h = hash_text(cfg.embedding_model, text)
                enqueue_embed(
                    db,
                    collection=collection,
                    source_id=key,
                    field_path=spec.path,
                    chunk_index=None,
                    input_text=text,
                    input_hash=h,
                    model=cfg.embedding_model,
                )
                enqueued += 1
        console.print(
            f"[green]Cleared shadow rows and enqueued {enqueued} reindex jobs.[/green]"
        )
    finally:
        conn.close()
