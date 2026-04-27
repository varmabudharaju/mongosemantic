from __future__ import annotations

import typer
from rich.console import Console

from mongosemantic.config import Settings
from mongosemantic.db.client import MongoConnection
from mongosemantic.state import reset_failed

console = Console()


def retry_cmd(
    all_: bool = typer.Option(False, "--all"),
    collection: str = typer.Option(None, "--collection", "-c"),
) -> None:
    """Reset failed embedding jobs back to pending."""
    if not all_ and not collection:
        console.print("[red]Pass --all or --collection.[/red]")
        raise typer.Exit(code=1)
    settings = Settings()
    conn = MongoConnection.open(settings.uri, settings.database)
    try:
        n = reset_failed(conn.db)
        console.print(f"[green]Reset {n} failed jobs to pending.[/green]")
    finally:
        conn.close()
