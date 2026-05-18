from __future__ import annotations

import secrets
import time
from collections import defaultdict, deque
from collections.abc import Callable

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

CSRF_COOKIE = "csrftoken"
CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class _CSRFMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        cookie_token = request.cookies.get(CSRF_COOKIE)
        if request.method not in SAFE_METHODS:
            header_token = request.headers.get(CSRF_HEADER, "")
            if (
                not cookie_token
                or not header_token
                or not secrets.compare_digest(cookie_token, header_token)
            ):
                return Response("CSRF token missing or mismatched", status_code=403)
        response = await call_next(request)
        if cookie_token is None:
            new_token = secrets.token_urlsafe(32)
            response.set_cookie(
                CSRF_COOKIE,
                new_token,
                httponly=False,
                samesite="strict",
                secure=False,
                path="/",
            )
        return response


class _SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'"
        )
        return response


class _RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, limit: int = 120, window_seconds: int = 60) -> None:
        super().__init__(app)
        self.limit = limit
        self.window = window_seconds
        self.history: dict[str, deque[float]] = defaultdict(deque)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        ip = request.client.host if request.client else "unknown"
        now = time.monotonic()
        cutoff = now - self.window
        q = self.history[ip]
        while q and q[0] < cutoff:
            q.popleft()
        if len(q) >= self.limit:
            return Response("rate limit exceeded", status_code=429)
        q.append(now)
        return await call_next(request)


def install_csrf(app: FastAPI) -> None:
    app.add_middleware(_CSRFMiddleware)


def install_security_headers(app: FastAPI) -> None:
    app.add_middleware(_SecurityHeadersMiddleware)


def install_rate_limit(app: FastAPI, limit: int = 120, window_seconds: int = 60) -> None:
    app.add_middleware(_RateLimitMiddleware, limit=limit, window_seconds=window_seconds)
