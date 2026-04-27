from __future__ import annotations

from rich.console import Console
from rich.table import Table

from mongosemantic.config import Settings
from mongosemantic.db.client import MongoConnection
from mongosemantic.state import count_by_status, list_configured

console = Console()


def status_cmd() -> None:
    """Print health overview: topology, configured collections, job counts."""
    settings = Settings()
    conn = MongoConnection.open(settings.uri, settings.database)
    try:
        db = conn.db
        console.print(f"[bold]Topology:[/bold] {conn.topology.value}")
        cfgs = list_configured(db)
        console.print(f"[bold]Configured collections:[/bold] {len(cfgs)}")
        for c in cfgs:
            console.print(
                f"  - {c.collection}: {[f.path for f in c.fields]} ({c.embedding_model})"
            )
        counts = count_by_status(db)
        table = Table(title="Jobs")
        table.add_column("Status")
        table.add_column("Count", justify="right")
        for status_name in ("pending", "in_flight", "completed", "failed"):
            table.add_row(status_name, str(counts.get(status_name, 0)))
        console.print(table)
    finally:
        conn.close()
