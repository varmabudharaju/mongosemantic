"""`mongosemantic reindex-hnsw` — force-rebuild HNSW indexes from the
shadow collection. Useful when the staleness heuristic hasn't tripped
but you know the data changed (e.g. after a bulk import, after a
migration, or while diagnosing search quality issues).

This builds against the on-disk cache directory shared with the running
`ui` process. If `ui` is currently running it will pick up the new
files only after its in-memory index ages out — restart `ui` to get
the fresh build immediately.
"""
from __future__ import annotations

import typer
from rich.console import Console

from mongosemantic.config import Settings
from mongosemantic.db.client import MongoConnection, Topology
from mongosemantic.search.hnsw_index import HnswIndexManager
from mongosemantic.state import list_configured, load_config

console = Console()


def reindex_hnsw_cmd(
    collection: str | None = typer.Option(
        None, "--collection", "-c",
        help="Rebuild HNSW for one collection. Omit with --all to do everything.",
    ),
    all_collections: bool = typer.Option(
        False, "--all",
        help="Rebuild HNSW for every configured shadow-mode collection.",
    ),
) -> None:
    """Force-rebuild HNSW vector indexes from the shadow collection."""
    if not collection and not all_collections:
        raise typer.BadParameter("Pass --collection NAME or --all.")
    if collection and all_collections:
        raise typer.BadParameter("Pick either --collection or --all, not both.")
    settings = Settings.from_environment()
    conn = MongoConnection.open(settings.uri, settings.database)
    try:
        if conn.topology == Topology.ATLAS:
            console.print(
                "[yellow]Atlas serves $vectorSearch natively — "
                "HNSW indexes aren't used. Skipping.[/yellow]"
            )
            return
        if all_collections:
            cfgs = list(list_configured(conn.db))
        else:
            cfg = load_config(conn.db, collection)
            if cfg is None:
                console.print(f"[red]{collection} is not configured.[/red]")
                raise typer.Exit(code=2)
            cfgs = [cfg]
        manager = HnswIndexManager()
        total_built = 0
        for cfg in cfgs:
            if cfg.mode != "shadow":
                console.print(
                    f"[dim]{cfg.collection}: inline mode, skipping (HNSW not "
                    f"supported on inline yet).[/dim]"
                )
                continue
            for spec in cfg.fields:
                n = manager.build(conn.db, cfg, spec.path)
                total_built += n
                console.print(
                    f"  {cfg.collection}.{spec.path} ({cfg.embedding_model}): "
                    f"[green]{n}[/green] vectors indexed"
                )
        console.print(
            f"[green]Done.[/green] Indexed {total_built} vectors across "
            f"{len(cfgs)} collection(s). Restart `mongosemantic ui` to load "
            f"the new index into the running process."
        )
    finally:
        conn.close()
