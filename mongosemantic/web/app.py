from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from mongosemantic import __version__
from mongosemantic.web.content import CONTENT
from mongosemantic.worker.runner import ProviderRegistry
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

    @app.get("/healthz")
    def _healthz() -> JSONResponse:
        return JSONResponse({"ok": True})

    @app.get("/api/content")
    def _content() -> JSONResponse:
        return JSONResponse(CONTENT)

    @app.get("/api/version")
    def _version() -> JSONResponse:
        return JSONResponse({"version": __version__})

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
