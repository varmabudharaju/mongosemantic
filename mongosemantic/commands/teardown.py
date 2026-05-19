from __future__ import annotations

import typer
from rich.console import Console

from mongosemantic.config import Settings
from mongosemantic.db.client import MongoConnection
from mongosemantic.exceptions import NotConfiguredError
from mongosemantic.state import delete_config, load_config

console = Console()


def teardown_cmd(
    collection: str = typer.Option(..., "--collection", "-c"),
    drop_data: bool = typer.Option(
        True, "--drop-data/--keep-data",
        help="Drop the shadow collection (or clear inline _msem fields). Default true.",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    """Remove semantic-search configuration from a collection.

    By default also drops the shadow collection (or clears the inline `_msem`
    fields). Pass `--keep-data` to preserve the embeddings so you can
    rebuild the config later without re-embedding.
    """
    if not yes:
        typer.confirm(f"Remove semantic-search config from {collection}?", abort=True)
    settings = Settings()
    conn = MongoConnection.open(settings.uri, settings.database)
    try:
        db = conn.db
        cfg = load_config(db, collection)
        if not cfg:
            raise NotConfiguredError(f"{collection} not configured")
        if drop_data:
            if cfg.mode == "inline":
                db[collection].update_many({}, {"$unset": {"_msem": ""}})
                console.print(f"[blue]Cleared inline _msem on {collection}.[/blue]")
            elif cfg.shadow_collection:
                db.drop_collection(cfg.shadow_collection)
                console.print(f"[blue]Dropped {cfg.shadow_collection}.[/blue]")
        delete_config(db, collection)
        console.print(f"[green]Removed semantic-search config for {collection}.[/green]")
    finally:
        conn.close()
