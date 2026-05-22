from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def root() -> Response:
    return HTMLResponse(
        (STATIC_DIR / "index.html").read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


class _NoCacheStatic(StaticFiles):
    """Serve static files with Cache-Control: no-cache so editable installs
    don't get stuck on stale assets across UI restarts."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def _send(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers = [(k, v) for (k, v) in headers if k.lower() != b"cache-control"]
                headers.append((b"cache-control", b"no-cache, must-revalidate"))
                message["headers"] = headers
            await send(message)
        await super().__call__(scope, receive, _send)


def install(app: ASGIApp) -> None:
    app.include_router(router)
    app.mount("/static", _NoCacheStatic(directory=str(STATIC_DIR)), name="static")
