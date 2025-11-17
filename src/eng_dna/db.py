"""SQLite helpers for eng-dna (Background.md §4)."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Iterable, Optional

DB_FILENAME = "eng_dna.db"


def _dict_factory(cursor: sqlite3.Cursor, row: tuple) -> dict:
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


def resolve_db_path(
    explicit_path: Optional[str] = None, require_exists: bool = True, start: Optional[Path] = None
) -> Path:
    """Resolve the DB path.

    Searches for eng_dna.db upward from *start* (defaults to cwd) unless *explicit_path* or
    $EDNA_DB_PATH is provided.
    """

    if explicit_path:
        path = Path(explicit_path).expanduser().resolve()
        if require_exists and not path.exists():
            raise FileNotFoundError(f"No eng-dna database found at {path}")
        return path

    env_path = os.environ.get("EDNA_DB_PATH")
    if env_path:
        path = Path(env_path).expanduser().resolve()
        if require_exists and not path.exists():
            raise FileNotFoundError(
                f"$EDNA_DB_PATH points to {path}, but no database is present."
            )
        return path

    start_path = start or Path.cwd()
    for parent in [start_path, *start_path.parents]:
        candidate = parent / DB_FILENAME
        if candidate.exists():
            return candidate.resolve()

    if require_exists:
        raise FileNotFoundError(
            f"No {DB_FILENAME} found from {start_path} upward. Run 'edna init' first or point"
            " EDNA_DB_PATH at an existing database."
        )
    return (start_path / DB_FILENAME).resolve()


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = _dict_factory
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(db_path: Path) -> None:
    conn = connect(db_path)
    try:
        ensure_schema(conn)
    finally:
        conn.close()


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the baseline schema if it does not yet exist."""

    schema_statements: Iterable[str] = [
        """
        CREATE TABLE IF NOT EXISTS artefacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dna_token TEXT UNIQUE NOT NULL,
            path TEXT NOT NULL,
            hash TEXT NOT NULL,
            type TEXT,
            description TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            artefact_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            description TEXT,
            metadata TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (artefact_id) REFERENCES artefacts(id) ON DELETE CASCADE
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            parent_id INTEGER NOT NULL,
            child_id INTEGER NOT NULL,
            relation_type TEXT NOT NULL,
            reason TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (parent_id) REFERENCES artefacts(id) ON DELETE CASCADE,
            FOREIGN KEY (child_id) REFERENCES artefacts(id) ON DELETE CASCADE
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            artefact_id INTEGER NOT NULL,
            tag TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE (artefact_id, tag),
            FOREIGN KEY (artefact_id) REFERENCES artefacts(id) ON DELETE CASCADE
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            artefact_id INTEGER NOT NULL,
            note TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (artefact_id) REFERENCES artefacts(id) ON DELETE CASCADE
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS artefact_projects (
            artefact_id INTEGER NOT NULL,
            project_id TEXT NOT NULL,
            added_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (artefact_id, project_id),
            FOREIGN KEY (artefact_id) REFERENCES artefacts(id) ON DELETE CASCADE,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_artefacts_hash ON artefacts(hash);
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_edges_child ON edges(child_id);
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_edges_parent ON edges(parent_id);
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_tags_tag ON tags(tag);
        """,
    ]

    with conn:
        for statement in schema_statements:
            conn.execute(statement)
