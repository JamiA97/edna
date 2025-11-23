"""Lineage sync helpers for export/import."""
from __future__ import annotations

import json
from collections import deque
from contextlib import nullcontext
from datetime import datetime, timezone
from typing import Any

LINEAGE_FORMAT = "eng-dna-lineage"
LINEAGE_VERSION = 1


def export_project_lineage(conn, project_id: str) -> dict:
    """Export a project's lineage closure as a portable JSON-ready bundle."""
    project = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not project:
        raise ValueError(f"Unknown project {project_id}")

    seed_rows = conn.execute(
        "SELECT artefact_id FROM artefact_projects WHERE project_id = ?",
        (project_id,),
    ).fetchall()
    seed_ids = {row["artefact_id"] for row in seed_rows}
    artefact_ids = _expand_lineage(conn, seed_ids)

    artefacts = _fetch_artefacts(conn, artefact_ids)
    id_to_dna = {row["id"]: row["dna_token"] for row in artefacts}

    tags = _fetch_tags(conn, artefact_ids)
    notes = _fetch_notes(conn, artefact_ids)
    events = _fetch_events(conn, artefact_ids)
    edges = _fetch_edges(conn, artefact_ids, id_to_dna)
    artefact_projects, related_project_ids = _fetch_artefact_projects(conn, artefact_ids)
    project_ids = set(related_project_ids)
    project_ids.add(project["id"])
    projects = _fetch_projects(conn, project_ids)

    bundle = {
        "format": LINEAGE_FORMAT,
        "version": LINEAGE_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": {"project_ids": [project_id]},
        "projects": projects,
        "artefacts": [
            {
                "dna": row["dna_token"],
                "path": row["path"],
                "hash": row["hash"],
                "type": row.get("type"),
                "description": row.get("description"),
                "created_at": row.get("created_at"),
                "updated_at": row.get("updated_at"),
            }
            for row in sorted(artefacts, key=lambda r: r["dna_token"])
        ],
        "tags": tags,
        "notes": notes,
        "events": events,
        "edges": edges,
        "artefact_projects": artefact_projects,
    }
    return bundle


