from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

router = APIRouter()


def _asset_version() -> str:
    """A short fingerprint derived from app.js + style.css mtimes — busts cache
    on every edit without us needing to hand-maintain a version string."""
    mtimes = []
    for name in ("app.js", "style.css"):
        try:
            mtimes.append(int((STATIC_DIR / name).stat().st_mtime))
        except OSError:
            mtimes.append(0)
    return str(max(mtimes))


@router.get("/", response_class=HTMLResponse)
def root() -> Response:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    v = _asset_version()
    html = html.replace('/static/app.js', f'/static/app.js?v={v}')
    html = html.replace('/static/style.css', f'/static/style.css?v={v}')
    return HTMLResponse(
        html, headers={"Cache-Control": "no-cache, must-revalidate"},
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
