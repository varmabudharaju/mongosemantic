from __future__ import annotations

import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from mongosemantic.config import Settings
from mongosemantic.db.client import MongoConnection
from mongosemantic.exceptions import NotConfiguredError
from mongosemantic.state import ensure_indexes, load_config
from mongosemantic.sync.enqueue import enqueue_for_doc

console = Console()

def index_cmd(
    collection: str = typer.Option(..., "--collection", "-c"),
    batch_size: int = typer.Option(500, "--batch-size"),
) -> None:
    """Enqueue embed jobs for every existing document in a configured collection."""
    settings = Settings()
    conn = MongoConnection.open(settings.uri, settings.database)
    try:
        db = conn.db
        ensure_indexes(db)
        cfg = load_config(db, collection)
        if not cfg:
            raise NotConfiguredError(
                f"{collection} is not configured. Run `mongosemantic apply` first."
            )
        total = db[collection].estimated_document_count()
        processed = 0
        with Progress(
            SpinnerColumn(),
            TextColumn("[bold]Enqueuing[/bold] {task.completed}/{task.total}"),
            BarColumn(),
            TimeElapsedColumn(),
        ) as progress:
            task_id = progress.add_task("enqueue", total=total)
            for doc in db[collection].find({}, batch_size=batch_size):
                enqueue_for_doc(db, cfg, source_id=doc.get("_id"), doc=doc)
                processed += 1
                progress.update(task_id, completed=processed)
        console.print(
            f"[green]Enqueued embed jobs for {processed} documents.[/green] "
            f"Run `mongosemantic worker` to process them."
        )
    finally:
        conn.close()
