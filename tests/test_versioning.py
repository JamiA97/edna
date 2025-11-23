from __future__ import annotations

from pathlib import Path

import pytest
from eng_dna import artefacts, operations


def test_new_version_is_created_on_hash_change(db, tmp_path: Path) -> None:
    target = tmp_path / "report.txt"
    target.write_text("v1", encoding="utf-8")

    first = operations.tag_file(
        db,
        target,
        artefact_type="text",
        description="report",
        tags=None,
        project_ids=None,
    )
    target.write_text("v2", encoding="utf-8")

    second = operations.tag_file(
        db,
        target,
        artefact_type="text",
        description="report",
        tags=None,
        project_ids=None,
    )

    assert first["dna_token"] != second["dna_token"]
    parents = artefacts.list_parents(db, second["id"])
    assert any(parent["dna_token"] == first["dna_token"] for parent in parents)


def test_retagging_same_content_reuses_existing_artefact(db, tmp_path: Path) -> None:
    target = tmp_path / "spec.dat"
    target.write_bytes(b"payload")

    first = operations.tag_file(
        db,
        target,
        artefact_type="binary",
        description="spec",
        tags=None,
        project_ids=None,
    )

    second = operations.tag_file(
        db,
        target,
        artefact_type="binary",
        description="updated description",
        tags=["docs"],
        project_ids=None,
    )

    assert second["id"] == first["id"]
    count = db.execute("SELECT COUNT(*) AS c FROM artefacts").fetchone()["c"]
    assert count == 1


def test_show_does_not_create_new_artefact(db, tmp_path: Path) -> None:
    target = tmp_path / "notes.txt"
    target.write_text("initial", encoding="utf-8")
    artefact = operations.tag_file(
        db,
        target,
        artefact_type="text",
        description="notes",
        tags=None,
        project_ids=None,
    )

    viewed = operations.show_target(db, str(target))
    assert viewed["id"] == artefact["id"]
    count = db.execute("SELECT COUNT(*) AS c FROM artefacts").fetchone()["c"]
    assert count == 1
    events = artefacts.list_events(db, artefact["id"])
    assert not any(event["event_type"] == "version_created" for event in events)


def test_show_updates_path_on_move(db, tmp_path: Path) -> None:
    target = tmp_path / "diagram.txt"
    target.write_text("diagram", encoding="utf-8")
    artefact = operations.tag_file(
        db,
        target,
        artefact_type="drawing",
        description="diagram",
        tags=None,
        project_ids=None,
    )

    archive = tmp_path / "archive"
    archive.mkdir()
    new_path = archive / target.name
    target.rename(new_path)
    sidecar = target.with_name(target.name + ".edna")
    if sidecar.exists():
        sidecar.rename(archive / sidecar.name)

    updated = operations.show_target(db, str(new_path))
    assert updated["dna_token"] == artefact["dna_token"]
    assert updated["path"] == str(new_path.resolve())
    count = db.execute("SELECT COUNT(*) AS c FROM artefacts").fetchone()["c"]
    assert count == 1
    events = artefacts.list_events(db, updated["id"])
    assert any(event["event_type"] == "moved" for event in events)


def test_wip_mode_keeps_same_dna_on_hash_change(db, tmp_path: Path) -> None:
    target = tmp_path / "script.py"
    target.write_text("v1", encoding="utf-8")

    first = operations.tag_file(
        db,
        target,
        artefact_type="script",
        description=None,
        tags=None,
        project_ids=None,
        mode="wip",
    )
    target.write_text("v2", encoding="utf-8")

    second = operations.tag_file(
        db,
        target,
        artefact_type="script",
        description=None,
        tags=None,
        project_ids=None,
        mode="wip",
    )

    assert first["id"] == second["id"]
    assert first["dna_token"] == second["dna_token"]
    count = db.execute("SELECT COUNT(*) AS c FROM artefacts").fetchone()["c"]
    assert count == 1
    events = artefacts.list_events(db, first["id"])
    assert any(event["event_type"] == "wip_saved" for event in events)
    assert not any(event["event_type"] == "version_created" for event in events)


def test_wip_then_snapshot_creates_single_new_version(db, tmp_path: Path) -> None:
    target = tmp_path / "analysis.py"
    target.write_text("base", encoding="utf-8")

    baseline = operations.tag_file(
        db,
        target,
        artefact_type="script",
        description=None,
        tags=None,
        project_ids=None,
        mode="snapshot",
    )
    target.write_text("iter1", encoding="utf-8")
    operations.tag_file(
        db,
        target,
        artefact_type="script",
        description=None,
        tags=None,
        project_ids=None,
        mode="wip",
    )
    target.write_text("iter2", encoding="utf-8")
    operations.tag_file(
        db,
        target,
        artefact_type="script",
        description=None,
        tags=None,
        project_ids=None,
        mode="wip",
    )
    target.write_text("snapshot", encoding="utf-8")

    final_snapshot = operations.tag_file(
        db,
        target,
        artefact_type="script",
        description=None,
        tags=None,
        project_ids=None,
        mode="snapshot",
    )

    assert final_snapshot["id"] != baseline["id"]
    count = db.execute("SELECT COUNT(*) AS c FROM artefacts").fetchone()["c"]
    assert count == 2
    parents = artefacts.list_parents(db, final_snapshot["id"])
    assert any(parent["id"] == baseline["id"] for parent in parents)
    parent_events = artefacts.list_events(db, baseline["id"])
    assert any(event["event_type"] == "wip_saved" for event in parent_events)
    child_events = artefacts.list_events(db, final_snapshot["id"])
    assert not any(event["event_type"] == "wip_saved" for event in child_events)


def test_invalid_mode_raises(db, tmp_path: Path) -> None:
    target = tmp_path / "bad.txt"
    target.write_text("oops", encoding="utf-8")

    with pytest.raises(ValueError):
        operations.tag_file(
            db,
            target,
            artefact_type=None,
            description=None,
            tags=None,
            project_ids=None,
            mode="invalid",
        )
