from __future__ import annotations

from pathlib import Path

from eng_dna import artefacts, operations
from eng_dna.sidecar import get_sidecar_path


def test_delete_project_operations(db, tmp_path: Path) -> None:
    artefacts.create_project(db, "a", "Alpha", None)
    artefacts.create_project(db, "b", "Beta", None)

    only_a_path = tmp_path / "only_a.txt"
    only_a_path.write_text("alpha", encoding="utf-8")
    both_path = tmp_path / "both.txt"
    both_path.write_text("alpha beta", encoding="utf-8")

    only_a = operations.tag_file(
        db,
        only_a_path,
        artefact_type="text",
        description=None,
        tags=None,
        project_ids=["a"],
    )
    both = operations.tag_file(
        db,
        both_path,
        artefact_type="text",
        description=None,
        tags=None,
        project_ids=["a", "b"],
    )

    dry_run = operations.delete_project(db, "a", purge_sidecars=True, dry_run=True)
    assert dry_run["artefact_count"] == 2
    assert dry_run["exclusive_artefact_count"] == 1
    assert get_sidecar_path(only_a_path) in dry_run["sidecars_to_delete"]
    assert get_sidecar_path(both_path) not in dry_run["sidecars_to_delete"]
    assert artefacts.get_project(db, "a")

    result = operations.delete_project(db, "a", purge_sidecars=True, dry_run=False)
    assert result["deleted_project"]
    assert not artefacts.get_project(db, "a")
    assert artefacts.get_project(db, "b")

    # Artefacts remain; links updated
    assert artefacts.lookup_by_dna(db, only_a["dna_token"])
    assert artefacts.lookup_by_dna(db, both["dna_token"])

    # only_a loses all projects; both still linked to b
    assert not artefacts.list_projects(db, only_a["id"])
    remaining = artefacts.list_projects(db, both["id"])
    assert len(remaining) == 1 and remaining[0]["id"] == "b"

    # Sidecar handling
    assert not get_sidecar_path(only_a_path).exists()
    assert get_sidecar_path(both_path).exists()
