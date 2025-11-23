from __future__ import annotations

from typer.testing import CliRunner

from eng_dna import artefacts
from eng_dna.cli import app
from eng_dna.db import connect, ensure_schema

runner = CliRunner()


def _prepare_db(tmp_path):
    db_path = tmp_path / "eng_dna.db"
    conn = connect(db_path)
    ensure_schema(conn)
    conn.close()
    return db_path


def test_cli_tag_default_snapshot_creates_version(tmp_path, monkeypatch) -> None:
    db_path = _prepare_db(tmp_path)
    monkeypatch.setenv("EDNA_DB_PATH", str(db_path))

    target = tmp_path / "cli_snapshot.txt"
    target.write_text("one", encoding="utf-8")
    first = runner.invoke(app, ["tag", str(target), "--type", "text"])
    assert first.exit_code == 0, first.stdout

    target.write_text("two", encoding="utf-8")
    second = runner.invoke(app, ["tag", str(target), "--type", "text"])
    assert second.exit_code == 0, second.stdout

    conn = connect(db_path)
    ensure_schema(conn)
    count = conn.execute("SELECT COUNT(*) AS c FROM artefacts").fetchone()["c"]
    conn.close()
    assert count == 2


def test_cli_tag_wip_updates_in_place(tmp_path, monkeypatch) -> None:
    db_path = _prepare_db(tmp_path)
    monkeypatch.setenv("EDNA_DB_PATH", str(db_path))

    target = tmp_path / "cli_wip.txt"
    target.write_text("draft1", encoding="utf-8")
    first = runner.invoke(app, ["tag", str(target), "--type", "text", "--mode", "wip"])
    assert first.exit_code == 0, first.stdout

    target.write_text("draft2", encoding="utf-8")
    second = runner.invoke(app, ["tag", str(target), "--type", "text", "--mode", "wip"])
    assert second.exit_code == 0, second.stdout

    conn = connect(db_path)
    ensure_schema(conn)
    count = conn.execute("SELECT COUNT(*) AS c FROM artefacts").fetchone()["c"]
    artefact = artefacts.lookup_by_path(conn, str(target.resolve()))
    events = artefacts.list_events(conn, artefact["id"])
    conn.close()

    assert count == 1
    assert any(event["event_type"] == "wip_saved" for event in events)
    assert not any(event["event_type"] == "version_created" for event in events)


def test_tag_help_mentions_mode() -> None:
    result = runner.invoke(app, ["tag", "--help"])
    assert result.exit_code == 0
    assert "--mode" in result.stdout
    assert "snapshot" in result.stdout and "wip" in result.stdout
