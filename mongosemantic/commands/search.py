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
from mongosemantic.search.hybrid import build_hybrid_pipeline, search_index_name
from mongosemantic.search.inline import build_inline_atlas_pipeline, build_inline_brute_pipeline
from mongosemantic.state import load_config

console = Console()

def _resolved_vector_index_name(cfg, field_path: str) -> str:
    """Prefer the per-field name stored in cfg (migrations may have changed it);
    fall back to the deterministic name for legacy configs."""
    stored = (cfg.vector_index_names or {}).get(field_path)
    return stored or vector_index_name(cfg.collection, field_path)


def _run_one_field(
    db, cfg, collection: str, field_path: str, query_vec: list[float],
    limit: int, topology: Topology,
):
    idx_name = _resolved_vector_index_name(cfg, field_path)
    if cfg.mode == "inline":
        target = db[collection]
        if topology == Topology.ATLAS and atlas_vector_index_exists(target, collection, field_path):
            pipeline = build_inline_atlas_pipeline(
                field_path=field_path,
                query_vector=query_vec,
                limit=limit,
                index_name=idx_name,
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
                index_name=idx_name,
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


def hybrid_available(cfg, topology: Topology) -> bool:
    """Hybrid requires Atlas + shadow mode (we need a chunk_text column to index)."""
    return topology == Topology.ATLAS and cfg.mode == "shadow"


def _run_hybrid_field(
    db, cfg, collection: str, field_path: str, query_text: str,
    query_vec: list[float], limit: int,
):
    shadow = db[cfg.shadow_collection]
    stored_search = (cfg.search_index_names or {}).get(field_path)
    pipeline = build_hybrid_pipeline(
        source_collection=collection,
        field_path=field_path,
        query_text=query_text,
        query_vector=query_vec,
        limit=limit,
        vector_index_name=_resolved_vector_index_name(cfg, field_path),
        search_index_name=stored_search or search_index_name(collection, field_path),
    )
    rows = list(shadow.aggregate(pipeline))
    for r in rows:
        r["source_collection"] = collection
    return rows


def run_one_hybrid(db, cfg, collection: str, query_text: str,
                   query_vec: list[float], limit: int, topology: Topology):
    """Hybrid version of `_run_one` — fans out across every field, merges, top-k."""
    merged: list[dict] = []
    for spec in cfg.fields:
        merged.extend(
            _run_hybrid_field(db, cfg, collection, spec.path, query_text, query_vec, limit)
        )
    merged.sort(key=lambda r: r.get("score", 0.0), reverse=True)
    return merged[:limit]

def search_cmd(
    query: str = typer.Argument(...),
    collection: str | None = typer.Option(None, "--collection", "-c"),
    limit: int = typer.Option(10, "--limit"),
    hybrid: bool = typer.Option(False, "--hybrid",
        help="Combine semantic + keyword search (Atlas + shadow mode only)."),
) -> None:
    """Search by meaning. Omit --collection to search all configured collections."""
    settings = Settings.from_environment()
    conn = MongoConnection.open(settings.uri, settings.database)
    try:
        db = conn.db

        # The query has to be embedded with the *same* model the collection
        # was indexed with — embedding-model and embedding-dim must match
        # the stored vectors or the search returns garbage.
        qvec_cache: dict[str, list[float]] = {}
        def _qvec(model: str) -> list[float]:
            if model not in qvec_cache:
                qvec_cache[model] = get_provider(model).embed(query).tolist()
            return qvec_cache[model]

        def _run(cfg, name):
            qv = _qvec(cfg.embedding_model)
            if hybrid and hybrid_available(cfg, conn.topology):
                return run_one_hybrid(db, cfg, name, query, qv, limit, conn.topology)
            return _run_one(db, cfg, name, qv, limit, conn.topology)

        if collection:
            cfg = load_config(db, collection)
            if not cfg:
                raise NotConfiguredError(f"{collection} not configured")
            if hybrid and not hybrid_available(cfg, conn.topology):
                console.print(
                    "[yellow]Hybrid search requires Atlas + shadow-mode collections. "
                    "Falling back to pure semantic.[/yellow]"
                )
            rows = _run(cfg, collection)
            if hybrid and not rows and hybrid_available(cfg, conn.topology):
                console.print(
                    "[yellow]Hybrid returned no rows. Both Atlas indexes (vectorSearch "
                    "+ search) must exist and be queryable — they build for ~30–90 s "
                    "after `apply`, and on free tier the 3-index cluster cap can block "
                    "their creation (apply reports this). Check the cluster's Search "
                    "tab, or retry without --hybrid.[/yellow]"
                )
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
                all_rows.extend(_run(cfg, name))
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
