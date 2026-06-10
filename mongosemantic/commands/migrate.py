from __future__ import annotations

import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from mongosemantic.config import Settings
from mongosemantic.db.client import MongoConnection
from mongosemantic.migration import MigrationError, migrate_collection

console = Console()


def migrate_cmd(
    collection: str = typer.Option(..., "--collection", "-c"),
    model: str = typer.Option(..., "--model", "-m",
        help="New embedding model. Run `mongosemantic apply --help` to see supported names."),
    drop_archive: bool = typer.Option(
        False, "--drop-archive",
        help="After a successful migration, drop the old shadow collection. "
             "Default is to keep it (rename only) so you can roll back.",
    ),
) -> None:
    """Switch an existing collection's embedding model with near-zero downtime.

    Builds new embeddings into a temp shadow, then atomically swaps it into
    place via `renameCollection`. The old shadow becomes an archive
    (`{shadow}_archive_{ts}`) you can drop later, or pass `--drop-archive`
    to drop it as part of the same command.
    """
    settings = Settings.from_environment()
    conn = MongoConnection.open(settings.uri, settings.database)
    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[bold]Migrating[/bold] {task.completed}/{task.total}"),
            BarColumn(),
            TimeElapsedColumn(),
        ) as progress:
            task_id = progress.add_task("migrate", total=conn.db[collection].estimated_document_count())

            def _on_progress(processed: int, total: int) -> None:
                progress.update(task_id, completed=processed, total=total or 1)

            try:
                result = migrate_collection(
                    conn, collection, model, progress=_on_progress,
                )
            except MigrationError as e:
                console.print(f"[red]migration aborted: {e}[/red]")
                raise typer.Exit(code=2) from e

        console.print(
            f"[green]Migrated {result.collection!r}: "
            f"{result.old_model} ({result.old_dim}d) → "
            f"{result.new_model} ({result.new_dim}d) "
            f"— {result.documents} documents, {result.chunks_written} chunks.[/green]"
        )
        if drop_archive:
            conn.db.drop_collection(result.archive_collection)
            console.print(f"[blue]Dropped archive {result.archive_collection}.[/blue]")
        else:
            console.print(
                f"[blue]Archive preserved at {result.archive_collection!r}. "
                f"Drop it manually or re-run with --drop-archive once verified.[/blue]"
            )
    finally:
        conn.close()
