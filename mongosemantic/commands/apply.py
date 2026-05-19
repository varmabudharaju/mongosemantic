from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

import typer
from rich.console import Console

from mongosemantic.config import MODEL_DIMS, Settings
from mongosemantic.db.client import MongoConnection, Topology
from mongosemantic.db.indexes import (
    create_atlas_search_index,
    create_atlas_vector_index,
    ensure_shadow_indexes,
    shadow_collection_name,
    suggested_atlas_command,
)
from mongosemantic.db.queries import inline_embedding_path
from mongosemantic.search.hybrid import search_index_name
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
            # Per-field try/except so a failure on field N doesn't hide the
            # state of fields 1..N-1. Partial successes are kept (we don't
            # roll back working indexes); failures get a clear summary and a
            # non-zero exit so callers / CI catch them.
            succeeded: list[str] = []
            failed: list[tuple[str, str]] = []  # (field, error message)

            for p in fields:
                try:
                    if mode == "shadow":
                        name = create_atlas_vector_index(
                            db[shadow_name], collection, p, dim, path="embedding"
                        )
                        # Hybrid search needs a sibling text index on chunk_text.
                        search_name = create_atlas_search_index(
                            db[shadow_name], search_index_name(collection, p)
                        )
                        console.print(
                            f"[green]Atlas indexes created for {p!r}: "
                            f"vector={name}, search={search_name}[/green]"
                        )
                    else:
                        name = create_atlas_vector_index(
                            db[collection], collection, p, dim,
                            path=inline_embedding_path(p),
                        )
                        console.print(
                            f"[green]Atlas vector index created for {p!r}: {name}[/green]"
                        )
                    succeeded.append(p)
                except Exception as e:  # noqa: BLE001 — surface anything Atlas threw
                    failed.append((p, str(e)))
                    console.print(
                        f"[red]Atlas index creation failed for field {p!r}: {e}[/red]"
                    )

            if failed:
                # M0 free tier caps FTS indexes at 3 per cluster. Multi-field
                # shadow apply needs 2 indexes (vector + search) per field, so
                # 2-field apply hits the cap. Detect by message contents so we
                # don't need a separate Atlas API call to read the tier.
                hit_fts_cap = any(
                    "maximum number of fts indexes" in msg.lower() for _, msg in failed
                )
                console.print(
                    f"[red]apply failed for {len(failed)} of {len(fields)} field(s); "
                    f"succeeded: {succeeded or 'none'}; failed: {[f for f, _ in failed]}[/red]"
                )
                if succeeded:
                    console.print(
                        f"[yellow]Indexes for {succeeded} are already created and remain in "
                        f"place. Re-run `apply` after freeing index slots (or run `teardown` "
                        f"first) to reach a clean state.[/yellow]"
                    )
                if hit_fts_cap:
                    console.print(
                        "[yellow]Atlas free tier (M0/M2/M5) limits search indexes to 3 per "
                        "cluster. Each shadow-mode field needs 2 indexes (vectorSearch + "
                        "search), so 2-field multi-field apply needs 4 — over the cap. "
                        "Either use a single field, drop to one mode (omit hybrid), or "
                        "upgrade to M10+.[/yellow]"
                    )
                console.print("[yellow]Suggested manual commands for failed fields:[/yellow]")
                for p, _ in failed:
                    if mode == "shadow":
                        console.print(suggested_atlas_command(collection, p, shadow_name, dim))
                    else:
                        console.print(
                            suggested_atlas_command(
                                collection, p, collection, dim,
                                path=inline_embedding_path(p),
                            )
                        )
                raise typer.Exit(code=2)
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
