from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from mongosemantic.config import Settings
from mongosemantic.db.client import MongoConnection, Topology
from mongosemantic.db.indexes import (
    atlas_search_index_exists,
    atlas_vector_index_exists,
    vector_index_name,
)
from mongosemantic.embeddings.provider import get_provider
from mongosemantic.exceptions import NotConfiguredError
from mongosemantic.search.atlas import build_atlas_pipeline
from mongosemantic.search.brute_force import build_brute_pipeline
from mongosemantic.search.cross_collection import min_max_normalize, per_collection_targets
from mongosemantic.search.filtering import FilterError, parse_filter, prefilter_source_ids
from mongosemantic.search.hybrid import build_hybrid_pipeline, search_index_name
from mongosemantic.search.inline import build_inline_atlas_pipeline, build_inline_brute_pipeline
from mongosemantic.search.local_hybrid import HYBRID_WEIGHTS, rrf_fuse, text_leg
from mongosemantic.search.rerank import (
    RERANK_CANDIDATE_MULTIPLIER,
    get_reranker,
    rerank_reason,
)
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
    source_filter: dict | None = None,
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
                source_filter=source_filter,
            )
        else:
            # Inline brute force runs directly on the source docs, so the
            # user filter applies as-is in the $match.
            pipeline = build_inline_brute_pipeline(
                field_path=field_path,
                query_vector=query_vec,
                limit=limit,
                filter_match=source_filter,
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
                source_filter=source_filter,
            )
        else:
            # Shadow chunks carry no source metadata — pre-resolve the
            # matching source _ids and constrain the scan to them.
            filter_match = None
            if source_filter:
                ids = prefilter_source_ids(db, collection, source_filter)
                filter_match = {"source_id": {"$in": ids}}
            pipeline = build_brute_pipeline(
                source_collection=collection,
                field_path=field_path,
                query_vector=query_vec,
                limit=limit,
                filter_match=filter_match,
            )
    rows = list(target.aggregate(pipeline))
    for r in rows:
        r["source_collection"] = collection
    return rows


def _run_one(db, cfg, collection: str, query_vec: list[float], limit: int,
             topology: Topology, source_filter: dict | None = None):
    merged: list[dict] = []
    for spec in cfg.fields:
        merged.extend(
            _run_one_field(db, cfg, collection, spec.path, query_vec, limit,
                           topology, source_filter=source_filter)
        )
    merged.sort(key=lambda r: r.get("score", 0.0), reverse=True)
    return merged[:limit]


def hybrid_available(cfg, topology: Topology) -> bool:
    """Hybrid needs a chunk_text column to keyword-search -> shadow mode, any topology."""
    return cfg.mode == "shadow"


def _atlas_native_hybrid_ready(db, cfg, collection: str, field_path: str) -> bool:
    """True when both Atlas indexes ($vectorSearch + $search) exist on the shadow,
    i.e. the server-side $rankFusion path can actually answer."""
    shadow = db[cfg.shadow_collection]
    stored_search = (cfg.search_index_names or {}).get(field_path)
    sname = stored_search or search_index_name(collection, field_path)
    return atlas_vector_index_exists(shadow, collection, field_path) and \
        atlas_search_index_exists(shadow, sname)


def _run_hybrid_field(
    db, cfg, collection: str, field_path: str, query_text: str,
    query_vec: list[float], limit: int, topology: Topology,
    source_filter: dict | None = None, hnsw=None,
):
    if topology == Topology.ATLAS and _atlas_native_hybrid_ready(db, cfg, collection, field_path):
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
            source_filter=source_filter,
        )
        rows = list(shadow.aggregate(pipeline))
    else:
        # Client-side RRF: vector leg (HNSW when loaded, exact otherwise)
        # fused with a classic $text keyword leg. Works on any topology,
        # including Atlas clusters whose Search indexes are cap-blocked.
        allowed = prefilter_source_ids(db, collection, source_filter) if source_filter else None
        vec_rows = None
        if hnsw is not None:
            vec_rows = hnsw.query(db, cfg, field_path, query_vec, limit, allowed_ids=allowed)
        if vec_rows is None:
            vec_rows = _run_one_field(db, cfg, collection, field_path, query_vec,
                                      limit, topology, source_filter=source_filter)
        txt_rows = text_leg(db, cfg, collection, field_path, query_text, limit,
                            allowed_ids=allowed)
        rows = rrf_fuse([vec_rows, txt_rows], weights=HYBRID_WEIGHTS, limit=limit)
    for r in rows:
        r["source_collection"] = collection
    return rows


def run_one_hybrid(db, cfg, collection: str, query_text: str,
                   query_vec: list[float], limit: int, topology: Topology,
                   source_filter: dict | None = None, hnsw=None):
    """Hybrid version of `_run_one` — fans out across every field, merges, top-k."""
    merged: list[dict] = []
    for spec in cfg.fields:
        merged.extend(
            _run_hybrid_field(db, cfg, collection, spec.path, query_text, query_vec,
                              limit, topology, source_filter=source_filter, hnsw=hnsw)
        )
    merged.sort(key=lambda r: r.get("score", 0.0), reverse=True)
    return merged[:limit]

def search_cmd(
    query: str = typer.Argument(...),
    collection: str | None = typer.Option(None, "--collection", "-c"),
    limit: int = typer.Option(10, "--limit"),
    hybrid: bool = typer.Option(False, "--hybrid",
        help="Combine semantic + keyword search (shadow mode; any topology)."),
    filter_json: str | None = typer.Option(
        None, "--filter",
        help='MongoDB filter on source documents, e.g. \'{"year": {"$gte": 1960}}\'.'),
    rerank: bool = typer.Option(
        False, "--rerank",
        help="Re-score results with a local cross-encoder (better precision, slower)."),
) -> None:
    """Search by meaning. Omit --collection to search all configured collections."""
    try:
        source_filter = parse_filter(filter_json) if filter_json else None
    except FilterError as e:
        console.print(f"[red]Invalid --filter: {e}[/red]")
        raise typer.Exit(code=2) from e
    # Rerank is two-stage retrieval: over-fetch candidates, cross-encode, cut.
    fetch_limit = limit * RERANK_CANDIDATE_MULTIPLIER if rerank else limit
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
                return run_one_hybrid(db, cfg, name, query, qv, fetch_limit,
                                      conn.topology, source_filter=source_filter)
            return _run_one(db, cfg, name, qv, fetch_limit, conn.topology,
                            source_filter=source_filter)

        if collection:
            cfg = load_config(db, collection)
            if not cfg:
                raise NotConfiguredError(f"{collection} not configured")
            if hybrid and not hybrid_available(cfg, conn.topology):
                console.print(
                    "[yellow]Hybrid search requires shadow-mode collections "
                    "(inline mode has no chunk_text column to keyword-search). "
                    "Falling back to pure semantic.[/yellow]"
                )
            rows = _run(cfg, collection)
            if (hybrid and not rows and hybrid_available(cfg, conn.topology)
                    and conn.topology == Topology.ATLAS):
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
            rows = all_rows[:fetch_limit]

        if rerank:
            reranker = get_reranker()
            if reranker is None:
                console.print(
                    f"[yellow]Rerank model unavailable ({rerank_reason()}); "
                    "returning vector-ranked results.[/yellow]"
                )
                rows = rows[:limit]
            else:
                # One rerank over the merged rows: cross-encoder scores are
                # comparable across embedding models, which incidentally fixes
                # mixed-model ordering too.
                rows = reranker.rerank(query, rows, limit)

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
