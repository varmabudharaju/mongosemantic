from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import certifi
from pymongo import MongoClient


class Topology(str, Enum):
    ATLAS = "atlas"
    REPLICA_SET = "replica_set"
    STANDALONE = "standalone"

def detect_topology(
    client: MongoClient, uri: str, hello_info: dict | None = None
) -> Topology:
    if ".mongodb.net" in uri:
        return Topology.ATLAS
    info = hello_info if hello_info is not None else client.admin.command("hello")
    if info.get("setName") or info.get("msg") == "isdbgrid":
        return Topology.REPLICA_SET
    return Topology.STANDALONE


def redact_uri(uri: str) -> str:
    """Mask credentials: mongodb+srv://user:pass@host -> mongodb+srv://<redacted>@host."""
    if "@" not in uri:
        return uri
    scheme, rest = uri.split("://", 1)
    creds, host = rest.rsplit("@", 1)
    if ":" in creds:
        return f"{scheme}://<redacted>@{host}"
    return uri  # no creds to redact


def scrub_uri(details: str, uri: str) -> str:
    """Replace a known URI inside arbitrary text with its redacted form.

    PyMongo exception reprs can echo back the URI they were given —
    password included. Anything that prints an exception next to a URI
    must run the text through this first.
    """
    if not uri or uri not in details:
        return details
    return details.replace(uri, redact_uri(uri))


def _uri_uses_tls(uri: str) -> bool:
    """True iff the URI implies TLS — either mongodb+srv:// (TLS by default
    per the spec) or an explicit tls=true / ssl=true query parameter."""
    if uri.startswith("mongodb+srv://"):
        return True
    low = uri.lower()
    return "tls=true" in low or "ssl=true" in low


@dataclass
class MongoConnection:
    client: MongoClient
    uri: str
    database_name: str
    topology: Topology

    @classmethod
    def open(cls, uri: str, database_name: str) -> MongoConnection:
        # Default tlsCAFile to certifi's bundle so TLS verification works on
        # systems whose Python lacks a discoverable system CA bundle (notably
        # macOS python.org / Apple Python without Install Certificates.command).
        # Only inject it when TLS is actually in play — otherwise we'd force
        # TLS onto plain `mongodb://localhost` URIs (e.g. local Docker) and
        # cause an "SSL handshake failed" error against a non-TLS server.
        kwargs: dict = {"serverSelectionTimeoutMS": 5000}
        if _uri_uses_tls(uri) and "tlsCAFile" not in uri:
            kwargs["tlsCAFile"] = certifi.where()
        client = MongoClient(uri, **kwargs)
        info = client.admin.command("hello")  # single call, reused by detect_topology
        return cls(
            client=client,
            uri=uri,
            database_name=database_name,
            topology=detect_topology(client, uri, hello_info=info),
        )

    @property
    def db(self):
        return self.client[self.database_name]

    def close(self) -> None:
        self.client.close()
