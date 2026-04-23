from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from mongosemantic.cli import app

runner = CliRunner()

def test_inspect_prints_suitability_table(monkeypatch):
    monkeypatch.setenv("MONGOSEMANTIC_URI", "mongodb://fake")
    monkeypatch.setenv("MONGOSEMANTIC_DB", "d")
    monkeypatch.setenv("MONGOSEMANTIC_MODEL", "local-fast")
    fake_conn = MagicMock()
    fake_db = MagicMock()
    fake_conn.db = fake_db
    fake_conn.topology.value = "atlas"
    with patch("mongosemantic.commands.inspect.MongoConnection.open", return_value=fake_conn), \
         patch("mongosemantic.commands.inspect.inspect_collection") as fake_inspect:
        from mongosemantic.db.schema import FieldStats
        fake_inspect.return_value = {
            "title": FieldStats(type_name="string", count=10, null_count=0, total_len=10 * 50),
            "body": FieldStats(type_name="string", count=10, null_count=0, total_len=10 * 2000),
        }
        r = runner.invoke(app, ["inspect", "--collection", "articles"])
        assert r.exit_code == 0
        assert "title" in r.stdout
        assert "body" in r.stdout
        assert "suitability" in r.stdout.lower() or "great" in r.stdout.lower()
