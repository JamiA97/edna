from __future__ import annotations

from pathlib import Path

from eng_dna import artefacts, sync
from eng_dna.db import connect, ensure_schema

DNA_PARENT = "edna_demo_parent"
DNA_CHILD = "edna_demo_child"


def _make_conn(path: Path):
    conn = connect(path)
    ensure_schema(conn)
    return conn


def _seed_lineage(conn):
    artefacts.create_project(conn, "demo", "Demo Build", "Primary handoff")
    artefacts.create_project(conn, "aux", "Aux Build", None)
    parent = artefacts.create_artefact(
        conn,
        dna_token=DNA_PARENT,
        path="/export/parent.txt",
        file_hash="hash-parent",
        artefact_type="design",
        description="root design",
    )
    child = artefacts.create_artefact(
        conn,
        dna_token=DNA_CHILD,
        path="/export/child.txt",
        file_hash="hash-child",
        artefact_type="report",
        description="derived report",
    )
    artefacts.assign_projects(conn, parent["id"], ["demo"])
    artefacts.assign_projects(conn, child["id"], ["demo", "aux"])
    artefacts.add_tags(conn, parent["id"], ["baseline"])
    with conn:
        conn.execute(
            "INSERT INTO notes (artefact_id, note, created_at) VALUES (?, ?, ?)",
            (parent["id"], "remember this", "2024-01-01T12:00:00Z"),
        )
    artefacts.create_edge(
        conn,
        parent_id=parent["id"],
        child_id=child["id"],
        relation_type="derived_from",
        reason="handoff",
    )
    return parent, child


def _build_bundle(tmp_path: Path) -> dict:
    conn = _make_conn(tmp_path / "source.db")
    try:
        _seed_lineage(conn)
        return sync.export_project_lineage(conn, "demo")
    finally:
        conn.close()


def test_export_project_lineage_basic(db):
    _seed_lineage(db)
    bundle = sync.export_project_lineage(db, "demo")
    assert bundle["format"] == sync.LINEAGE_FORMAT
    assert bundle["version"] == sync.LINEAGE_VERSION
    assert bundle["source"]["project_ids"] == ["demo"]
    assert {proj["id"] for proj in bundle["projects"]} == {"demo", "aux"}
    assert {art["dna"] for art in bundle["artefacts"]} == {DNA_PARENT, DNA_CHILD}
    assert any(tag["dna"] == DNA_PARENT and tag["tag"] == "baseline" for tag in bundle["tags"])
    assert any(edge["parent_dna"] == DNA_PARENT and edge["child_dna"] == DNA_CHILD for edge in bundle["edges"])
    assert len(bundle["artefact_projects"]) == 3


def test_import_lineage_creates_missing_records(tmp_path):
    bundle = _build_bundle(tmp_path)
    dest = _make_conn(tmp_path / "dest.db")
    try:
        stats = sync.import_lineage(dest, bundle)
        assert stats["projects_new"] == 2
        assert stats["artefacts_new"] == 2
        parent = dest.execute(
            "SELECT path FROM artefacts WHERE dna_token = ?",
            (DNA_PARENT,),
        ).fetchone()
        assert parent["path"] == "/export/parent.txt"
        tag = dest.execute(
            """
            SELECT t.tag FROM tags t
            JOIN artefacts a ON a.id = t.artefact_id
            WHERE a.dna_token = ?
            """,
            (DNA_PARENT,),
        ).fetchone()
        assert tag["tag"] == "baseline"
    finally:
        dest.close()


def test_import_lineage_merges_into_existing_db(tmp_path):
    bundle = _build_bundle(tmp_path)
    dest = _make_conn(tmp_path / "merge.db")
    try:
        artefacts.create_project(dest, "demo", "Local Demo", None)
        existing = artefacts.create_artefact(
            dest,
            dna_token=DNA_PARENT,
            path="/local/parent.txt",
            file_hash="local-hash",
            artefact_type="design",
            description="local copy",
        )
        artefacts.assign_projects(dest, existing["id"], ["demo"])
        stats = sync.import_lineage(dest, bundle)
        assert stats["artefacts_existing"] == 1
        assert stats["artefacts_new"] == 1
        row = dest.execute(
            "SELECT path FROM artefacts WHERE dna_token = ?",
            (DNA_PARENT,),
        ).fetchone()
        assert row["path"] == "/local/parent.txt"
        child = dest.execute(
            "SELECT path FROM artefacts WHERE dna_token = ?",
            (DNA_CHILD,),
        ).fetchone()
        assert child["path"] == "/export/child.txt"
    finally:
        dest.close()


def test_import_lineage_is_idempotent(tmp_path):
    bundle = _build_bundle(tmp_path)
    dest = _make_conn(tmp_path / "idem.db")
    try:
        first = sync.import_lineage(dest, bundle)
        assert first["artefacts_new"] == 2
        second = sync.import_lineage(dest, bundle)
        assert second["artefacts_new"] == 0
        assert second["events_inserted"] == 0
        assert second["edges_inserted"] == 0
        assert second["links_inserted"] == 0
    finally:
        dest.close()


def test_import_lineage_dry_run(tmp_path):
    bundle = _build_bundle(tmp_path)
    dest = _make_conn(tmp_path / "dry.db")
    try:
        stats = sync.import_lineage(dest, bundle, dry_run=True)
        assert stats["artefacts_new"] == 2
        count = dest.execute("SELECT COUNT(*) AS cnt FROM artefacts").fetchone()
        assert count["cnt"] == 0
    finally:
        dest.close()
