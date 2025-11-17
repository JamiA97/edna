"""Database operations for artefacts and projects (Background.md §3-6)."""
from __future__ import annotations

import json
from typing import Iterable, Optional

from .identity import generate_dna_token, normalize_path


def fetchone(conn, query: str, args: Iterable) -> Optional[dict]:
    cur = conn.execute(query, tuple(args))
    return cur.fetchone()


def lookup_by_dna(conn, dna_token: str) -> Optional[dict]:
    return fetchone(conn, "SELECT * FROM artefacts WHERE dna_token = ?", [dna_token])


def lookup_by_path(conn, path: str) -> Optional[dict]:
    return fetchone(conn, "SELECT * FROM artefacts WHERE path = ?", [normalize_path(path)])


def lookup_by_hash(conn, file_hash: str) -> Optional[dict]:
    return fetchone(conn, "SELECT * FROM artefacts WHERE hash = ?", [file_hash])


def fetch_artefact(conn, artefact_id: int) -> Optional[dict]:
    return fetchone(conn, "SELECT * FROM artefacts WHERE id = ?", [artefact_id])


def list_tags(conn, artefact_id: int) -> list[str]:
    cur = conn.execute("SELECT tag FROM tags WHERE artefact_id = ? ORDER BY tag", (artefact_id,))
    return [row["tag"] for row in cur.fetchall()]


def list_projects(conn, artefact_id: int) -> list[dict]:
    cur = conn.execute(
        """
        SELECT p.* FROM projects p
        JOIN artefact_projects ap ON ap.project_id = p.id
        WHERE ap.artefact_id = ?
        ORDER BY p.id
        """,
        (artefact_id,),
    )
    return cur.fetchall()


def list_events(conn, artefact_id: int) -> list[dict]:
    cur = conn.execute(
        "SELECT * FROM events WHERE artefact_id = ? ORDER BY created_at DESC",
        (artefact_id,),
    )
    return cur.fetchall()


def create_artefact(
    conn,
    *,
    dna_token: str,
    path: str,
    file_hash: str,
    artefact_type: Optional[str],
    description: Optional[str],
    tags: Optional[list[str]] = None,
    project_ids: Optional[list[str]] = None,
) -> dict:
    norm_path = normalize_path(path)
    with conn:
        cur = conn.execute(
            """
            INSERT INTO artefacts (dna_token, path, hash, type, description)
            VALUES (?, ?, ?, ?, ?)
            """,
            (dna_token, norm_path, file_hash, artefact_type, description),
        )
        artefact_id = cur.lastrowid
        record_event(
            conn,
            artefact_id,
            event_type="created",
            description=description or "tagged",
            metadata={"hash": file_hash},
        )
        if tags:
            add_tags(conn, artefact_id, tags)
        if project_ids:
            assign_projects(conn, artefact_id, project_ids)
    return fetch_artefact(conn, artefact_id)


def add_tags(conn, artefact_id: int, tags: list[str]) -> None:
    with conn:
        for tag in tags:
            conn.execute(
                "INSERT OR IGNORE INTO tags (artefact_id, tag) VALUES (?, ?)",
                (artefact_id, tag.lower()),
            )


def assign_projects(conn, artefact_id: int, project_ids: list[str]) -> None:
    with conn:
        for project_id in project_ids:
            project = fetchone(conn, "SELECT * FROM projects WHERE id = ?", [project_id])
            if not project:
                raise ValueError(f"Project '{project_id}' does not exist")
            conn.execute(
                "INSERT OR IGNORE INTO artefact_projects (artefact_id, project_id) VALUES (?, ?)",
                (artefact_id, project_id),
            )


def record_event(
    conn,
    artefact_id: int,
    *,
    event_type: str,
    description: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> None:
    meta_str = json.dumps(metadata) if metadata else None
    with conn:
        conn.execute(
            """
            INSERT INTO events (artefact_id, event_type, description, metadata)
            VALUES (?, ?, ?, ?)
            """,
            (artefact_id, event_type, description, meta_str),
        )


def update_path(conn, artefact_id: int, new_path: str) -> None:
    with conn:
        conn.execute(
            "UPDATE artefacts SET path = ?, updated_at = datetime('now') WHERE id = ?",
            (normalize_path(new_path), artefact_id),
        )


def update_hash(conn, artefact_id: int, new_hash: str) -> None:
    with conn:
        conn.execute(
            "UPDATE artefacts SET hash = ?, updated_at = datetime('now') WHERE id = ?",
            (new_hash, artefact_id),
        )


def create_edge(
    conn,
    *,
    parent_id: int,
    child_id: int,
    relation_type: str,
    reason: Optional[str] = None,
) -> None:
    with conn:
        conn.execute(
            """
            INSERT INTO edges (parent_id, child_id, relation_type, reason)
            VALUES (?, ?, ?, ?)
            """,
            (parent_id, child_id, relation_type, reason),
        )


def list_parents(conn, child_id: int) -> list[dict]:
    cur = conn.execute(
        """
        SELECT a.* , e.relation_type, e.reason
        FROM edges e
        JOIN artefacts a ON a.id = e.parent_id
        WHERE e.child_id = ?
        ORDER BY a.created_at DESC
        """,
        (child_id,),
    )
    return cur.fetchall()


def list_children(conn, parent_id: int) -> list[dict]:
    cur = conn.execute(
        """
        SELECT a.* , e.relation_type, e.reason
        FROM edges e
        JOIN artefacts a ON a.id = e.child_id
        WHERE e.parent_id = ?
        ORDER BY a.created_at DESC
        """,
        (parent_id,),
    )
    return cur.fetchall()


def create_project(conn, project_id: str, name: str, description: Optional[str]) -> dict:
    with conn:
        conn.execute(
            "INSERT INTO projects (id, name, description) VALUES (?, ?, ?)",
            (project_id, name, description),
        )
    return fetchone(conn, "SELECT * FROM projects WHERE id = ?", [project_id])


def get_project(conn, project_id: str) -> Optional[dict]:
    return fetchone(conn, "SELECT * FROM projects WHERE id = ?", [project_id])


def list_project_files(conn, project_id: str) -> list[dict]:
    cur = conn.execute(
        """
        SELECT a.* FROM artefacts a
        JOIN artefact_projects ap ON ap.artefact_id = a.id
        WHERE ap.project_id = ?
        ORDER BY a.created_at DESC
        """,
        (project_id,),
    )
    return cur.fetchall()


def create_version(
    conn,
    artefact: dict,
    *,
    new_hash: str,
    new_path: str,
    description: Optional[str] = None,
) -> dict:
    dna = generate_dna_token()
    new_art = create_artefact(
        conn,
        dna_token=dna,
        path=new_path,
        file_hash=new_hash,
        artefact_type=artefact.get("type"),
        description=description or artefact.get("description"),
        tags=list_tags(conn, artefact["id"]),
        project_ids=[p["id"] for p in list_projects(conn, artefact["id"])],
    )
    create_edge(
        conn,
        parent_id=artefact["id"],
        child_id=new_art["id"],
        relation_type="derived_from",
        reason="content_changed",
    )
    record_event(
        conn,
        artefact["id"],
        event_type="version_superseded",
        metadata={"new_dna": dna},
    )
    record_event(
        conn,
        new_art["id"],
        event_type="version_created",
        metadata={"parent_dna": artefact["dna_token"]},
    )
    return new_art
