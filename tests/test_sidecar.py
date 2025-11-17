from __future__ import annotations

from pathlib import Path

from eng_dna import artefacts, operations


def test_sidecar_recreated_when_missing(db, tmp_path: Path) -> None:
    data = tmp_path / "dataset.dat"
    data.write_bytes(b"payload")

    artefact = operations.tag_file(
        db,
        data,
        artefact_type="binary",
        description="binary data",
        tags=None,
        project_ids=None,
    )
    assert (data.with_name(data.name + ".edna")).exists()

    new_dir = tmp_path / "new"
    new_dir.mkdir()
    moved = new_dir / data.name
    data.rename(moved)

    restored = operations.resolve_file_reference(db, moved)
    assert restored["id"] == artefact["id"] or restored["dna_token"] == artefact["dna_token"]
    assert (moved.with_name(moved.name + ".edna")).exists()

    events = artefacts.list_events(db, restored["id"])
    assert any(event["event_type"] == "sidecar_restored" for event in events)
