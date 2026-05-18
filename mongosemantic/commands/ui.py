from __future__ import annotations

import typer
import uvicorn
from rich.console import Console

from mongosemantic.web.app import create_app

console = Console()


def ui_cmd(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host. Default localhost-only."),
    port: int = typer.Option(8080, "--port"),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code changes (dev only)."),
) -> None:
    """Launch the web dashboard."""
    if host != "127.0.0.1":
        console.print(
            f"[yellow]Binding to {host} (not localhost). "
            f"This UI has no built-in auth — put it behind your own auth proxy.[/yellow]"
        )
    console.print(f"[green]mongosemantic UI → http://{host}:{port}[/green]")
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
