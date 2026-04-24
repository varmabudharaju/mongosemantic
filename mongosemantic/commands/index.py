from __future__ import annotations

import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from mongosemantic.config import Settings
from mongosemantic.db.client import MongoConnection
from mongosemantic.exceptions import NotConfiguredError
from mongosemantic.state import enqueue_embed, ensure_indexes, load_config
from mongosemantic.sync.change_stream import _get_path, _resolve_text, hash_text

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
        shadow = db[cfg.shadow_collection]
        processed = 0
        with Progress(
            SpinnerColumn(),
            TextColumn("[bold]Enqueuing[/bold] {task.completed}/{task.total}"),
            BarColumn(),
            TimeElapsedColumn(),
        ) as progress:
            task_id = progress.add_task("enqueue", total=total)
            for doc in db[collection].find({}, batch_size=batch_size):
                key = doc.get("_id")
                for spec in cfg.fields:
                    text = _resolve_text(_get_path(doc, spec.path))
                    if not text:
                        continue
                    new_hash = hash_text(cfg.embedding_model, text)
                    existing = shadow.find_one(
                        {
                            "source_id": key,
                            "field_path": spec.path,
                            "chunk_index": 0,
                            "embedding_model": cfg.embedding_model,
                        },
                        {"embedding_hash": 1},
                    )
                    if existing and existing.get("embedding_hash") == new_hash:
                        continue
                    enqueue_embed(
                        db,
                        collection=collection,
                        source_id=key,
                        field_path=spec.path,
                        chunk_index=None,
                        input_text=text,
                        input_hash=new_hash,
                        model=cfg.embedding_model,
                    )
                processed += 1
                progress.update(task_id, completed=processed)
        console.print(
            f"[green]Enqueued embed jobs for {processed} documents.[/green] "
            f"Run `mongosemantic worker` to process them."
        )
    finally:
        conn.close()
