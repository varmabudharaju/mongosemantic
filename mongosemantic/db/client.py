from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from pymongo import MongoClient


class Topology(str, Enum):
    ATLAS = "atlas"
    REPLICA_SET = "replica_set"
    STANDALONE = "standalone"

def detect_topology(client: MongoClient, uri: str) -> Topology:
    if ".mongodb.net" in uri:
        return Topology.ATLAS
    info = client.admin.command("hello")
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
        client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        client.admin.command("hello")  # forces connect
        return cls(
            client=client,
            uri=uri,
            database_name=database_name,
            topology=detect_topology(client, uri),
        )

    @property
    def db(self):
        return self.client[self.database_name]

    def close(self) -> None:
        self.client.close()
