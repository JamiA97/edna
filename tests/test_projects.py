from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from eng_dna import artefacts, operations
from eng_dna.cli import app
from eng_dna.db import connect, ensure_schema

runner = CliRunner()


def test_project_files_listing(db, tmp_path: Path) -> None:
    artefacts.create_project(db, "airframe", "Airframe", "")
    first = tmp_path / "wing.md"
    first.write_text("wing data", encoding="utf-8")
    operations.tag_file(
        db,
        first,
        artefact_type="markdown",
        description="wing study",
        tags=["wing"],
        project_ids=["airframe"],
    )
    files = artefacts.list_project_files(db, "airframe")
    assert len(files) == 1
    assert files[0]["path"].endswith("wing.md")


def test_project_list_cli(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "eng_dna.db"
    conn = connect(db_path)
    ensure_schema(conn)
    artefacts.create_project(conn, "zeta", "Zeta", "")
    artefacts.create_project(conn, "alpha", "Alpha", "")
    artefacts.create_project(conn, "mu", "Mu", None)
    conn.close()

    monkeypatch.setenv("EDNA_DB_PATH", str(db_path))
    result = runner.invoke(app, ["project", "list"])
    assert result.exit_code == 0, result.stdout
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert lines == [
        "alpha | Alpha",
        "mu | Mu",
        "zeta | Zeta",
    ]
