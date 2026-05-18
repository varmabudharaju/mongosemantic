from unittest.mock import patch

from typer.testing import CliRunner

from mongosemantic.cli import app

runner = CliRunner()


def test_ui_command_invokes_uvicorn(monkeypatch):
    monkeypatch.setenv("MONGOSEMANTIC_URI", "mongodb://x")
    monkeypatch.setenv("MONGOSEMANTIC_DB", "d")
    monkeypatch.setenv("MONGOSEMANTIC_MODEL", "local-fast")
    with patch("mongosemantic.commands.ui.uvicorn.run") as fake_run:
        r = runner.invoke(app, ["ui", "--port", "9999"])
        assert r.exit_code == 0, r.output
        fake_run.assert_called_once()
        kwargs = fake_run.call_args.kwargs
        assert kwargs["host"] == "127.0.0.1"
        assert kwargs["port"] == 9999
