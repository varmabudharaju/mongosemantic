from __future__ import annotations

import contextlib
import logging
import threading

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from mongosemantic import __version__
from mongosemantic.search.hnsw_index import HnswIndexManager
from mongosemantic.web.content import CONTENT
from mongosemantic.web.routes import aggregation as _aggregation_routes
from mongosemantic.web.routes import apply as _apply_routes
from mongosemantic.web.routes import collections as _collections_routes
from mongosemantic.web.routes import dashboard as _dashboard_routes
from mongosemantic.web.routes import index as _index_routes
from mongosemantic.web.routes import migrate as _migrate_routes
from mongosemantic.web.routes import search as _search_routes
from mongosemantic.web.routes import system as _system_routes
from mongosemantic.web.routes import ui as _ui_routes
from mongosemantic.web.routes import visualize as _visualize_routes
from mongosemantic.web.security import (
    install_csrf,
    install_rate_limit,
    install_security_headers,
)
from mongosemantic.worker.runner import ProviderRegistry

log = logging.getLogger("mongosemantic.web")


def _spawn_hnsw_warmup(app: FastAPI) -> None:
    """Try to (lazy-load or build) HNSW indexes for every configured
    collection in a background thread. Search falls back to brute-force
    until the build finishes for each (collection, field, model).

    This runs once at startup and exits — staleness-driven rebuilds are
    triggered separately from the worker hot path.
    """

    def _run() -> None:
        try:
            from mongosemantic.config import Settings
            from mongosemantic.db.client import MongoConnection, Topology
            from mongosemantic.state import list_configured

            settings = Settings.try_from_environment()
            if settings is None:
                return  # No connection configured yet — supervisor handles UX.
            conn = MongoConnection.open(settings.uri, settings.database)
            try:
                if conn.topology == Topology.ATLAS:
                    # Atlas serves $vectorSearch natively; an HNSW build
                    # here would be wasted RAM.
                    return
                for cfg in list_configured(conn.db):
                    if cfg.mode != "shadow":
                        continue
                    for spec in cfg.fields:
                        key = (cfg.collection, spec.path, cfg.embedding_model)
                        # Disk-load first; only build if no cached file.
                        if app.state.hnsw._load_from_disk(key, cfg.embedding_dim):
                            continue
                        try:
                            app.state.hnsw.build(conn.db, cfg, spec.path)
                        except Exception:
                            log.exception("HNSW warmup build failed for %s", key)
            finally:
                conn.close()
        except Exception:
            log.exception("HNSW warmup thread crashed")
        finally:
            # Operational signal: loading/building large indexes contends
            # with request handling, so "warmup finished" marks the moment
            # the server is fully responsive. Scripts (and the capture
            # tooling) key off this line.
            log.info("HNSW warmup finished")

    threading.Thread(target=_run, name="hnsw-warmup", daemon=True).start()


def create_app() -> FastAPI:
    app = FastAPI(
        title="mongosemantic",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    install_security_headers(app)
    install_rate_limit(app, limit=120, window_seconds=60)
    install_csrf(app)
    # Process-wide embedding-provider cache. Routes that need to embed
    # query text (and the embedded worker) read this off app.state so the
    # SentenceTransformer / OpenAI client is loaded exactly once per
    # process instead of once per request.
    app.state.providers = ProviderRegistry()
    # HNSW vector index manager. Search reads it off app.state and serves
    # fast ANN queries on non-Atlas topologies; brute-force aggregation is
    # the fallback when no index exists yet for a given (collection, field,
    # model). Indexes are built lazily in a background thread on startup.
    app.state.hnsw = HnswIndexManager()
    _spawn_hnsw_warmup(app)

    @app.get("/healthz")
    def _healthz() -> JSONResponse:
        return JSONResponse({"ok": True})

    @app.get("/api/content")
    def _content() -> JSONResponse:
        return JSONResponse(CONTENT)

    @app.get("/api/version")
    def _version() -> JSONResponse:
        return JSONResponse({"version": __version__})

    @app.on_event("shutdown")
    def _shutdown() -> None:
        # Drop any HNSW indexes from memory. Files on disk remain so the
        # next process boots quickly from the cache.
        with contextlib.suppress(Exception):
            app.state.hnsw._indexes.clear()

    app.include_router(_system_routes.router)
    app.include_router(_collections_routes.router)
    app.include_router(_apply_routes.router)
    app.include_router(_index_routes.router)
    app.include_router(_search_routes.router)
    app.include_router(_aggregation_routes.router)
    app.include_router(_dashboard_routes.router)
    app.include_router(_migrate_routes.router)
    app.include_router(_visualize_routes.router)
    _ui_routes.install(app)

    return app
