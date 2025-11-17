"""High-level operations backing the CLI."""
from __future__ import annotations

import itertools
from pathlib import Path
from typing import Iterable, Optional

from . import artefacts
from .identity import compute_file_hash, generate_dna_token, looks_like_dna, normalize_path
from .sidecar import read_identity, write_identity


def tag_file(
    conn,
    file_path: Path,
    *,
    artefact_type: Optional[str],
    description: Optional[str],
    tags: Optional[list[str]],
    project_ids: Optional[list[str]],
    force_overwrite: bool = False,
) -> dict:
    file_path = file_path.expanduser().resolve()
    file_hash = compute_file_hash(file_path)
    identity = read_identity(file_path)

    existing = None
    if identity and identity.dna_token:
        existing = artefacts.lookup_by_dna(conn, identity.dna_token)
    if not existing:
        existing = artefacts.lookup_by_hash(conn, file_hash)

    if existing:
        return _handle_existing_file(
            conn,
            existing,
            file_path,
            file_hash,
            artefact_type,
            description,
            tags,
            project_ids,
            force_overwrite=force_overwrite,
            identity_found=bool(identity),
            command="tag",
        )

    dna_token = generate_dna_token()
    created = artefacts.create_artefact(
        conn,
        dna_token=dna_token,
        path=str(file_path),
        file_hash=file_hash,
        artefact_type=artefact_type,
        description=description,
        tags=[t.lower() for t in tags] if tags else None,
        project_ids=project_ids,
    )
    write_identity(file_path, created["dna_token"], file_hash, created.get("type"), created["path"])
    return created


def show_target(
    conn,
    target: str,
    *,
    force_overwrite: bool = False,
) -> dict:
    artefact, _ = resolve_target(
        conn,
        target,
        force_overwrite=force_overwrite,
        allow_versioning=False,
    )
    return artefact


def resolve_target(
    conn,
    target: str,
    *,
    force_overwrite: bool = False,
    allow_versioning: bool = False,
) -> tuple[dict, Optional[Path]]:
    target_path = Path(target)
    if target_path.exists():
        artefact = resolve_file_reference(
            conn,
            target_path,
            force_overwrite=force_overwrite,
            allow_versioning=allow_versioning,
        )
        return artefact, target_path
    if looks_like_dna(target):
        artefact = artefacts.lookup_by_dna(conn, target)
        if not artefact:
            raise ValueError(f"No artefact with DNA {target}")
        return artefact, None
    # treat as normalised path even if file missing
    artefact = artefacts.lookup_by_path(conn, target)
    if not artefact:
        raise ValueError(f"Could not resolve target {target}")
    return artefact, None


def resolve_file_reference(
    conn,
    file_path: Path,
    *,
    force_overwrite: bool = False,
    allow_versioning: bool = False,
) -> dict:
    file_path = file_path.expanduser().resolve()
    identity = read_identity(file_path)
    file_hash = compute_file_hash(file_path)

    artefact = None
    if identity and identity.dna_token:
        artefact = artefacts.lookup_by_dna(conn, identity.dna_token)
    if not artefact and identity and identity.file_hash:
        artefact = artefacts.lookup_by_hash(conn, identity.file_hash)
    if not artefact:
        artefact = artefacts.lookup_by_hash(conn, file_hash)
    if not artefact:
        raise ValueError(
            "File is not tracked. Run 'edna tag' first to assign a DNA token."
        )

    updated = _post_resolve_housekeeping(
        conn,
        artefact,
        file_path,
        identity_found=bool(identity),
        file_hash=file_hash,
        force_overwrite=force_overwrite,
        allow_versioning=allow_versioning,
    )
    return updated


def _post_resolve_housekeeping(
    conn,
    artefact: dict,
    file_path: Path,
    *,
    identity_found: bool,
    file_hash: str,
    force_overwrite: bool,
    allow_versioning: bool,
) -> dict:
    artefact = _ensure_path(conn, artefact, file_path)
    if artefact["hash"] != file_hash:
        if not allow_versioning:
            if force_overwrite:
                raise ValueError(
                    "Hash overwrites must be performed via 'edna tag --force-overwrite'.",
                )
            return artefact
        artefact = _handle_hash_change(
            conn,
            artefact,
            file_path,
            file_hash,
            force_overwrite=force_overwrite,
        )
    else:
        write_identity(file_path, artefact["dna_token"], file_hash, artefact.get("type"), artefact["path"])
        if not identity_found:
            artefacts.record_event(
                conn,
                artefact["id"],
                event_type="sidecar_restored",
                metadata={"path": artefact["path"]},
            )
    return artefact


