from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from mongosemantic.config import Settings
from mongosemantic.db.client import MongoConnection, Topology
from mongosemantic.db.indexes import atlas_vector_index_exists, vector_index_name
from mongosemantic.embeddings.provider import get_provider
from mongosemantic.exceptions import NotConfiguredError
from mongosemantic.search.atlas import build_atlas_pipeline
from mongosemantic.search.brute_force import build_brute_pipeline
from mongosemantic.search.cross_collection import min_max_normalize, per_collection_targets
from mongosemantic.search.inline import build_inline_atlas_pipeline, build_inline_brute_pipeline
from mongosemantic.state import load_config

console = Console()

def _run_one_field(
    db, cfg, collection: str, field_path: str, query_vec: list[float],
    limit: int, topology: Topology,
):
    if cfg.mode == "inline":
        target = db[collection]
        if topology == Topology.ATLAS and atlas_vector_index_exists(target, collection, field_path):
            pipeline = build_inline_atlas_pipeline(
                field_path=field_path,
                query_vector=query_vec,
                limit=limit,
                index_name=vector_index_name(collection, field_path),
            )
        else:
            pipeline = build_inline_brute_pipeline(
                field_path=field_path,
                query_vector=query_vec,
                limit=limit,
            )
    else:
        target = db[cfg.shadow_collection]
        if topology == Topology.ATLAS and atlas_vector_index_exists(target, collection, field_path):
            pipeline = build_atlas_pipeline(
                source_collection=collection,
                field_path=field_path,
                query_vector=query_vec,
                limit=limit,
                index_name=vector_index_name(collection, field_path),
            )
        else:
            pipeline = build_brute_pipeline(
                source_collection=collection,
                field_path=field_path,
                query_vector=query_vec,
                limit=limit,
            )
    rows = list(target.aggregate(pipeline))
    for r in rows:
        r["source_collection"] = collection
    return rows


def _run_one(db, cfg, collection: str, query_vec: list[float], limit: int, topology: Topology):
    merged: list[dict] = []
    for spec in cfg.fields:
        merged.extend(
            _run_one_field(db, cfg, collection, spec.path, query_vec, limit, topology)
        )
    merged.sort(key=lambda r: r.get("score", 0.0), reverse=True)
    return merged[:limit]

def search_cmd(
    query: str = typer.Argument(...),
    collection: str | None = typer.Option(None, "--collection", "-c"),
    limit: int = typer.Option(10, "--limit"),
) -> None:
    """Search by meaning. Omit --collection to search all configured collections."""
    settings = Settings()
    conn = MongoConnection.open(settings.uri, settings.database)
    try:
        db = conn.db
        provider = get_provider(settings.model)
        qvec = provider.embed(query).tolist()

        if collection:
            cfg = load_config(db, collection)
            if not cfg:
                raise NotConfiguredError(f"{collection} not configured")
            rows = _run_one(db, cfg, collection, qvec, limit, conn.topology)
        else:
            all_rows: list[dict] = []
            targets = per_collection_targets(db)
            if not targets:
                raise NotConfiguredError("No collections are configured.")
            models_per_collection: dict[str, str] = {}
            for name in targets:
                cfg = load_config(db, name)
                if cfg is None:
                    continue
                models_per_collection[name] = cfg.embedding_model
                rows = _run_one(db, cfg, name, qvec, limit, conn.topology)
                all_rows.extend(rows)
            if len(set(models_per_collection.values())) > 1:
                all_rows = min_max_normalize(all_rows, "score")
            all_rows.sort(key=lambda r: r.get("score", 0.0), reverse=True)
            rows = all_rows[:limit]

        table = Table(title=f'Search: "{query}"')
        table.add_column("Score", justify="right")
        table.add_column("Collection")
        table.add_column("Field")
        table.add_column("Snippet")
        for row in rows:
            snippet = (row.get("chunk_text") or "")[:160].replace("\n", " ")
            table.add_row(
                f"{row.get('score', 0):.3f}",
                row.get("source_collection", "-"),
                row.get("field_path", "-"),
                snippet,
            )
        console.print(table)
    finally:
        conn.close()
