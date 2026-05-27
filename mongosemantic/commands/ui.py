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
    if reload:
        if not no_worker:
            console.print(
                "[yellow]--reload skips the embedded worker. "
                "Run `mongosemantic worker` in another terminal.[/yellow]"
            )
        uvicorn.run(
            "mongosemantic.web.app:create_app",
            host=host,
            port=port,
            reload=True,
            factory=True,
        )
        return
    # Build the app first so we can hand its provider cache to the embedded
    # worker — worker and search end up sharing one SentenceTransformer
    # instance per process instead of each loading their own.
    app = create_app()
    if not no_worker:
        supervisor = EmbeddedWorkerSupervisor(registry=app.state.providers)
        supervisor.start()
        console.print("[green]Embedded worker running in background.[/green]")
    uvicorn.run(app, host=host, port=port)