def _handle_existing_file(
    conn,
    artefact: dict,
    file_path: Path,
    file_hash: str,
    artefact_type: Optional[str],
    description: Optional[str],
    tags: Optional[list[str]],
    project_ids: Optional[list[str]],
    *,
    force_overwrite: bool,
    identity_found: bool,
    command: str,
) -> dict:
    artefact = _post_resolve_housekeeping(
        conn,
        artefact,
        file_path,
        identity_found=identity_found,
        file_hash=file_hash,
        force_overwrite=force_overwrite,
        allow_versioning=True,
    )
    if artefact_type and artefact.get("type") != artefact_type:
        with conn:
            conn.execute(
                "UPDATE artefacts SET type = ?, updated_at = datetime('now') WHERE id = ?",
                (artefact_type, artefact["id"]),
            )
        artefact = artefacts.fetch_artefact(conn, artefact["id"])
    if description:
        with conn:
            conn.execute(
                "UPDATE artefacts SET description = ?, updated_at = datetime('now') WHERE id = ?",
                (description, artefact["id"]),
            )
        artefact = artefacts.fetch_artefact(conn, artefact["id"])
    if tags:
        artefacts.add_tags(conn, artefact["id"], [t.lower() for t in tags])
    if project_ids:
        artefacts.assign_projects(conn, artefact["id"], project_ids)
    artefacts.record_event(
        conn,
        artefact["id"],
        event_type=f"{command}_existing",
        metadata={"hash": artefact["hash"]},
    )
    return artefact


def _ensure_path(conn, artefact: dict, file_path: Path) -> dict:
    norm = normalize_path(file_path)
    if artefact["path"] != norm:
        artefacts.update_path(conn, artefact["id"], norm)
        artefacts.record_event(
            conn,
            artefact["id"],
            event_type="moved",
            metadata={"from": artefact["path"], "to": norm},
        )
        artefact = artefacts.fetch_artefact(conn, artefact["id"])
    return artefact


def _handle_hash_change(
    conn,
    artefact: dict,
    file_path: Path,
    new_hash: str,
    *,
    force_overwrite: bool,
) -> dict:
    if force_overwrite:
        artefacts.update_hash(conn, artefact["id"], new_hash)
        artefacts.record_event(
            conn,
            artefact["id"],
            event_type="hash_overwritten",
            metadata={"hash": new_hash},
        )
        updated = artefacts.fetch_artefact(conn, artefact["id"])
        write_identity(
            file_path,
            updated["dna_token"],
            new_hash,
            updated.get("type"),
            updated["path"],
        )
        return updated

    new_version = artefacts.create_version(
        conn,
        artefact,
        new_hash=new_hash,
        new_path=str(file_path),
        description=artefact.get("description"),
    )
    write_identity(
        file_path,
        new_version["dna_token"],
        new_hash,
        new_version.get("type"),
        new_version["path"],
    )
    return new_version


def link_artefacts(
    conn,
    child: dict,
    parents: Iterable[dict],
    relation_type: str,
    reason: Optional[str],
) -> None:
    for parent in parents:
        artefacts.create_edge(
            conn,
            parent_id=parent["id"],
            child_id=child["id"],
            relation_type=relation_type,
            reason=reason,
        )
        artefacts.record_event(
            conn,
            child["id"],
            event_type="linked",
            metadata={"parent": parent["dna_token"], "relation": relation_type},
        )


def trace_ancestors(conn, artefact: dict, depth: int = 0, seen: Optional[set[int]] = None) -> list[str]:
    seen = seen or set()
    lines = []
    indent = "  " * depth
    lines.append(f"{indent}- {artefact['dna_token']} ({artefact['path']})")
    if artefact["id"] in seen:
        lines[-1] += " *"
        return lines
    seen.add(artefact["id"])
    for parent in artefacts.list_parents(conn, artefact["id"]):
        lines.extend(trace_ancestors(conn, parent, depth + 1, seen))
    return lines


def search_artefacts(
    conn,
    *,
    tags: Optional[list[str]] = None,
    artefact_type: Optional[str] = None,
    project_id: Optional[str] = None,
) -> list[dict]:
    clauses = ["1=1"]
    params: list[str] = []
    join_tags = False
    join_projects = False

    if tags:
        join_tags = True
        placeholders = ",".join(["?"] * len(tags))
        clauses.append(f"t.tag IN ({placeholders})")
        params.extend([t.lower() for t in tags])
    if artefact_type:
        clauses.append("a.type = ?")
        params.append(artefact_type)
    if project_id:
        join_projects = True
        clauses.append("ap.project_id = ?")
        params.append(project_id)

    query = "SELECT DISTINCT a.* FROM artefacts a"
    if join_tags:
        query += " JOIN tags t ON t.artefact_id = a.id"
    if join_projects:
        query += " JOIN artefact_projects ap ON ap.artefact_id = a.id"
    query += " WHERE " + " AND ".join(clauses) + " ORDER BY a.created_at DESC"
    cur = conn.execute(query, tuple(params))
    return cur.fetchall()


def rescan_tree(conn, root: Path) -> list[str]:
    """Walk *root* to reconcile files and sidecars."""
    root = root.expanduser().resolve()
    updated: list[str] = []
    for path in sorted(root.rglob("*")):
        if path.is_dir() or path.suffix == ".edna":
            continue
        try:
            artefact = resolve_file_reference(conn, path)
        except Exception:
            continue
        updated.append(artefact["dna_token"])
    return updated
