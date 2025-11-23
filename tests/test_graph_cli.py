from __future__ import annotations

from typer.testing import CliRunner

from eng_dna import operations
from eng_dna.cli import app
from eng_dna.db import connect, ensure_schema

runner = CliRunner()


def test_graph_cli_mermaid(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "eng_dna.db"
    conn = connect(db_path)
    ensure_schema(conn)

    parent_path = tmp_path / "parent.txt"
    parent_path.write_text("parent", encoding="utf-8")
    child_path = tmp_path / "child.txt"
    child_path.write_text("child", encoding="utf-8")

    parent = operations.tag_file(
        conn,
        parent_path,
        artefact_type="text",
        description=None,
        tags=None,
        project_ids=None,
    )
    child = operations.tag_file(
        conn,
        child_path,
        artefact_type="text",
        description=None,
        tags=None,
        project_ids=None,
    )
    operations.link_artefacts(conn, child, [parent], relation_type="derived_from", reason=None)
    conn.close()

    monkeypatch.setenv("EDNA_DB_PATH", str(db_path))
    result = runner.invoke(app, ["graph", str(child_path)])
    assert result.exit_code == 0, result.stdout
    assert "flowchart" in result.stdout


def test_unlink_cli(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "eng_dna.db"
    conn = connect(db_path)
    ensure_schema(conn)

    parent_path = tmp_path / "parent.txt"
    parent_path.write_text("parent", encoding="utf-8")
    child_path = tmp_path / "child.txt"
    child_path.write_text("child", encoding="utf-8")

    parent = operations.tag_file(
        conn,
        parent_path,
        artefact_type="text",
        description=None,
        tags=None,
        project_ids=None,
    )
    child = operations.tag_file(
        conn,
        child_path,
        artefact_type="text",
        description=None,
        tags=None,
        project_ids=None,
    )
    operations.link_artefacts(conn, child, [parent], relation_type="derived_from", reason=None)
    conn.close()

    monkeypatch.setenv("EDNA_DB_PATH", str(db_path))
    result = runner.invoke(app, ["unlink", str(child_path), "--from", str(parent_path)])
    assert result.exit_code == 0, result.stdout
    assert "Unlinked" in result.stdout
    assert parent["dna_token"] in result.stdout
    assert child["dna_token"] in result.stdout

    second = runner.invoke(app, ["unlink", str(child_path), "--from", str(parent_path)])
    assert second.exit_code == 0, second.stdout
    assert "No matching links removed" in second.stdout
