from __future__ import annotations

import typer
from dotenv import load_dotenv

app = typer.Typer(
    help="Zero-config semantic search for any MongoDB database.",
    add_completion=False,
    no_args_is_help=True,
)

load_dotenv()  # pick up .env if present

from mongosemantic.commands import apply as _apply_mod  # noqa: E402
from mongosemantic.commands import index as _index_mod  # noqa: E402
from mongosemantic.commands import inspect as _inspect_mod  # noqa: E402
from mongosemantic.commands import integrate as _integrate_mod  # noqa: E402
from mongosemantic.commands import migrate as _migrate_mod  # noqa: E402
from mongosemantic.commands import reindex as _reindex_mod  # noqa: E402
from mongosemantic.commands import retry as _retry_mod  # noqa: E402
from mongosemantic.commands import search as _search_mod  # noqa: E402
from mongosemantic.commands import serve as _serve_mod  # noqa: E402
from mongosemantic.commands import status as _status_mod  # noqa: E402
from mongosemantic.commands import teardown as _teardown_mod  # noqa: E402
from mongosemantic.commands import ui as _ui_mod  # noqa: E402

app.command("inspect")(_inspect_mod.inspect_cmd)
app.command("apply")(_apply_mod.apply_cmd)
app.command("index")(_index_mod.index_cmd)
app.command("search")(_search_mod.search_cmd)
app.command("status")(_status_mod.status_cmd)
app.command("retry")(_retry_mod.retry_cmd)
app.command("reindex")(_reindex_mod.reindex_cmd)
app.command("ui")(_ui_mod.ui_cmd)
app.command("serve")(_serve_mod.serve_cmd)
app.command("integrate")(_integrate_mod.integrate_cmd)
app.command("migrate")(_migrate_mod.migrate_cmd)
app.command("teardown")(_teardown_mod.teardown_cmd)


@app.command("worker")
def worker_cmd(
    poll_interval: int = typer.Option(30, "--poll-interval", help="Polling seconds (standalone)"),
    batch_size: int = typer.Option(32, "--batch-size"),
    once: bool = typer.Option(
        False, "--once",
        help="Process all pending jobs once and exit. Skips change streams + heartbeat. "
             "Useful for cron jobs and ad-hoc catch-up runs."
    ),
) -> None:
    """Run the sync + embedding background worker."""
    from mongosemantic.commands.worker_cmd import run_worker
    run_worker(poll_interval=poll_interval, batch_size=batch_size, once=once)
