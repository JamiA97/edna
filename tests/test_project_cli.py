from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from eng_dna import artefacts, operations
from eng_dna.cli import app
from eng_dna.db import connect, ensure_schema

runner = CliRunner()


def test_project_delete_cli(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "eng_dna.db"
    conn = connect(db_path)
    ensure_schema(conn)
    artefacts.create_project(conn, "demo", "Demo", None)

    artefact_path = tmp_path / "file.txt"
    artefact_path.write_text("demo project", encoding="utf-8")
    operations.tag_file(
        conn,
        artefact_path,
        artefact_type="text",
        description=None,
        tags=None,
        project_ids=["demo"],
    )
    conn.close()

    monkeypatch.setenv("EDNA_DB_PATH", str(db_path))

    dry_run = runner.invoke(app, ["project", "delete", "demo", "--dry-run"])
    assert dry_run.exit_code == 0, dry_run.stdout
    assert "Project:" in dry_run.stdout
    assert "Artefacts linked" in dry_run.stdout

    blocked = runner.invoke(app, ["project", "delete", "demo"])
    assert blocked.exit_code == 1, blocked.stdout
    assert "Re-run with --force" in blocked.stdout

    deleted = runner.invoke(app, ["project", "delete", "demo", "--force"])
    assert deleted.exit_code == 0, deleted.stdout
    assert "Deleted project demo" in deleted.stdout

    conn = connect(db_path)
    ensure_schema(conn)
    assert artefacts.get_project(conn, "demo") is None
    conn.close()
