from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

import typer
from rich.console import Console

from mongosemantic.config import MODEL_DIMS, Settings
from mongosemantic.db.client import MongoConnection, Topology
from mongosemantic.db.indexes import (
    create_atlas_vector_index,
    ensure_shadow_indexes,
    shadow_collection_name,
    suggested_atlas_command,
)
from mongosemantic.db.queries import inline_embedding_path
from mongosemantic.state import (
    CollectionConfig,
    FieldSpec,
    ensure_indexes,
    save_config,
)

console = Console()


def apply_cmd(
    collection: str = typer.Option(..., "--collection", "-c"),
    fields: Annotated[list[str], typer.Option("--field", "-f")] = ...,
    mode: str = typer.Option("shadow", "--mode", help="shadow|inline"),
    chunked: bool = typer.Option(False, "--chunked"),
    chunk_size: int = typer.Option(512, "--chunk-size"),
    chunk_overlap: int = typer.Option(64, "--chunk-overlap"),
    model: str | None = typer.Option(None, "--model"),
) -> None:
    """Configure semantic search on a collection."""
    settings = Settings()
    chosen_model = model or settings.model
    if chosen_model not in MODEL_DIMS:
        raise typer.BadParameter(f"Unknown model: {chosen_model}")
    dim = MODEL_DIMS[chosen_model]

    if mode not in ("shadow", "inline"):
        raise typer.BadParameter(f"Unknown mode: {mode!r} (use shadow or inline)")
    if chunked and mode == "inline":
        console.print(
            "[red]Chunked embeddings require shadow mode. Re-run with --mode shadow "
            "or drop --chunked.[/red]"
        )
        raise typer.Exit(code=2)

    conn = MongoConnection.open(settings.uri, settings.database)
    try:
        db = conn.db
        ensure_indexes(db)

        field_specs = [
            FieldSpec(path=p, chunked=chunked, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
            for p in fields
        ]
        now = datetime.now(timezone.utc)

        if mode == "shadow":
            shadow_name = shadow_collection_name(collection)
            ensure_shadow_indexes(db[shadow_name])
            cfg = CollectionConfig(
                collection=collection,
                mode="shadow",
                shadow_collection=shadow_name,
                fields=field_specs,
                embedding_model=chosen_model,
                embedding_dim=dim,
                created_at=now,
                updated_at=now,
            )
        else:
            cfg = CollectionConfig(
                collection=collection,
                mode="inline",
                shadow_collection=None,
                fields=field_specs,
                embedding_model=chosen_model,
                embedding_dim=dim,
                created_at=now,
                updated_at=now,
            )
        save_config(db, cfg)

        if conn.topology == Topology.ATLAS:
            try:
                for p in fields:
                    if mode == "shadow":
                        name = create_atlas_vector_index(
                            db[shadow_name], collection, p, dim, path="embedding"
                        )
                    else:
                        name = create_atlas_vector_index(
                            db[collection], collection, p, dim,
                            path=inline_embedding_path(p),
                        )
                    console.print(f"[green]Atlas vector index created: {name}[/green]")
            except Exception as e:
                console.print(f"[yellow]Could not auto-create Atlas vector index: {e}[/yellow]")
                for p in fields:
                    if mode == "shadow":
                        console.print(suggested_atlas_command(collection, p, shadow_name, dim))
                    else:
                        console.print(
                            suggested_atlas_command(
                                collection, p, collection, dim,
                                path=inline_embedding_path(p),
                            )
                        )
        else:
            console.print(
                "[blue]No vector index created (self-hosted). Brute-force aggregation will be used — "
                "fine up to ~100k embeddings.[/blue]"
            )

        console.print(
            f"[green]Configured semantic search on {collection} ({mode}): {fields}.[/green]"
        )
    finally:
        conn.close()
