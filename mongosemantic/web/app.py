from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from mongosemantic import __version__
from mongosemantic.web.content import CONTENT


def create_app() -> FastAPI:
    app = FastAPI(
        title="mongosemantic",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/healthz")
    def _healthz() -> JSONResponse:
        return JSONResponse({"ok": True})

    @app.get("/api/content")
    def _content() -> JSONResponse:
        return JSONResponse(CONTENT)

    return app
