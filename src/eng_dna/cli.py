"""Typer-based CLI entrypoints for EDNA.

The CLI is intentionally thin: it parses arguments, opens the database, and
delegates behaviour to ``operations`` or ``sync``. Docstrings capture the
intentional coupling between commands and the higher-level workflows they
trigger (tagging, lineage, export/import).
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Optional, List

import typer
import click

from . import artefacts, operations, sync
from .db import connect, ensure_schema, init_db, resolve_db_path

app = typer.Typer(help="Engineering Memory / Design Lineage CLI")
project_app = typer.Typer(help="Project management commands")
app.add_typer(project_app, name="project")


@app.callback()
def main(ctx: typer.Context, db: Optional[Path] = typer.Option(None, help="Path to eng_dna.db")) -> None:
    """
    Configure shared CLI context.

    What:
        Stores the optional database override in the Typer context so commands
        can reuse consistent DB resolution rules.

    Parameters:
        ctx: Typer context object.
        db: Optional explicit path to the database file.

    Returns:
        None.

    Side Effects:
        None; prepares context only.
    """
    ctx.obj = {"db": db}


@app.command()
def init(path: Optional[Path] = typer.Option(None, "--path", help="Directory for eng_dna.db")) -> None:
    """
    Initialise a new EDNA database in the given directory or nearest default.

    Creates the file if missing and exits early when the target already exists.

    Parameters:
        path: Optional directory to place ``eng_dna.db``.

    Returns:
        None.

    Side Effects:
        Writes a SQLite database file and schema when needed.
    """
    target: Path
    if path:
        target = path.expanduser().resolve() / "eng_dna.db"
    else:
        target = resolve_db_path(require_exists=False)
    if target.exists():
        typer.echo(f"Database already exists at {target}")
        return
    init_db(target)
    typer.echo(f"Initialised database at {target}")


@app.command()
def tag(
    file: Path = typer.Argument(..., help="File to tag"),
    artefact_type: Optional[str] = typer.Option(None, "--type", help="Artefact type"),
    description: Optional[str] = typer.Option(None, "-d", "--description", help="Description"),
    tags: List[str] = typer.Option([], "--tag", help="Add tag (repeatable)"),
    projects: List[str] = typer.Option([], "--project", help="Assign to project"),
    force_overwrite: bool = typer.Option(False, "--force-overwrite", help="Overwrite hash instead of versioning"),
) -> None:
    """
    Tag a file with EDNA metadata and optionally attach tags/projects.

    Delegates to ``operations.tag_file`` which encapsulates versioning rules and
    sidecar management.

    Parameters:
        file: Target file to tag.
        artefact_type: Optional type label.
        description: Optional description.
        tags: Repeated tag options.
        projects: Repeated project identifiers.
        force_overwrite: Overwrite stored hash instead of creating a version.

    Returns:
        None.

    Side Effects:
        May write sidecar, create/update DB records, and emit events.
    """
    with _db() as conn:
        result = operations.tag_file(
            conn,
            file,
            artefact_type=artefact_type,
            description=description,
            tags=list(tags) or None,
            project_ids=list(projects) or None,
            force_overwrite=force_overwrite,
        )
        _print_brief(result)


@app.command()
def show(
    target: str = typer.Argument(..., help="File path or DNA token"),
    force_overwrite: bool = typer.Option(False, "--force-overwrite", help="Overwrite hash on mismatch"),
) -> None:
    """
    Display metadata for an artefact referenced by path or DNA.

    Uses the resolver to repair missing sidecars/paths but never overwrites
    hashes in show mode.

    Parameters:
        target: File path, stored path, or DNA token.
        force_overwrite: Unsupported for show; validated for user feedback.

    Returns:
        None.

    Side Effects:
        May update sidecar/path metadata during resolution.
    """
    if force_overwrite:
        raise typer.BadParameter("--force-overwrite is not supported for 'show'; use 'edna tag' instead.")
    with _db() as conn:
        artefact = operations.show_target(conn, target, force_overwrite=force_overwrite)
        _print_artefact(conn, artefact)


@app.command()
def link(
    child: str = typer.Argument(..., help="Child file or DNA"),
    parents: List[str] = typer.Option([], "--from", help="Parent(s) to link from"),
    relation_type: str = typer.Option("derived_from", "--relation", help="Relation type"),
    reason: Optional[str] = typer.Option(None, "--reason", help="Reason for the link"),
) -> None:
    """
    Link a child artefact to one or more parents with a relation label.

    Parameters:
        child: Child file or DNA token.
        parents: Parent references to link from.
        relation_type: Relation label (e.g. derived_from).
        reason: Optional rationale to store alongside the edge.

    Returns:
        None.

    Side Effects:
        Writes lineage edges and events to the database.
    """
    with _db() as conn:
        child_art, _ = operations.resolve_target(conn, child)
        parent_arts = [operations.resolve_target(conn, parent)[0] for parent in parents]
        operations.link_artefacts(conn, child_art, parent_arts, relation_type, reason)
        typer.echo(
            f"Linked {child_art['dna_token']} to {[parent['dna_token'] for parent in parent_arts]}"
        )


@app.command()
def trace(target: str = typer.Argument(..., help="File or DNA")) -> None:
    """
    Print a simple ancestor trace for an artefact.

    Parameters:
        target: File path or DNA token to trace from.

    Returns:
        None.

    Side Effects:
        Reads lineage information; may trigger housekeeping when resolving files.
    """
    with _db() as conn:
        artefact, _ = operations.resolve_target(conn, target)
        for line in operations.trace_ancestors(conn, artefact):
            typer.echo(line)


@app.command()
def graph(
    target: str = typer.Argument(..., help="File path or DNA token"),
    fmt: str = typer.Option("mermaid", "--format", "-f", help="Output format: mermaid or dot"),
    scope: str = typer.Option("ancestors", "--scope", help="Scope: ancestors, descendants, or full"),
    direction: str = typer.Option("TB", "--direction", help="Graph direction: TB or LR"),
) -> None:
    """
    Render a lineage graph rooted at a target artefact.

    Parameters:
        target: File path or DNA token.
        fmt: Output format ('mermaid' or 'dot').
        scope: Graph scope (ancestors/descendants/full).
        direction: Graph layout direction.

    Returns:
        None.

    Side Effects:
        Reads lineage data; may repair sidecars when resolving the target file.
    """
    format_opt = fmt.lower()
    if format_opt not in {"mermaid", "dot"}:
        raise typer.BadParameter("Invalid --format. Choose 'mermaid' or 'dot'.")
    scope_opt = scope.lower()
    if scope_opt not in {"ancestors", "descendants", "full"}:
        raise typer.BadParameter("Invalid --scope. Choose 'ancestors', 'descendants', or 'full'.")
    direction_opt = direction.upper()
    if direction_opt not in {"TB", "LR"}:
        raise typer.BadParameter("Invalid --direction. Choose 'TB' or 'LR'.")

    with _db() as conn:
        artefact, _ = operations.resolve_target(conn, target)
        nodes, edges = operations.build_lineage_graph(conn, artefact, scope=scope_opt)
        if format_opt == "mermaid":
            output = operations.format_lineage_as_mermaid(nodes, edges, direction=direction_opt)
        else:
            output = operations.format_lineage_as_dot(nodes, edges, direction=direction_opt)
        typer.echo(output)


@app.command()
def search(
    tags: List[str] = typer.Option([], "--tag", help="Filter by tag"),
    artefact_type: Optional[str] = typer.Option(None, "--type", help="Filter by type"),
    project_id: Optional[str] = typer.Option(None, "--project", help="Filter by project"),
) -> None:
    """
    Search artefacts by tags, type, and/or project membership.

    Parameters:
        tags: Repeatable tag filter.
        artefact_type: Optional type filter.
        project_id: Optional project filter.

    Returns:
        None.

    Side Effects:
        Database read only.
    """
    with _db() as conn:
        results = operations.search_artefacts(
            conn,
            tags=list(tags) or None,
            artefact_type=artefact_type,
            project_id=project_id,
        )
        if not results:
            typer.echo("No artefacts matched your query")
            return
        for artefact in results:
            typer.echo(f"{artefact['dna_token']} | {artefact['type'] or 'n/a'} | {artefact['path']}")


@app.command()
def rescan(root: Optional[Path] = typer.Argument(None, help="Root directory")) -> None:
    """
    Rescan a directory tree to repair sidecars and reconcile paths.

    Parameters:
        root: Optional root path (defaults to cwd).

    Returns:
        None.

    Side Effects:
        Walks the filesystem, hashes files, and may update DB metadata/events.
    """
    with _db() as conn:
        target = root.expanduser().resolve() if root else Path.cwd()
        tokens = operations.rescan_tree(conn, target)
        typer.echo(f"Updated {len(tokens)} artefacts while scanning {target}")


@app.command()
def export(
    project_id: str = typer.Option(..., "--project", help="Project id to export"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output JSON file"),
) -> None:
    """
    Export a project's lineage closure as portable JSON.

    Parameters:
        project_id: Project identifier.
        output: Optional output file path.

    Returns:
        None.

    Side Effects:
        Reads database; writes JSON bundle to disk.
    """
    with _db() as conn:
        bundle = sync.export_project_lineage(conn, project_id)
    output_path = output or (Path.cwd() / f"edna_lineage_{project_id}.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as fh:
        json.dump(bundle, fh, indent=2)
        fh.write("\n")
    typer.echo(
        f"Exported lineage for project {project_id} to {output_path} "
        f"(artefacts: {len(bundle['artefacts'])}, edges: {len(bundle['edges'])}, events: {len(bundle['events'])})"
    )


@app.command(name="import")
def import_lineage(
    bundle_path: Path = typer.Argument(..., help="Path to lineage JSON bundle"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Do not modify database"),
) -> None:
    """
    Import a lineage bundle produced by ``edna export``.

    Parameters:
        bundle_path: Path to JSON bundle.
        dry_run: If True, validate without writing changes.

    Returns:
        None.

    Side Effects:
        Reads bundle from disk; may write to database unless dry-run.
    """
    try:
        data = json.loads(bundle_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:  # pragma: no cover - CLI entrypoint
        raise typer.BadParameter(f"Bundle not found at {bundle_path}") from exc
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"Invalid lineage JSON: {exc}") from exc
    with _db() as conn:
        result = sync.import_lineage(conn, data, dry_run=dry_run)
    if dry_run:
        typer.echo("Dry run: no database changes applied.")
    typer.echo(f"Projects: {result['projects_new']} new, {result['projects_existing']} existing")
    typer.echo(f"Artefacts: {result['artefacts_new']} new, {result['artefacts_existing']} existing")
    typer.echo(f"Tags: {result['tags_inserted']} inserted, {result['tags_skipped']} skipped")
    typer.echo(f"Notes: {result['notes_inserted']} inserted, {result['notes_skipped']} skipped")
    typer.echo(f"Events: {result['events_inserted']} inserted, {result['events_skipped']} skipped")
    typer.echo(f"Edges: {result['edges_inserted']} inserted, {result['edges_skipped']} skipped")
    typer.echo(f"Artefact-project links: {result['links_inserted']} inserted, {result['links_skipped']} skipped")


@project_app.command("add")
def project_add(
    project_id: str = typer.Argument(..., help="Project id"),
    name: str = typer.Option(..., "--name", help="Project name"),
    description: Optional[str] = typer.Option(None, "--description", help="Description"),
) -> None:
    """
    Create a new project record.

    Parameters:
        project_id: Stable project identifier.
        name: Project name.
        description: Optional description.

    Returns:
        None.

    Side Effects:
        Inserts into the projects table.
    """
    with _db() as conn:
        project = artefacts.create_project(conn, project_id, name, description)
        typer.echo(f"Project {project['id']} created")


@project_app.command("show")
def project_show(project_id: str = typer.Argument(..., help="Project id")) -> None:
    """
    Display summary information for a project.

    Parameters:
        project_id: Project identifier.

    Returns:
        None.

    Side Effects:
        Reads project and artefact linkage information.
    """
    with _db() as conn:
        project = artefacts.get_project(conn, project_id)
        if not project:
            raise typer.BadParameter(f"Unknown project {project_id}")
        typer.echo(f"{project['id']}: {project['name']}")
        typer.echo(project.get("description") or "(no description)")
        files = artefacts.list_project_files(conn, project_id)
        typer.echo(f"Artefacts: {len(files)}")


@project_app.command("files")
def project_files(project_id: str = typer.Argument(..., help="Project id")) -> None:
    """
    List artefacts attached to a project.

    Parameters:
        project_id: Project identifier.

    Returns:
        None.

    Side Effects:
        Database read only.
    """
    with _db() as conn:
        files = artefacts.list_project_files(conn, project_id)
        if not files:
            typer.echo("No artefacts linked to this project")
            return
        for artefact in files:
            typer.echo(f"{artefact['dna_token']} | {artefact['path']}")


def _print_artefact(conn, artefact: dict) -> None:
    """
    Render detailed artefact information for CLI output.

    Parameters:
        conn: Database connection for fetching tags/projects/events.
        artefact: Artefact row to display.

    Returns:
        None.

    Side Effects:
        Database reads for related data; writes to stdout.
    """
    typer.echo(f"DNA: {artefact['dna_token']}")
    typer.echo(f"Path: {artefact['path']}")
    typer.echo(f"Hash: {artefact['hash']}")
    typer.echo(f"Type: {artefact.get('type') or 'n/a'}")
    typer.echo(f"Description: {artefact.get('description') or 'n/a'}")
    tags = artefacts.list_tags(conn, artefact["id"])
    typer.echo(f"Tags: {', '.join(tags) if tags else 'n/a'}")
    projects = artefacts.list_projects(conn, artefact["id"])
    typer.echo("Projects: " + (", ".join([p["id"] for p in projects]) if projects else "n/a"))
    events = artefacts.list_events(conn, artefact["id"])
    for event in events[:5]:
        typer.echo(
            f"- {event['created_at']}: {event['event_type']}"
            + (f" ({event.get('description')})" if event.get("description") else "")
        )


def _print_brief(artefact: dict) -> None:
    """
    Render minimal artefact information for CLI output.

    Parameters:
        artefact: Artefact row to display.

    Returns:
        None.

    Side Effects:
        Writes to stdout.
    """
    typer.echo(f"DNA: {artefact['dna_token']}")
    typer.echo(f"Path: {artefact['path']}")
    typer.echo(f"Hash: {artefact['hash']}")


@contextmanager
def _db():
    """
    Context manager that resolves and opens the EDNA database.

    Applies schema migrations on open to guarantee tables exist before command
    logic executes.

    Yields:
        SQLite connection with schema ensured.

    Side Effects:
        May create database file and schema; closes connection on exit.
    """
    ctx = click.get_current_context()
    db_opt = ctx.obj.get("db") if ctx.obj else None
    path = resolve_db_path(explicit_path=str(db_opt) if db_opt else None)
    conn = connect(path)
    ensure_schema(conn)
    try:
        yield conn
    finally:
        conn.close()


if __name__ == "__main__":  # pragma: no cover
    app()
