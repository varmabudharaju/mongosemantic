from __future__ import annotations

import signal
import threading
import time

from rich.console import Console

from mongosemantic.config import Settings
from mongosemantic.db.client import MongoConnection, Topology
from mongosemantic.embeddings.provider import get_provider
from mongosemantic.state import ensure_indexes, list_configured
from mongosemantic.sync.change_stream import ChangeStreamListener
from mongosemantic.sync.polling import poll_once
from mongosemantic.worker.runner import WorkerRunner, process_batch

console = Console()


def _process_all_pending(db, provider, batch_size: int) -> int:
    """Process every pending job and exit. Used by `worker --once`."""
    total = 0
    while True:
        n = process_batch(db, provider, worker_id="worker-once", batch_size=batch_size)
        if n == 0:
            break
        total += n
    return total


def run_worker(poll_interval: int, batch_size: int, once: bool = False) -> None:
    settings = Settings.from_environment()
    conn = MongoConnection.open(settings.uri, settings.database)
    db = conn.db
    ensure_indexes(db)
    provider = get_provider(settings.model)
    if once:
        # One-shot run: process all pending jobs, then exit. Skips change
        # streams / polling / heartbeat. Useful for cron, scripted demos,
        # or quick ad-hoc catch-up runs.
        if conn.topology == Topology.STANDALONE:
            for cfg in list_configured(db):
                poll_once(db, cfg.collection)
        n = _process_all_pending(db, provider, batch_size)
        console.print(f"[green]Processed {n} job(s) and exiting.[/green]")
        conn.close()
        return
    runner = WorkerRunner(db, provider, batch_size=batch_size)
    threads: list[threading.Thread] = []
    threads.append(threading.Thread(target=runner.run, name="embed-worker", daemon=True))

    listener = None
    if conn.topology in (Topology.ATLAS, Topology.REPLICA_SET):
        configured = [c.collection for c in list_configured(db)]
        if configured:
            listener = ChangeStreamListener(db, configured)
            threads.append(
                threading.Thread(target=listener.run, name="change-stream", daemon=True)
            )
        console.print(f"[green]Change streams: {configured}[/green]")
    else:
        console.print(
            f"[yellow]Standalone MongoDB. Polling every {poll_interval}s.[/yellow]"
        )

    stop = threading.Event()

    def _shutdown(*_):
        console.print("\n[yellow]Shutting down…[/yellow]")
        stop.set()
        runner.stop()
        if listener:
            listener.stop()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    for t in threads:
        t.start()

    try:
        while not stop.is_set():
            if conn.topology == Topology.STANDALONE:
                for cfg in list_configured(db):
                    try:
                        poll_once(db, cfg.collection)
                    except Exception:
                        console.print_exception(show_locals=False)
            for _ in range(poll_interval):
                if stop.is_set():
                    break
                time.sleep(1)
    finally:
        conn.close()
