from __future__ import annotations

from pathlib import Path

from eng_dna import artefacts, operations


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