def import_lineage(conn, bundle: dict, dry_run: bool = False) -> dict:
    """Import a lineage bundle into the local database."""
    _validate_bundle(bundle)
    stats = {
        "projects_new": 0,
        "projects_existing": 0,
        "artefacts_new": 0,
        "artefacts_existing": 0,
        "tags_inserted": 0,
        "tags_skipped": 0,
        "notes_inserted": 0,
        "notes_skipped": 0,
        "events_inserted": 0,
        "events_skipped": 0,
        "edges_inserted": 0,
        "edges_skipped": 0,
        "links_inserted": 0,
        "links_skipped": 0,
    }

    context = nullcontext() if dry_run else conn
    dna_to_id: dict[str, int] = {}
    temp_id = -1

    with context:
        for project in bundle.get("projects", []):
            pid = project["id"]
            existing = conn.execute("SELECT id FROM projects WHERE id = ?", (pid,)).fetchone()
            if existing:
                stats["projects_existing"] += 1
                continue
            stats["projects_new"] += 1
            if dry_run:
                continue
            if project.get("created_at"):
                conn.execute(
                    "INSERT INTO projects (id, name, description, created_at) VALUES (?, ?, ?, ?)",
                    (pid, project.get("name"), project.get("description"), project.get("created_at")),
                )
            else:
                conn.execute(
                    "INSERT INTO projects (id, name, description) VALUES (?, ?, ?)",
                    (pid, project.get("name"), project.get("description")),
                )

        for artefact in bundle.get("artefacts", []):
            dna = artefact["dna"]
            existing = conn.execute(
                "SELECT id FROM artefacts WHERE dna_token = ?",
                (dna,),
            ).fetchone()
            if existing:
                dna_to_id[dna] = existing["id"]
                stats["artefacts_existing"] += 1
                continue
            stats["artefacts_new"] += 1
            if dry_run:
                dna_to_id[dna] = temp_id
                temp_id -= 1
                continue
            created_at = artefact.get("created_at")
            updated_at = artefact.get("updated_at")
            fields = [
                artefact["dna"],
                artefact.get("path") or artefact["dna"],
                artefact.get("hash") or "",
                artefact.get("type"),
                artefact.get("description"),
            ]
            query = """
                INSERT INTO artefacts (dna_token, path, hash, type, description, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """
            cur = conn.execute(
                query,
                (
                    fields[0],
                    fields[1],
                    fields[2],
                    fields[3],
                    fields[4],
                    created_at or datetime.now(timezone.utc).isoformat(),
                    updated_at or datetime.now(timezone.utc).isoformat(),
                ),
            )
            dna_to_id[dna] = cur.lastrowid

        for item in bundle.get("tags", []):
            art_id = _resolve_dna(dna_to_id, item["dna"])
            tag = item["tag"].lower()
            exists = conn.execute(
                "SELECT 1 FROM tags WHERE artefact_id = ? AND tag = ?",
                (art_id, tag),
            ).fetchone()
            if exists:
                stats["tags_skipped"] += 1
                continue
            stats["tags_inserted"] += 1
            if not dry_run:
                conn.execute(
                    "INSERT INTO tags (artefact_id, tag) VALUES (?, ?)",
                    (art_id, tag),
                )

        for note in bundle.get("notes", []):
            art_id = _resolve_dna(dna_to_id, note["dna"])
            created_at = note.get("created_at")
            if created_at:
                exists = conn.execute(
                    """
                    SELECT 1 FROM notes
                    WHERE artefact_id = ? AND note = ? AND created_at = ?
                    """,
                    (art_id, note["note"], created_at),
                ).fetchone()
            else:
                params = (art_id, note["note"])
                exists = conn.execute(
                    """
                    SELECT 1 FROM notes
                    WHERE artefact_id = ? AND note = ?
                    """,
                    (art_id, note["note"]),
                ).fetchone()
            if exists:
                stats["notes_skipped"] += 1
                continue
            stats["notes_inserted"] += 1
            if dry_run:
                continue
            if created_at:
                conn.execute(
                    "INSERT INTO notes (artefact_id, note, created_at) VALUES (?, ?, ?)",
                    (art_id, note["note"], created_at),
                )
            else:
                conn.execute(
                    "INSERT INTO notes (artefact_id, note) VALUES (?, ?)",
                    (art_id, note["note"]),
                )

        for event in bundle.get("events", []):
            art_id = _resolve_dna(dna_to_id, event["dna"])
            metadata = event.get("metadata")
            canonical_meta = _canonical_metadata(metadata)
            duplicate = _event_exists(
                conn,
                art_id,
                event["event_type"],
                event.get("description"),
                canonical_meta,
                event.get("created_at"),
            )
            if duplicate:
                stats["events_skipped"] += 1
                continue
            stats["events_inserted"] += 1
            if dry_run:
                continue
            if event.get("created_at"):
                conn.execute(
                    """
                    INSERT INTO events (artefact_id, event_type, description, metadata, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        art_id,
                        event["event_type"],
                        event.get("description"),
                        _dump_metadata(metadata),
                        event.get("created_at"),
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO events (artefact_id, event_type, description, metadata)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        art_id,
                        event["event_type"],
                        event.get("description"),
                        _dump_metadata(metadata),
                    ),
                )

        for edge in bundle.get("edges", []):
            parent_id = _resolve_dna(dna_to_id, edge["parent_dna"])
            child_id = _resolve_dna(dna_to_id, edge["child_dna"])
            exists = conn.execute(
                """
                SELECT 1 FROM edges
                WHERE parent_id = ? AND child_id = ? AND relation_type = ?
                AND ((reason IS NULL AND ? IS NULL) OR reason = ?)
                """,
                (parent_id, child_id, edge.get("relation_type"), edge.get("reason"), edge.get("reason")),
            ).fetchone()
            if exists:
                stats["edges_skipped"] += 1
                continue
            stats["edges_inserted"] += 1
            if dry_run:
                continue
            if edge.get("created_at"):
                conn.execute(
                    """
                    INSERT INTO edges (parent_id, child_id, relation_type, reason, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        parent_id,
                        child_id,
                        edge.get("relation_type"),
                        edge.get("reason"),
                        edge.get("created_at"),
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO edges (parent_id, child_id, relation_type, reason)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        parent_id,
                        child_id,
                        edge.get("relation_type"),
                        edge.get("reason"),
                    ),
                )

        for link in bundle.get("artefact_projects", []):
            art_id = _resolve_dna(dna_to_id, link["dna"])
            exists = conn.execute(
                """
                SELECT 1 FROM artefact_projects
                WHERE artefact_id = ? AND project_id = ?
                """,
                (art_id, link["project_id"]),
            ).fetchone()
            if exists:
                stats["links_skipped"] += 1
                continue
            stats["links_inserted"] += 1
            if dry_run:
                continue
            if link.get("added_at"):
                conn.execute(
                    """
                    INSERT INTO artefact_projects (artefact_id, project_id, added_at)
                    VALUES (?, ?, ?)
                    """,
                    (art_id, link["project_id"], link.get("added_at")),
                )
            else:
                conn.execute(
                    "INSERT INTO artefact_projects (artefact_id, project_id) VALUES (?, ?)",
                    (art_id, link["project_id"]),
                )

    return stats


