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
        # Skipped when the URI already specifies tlsCAFile so users with a
        # private/corporate CA aren't silently overridden.
        kwargs: dict = {"serverSelectionTimeoutMS": 5000}
        if "tlsCAFile" not in uri:
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
