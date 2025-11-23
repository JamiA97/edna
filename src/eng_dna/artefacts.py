"""Low-level database helpers for artefacts, lineage, tags, and projects.

This module encapsulates all SQLite access used by EDNA so higher-level
operations remain declarative. It keeps schema details, event logging, and
version linkage in one place to avoid divergent SQL across commands.
"""
from __future__ import annotations

import json
from typing import Iterable, Optional

from .identity import generate_dna_token, normalize_path


def fetchone(conn, query: str, args: Iterable) -> Optional[dict]:
    """
    Execute a query and return a single row as a dict.

    Parameters:
        conn: Open SQLite connection.
        query: SQL with placeholders.
        args: Iterable of positional parameters.

    Returns:
        Row dict or None if no results.

    Side Effects:
        Issues a read query against the database.
    """
    cur = conn.execute(query, tuple(args))
    return cur.fetchone()


def lookup_by_dna(conn, dna_token: str) -> Optional[dict]:
    """
    Fetch an artefact by its immutable DNA token.

    Parameters:
        conn: Database connection.
        dna_token: DNA token string.

    Returns:
        Artefact row or None.

    Side Effects:
        Database read.
    """
    return fetchone(conn, "SELECT * FROM artefacts WHERE dna_token = ?", [dna_token])


def lookup_by_path(conn, path: str) -> Optional[dict]:
    """
    Fetch an artefact by its stored path.

    Paths are normalised before comparison so updates triggered by move-detection
    stay consistent across operations.

    Parameters:
        conn: Database connection.
        path: Path to resolve.

    Returns:
        Artefact row or None.

    Side Effects:
        Database read.
    """
    return fetchone(conn, "SELECT * FROM artefacts WHERE path = ?", [normalize_path(path)])


def lookup_by_hash(conn, file_hash: str) -> Optional[dict]:
    """
    Fetch an artefact by file hash.

    Parameters:
        conn: Database connection.
        file_hash: SHA-256 hash hex digest.

    Returns:
        Artefact row or None.

    Side Effects:
        Database read.
    """
    return fetchone(conn, "SELECT * FROM artefacts WHERE hash = ?", [file_hash])


def fetch_artefact(conn, artefact_id: int) -> Optional[dict]:
    """
    Fetch an artefact by primary key.

    Parameters:
        conn: Database connection.
        artefact_id: Internal artefact id.

    Returns:
        Artefact row or None.

    Side Effects:
        Database read.
    """
    return fetchone(conn, "SELECT * FROM artefacts WHERE id = ?", [artefact_id])


def list_tags(conn, artefact_id: int) -> list[str]:
    """
    List tags attached to an artefact.

    Parameters:
        conn: Database connection.
        artefact_id: Artefact id.

    Returns:
        Sorted list of tag strings.

    Side Effects:
        Database read.
    """
    cur = conn.execute("SELECT tag FROM tags WHERE artefact_id = ? ORDER BY tag", (artefact_id,))
    return [row["tag"] for row in cur.fetchall()]


def list_projects(conn, artefact_id: int) -> list[dict]:
    """
    List projects an artefact belongs to.

    Parameters:
        conn: Database connection.
        artefact_id: Artefact id.

    Returns:
        List of project rows ordered by id.

    Side Effects:
        Database read via join on artefact_projects.
    """
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
    """
    List recorded events for an artefact (newest first).

    Parameters:
        conn: Database connection.
        artefact_id: Artefact id.

    Returns:
        List of event rows.

    Side Effects:
        Database read.
    """
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
    """
    Insert a new artefact row and attach tags/projects.

    Parameters:
        conn: Database connection.
        dna_token: Assigned DNA token.
        path: File path (will be normalised).
        file_hash: SHA-256 content hash.
        artefact_type: Optional type label.
        description: Optional description.
        tags: Tag strings to attach.
        project_ids: Project ids to link to.

    Returns:
        Newly created artefact row.

    Side Effects:
        Writes artefacts row, tags, project links, and a 'created' event.
    """
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
    """
    Attach tags to an artefact (idempotent).

    Parameters:
        conn: Database connection.
        artefact_id: Artefact id.
        tags: Tag strings.

    Returns:
        None.

    Side Effects:
        Inserts into tags table; ignores duplicates.
    """
    with conn:
        for tag in tags:
            conn.execute(
                "INSERT OR IGNORE INTO tags (artefact_id, tag) VALUES (?, ?)",
                (artefact_id, tag.lower()),
            )


