"""`mongosemantic integrate claude` — config-writer tests."""
import json

from typer.testing import CliRunner

from mongosemantic.cli import app

runner = CliRunner()


def _env(monkeypatch):
    monkeypatch.setenv("MONGOSEMANTIC_URI", "mongodb://localhost:27117/?replicaSet=rs0")
    monkeypatch.setenv("MONGOSEMANTIC_DB", "demo")
    monkeypatch.setenv("MONGOSEMANTIC_MODEL", "local-fast")


def test_integrate_dry_run_prints_json(monkeypatch):
    _env(monkeypatch)
    r = runner.invoke(app, ["integrate", "claude", "--dry-run"])
    assert r.exit_code == 0, r.output
    body = json.loads(r.stdout)
    entry = body["mcpServers"]["mongosemantic"]
    assert entry["args"] == ["serve", "--transport", "stdio"]
    assert entry["env"]["MONGOSEMANTIC_URI"] == "mongodb://localhost:27117/?replicaSet=rs0"
    assert entry["env"]["MONGOSEMANTIC_DB"] == "demo"


def test_integrate_writes_to_temp_config(monkeypatch, tmp_path):
    _env(monkeypatch)
    cfg_path = tmp_path / "claude_desktop_config.json"
    monkeypatch.setattr(
        "mongosemantic.commands.integrate._claude_config_path",
        lambda: cfg_path,
    )
    r = runner.invoke(app, ["integrate", "claude"])
    assert r.exit_code == 0, r.output
    saved = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert "mongosemantic" in saved["mcpServers"]
    assert saved["mcpServers"]["mongosemantic"]["args"] == ["serve", "--transport", "stdio"]


def test_integrate_preserves_existing_servers(monkeypatch, tmp_path):
    _env(monkeypatch)
    cfg_path = tmp_path / "claude_desktop_config.json"
    cfg_path.write_text(json.dumps({
        "mcpServers": {"some_other_tool": {"command": "x"}}
    }), encoding="utf-8")
    monkeypatch.setattr(
        "mongosemantic.commands.integrate._claude_config_path",
        lambda: cfg_path,
    )
    r = runner.invoke(app, ["integrate", "claude"])
    assert r.exit_code == 0
    saved = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert "some_other_tool" in saved["mcpServers"]
    assert "mongosemantic" in saved["mcpServers"]


def test_integrate_rejects_unknown_target(monkeypatch):
    _env(monkeypatch)
    r = runner.invoke(app, ["integrate", "cursor"])
    assert r.exit_code != 0


def test_integrate_refuses_corrupt_existing_config(monkeypatch, tmp_path):
    _env(monkeypatch)
    cfg_path = tmp_path / "claude_desktop_config.json"
    cfg_path.write_text("not json {{{", encoding="utf-8")
    monkeypatch.setattr(
        "mongosemantic.commands.integrate._claude_config_path",
        lambda: cfg_path,
    )
    r = runner.invoke(app, ["integrate", "claude"])
    assert r.exit_code != 0
    # untouched
    assert cfg_path.read_text(encoding="utf-8") == "not json {{{"
