from __future__ import annotations

import socket

from pymongo.errors import (
    ConfigurationError,
    OperationFailure,
    ServerSelectionTimeoutError,
)

from mongosemantic.web.connection_errors import map_exception, validate_uri_prefix


def test_validate_uri_prefix_accepts_mongodb():
    err = validate_uri_prefix("mongodb://x/")
    assert err is None


def test_validate_uri_prefix_accepts_srv():
    err = validate_uri_prefix("mongodb+srv://x.mongodb.net/")
    assert err is None


def test_validate_uri_prefix_rejects_other():
    err = validate_uri_prefix("http://example.com")
    assert err is not None
    assert err.code == "bad_scheme"
    assert "mongodb://" in err.message


def test_validate_uri_prefix_rejects_empty():
    err = validate_uri_prefix("")
    assert err is not None
    assert err.code == "bad_scheme"


def test_map_auth_failed():
    exc = OperationFailure("auth failed", code=18)
    err = map_exception(exc)
    assert err.code == "auth_failed"
    assert "rejected" in err.message.lower()
    assert "URL-encode" in err.hint


def test_map_malformed_uri():
    exc = ConfigurationError("Empty host (or empty string) is not allowed")
    err = map_exception(exc)
    assert err.code == "malformed_uri"
    assert "parse" in err.message.lower()


def test_map_dns_failure_via_gaierror():
    exc = socket.gaierror(-2, "Name or service not known")
    err = map_exception(exc)
    assert err.code == "dns_failure"


def test_map_dns_failure_via_configuration_srv():
    exc = ConfigurationError(
        "All nameservers failed to answer the SRV query for _mongodb._tcp.x.mongodb.net"
    )
    err = map_exception(exc)
    assert err.code == "dns_failure"


def test_map_ip_not_allowlisted():
    exc = ServerSelectionTimeoutError(
        "No replica set members available: connection refused; "
        "your IP that isn't whitelisted in atlas"
    )
    err = map_exception(exc)
    assert err.code == "ip_not_allowlisted"
    assert "Network Access" in err.hint


def test_map_generic_timeout():
    exc = ServerSelectionTimeoutError("No servers found yet")
    err = map_exception(exc)
    assert err.code == "timeout"
    assert "5 seconds" in err.message


def test_map_tls_failure():
    exc = ServerSelectionTimeoutError(
        "SSL handshake failed: certificate verify failed"
    )
    err = map_exception(exc)
    assert err.code == "tls_failure"
    assert "certifi" in err.hint


def test_map_db_not_readable():
    exc = OperationFailure("not authorized on mydb to execute command", code=13)
    err = map_exception(exc)
    assert err.code == "db_not_readable"
    assert "roles" in err.hint.lower()


def test_map_unknown_exception():
    exc = RuntimeError("totally unexpected")
    err = map_exception(exc)
    assert err.code == "unknown"
    assert "RuntimeError" in err.message
    assert "totally unexpected" in err.details


def test_details_contains_repr():
    exc = OperationFailure("auth failed", code=18)
    err = map_exception(exc)
    assert "OperationFailure" in err.details
