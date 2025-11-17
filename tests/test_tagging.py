from __future__ import annotations

from pathlib import Path

from eng_dna import artefacts, operations
from eng_dna.identity import compute_file_hash


def test_tagging_and_linking(db, tmp_path: Path) -> None:
    artefacts.create_project(db, "proj1", "Demo", None)

    parent_path = tmp_path / "parent.txt"
    parent_path.write_text("parent", encoding="utf-8")
    child_path = tmp_path / "child.txt"
    child_path.write_text("child", encoding="utf-8")

    parent = operations.tag_file(
        db,
        parent_path,
        artefact_type="text",
        description="parent file",
        tags=["geometry"],
        project_ids=["proj1"],
    )
    child = operations.tag_file(
        db,
        child_path,
        artefact_type="text",
        description="child file",
        tags=["report"],
        project_ids=None,
    )

    operations.link_artefacts(db, child, [parent], relation_type="derived_from", reason="unit test")

    parents = artefacts.list_parents(db, child["id"])
    assert parents and parents[0]["dna_token"] == parent["dna_token"]

    tags = artefacts.list_tags(db, parent["id"])
    assert "geometry" in tags
    projects = artefacts.list_projects(db, parent["id"])
    assert projects and projects[0]["id"] == "proj1"

    results = operations.search_artefacts(db, tags=["geometry"], project_id="proj1")
    assert any(r["dna_token"] == parent["dna_token"] for r in results)