def _validate_bundle(bundle: dict) -> None:
    if not isinstance(bundle, dict):
        raise ValueError("Lineage bundle must be a dictionary.")
    if bundle.get("format") != LINEAGE_FORMAT:
        raise ValueError("Unsupported lineage bundle format.")
    version = bundle.get("version")
    if version != LINEAGE_VERSION:
        raise ValueError(f"Unsupported lineage bundle version: {version}")


def _resolve_dna(mapping: dict[str, int], dna: str) -> int:
    if dna not in mapping:
        raise ValueError(f"Unknown artefact DNA referenced: {dna}")
    return mapping[dna]


def _expand_lineage(conn, seed_ids: set[int]) -> set[int]:
    if not seed_ids:
        return set()
    result = set(seed_ids)
    queue: deque[int] = deque(seed_ids)
    while queue:
        current = queue.popleft()
        parents = conn.execute(
            "SELECT parent_id FROM edges WHERE child_id = ?",
            (current,),
        ).fetchall()
        for row in parents:
            parent_id = row["parent_id"]
            if parent_id not in result:
                result.add(parent_id)
                queue.append(parent_id)
        children = conn.execute(
            "SELECT child_id FROM edges WHERE parent_id = ?",
            (current,),
        ).fetchall()
        for row in children:
            child_id = row["child_id"]
            if child_id not in result:
                result.add(child_id)
                queue.append(child_id)
    return result


def _fetch_artefacts(conn, artefact_ids: set[int]) -> list[dict]:
    if not artefact_ids:
        return []
    sorted_ids = sorted(artefact_ids)
    placeholders = ",".join("?" for _ in sorted_ids)
    cur = conn.execute(
        f"SELECT * FROM artefacts WHERE id IN ({placeholders})",
        tuple(sorted_ids),
    )
    return cur.fetchall()


def _fetch_tags(conn, artefact_ids: set[int]) -> list[dict]:
    if not artefact_ids:
        return []
    sorted_ids = sorted(artefact_ids)
    placeholders = ",".join("?" for _ in sorted_ids)
    cur = conn.execute(
        f"""
        SELECT a.dna_token AS dna, t.tag
        FROM tags t
        JOIN artefacts a ON a.id = t.artefact_id
        WHERE t.artefact_id IN ({placeholders})
        ORDER BY a.dna_token, t.tag
        """,
        tuple(sorted_ids),
    )
    return cur.fetchall()


def _fetch_notes(conn, artefact_ids: set[int]) -> list[dict]:
    if not artefact_ids:
        return []
    sorted_ids = sorted(artefact_ids)
    placeholders = ",".join("?" for _ in sorted_ids)
    cur = conn.execute(
        f"""
        SELECT a.dna_token AS dna, n.note, n.created_at
        FROM notes n
        JOIN artefacts a ON a.id = n.artefact_id
        WHERE n.artefact_id IN ({placeholders})
        ORDER BY n.created_at ASC
        """,
        tuple(sorted_ids),
    )
    return cur.fetchall()


