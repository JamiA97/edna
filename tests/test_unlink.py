from __future__ import annotations

import json
from pathlib import Path

from eng_dna import artefacts, operations


def test_unlink_operations(db, tmp_path: Path) -> None:
    parent_path = tmp_path / "parent.txt"
    parent_path.write_text("parent", encoding="utf-8")
    child_path = tmp_path / "child.txt"
    child_path.write_text("child", encoding="utf-8")

    parent = operations.tag_file(
        db,
        parent_path,
        artefact_type="text",
        description=None,
        tags=None,
        project_ids=None,
    )
    child = operations.tag_file(
        db,
        child_path,
        artefact_type="text",
        description=None,
        tags=None,
        project_ids=None,
    )
    operations.link_artefacts(db, child, [parent], relation_type="derived_from", reason=None)

    assert artefacts.list_parents(db, child["id"])

    preview = operations.unlink_artefacts(db, child, [parent], relation_type="derived_from", dry_run=True)
    assert len(preview) == 1
    assert artefacts.list_parents(db, child["id"])

    removed = operations.unlink_artefacts(db, child, [parent], relation_type="derived_from", dry_run=False)
    assert len(removed) == 1
    assert not artefacts.list_parents(db, child["id"])

    events = artefacts.list_events(db, child["id"])
    unlinked_events = [event for event in events if event["event_type"] == "unlinked"]
    assert unlinked_events
    metadata = json.loads(unlinked_events[0]["metadata"])
    assert metadata["parent"] == parent["dna_token"]
    assert metadata["relation"] == "derived_from"