def assign_projects(conn, artefact_id: int, project_ids: list[str]) -> None:
    """
    Link an artefact to projects.

    Validates projects exist before linking to avoid dangling references.

    Parameters:
        conn: Database connection.
        artefact_id: Artefact id.
        project_ids: Project ids to assign.

    Returns:
        None.

    Side Effects:
        Reads project table; inserts into artefact_projects with upsert semantics.
    """
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
    """
    Append an event describing an action on an artefact.

    Parameters:
        conn: Database connection.
        artefact_id: Artefact id.
        event_type: Short event key (e.g. created, moved, linked).
        description: Optional human readable context.
        metadata: Optional structured metadata to JSON-encode.

    Returns:
        None.

    Side Effects:
        Inserts into events table.
    """
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
    """
    Persist a path change for an artefact.

    Parameters:
        conn: Database connection.
        artefact_id: Artefact id.
        new_path: New absolute path.

    Returns:
        None.

    Side Effects:
        Updates artefacts.path and timestamps.
    """
    with conn:
        conn.execute(
            "UPDATE artefacts SET path = ?, updated_at = datetime('now') WHERE id = ?",
            (normalize_path(new_path), artefact_id),
        )


def update_hash(conn, artefact_id: int, new_hash: str) -> None:
    """
    Persist a hash change for an artefact.

    Used when the operator forces a hash overwrite instead of creating a new
    version.

    Parameters:
        conn: Database connection.
        artefact_id: Artefact id.
        new_hash: New SHA-256 hex digest.

    Returns:
        None.

    Side Effects:
        Updates artefacts.hash and timestamps.
    """
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
    """
    Create a lineage edge between two artefacts.

    Parameters:
        conn: Database connection.
        parent_id: Parent artefact id.
        child_id: Child artefact id.
        relation_type: Relation label (e.g. derived_from).
        reason: Optional detail for auditing.

    Returns:
        None.

    Side Effects:
        Inserts into edges table.
    """
    with conn:
        conn.execute(
            """
            INSERT INTO edges (parent_id, child_id, relation_type, reason)
            VALUES (?, ?, ?, ?)
            """,
            (parent_id, child_id, relation_type, reason),
        )


def list_parents(conn, child_id: int) -> list[dict]:
    """
    List parents of a child artefact, including edge metadata.

    Parameters:
        conn: Database connection.
        child_id: Child artefact id.

    Returns:
        List of parent artefact rows with relation data.

    Side Effects:
        Database read with join on edges.
    """
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
    """
    List children of a parent artefact, including edge metadata.

    Parameters:
        conn: Database connection.
        parent_id: Parent artefact id.

    Returns:
        List of child artefact rows with relation data.

    Side Effects:
        Database read with join on edges.
    """
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
    """
    Insert a new project record.

    Parameters:
        conn: Database connection.
        project_id: Stable project identifier.
        name: Human-friendly name.
        description: Optional description.

    Returns:
        Project row that was inserted.

    Side Effects:
        Writes to projects table.
    """
    with conn:
        conn.execute(
            "INSERT INTO projects (id, name, description) VALUES (?, ?, ?)",
            (project_id, name, description),
        )
    return fetchone(conn, "SELECT * FROM projects WHERE id = ?", [project_id])


def get_project(conn, project_id: str) -> Optional[dict]:
    """
    Fetch a project by id.

    Parameters:
        conn: Database connection.
        project_id: Project id.

    Returns:
        Project row or None.

    Side Effects:
        Database read.
    """
    return fetchone(conn, "SELECT * FROM projects WHERE id = ?", [project_id])


def list_all_projects(conn) -> list[dict]:
    """
    List all projects ordered by id.

    Parameters:
        conn: Database connection.

    Returns:
        List of project rows ordered by id.

    Side Effects:
        Database read.
    """
    cur = conn.execute("SELECT * FROM projects ORDER BY id")
    return cur.fetchall()


def list_project_files(conn, project_id: str) -> list[dict]:
    """
    List artefacts linked to a project.

    Parameters:
        conn: Database connection.
        project_id: Project id.

    Returns:
        List of artefact rows.

    Side Effects:
        Database read across artefact_projects join.
    """
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
    """
    Create a new artefact version and link it to its parent.

    Parameters:
        conn: Database connection.
        artefact: Parent artefact row.
        new_hash: Hash of the updated content.
        new_path: Path of the new version.
        description: Optional description to carry forward/override.

    Returns:
        Newly created child artefact row.

    Side Effects:
        Inserts artefact, edges, events, tags, and project links; updates lineage
        graph with a derived_from edge.
    """
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