def _fetch_events(conn, artefact_ids: set[int]) -> list[dict]:
    if not artefact_ids:
        return []
    sorted_ids = sorted(artefact_ids)
    placeholders = ",".join("?" for _ in sorted_ids)
    cur = conn.execute(
        f"""
        SELECT a.dna_token AS dna, e.event_type, e.description, e.metadata, e.created_at
        FROM events e
        JOIN artefacts a ON a.id = e.artefact_id
        WHERE e.artefact_id IN ({placeholders})
        ORDER BY e.created_at ASC
        """,
        tuple(sorted_ids),
    )
    rows = []
    for row in cur.fetchall():
        rows.append(
            {
                "dna": row["dna"],
                "event_type": row["event_type"],
                "description": row.get("description"),
                "metadata": _safe_json_loads(row.get("metadata")),
                "created_at": row.get("created_at"),
            }
        )
    return rows


def _fetch_edges(conn, artefact_ids: set[int], id_to_dna: dict[int, str]) -> list[dict]:
    if not artefact_ids:
        return []
    sorted_ids = sorted(artefact_ids)
    placeholders = ",".join("?" for _ in sorted_ids)
    cur = conn.execute(
        f"""
        SELECT * FROM edges
        WHERE parent_id IN ({placeholders}) OR child_id IN ({placeholders})
        """,
        tuple(sorted_ids) * 2,
    )
    rows = []
    for row in cur.fetchall():
        if row["parent_id"] not in artefact_ids or row["child_id"] not in artefact_ids:
            continue
        rows.append(
            {
                "parent_dna": id_to_dna[row["parent_id"]],
                "child_dna": id_to_dna[row["child_id"]],
                "relation_type": row.get("relation_type"),
                "reason": row.get("reason"),
                "created_at": row.get("created_at"),
            }
        )
    rows.sort(key=lambda r: (r["parent_dna"], r["child_dna"], r.get("relation_type") or ""))
    return rows


def _fetch_artefact_projects(conn, artefact_ids: set[int]) -> tuple[list[dict], set[str]]:
    if not artefact_ids:
        return [], set()
    sorted_ids = sorted(artefact_ids)
    placeholders = ",".join("?" for _ in sorted_ids)
    cur = conn.execute(
        f"""
        SELECT a.dna_token AS dna, ap.project_id, ap.added_at
        FROM artefact_projects ap
        JOIN artefacts a ON a.id = ap.artefact_id
        WHERE ap.artefact_id IN ({placeholders})
        ORDER BY ap.project_id, a.dna_token
        """,
        tuple(sorted_ids),
    )
    rows = cur.fetchall()
    project_ids = {row["project_id"] for row in rows}
    return rows, project_ids


def _fetch_projects(conn, project_ids: set[str]) -> list[dict]:
    if not project_ids:
        return []
    sorted_ids = sorted(project_ids)
    placeholders = ",".join("?" for _ in sorted_ids)
    cur = conn.execute(
        f"SELECT * FROM projects WHERE id IN ({placeholders}) ORDER BY id",
        tuple(sorted_ids),
    )
    return cur.fetchall()


def _event_exists(
    conn,
    artefact_id: int,
    event_type: str,
    description: str | None,
    canonical_metadata: Any,
    created_at: str | None,
) -> bool:
    rows = conn.execute(
        """
        SELECT event_type, description, metadata, created_at
        FROM events WHERE artefact_id = ?
        """,
        (artefact_id,),
    ).fetchall()
    for row in rows:
        if row["event_type"] != event_type:
            continue
        if row.get("description") != description:
            continue
        if row.get("created_at") != created_at:
            continue
        existing_meta = _canonical_metadata(_safe_json_loads(row.get("metadata")))
        if existing_meta == canonical_metadata:
            return True
    return False


def _dump_metadata(metadata: Any) -> str | None:
    if metadata is None:
        return None
    return json.dumps(metadata, sort_keys=True)


def _safe_json_loads(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value


def _canonical_metadata(metadata: Any) -> Any:
    if metadata is None:
        return None
    if isinstance(metadata, str):
        parsed = _safe_json_loads(metadata)
        return json.dumps(parsed, sort_keys=True) if isinstance(parsed, (dict, list)) else metadata
    if isinstance(metadata, (dict, list)):
        return json.dumps(metadata, sort_keys=True)
    return metadata
