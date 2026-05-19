from __future__ import annotations

import typer
from rich.console import Console

from mongosemantic.mcp_server import create_mcp

console = Console()


def serve_cmd(
    transport: str = typer.Option(
        "stdio", "--transport", "-t",
        help="MCP transport: 'stdio' (Claude Desktop, default) or 'sse' (remote HTTP).",
    ),
    host: str = typer.Option("127.0.0.1", "--host", help="SSE bind host."),
    port: int = typer.Option(8090, "--port", help="SSE port."),
) -> None:
    """Run the MCP server so AI agents (Claude Desktop, Cursor, …) can query MongoDB by meaning."""
    if transport not in ("stdio", "sse"):
        raise typer.BadParameter(f"transport must be 'stdio' or 'sse', got {transport!r}")
    app = create_mcp()
    if transport == "stdio":
        # stdio talks over the process's stdin/stdout. Anything written to stdout
        # by other code (rich, print, …) will corrupt the stream — so we say
        # nothing here on purpose.
        app.run(transport="stdio")
    else:
        app.settings.host = host
        app.settings.port = port
        console.print(f"[green]mongosemantic MCP (SSE) → http://{host}:{port}/sse[/green]")
        app.run(transport="sse")
