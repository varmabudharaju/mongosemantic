from __future__ import annotations

import typer
from rich.console import Console

from mongosemantic.config import Settings
from mongosemantic.db.client import MongoConnection
from mongosemantic.exceptions import NotConfiguredError
from mongosemantic.state import ensure_indexes, load_config
from mongosemantic.sync.enqueue import enqueue_for_doc

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
        # Clear any prior embedding state for this collection so the worker writes fresh ones
        # and downstream sync (change stream / polling) does not short-circuit on stale hashes.
        if cfg.mode == "inline":
            db[collection].update_many({}, {"$unset": {"_msem": ""}})
        elif cfg.shadow_collection:
            db[cfg.shadow_collection].delete_many({"source_collection": collection})
        enqueued = 0
        for doc in db[collection].find({}):
            enqueued += enqueue_for_doc(
                db, cfg, source_id=doc.get("_id"), doc=doc, force=True
            )
        console.print(
            f"[green]Cleared shadow rows and enqueued {enqueued} reindex jobs.[/green]"
        )
    finally:
        conn.close()
