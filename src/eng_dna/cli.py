"""Typer-based CLI for the eng-dna tool."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Optional, List

import typer
import click

from . import artefacts, operations
from .db import connect, ensure_schema, init_db, resolve_db_path

app = typer.Typer(help="Engineering Memory / Design Lineage CLI")
project_app = typer.Typer(help="Project management commands")
app.add_typer(project_app, name="project")


@app.callback()
def main(ctx: typer.Context, db: Optional[Path] = typer.Option(None, help="Path to eng_dna.db")) -> None:
    ctx.obj = {"db": db}


@app.command()
def init(path: Optional[Path] = typer.Option(None, "--path", help="Directory for eng_dna.db")) -> None:
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
    with _db() as conn:
        child_art, _ = operations.resolve_target(conn, child)
        parent_arts = [operations.resolve_target(conn, parent)[0] for parent in parents]
        operations.link_artefacts(conn, child_art, parent_arts, relation_type, reason)
        typer.echo(
            f"Linked {child_art['dna_token']} to {[parent['dna_token'] for parent in parent_arts]}"
        )


@app.command()
def trace(target: str = typer.Argument(..., help="File or DNA")) -> None:
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
    with _db() as conn:
        target = root.expanduser().resolve() if root else Path.cwd()
        tokens = operations.rescan_tree(conn, target)
        typer.echo(f"Updated {len(tokens)} artefacts while scanning {target}")


@project_app.command("add")
def project_add(
    project_id: str = typer.Argument(..., help="Project id"),
    name: str = typer.Option(..., "--name", help="Project name"),
    description: Optional[str] = typer.Option(None, "--description", help="Description"),
) -> None:
    with _db() as conn:
        project = artefacts.create_project(conn, project_id, name, description)
        typer.echo(f"Project {project['id']} created")


@project_app.command("show")
def project_show(project_id: str = typer.Argument(..., help="Project id")) -> None:
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
    with _db() as conn:
        files = artefacts.list_project_files(conn, project_id)
        if not files:
            typer.echo("No artefacts linked to this project")
            return
        for artefact in files:
            typer.echo(f"{artefact['dna_token']} | {artefact['path']}")


def _print_artefact(conn, artefact: dict) -> None:
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
    typer.echo(f"DNA: {artefact['dna_token']}")
    typer.echo(f"Path: {artefact['path']}")
    typer.echo(f"Hash: {artefact['hash']}")


@contextmanager
def _db():
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
