"""High-level operations backing the CLI and lineage tooling.

These functions orchestrate identity resolution, versioning rules, lineage edge
creation, and sidecar maintenance. Business logic lives here so CLI handlers
stay thin and database primitives remain reusable.
"""
from __future__ import annotations

import itertools
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from . import artefacts
from .identity import compute_file_hash, generate_dna_token, looks_like_dna, normalize_path
from .sidecar import read_identity, write_identity


@dataclass
class LineageNode:
    """Lightweight representation of an artefact used when rendering graphs."""
    id: int
    dna_token: str
    path: str
    type: Optional[str]


@dataclass
class LineageEdge:
    """In-memory edge describing parent-child lineage relationships."""
    parent_id: int
    child_id: int
    relation_type: Optional[str]
    reason: Optional[str]


def tag_file(
    conn,
    file_path: Path,
    *,
    artefact_type: Optional[str],
    description: Optional[str],
    tags: Optional[list[str]],
    project_ids: Optional[list[str]],
    force_overwrite: bool = False,
    mode: str = "snapshot",
) -> dict:
    """
    Tag a file by assigning (or reconciling) a DNA record and metadata.

    What:
        Computes the file hash, looks for existing identity via sidecar or hash,
        and either updates metadata, creates a new artefact, or versions it.
    Why:
        Central entrypoint for CLI tagging; keeps decision tree consistent
        between first-time and repeat tagging.

    Parameters:
        conn: Database connection.
        file_path: File to tag.
        artefact_type: Optional type label.
        description: Optional description.
        tags: Optional list of tags to attach.
        project_ids: Optional list of project ids to link.
        force_overwrite: If True, allows overwriting hash instead of versioning.
        mode: 'snapshot' (default) to create new versions on hash change or
            'wip' to update the existing artefact in place.

    Returns:
        Artefact row representing the tracked item (new or existing/versioned).

    Side Effects:
        Reads file for hashing; may write sidecar; may insert/update DB rows and
        record events.
    """
    valid_modes = {"snapshot", "wip"}
    mode_normalised = mode.lower() if mode else "snapshot"
    if mode_normalised not in valid_modes:
        raise ValueError(f"Invalid mode '{mode}'. Expected one of {sorted(valid_modes)}.")
    if mode_normalised == "wip" and force_overwrite:
        raise ValueError("--force-overwrite cannot be combined with mode='wip'; WIP already updates in place.")

    file_path = file_path.expanduser().resolve()
    file_hash = compute_file_hash(file_path)
    identity = read_identity(file_path)

    existing = None
    if identity and identity.dna_token:
        existing = artefacts.lookup_by_dna(conn, identity.dna_token)
    if not existing:
        existing = artefacts.lookup_by_hash(conn, file_hash)

    # Decision tree: prefer DNA from sidecar, fall back to hash match, otherwise create new.
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
            mode=mode_normalised,
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
    """
    Resolve and return an artefact without creating a version.

    What:
        Thin wrapper around resolve_target that prohibits overwriting hashes in a
        read-only context.

    Parameters:
        conn: Database connection.
        target: File path, DNA token, or stored path reference.
        force_overwrite: Unsupported for show (retained for signature parity).

    Returns:
        Artefact row corresponding to the target.

    Side Effects:
        May update sidecar/path during housekeeping when resolving a file.
    """
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
    """
    Resolve a user-supplied target into an artefact.

    Decision tree:
        1) If the argument points to an existing file, prefer on-disk identity
           (sidecar/embedded) then hash. This path allows versioning if enabled.
        2) If the string looks like a DNA token, fetch directly.
        3) Otherwise treat it as a stored path even if the file no longer
           exists, ensuring historical records remain reachable.

    Parameters:
        conn: Database connection.
        target: Path-like string or DNA token.
        force_overwrite: Allow hash overwrite during housekeeping when True.
        allow_versioning: Permit new versions on hash mismatch when True.

    Returns:
        Tuple of (artefact row, Path if the target was a file else None).

    Side Effects:
        When resolving a file, may rewrite sidecars, update DB path records, and
        emit events depending on housekeeping outcomes.
    """
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
    # Treat as normalised path even when the file is missing; keeps historical lookups alive.
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
    """
    Resolve an on-disk file to a tracked artefact.

    What:
        Normalises the path, reads identity markers, hashes the file, then
        searches by DNA, embedded hash, and finally fresh hash before applying
        housekeeping actions.

    Why:
        Keeps consistent reconciliation logic for tag, show, rescan, and graph
        workflows.

    Parameters:
        conn: Database connection.
        file_path: Path to an existing file.
        force_overwrite: Allow hash overwrite if mismatched.
        allow_versioning: Whether to create a new version on hash mismatch.

    Returns:
        Up-to-date artefact row reflecting any housekeeping changes.

    Side Effects:
        Reads file for hashing; may update DB path or hash; may write sidecar.
    """
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
        mode="snapshot",
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
    mode: str,
) -> dict:
    """
    Apply reconciliation after resolving a file to an artefact.

    What:
        - Normalises and updates stored paths when files move.
        - Detects hash mismatches to decide between versioning or overwrite.
        - Restores missing sidecars when identity is absent on disk.

    Why:
        Keeps resolution side-effects predictable and auditable through events.

    Parameters:
        conn: Database connection.
        artefact: Resolved artefact row.
        file_path: Current file path.
        identity_found: Whether a sidecar/embedded identity was present.
        file_hash: Freshly computed hash.
        force_overwrite: Allow hash overwrite on mismatch.
        allow_versioning: Permit automatic version creation on mismatch.
        mode: Version handling ('snapshot' or 'wip') when versioning is allowed.

    Returns:
        Updated artefact row (possibly new version).

    Side Effects:
        Writes events for moves, sidecar restoration, hash overwrite/version
        actions; may update artefacts table and rewrite sidecars.
    """
    artefact = _ensure_path(conn, artefact, file_path)
    if artefact["hash"] != file_hash:
        if not allow_versioning:
            if force_overwrite:
                raise ValueError(
                    "Hash overwrites must be performed via 'edna tag --force-overwrite'.",
                )
            return artefact
        # Hash mismatch with versioning allowed: either overwrite or create a new version downstream.
        artefact = _handle_hash_change(
            conn,
            artefact,
            file_path,
            file_hash,
            force_overwrite=force_overwrite,
            mode=mode,
        )
    else:
        write_identity(file_path, artefact["dna_token"], file_hash, artefact.get("type"), artefact["path"])
        if not identity_found:
            # If the sidecar/embedded marker was missing, rewrite it and record restoration.
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
    mode: str,
) -> dict:
    """
    Update metadata for a file already tracked by EDNA.

    What:
        Runs housekeeping, applies optional type/description/tag/project updates,
        and records an event indicating the command touched an existing record.

    Parameters:
        conn: Database connection.
        artefact: Existing artefact row.
        file_path: Current file location.
        file_hash: Computed hash.
        artefact_type: Optional type override.
        description: Optional description override.
        tags: Optional tags to add.
        project_ids: Optional projects to link.
        force_overwrite: Allow hash overwrite when versioning.
        identity_found: True if a sidecar/embedded marker was present.
        command: Name of the invoking command for event logging.
        mode: 'snapshot' to create versions on hash change or 'wip' to update in place.

    Returns:
        Updated artefact row (or new version).

    Side Effects:
        May update DB fields, attach tags/projects, and write events/sidecars.
    """
    artefact = _post_resolve_housekeeping(
        conn,
        artefact,
        file_path,
        identity_found=identity_found,
        file_hash=file_hash,
        force_overwrite=force_overwrite,
        allow_versioning=True,
        mode=mode,
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
    """
    Reconcile stored path with the current file location.

    Why:
        Files can move; EDNA normalises the incoming path and updates the DB so
        future lookups by path remain accurate.

    Parameters:
        conn: Database connection.
        artefact: Artefact row to update if needed.
        file_path: Current on-disk location.

    Returns:
        Artefact row (refreshed if a path change occurred).

    Side Effects:
        Updates artefacts.path, records a 'moved' event, and rewrites the
        sidecar later in the workflow.
    """
    norm = normalize_path(file_path)
    if artefact["path"] != norm:
        # Path normalisation ensures moves/symlinks are captured consistently before logging events.
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
    mode: str,
) -> dict:
    """
    Respond to a hash mismatch between disk and database.

    Decision points:
        - If mode is 'wip', update the existing record hash and log a wip event.
        - If force_overwrite is True, update the existing record hash and log it.
        - Otherwise, create a new version linked to the parent and emit events.

    Parameters:
        conn: Database connection.
        artefact: Existing artefact row.
        file_path: Current path for the changed file.
        new_hash: Fresh SHA-256 digest.
        force_overwrite: Whether to bypass versioning.
        mode: Versioning behaviour selector ('snapshot' or 'wip').

    Returns:
        Artefact row representing the updated or new version.

    Side Effects:
        Updates hash or inserts a child artefact; records events; writes sidecar.
    """
    valid_modes = {"snapshot", "wip"}
    if mode not in valid_modes:
        raise ValueError(f"Invalid mode '{mode}'. Expected one of {sorted(valid_modes)}.")

    if mode == "wip":
        if force_overwrite:
            raise ValueError("WIP mode cannot be combined with force_overwrite.")
        artefacts.update_hash(conn, artefact["id"], new_hash)
        artefacts.record_event(
            conn,
            artefact["id"],
            event_type="wip_saved",
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
    # Versioning is triggered on hash change unless explicitly overridden.
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
    """
    Create lineage links between a child and one or more parents.

    Parameters:
        conn: Database connection.
        child: Child artefact row.
        parents: Iterable of parent artefact rows.
        relation_type: Relation label (e.g. derived_from).
        reason: Optional rationale.

    Returns:
        None.

    Side Effects:
        Inserts edges and records a 'linked' event on the child for each parent.
    """
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
    """
    Produce a readable ancestor tree for an artefact.

    The traversal tracks visited nodes to prevent infinite recursion in cyclic
    graphs and marks repeated nodes with '*'.

    Parameters:
        conn: Database connection.
        artefact: Starting artefact row.
        depth: Current indentation depth (used during recursion).
        seen: Set of visited artefact ids to avoid cycles.

    Returns:
        List of formatted strings representing the ancestor tree.

    Side Effects:
        Reads lineage edges via database lookups.
    """
    seen = seen or set()
    lines = []
    indent = "  " * depth
    lines.append(f"{indent}- {artefact['dna_token']} ({artefact['path']})")
    if artefact["id"] in seen:
        # Recursion guard: mark already-seen nodes and stop descending to avoid loops.
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
    """
    Search artefacts by tags, type, and/or project membership.

    Parameters:
        conn: Database connection.
        tags: Optional tags to match (any).
        artefact_type: Optional type filter.
        project_id: Optional project filter.

    Returns:
        List of artefact rows matching the criteria.

    Side Effects:
        Database read with conditional joins depending on filters.
    """
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
    """
    Walk a directory tree to reconcile files and sidecars.

    What:
        Visits every non-sidecar file, resolves it to an artefact, and applies
        housekeeping (path updates, sidecar restoration, versioning decisions)
        without stopping on errors.

    Why:
        Provides a bulk-repair mechanism for missing sidecars or moved files.

    Parameters:
        conn: Database connection.
        root: Directory to scan recursively.

    Returns:
        List of DNA tokens updated during the scan.

    Side Effects:
        Reads and writes sidecars; may update DB paths/hashes/events for many
        artefacts.
    """
    root = root.expanduser().resolve()
    updated: list[str] = []
    for path in sorted(root.rglob("*")):
        if path.is_dir() or path.suffix == ".edna":
            continue
        try:
            artefact = resolve_file_reference(conn, path)
        except Exception:
            # Orphaned or untracked files are skipped so rescans remain resilient.
            continue
        updated.append(artefact["dna_token"])
    return updated


def build_lineage_graph(
    conn,
    root_artefact: dict,
    scope: str = "ancestors",
) -> tuple[dict[int, LineageNode], list[LineageEdge]]:
    """
    Build an in-memory graph of artefacts reachable from *root_artefact*.

    Parameters:
        conn: Database connection.
        root_artefact: Artefact row to anchor traversal.
        scope: 'ancestors', 'descendants', or 'full'.

    Returns:
        Tuple of node mapping and edge list suitable for rendering.

    Side Effects:
        Reads lineage edges/artefacts; tracks visited ids to avoid cycles.
    """
    valid_scopes = {"ancestors", "descendants", "full"}
    if scope not in valid_scopes:
        raise ValueError(f"Unknown scope '{scope}'. Expected one of {sorted(valid_scopes)}.")

    nodes: dict[int, LineageNode] = {}
    edges: list[LineageEdge] = []
    visited: set[int] = set()
    queue: deque[dict] = deque([root_artefact])

    # Local helper to de-duplicate nodes as the traversal walks parents/children.
    def _add_node(artefact: dict) -> None:
        if artefact["id"] not in nodes:
            nodes[artefact["id"]] = LineageNode(
                id=artefact["id"],
                dna_token=artefact["dna_token"],
                path=artefact["path"],
                type=artefact.get("type"),
            )

    while queue:
        current = queue.popleft()
        _add_node(current)
        if current["id"] in visited:
            continue
        visited.add(current["id"])

        if scope in {"ancestors", "full"}:
            parents = artefacts.list_parents(conn, current["id"])
            for parent in parents:
                _add_node(parent)
                edges.append(
                    LineageEdge(
                        parent_id=parent["id"],
                        child_id=current["id"],
                        relation_type=parent.get("relation_type"),
                        reason=parent.get("reason"),
                    )
                )
                # Queue parents to walk lineage upward without recursion overflow.
                if parent["id"] not in visited:
                    queue.append(parent)

        if scope in {"descendants", "full"}:
            children = artefacts.list_children(conn, current["id"])
            for child in children:
                _add_node(child)
                edges.append(
                    LineageEdge(
                        parent_id=current["id"],
                        child_id=child["id"],
                        relation_type=child.get("relation_type"),
                        reason=child.get("reason"),
                    )
                )
                # Queue children to follow derivations down the graph.
                if child["id"] not in visited:
                    queue.append(child)

    return nodes, edges


def format_lineage_as_mermaid(
    nodes: dict[int, LineageNode],
    edges: list[LineageEdge],
    *,
    direction: str = "TB",
) -> str:
    """
    Render a lineage graph as Mermaid flowchart markup.

    Parameters:
        nodes: Mapping of artefact id to LineageNode.
        edges: List of LineageEdge objects.
        direction: Flow direction ('TB' or 'LR').

    Returns:
        Mermaid flowchart string.

    Side Effects:
        None.
    """
    direction = direction if direction in {"TB", "LR"} else "TB"
    lines = [f"flowchart {direction}"]
    for artefact_id in sorted(nodes):
        node = nodes[artefact_id]
        label = _format_node_label(node)
        lines.append(f'    n_{node.id}["{_escape_mermaid(label)}"]')
    for edge in sorted(edges, key=lambda e: (e.parent_id, e.child_id, e.relation_type or "")):
        label = ""
        if edge.relation_type:
            relation_text = edge.relation_type.replace("|", "\\|")
            label = f"|{relation_text}|"
        lines.append(f"    n_{edge.parent_id} -->{label} n_{edge.child_id}")
    return "\n".join(lines)


def format_lineage_as_dot(
    nodes: dict[int, LineageNode],
    edges: list[LineageEdge],
    *,
    direction: str = "TB",
) -> str:
    """
    Render a lineage graph as Graphviz DOT.

    Parameters:
        nodes: Mapping of artefact id to LineageNode.
        edges: List of LineageEdge objects.
        direction: Rank direction ('TB' or 'LR').

    Returns:
        DOT source string.

    Side Effects:
        None.
    """
    direction = direction if direction in {"TB", "LR"} else "TB"
    lines = ["digraph edna_lineage {", f"    rankdir={direction};"]
    for artefact_id in sorted(nodes):
        node = nodes[artefact_id]
        label = _format_node_label(node)
        lines.append(f'    n_{node.id} [label="{_escape_dot(label)}"];')
    for edge in sorted(edges, key=lambda e: (e.parent_id, e.child_id, e.relation_type or "")):
        attrs = ""
        if edge.relation_type:
            attrs = f' [label="{_escape_dot(edge.relation_type)}"]'
        lines.append(f"    n_{edge.parent_id} -> n_{edge.child_id}{attrs};")
    lines.append("}")
    return "\n".join(lines)


def _format_node_label(node: LineageNode) -> str:
    """
    Build a concise label for a lineage node.

    Parameters:
        node: LineageNode to format.

    Returns:
        Human-readable node label including short DNA, type, and basename.

    Side Effects:
        None.
    """
    token = node.dna_token or ""
    short = token[5:] if token.startswith("edna_") else token
    short = short[:8] or token[:8] or "unknown"
    artefact_type = node.type or "n/a"
    basename = Path(node.path).name if node.path else ""
    basename = basename or node.path or "n/a"
    return f"{short} | {artefact_type} | {basename}"


def _escape_mermaid(text: str) -> str:
    """Escape text for safe inclusion in Mermaid labels."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _escape_dot(text: str) -> str:
    """Escape text for safe inclusion in DOT labels."""
    return text.replace("\\", "\\\\").replace('"', '\\"')
