"""`mongosemantic integrate claude` — write Claude Desktop's MCP config for the user.

Claude Desktop reads `~/Library/Application Support/Claude/claude_desktop_config.json`
(macOS) / `%APPDATA%\\Claude\\claude_desktop_config.json` (Windows) at startup. We
splice in a `mongosemantic` entry that boots `mongosemantic serve --transport stdio`
with the user's current connection settings as env vars.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import typer
from rich.console import Console

from mongosemantic.config import Settings

console = Console()


def _claude_config_path() -> Path:
    """Resolve Claude Desktop's config file on this platform."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if os.name == "nt":  # Windows
        appdata = os.environ.get("APPDATA")
        if not appdata:
            raise RuntimeError("APPDATA not set — can't locate Claude Desktop config")
        return Path(appdata) / "Claude" / "claude_desktop_config.json"
    # Linux / other — Claude Desktop isn't officially supported but support
    # XDG_CONFIG_HOME for forward-compat.
    base = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    return base / "Claude" / "claude_desktop_config.json"


def _build_entry(settings: Settings) -> dict:
    cmd = shutil.which("mongosemantic") or "mongosemantic"
    return {
        "command": cmd,
        "args": ["serve", "--transport", "stdio"],
        "env": {
            "MONGOSEMANTIC_URI": settings.uri,
            "MONGOSEMANTIC_DB": settings.database,
            "MONGOSEMANTIC_MODEL": settings.model,
        },
    }


def integrate_cmd(
    target: str = typer.Argument(..., help="Currently supported: 'claude' (Claude Desktop)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the config block without writing."),
) -> None:
    """Wire mongosemantic into an AI agent's config.

    Today only `claude` is supported — it writes the `mongosemantic` MCP entry
    into Claude Desktop's `claude_desktop_config.json`. After running, restart
    Claude Desktop and the tools will appear in the slash-menu.
    """
    if target != "claude":
        raise typer.BadParameter(f"unknown target {target!r}; currently only 'claude' is supported")

    settings = Settings()
    if not settings.uri:
        console.print("[red]MONGOSEMANTIC_URI is not set. Set it (or put it in .env) before integrating.[/red]")
        raise typer.Exit(code=1)

    entry = _build_entry(settings)
    if dry_run:
        console.print_json(data={"mcpServers": {"mongosemantic": entry}})
        return

    cfg_path = _claude_config_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    if cfg_path.exists():
        try:
            existing = json.loads(cfg_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            console.print(f"[yellow]Existing config at {cfg_path} is not valid JSON. Refusing to overwrite.[/yellow]")
            raise typer.Exit(code=1)
    else:
        existing = {}

    servers = existing.setdefault("mcpServers", {})
    was_present = "mongosemantic" in servers
    servers["mongosemantic"] = entry

    cfg_path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    action = "updated" if was_present else "added"
    console.print(
        f"[green]{action} 'mongosemantic' entry in {cfg_path}.[/green]\n"
        "[bold]Restart Claude Desktop[/bold] to pick up the change. "
        "The new tools will appear in the slash-menu."
    )
