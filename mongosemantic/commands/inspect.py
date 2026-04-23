from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from mongosemantic.config import Settings
from mongosemantic.db.client import MongoConnection
from mongosemantic.db.schema import inspect_collection, score_field

console = Console()

def _band(score: int) -> str:
    if score >= 80:
        return "[green]Great[/green]"
    if score >= 60:
        return "[green3]Good[/green3]"
    if score >= 40:
        return "[yellow]Usable[/yellow]"
    return "[red]Not recommended[/red]"

def inspect_cmd(
    collection: str = typer.Option(..., "--collection", "-c"),
    sample: int = typer.Option(500, "--sample"),
) -> None:
    """Sample a collection and score each field for semantic-search suitability."""
    settings = Settings()
    conn = MongoConnection.open(settings.uri, settings.database)
    try:
        stats = inspect_collection(conn.db[collection], sample_size=sample)
    finally:
        conn.close()
    if not stats:
        console.print(f"[yellow]No documents sampled in {collection}.[/yellow]")
        raise typer.Exit(code=0)
    table = Table(title=f"Inspect {collection} (topology: {conn.topology.value})")
    table.add_column("Field path")
    table.add_column("Type")
    table.add_column("Coverage")
    table.add_column("Avg length")
    table.add_column("Suitability")
    for path, fs in sorted(stats.items(), key=lambda kv: -score_field(kv[1])):
        score = score_field(fs)
        coverage = 1 - (fs.null_count / max(1, fs.count))
        table.add_row(
            path,
            fs.type_name,
            f"{coverage * 100:.0f}%",
            f"{fs.avg_len:.0f}",
            _band(score),
        )
    console.print(table)
