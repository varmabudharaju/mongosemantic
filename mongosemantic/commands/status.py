from __future__ import annotations

from rich.console import Console
from rich.table import Table

from mongosemantic.config import Settings
from mongosemantic.db.client import MongoConnection
from mongosemantic.state import (
    count_by_status,
    list_configured,
    list_heartbeats,
    recent_failed_jobs,
)

console = Console()


def status_cmd() -> None:
    """Print health overview: topology, configured collections, job counts,
    worker heartbeats, and recently failed jobs."""
    settings = Settings.from_environment()
    conn = MongoConnection.open(settings.uri, settings.database)
    try:
        db = conn.db
        console.print(f"[bold]Topology:[/bold] {conn.topology.value}")
        cfgs = list_configured(db)
        console.print(f"[bold]Configured collections:[/bold] {len(cfgs)}")
        for c in cfgs:
            console.print(
                f"  - {c.collection}: {[f.path for f in c.fields]} ({c.embedding_model}, {c.mode})"
            )

        counts = count_by_status(db)
        jobs_tbl = Table(title="Jobs")
        jobs_tbl.add_column("Status")
        jobs_tbl.add_column("Count", justify="right")
        for status_name in ("pending", "in_flight", "completed", "failed"):
            jobs_tbl.add_row(status_name, str(counts.get(status_name, 0)))
        console.print(jobs_tbl)

        heartbeats = list_heartbeats(db)
        if heartbeats:
            wt = Table(title="Workers")
            wt.add_column("Worker")
            wt.add_column("Status")
            wt.add_column("Last heartbeat")
            wt.add_column("Jobs processed", justify="right")
            for hb in heartbeats:
                colored = {
                    "running": "[green]running[/green]",
                    "stale":   "[yellow]stale[/yellow]",
                    "dead":    "[red]dead[/red]",
                }[hb.status]
                wt.add_row(
                    hb.worker_id,
                    colored,
                    hb.last_heartbeat.strftime("%Y-%m-%d %H:%M:%S"),
                    str(hb.jobs_processed),
                )
            console.print(wt)

        failed = recent_failed_jobs(db, limit=10)
        if failed:
            ft = Table(title="Recent failed jobs")
            ft.add_column("Collection")
            ft.add_column("Source")
            ft.add_column("Field")
            ft.add_column("Attempts", justify="right")
            ft.add_column("Error")
            for f in failed:
                err = (f.get("last_error") or "").splitlines()[0][:80]
                ft.add_row(
                    f.get("collection") or "-",
                    (f.get("source_id") or "-")[:24],
                    f.get("field_path") or "-",
                    str(f.get("attempts") or 0),
                    err,
                )
            console.print(ft)
    finally:
        conn.close()
