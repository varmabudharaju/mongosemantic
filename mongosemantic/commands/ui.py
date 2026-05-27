from __future__ import annotations

import typer
import uvicorn
from rich.console import Console

from mongosemantic.web.app import create_app
from mongosemantic.worker.embedded import EmbeddedWorkerSupervisor

console = Console()


def ui_cmd(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host. Default localhost-only."),
    port: int = typer.Option(8080, "--port"),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code changes (dev only)."),
    no_worker: bool = typer.Option(
        False, "--no-worker",
        help="Don't start the embedded worker. Use when running `mongosemantic worker` "
             "in a separate process.",
    ),
) -> None:
    """Launch the web dashboard."""
    if host != "127.0.0.1":
        console.print(
            f"[yellow]Binding to {host} (not localhost). "
            f"This UI has no built-in auth — put it behind your own auth proxy.[/yellow]"
        )
    console.print(f"[green]mongosemantic UI → http://{host}:{port}[/green]")
    # Start the embedded worker before uvicorn blocks. The supervisor handles
    # the "no connection configured yet" case by retrying on a timer, so it
    # is safe to start before the user has set up a connection in the UI.
    # In reload mode, uvicorn forks a child process and the parent's threads
    # don't survive the fork — skip the embedded worker to avoid a confusing
    # dead-thread silence; advanced users on --reload likely run `worker` too.
    if not no_worker and not reload:
        supervisor = EmbeddedWorkerSupervisor()
        supervisor.start()
        console.print("[green]Embedded worker running in background.[/green]")
    elif not no_worker and reload:
        console.print(
            "[yellow]--reload skips the embedded worker. "
            "Run `mongosemantic worker` in another terminal.[/yellow]"
        )
    if reload:
        uvicorn.run(
            "mongosemantic.web.app:create_app",
            host=host,
            port=port,
            reload=True,
            factory=True,
        )
    else:
        app = create_app()
        uvicorn.run(app, host=host, port=port)
