"""Map low-level PyMongo / socket exceptions to user-facing connection errors.

The web UI's Connection page renders these with a message, a hint, and a
"Show technical details" disclosure containing the raw `repr(exc)`.
"""
from __future__ import annotations

import socket
from dataclasses import dataclass

from pymongo.errors import (
    ConfigurationError,
    OperationFailure,
    ServerSelectionTimeoutError,
)


@dataclass(frozen=True)
class ConnectionError:
    code: str
    message: str
    hint: str
    details: str


def validate_uri_prefix(uri: str) -> ConnectionError | None:
    if uri.startswith("mongodb://") or uri.startswith("mongodb+srv://"):
        return None
    return ConnectionError(
        code="bad_scheme",
        message="URI must start with mongodb:// or mongodb+srv://.",
        hint="Copy it from Atlas → Connect → Drivers.",
        details=f"got: {uri[:40]!r}",
    )


def map_exception(exc: BaseException) -> ConnectionError:
    msg = str(exc)
    details = repr(exc)

    # Authentication errors (OperationFailure code 18)
    if isinstance(exc, OperationFailure):
        if getattr(exc, "code", None) == 18 or "authentication failed" in msg.lower():
            return ConnectionError(
                code="auth_failed",
                message="Username or password rejected.",
                hint=(
                    "Atlas: check Database Access. URL-encode special "
                    "characters in the password (e.g. @ → %40)."
                ),
                details=details,
            )
        if "not authorized" in msg.lower():
            return ConnectionError(
                code="db_not_readable",
                message=(
                    "Connected to the cluster, but the database is not "
                    "readable with these credentials."
                ),
                hint="Check the database user's roles (needs read on this database).",
                details=details,
            )

    # DNS failures (raw gaierror or SRV lookup wrapped in ConfigurationError)
    if isinstance(exc, socket.gaierror):
        return ConnectionError(
            code="dns_failure",
            message="Can't resolve the cluster hostname.",
            hint="Check the URI for typos, or your network/DNS.",
            details=details,
        )
    if isinstance(exc, ConfigurationError):
        if "SRV" in msg or "nameservers" in msg.lower():
            return ConnectionError(
                code="dns_failure",
                message="Can't resolve the cluster hostname.",
                hint="Check the URI for typos, or your network/DNS.",
                details=details,
            )
        if "empty host" in msg.lower() or "parse" in msg.lower():
            return ConnectionError(
                code="malformed_uri",
                message="Couldn't parse the URI. Check for missing characters around @ or the host.",
                hint="Format: mongodb+srv://user:pass@cluster.mongodb.net/",
                details=details,
            )
        return ConnectionError(
            code="malformed_uri",
            message="Couldn't parse the URI.",
            hint="Format: mongodb+srv://user:pass@cluster.mongodb.net/",
            details=details,
        )

    # Server selection timeouts — narrow by sub-pattern
    if isinstance(exc, ServerSelectionTimeoutError):
        low = msg.lower()
        if "isn't whitelisted" in low or "ip not in whitelist" in low or "ip allowlist" in low:
            return ConnectionError(
                code="ip_not_allowlisted",
                message="Atlas refused the connection — your current IP isn't allowlisted.",
                hint="Add it under Atlas → Network Access, then try again.",
                details=details,
            )
        if "ssl" in low or "tls" in low or "certificate" in low:
            return ConnectionError(
                code="tls_failure",
                message="TLS handshake failed.",
                hint=(
                    "mongosemantic uses the certifi CA bundle by default. "
                    "If you're behind a corporate proxy, set SSL_CERT_FILE to your proxy's CA."
                ),
                details=details,
            )
        return ConnectionError(
            code="timeout",
            message="Couldn't reach the cluster within 5 seconds.",
            hint=(
                "Common causes: cluster paused, IP not in Atlas Network Access, "
                "firewall blocking port 27017."
            ),
            details=details,
        )

    return ConnectionError(
        code="unknown",
        message=f"{type(exc).__name__}: {msg}",
        hint="See technical details below.",
        details=details,
    )
