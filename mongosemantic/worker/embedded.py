"""Run a worker inside the `ui` process so end users don't have to know
about the separate `mongosemantic worker` command.

Design:
- Daemon supervisor thread loops forever.
- Every CHECK_INTERVAL_S, reads the current Settings.
- If no connection configured yet: wait, try again later.
- If connection has changed (or no worker exists): tear down any running
  worker + listener, start a fresh one against the new connection.
- The actual embedding work happens in background threads owned by the
  worker, exactly like the standalone `worker` command.

This is best-effort: any failure is logged and the supervisor keeps
looping. The UI keeps working even if the embedded worker can't start
(e.g., model download fails) — users can still configure, inspect, and
search documents that were embedded earlier.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from mongosemantic.config import Settings
from mongosemantic.db.client import MongoConnection, Topology
from mongosemantic.state import ensure_indexes, list_configured
from mongosemantic.sync.change_stream import ChangeStreamListener
from mongosemantic.sync.polling import poll_once
from mongosemantic.worker.runner import ProviderRegistry, WorkerRunner

log = logging.getLogger("mongosemantic.embedded_worker")

CHECK_INTERVAL_S = 5.0
POLL_STANDALONE_EVERY_S = 30.0


@dataclass
class _Identity:
    uri: str
    database: str
    model: str


class _RunningWorker:
    """Bundle of a connection + worker + (optional) change-stream listener.

    Owns a polling thread for standalone topologies.
    """

    def __init__(
        self,
        conn: MongoConnection,
        identity: _Identity,
        registry: ProviderRegistry,
    ) -> None:
        self.conn = conn
        self.identity = identity
        # Per-model registry shared with the web app: the worker and the
        # search route use the same SentenceTransformer instance, loaded
        # exactly once per process.
        ensure_indexes(conn.db)
        self.runner = WorkerRunner(conn.db, registry)
        self.runner_thread = threading.Thread(
            target=self.runner.run, name="embed-worker", daemon=True
        )
        self.listener: ChangeStreamListener | None = None
        self.listener_thread: threading.Thread | None = None
        if conn.topology in (Topology.ATLAS, Topology.REPLICA_SET):
            configured = [c.collection for c in list_configured(conn.db)]
            if configured:
                self.listener = ChangeStreamListener(conn.db, configured)
                self.listener_thread = threading.Thread(
                    target=self.listener.run, name="embed-change-stream", daemon=True
                )
        self._poll_stop = threading.Event()
        self.poll_thread: threading.Thread | None = None
        if conn.topology == Topology.STANDALONE:
            self.poll_thread = threading.Thread(
                target=self._poll_loop, name="embed-poll", daemon=True
            )

    def start(self) -> None:
        self.runner_thread.start()
        if self.listener_thread:
            self.listener_thread.start()
        if self.poll_thread:
            self.poll_thread.start()

    def stop(self) -> None:
        self._poll_stop.set()
        try:
            self.runner.stop()
        except Exception:
            log.exception("worker stop failed")
        if self.listener:
            try:
                self.listener.stop()
            except Exception:
                log.exception("listener stop failed")
        try:
            self.conn.close()
        except Exception:
            log.exception("connection close failed")

    def _poll_loop(self) -> None:
        while not self._poll_stop.is_set():
            try:
                for cfg in list_configured(self.conn.db):
                    poll_once(self.conn.db, cfg.collection)
            except Exception:
                log.exception("standalone poll failed")
            for _ in range(int(POLL_STANDALONE_EVERY_S)):
                if self._poll_stop.is_set():
                    break
                time.sleep(1)


class EmbeddedWorkerSupervisor:
    """Watches Settings for connection changes and (re)starts a worker.

    Call .start() once from the UI process. The supervisor runs as a
    daemon thread so it dies with the process — no shutdown hook needed.

    Pass `registry` to share an embedding-provider cache with the web
    app (so worker and search use the same SentenceTransformer). If
    omitted, the supervisor owns its own registry.
    """

    def __init__(
        self,
        check_interval: float = CHECK_INTERVAL_S,
        registry: ProviderRegistry | None = None,
    ) -> None:
        self._check_interval = check_interval
        self._registry = registry or ProviderRegistry()
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="embed-supervisor", daemon=True
        )
        self._running: _RunningWorker | None = None

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._running:
            self._running.stop()
            self._running = None

    def _current_identity(self) -> _Identity | None:
        settings = Settings.try_from_environment()
        if settings is None:
            return None
        return _Identity(
            uri=settings.uri, database=settings.database, model=settings.model
        )

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                target = self._current_identity()
                if target is None:
                    self._maybe_stop_running()
                elif self._running is None or self._running.identity != target:
                    self._maybe_stop_running()
                    self._try_start(target)
            except Exception:
                log.exception("supervisor loop error")
            for _ in range(int(self._check_interval)):
                if self._stop.is_set():
                    break
                time.sleep(1)

    def _maybe_stop_running(self) -> None:
        if self._running is not None:
            log.info("stopping embedded worker (connection changed or removed)")
            self._running.stop()
            self._running = None

    def _try_start(self, identity: _Identity) -> None:
        try:
            conn = MongoConnection.open(identity.uri, identity.database)
        except Exception as e:
            log.warning("embedded worker: cannot connect yet (%s); will retry", e)
            return
        try:
            running = _RunningWorker(conn, identity, self._registry)
            running.start()
            self._running = running
            log.info("embedded worker started against %s/%s", identity.uri, identity.database)
        except Exception:
            log.exception("embedded worker: failed to start; will retry")
            try:
                conn.close()
            except Exception:
                pass
