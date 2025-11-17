1. Concept & Goals

Working name: eng-dna – Engineering Memory System

Core idea:
Attach a compact, opaque “digital DNA” token to every engineering artefact (geometry file, report, test dataset, script, etc.). That DNA links the artefact into a lineage graph stored in a small database. An AI layer then:

Reconstructs the design history behind any artefact.

Answers questions like “What is this file, where did it come from, and what else depends on it?”

Produces human-readable lineage summaries on demand.

Design principles:

Minimal friction: tagging should be a single CLI call integrated into existing workflows.

Local-first: runs on a laptop with SQLite; no cloud dependency required.

Non-human-readable DNA: token looks like noise, but is decodable by the system.

Tech-agnostic: works with CAD, CFD, scripts, reports, CSVs, etc.

2. Core Concepts

Artefact

Any file you care about: CAD export, JSON geometry, PDF report, CSV test results.

Identified by path, hash, and a DNA token.

DNA Token
Identity Model

Each artefact has three related identifiers:

DNA token (primary identity)

dna_token is the canonical identity of an artefact.

It does not change if the file is moved or renamed.

Hash (version identity)

Cryptographic hash (e.g. SHA256) of the file contents.

A changed hash means the contents have changed; by default this should create a new artefact (new dna_token) derived from the previous one.

Path (mutable location)

Stored as convenience metadata.

Can change freely (move/rename).

The system updates stored paths when artefacts are re-discovered.

In short:

DNA = who this artefact is.

Hash = which version of it this is.

Path = where it lives right now.

Short opaque string, e.g. edna_9f82a3b1-74c4-4ad7-b9b0-8e7c6c5f2b3a.

Stored inside the file where possible (comment/metadata), or in a sidecar file (.edna).

Maps to a record in the lineage store.

Lineage Graph

Nodes: artefacts, people, processes, decisions.

Edges: “derived from”, “used in”, “validated by”, “supersedes”, etc.

This is the truth behind the DNA token.

Events

Actions that modify lineage: “created”, “derived”, “validated”, “deprecated”.

Each event links artefacts together and logs context (who, when, why).

AI Layer

Builds summaries of a node or subgraph.

Answers natural language queries over the lineage graph + artefact metadata.



3. User Stories (High-Level)

As an engineer, I want to tag a new file with a DNA token and a short description so its origin and purpose are never lost.

As a designer, I want to link a derivative file to its parent(s) so I can see its design lineage.

As a reviewer, I want to ask in natural language “what’s the story behind this file?” and get a coherent summary.

As a team lead, I want to see all artefacts related to a project or concept (e.g. “X15 turbine rev 3 backsweep study”).

As a quality/audit user, I want to export a lineage report for a final artefact showing all key ancestors and decisions.

4. System Architecture (MVP)
4.1 Components

Local Lineage Store

SQLite DB (eng_dna.db).

Tables for artefacts, events, edges, tags, and notes.

CLI Tool (edna)

edna init – initialise DB in a workspace.

edna tag <file> ... – create DNA and artefact entry.

edna link <child> --from <parent1> [--from <parent2> ...] --reason "...".

edna show <file_or_dna> – show raw lineage info.

edna trace <file_or_dna> – show ancestor/descendant tree in text.

edna embed <file> – write DNA into file (if supported) or sidecar.

edna project add <id> --name "..."

edna project show <id> – show project summary and all linked artefacts.

edna project files <id> – list artefacts in the project.

edna tag <file> ... --project <id> – assign artefact to one or more projects at creation time.

edna search --project <id> [...] – filter search results by project.

edna rescan

Walks a directory tree looking for:

Files with embedded DNA

.edna sidecar files

Known files by hash

Updates:

Artefact path fields when files have moved.

Missing sidecars (recreate from DB).

Flags orphaned DB entries (artefacts whose files no longer exist).

This command allows EDNA to heal itself after large refactors or filesystem rearrangements.

Metadata Encoder/Decoder

Abstraction layer to:

Insert DNA in file comments (e.g. # EDNA: <token> in .py).

Insert in JSON/YAML under a reserved key.

Use sidecar .filename.edna for binary formats.

Sidecar .edna Files

For file types that cannot or should not be modified (e.g. most CAD/CFD/FEA formats), EDNA uses a sidecar file:

For filename.ext, the sidecar is filename.ext.edna.

Sidecars are small JSON files, e.g.:

{
  "dna": "edna_218f0d9e-93c1-4b48-abc3-0aef81b8041a",
  "hash": "sha256:...",
  "type": "cad",
  "path": "geometry/rotor_rev3.step",
  "created_at": "2025-05-22T10:15:36Z"
}


Sidecars act as an external identity wrapper for the artefact; the authoritative truth remains in the SQLite DB.

Runtime behaviour:

When tagging a file:

If a handler exists and is marked “safe to embed”, EDNA writes the DNA into the file (comment, metadata field, etc.).

Otherwise, EDNA creates or updates filename.ext.edna as the identity sidecar.

When reading a file:

EDNA first looks for an embedded DNA or sidecar.

If neither is found, EDNA hashes the file and attempts to match it to a known artefact by hash.

If a match is found, EDNA can regenerate the sidecar automatically.

Sidecars are expected to move together with their parent file, but the system is robust if they become separated (see rescan behaviour).

AI Service Layer (Optional in MVP but designed-in)

Simple Python module wrapping an LLM client.

Takes a subgraph + metadata and produces:

Summaries.

Natural-language answers.

“Design story” narrative.

(Optional) Local Web UI

Minimal FastAPI + simple frontend.

View/search lineage graph.

Click artefacts → see AI-generated summary and references.

5. Data Model (MVP)
5.1 Tables

artefacts

id (PK)

dna_token (unique)

path (relative path)

hash (e.g. SHA256 of contents)

type (e.g. geometry, report, test-data, script, config)

created_at

created_by (string, not auth – just user name/email)

description (short human description)

events

id (PK)

artefact_id (FK to artefacts)

event_type (created, derived, validated, deprecated, etc.)

timestamp

actor (string)

message (free text – explanation, decision, context)

edges

id (PK)

parent_id (FK to artefacts)

child_id (FK to artefacts)

relation_type (derived_from, uses, validated_by, etc.)

tags

id (PK)

artefact_id (FK)

tag (string; e.g. turbine, backsweep, X15, CFD, surge)

notes

id (PK)

artefact_id (FK)

note (free text)

created_at

author

projects

id (PK, string or integer; e.g. "888567")

name (short human name, e.g. "X15 turbine optimisation")

description

created_at

artefact_projects (join table)

id (PK)

artefact_id (FK → artefacts)

project_id (FK → projects)

This supports workflows such as:

“Given this file, which project(s) does it belong to?”

“List all artefacts for project 888567.”

Project membership does not depend on path; it is purely logical.


Later, you can add projects, releases, risk_items, etc.

6. Key Workflows
6.1 Tagging a New Artefact

User creates a file (e.g. rotor_design_v3.json).

Run:

edna tag rotor_design_v3.json \
    --type geometry \
    --desc "Rotor rev3: increased backsweep for higher efficiency" \
    --tag turbine --tag backsweep --tag concept-A


System:

Computes file hash.

Creates artefacts row + created event.

Generates dna_token.

Embeds token into the file (or sidecar).

Output: prints DNA token and maybe a short lineage summary.

6.2 Creating a Derived Artefact

Take parent file rotor_design_v3.json → produce rotor_design_v3_cfd_setup.py.

Run:

edna tag rotor_design_v3_cfd_setup.py \
    --type script \
    --desc "CFD setup script for rotor rev3"
edna link rotor_design_v3_cfd_setup.py \
    --from rotor_design_v3.json \
    --relation derived_from \
    --reason "CFD preparation for geometry validation"


System:

Tags script.

Adds edges row derived_from.

Adds event with reason.

6.3 Tracing Lineage

Run:

edna trace rotor_design_v3_cfd_results.csv


CLI prints a tree, e.g.:

rotor_design_v3_cfd_results.csv [test-data, dna: edna_xxx]
  └─ derived_from rotor_design_v3_cfd_setup.py [script, edna_yyy]
       └─ derived_from rotor_design_v3.json [geometry, edna_zzz]


Optionally, call AI layer for a human summary:

edna explain rotor_design_v3_cfd_results.csv


→ LLM: “This CSV contains CFD results for Rotor Rev3, which increased backsweep by X degrees to target higher efficiency at PR=2.5. It was derived from the CFD setup script ... which in turn is based on the geometry ... created on <date> as part of project <tag>.”

6.4 Search & Query

Text search: edna search --tag backsweep --type geometry

Natural language (with AI):
edna ask "Show me all artefacts involved in surge validation for X15 turbine rev3"
6.5 Moving or Renaming Files

EDNA must be robust to files being moved or renamed in the filesystem.

Case A: File and sidecar moved together

Example:

Before: geometry/rotor_rev3.step + geometry/rotor_rev3.step.edna

After: archive/2024/rotor_rev3.step + archive/2024/rotor_rev3.step.edna

Behaviour when a command is run on the moved file:

EDNA reads DNA from the file or sidecar.

Looks up the artefact in the DB.

Computes the file hash to confirm identity.

Detects that the stored path differs from the current location.

Updates the path field and logs a moved event.

Case B: File moved, sidecar not moved

EDNA attempts to read DNA; if no sidecar or embedded DNA is found:

Computes file hash.

If the hash matches a known artefact, EDNA re-associates the file with that artefact.

Recreates the .edna sidecar in the new location.

Logs a sidecar_restored event.

In both cases, project links and lineage are unaffected, because they are tied to the artefact’s id / dna_token, not to the file path.

6.6 Detecting Content Changes and Creating New Versions

When EDNA operates on a file whose contents have changed since the last known state:

It detects a hash mismatch between the current file and the stored hash.

Default behaviour (configurable):

Prompt the user (or obey a CLI flag) to either:

Create a new artefact version:

New dna_token.

New row in artefacts with current hash and path.

Add an edges row linking the new artefact to the previous one with relation_type = "derived_from".

Or overwrite the existing hash (discouraged except for early prototyping).

Creating a new version ensures proper design lineage is preserved.

7. AI Roles (Phase 2+)

Once the core lineage system is stable, the AI layer can do more interesting things:

Lineage Summarisation

Summarise a node or path: “Summarise the evolution from initial geometry to final test report.”

Semantic Search

Use embeddings of descriptions/notes/tags to handle fuzzy queries like “early compressor studies for hydrogen engines”.

Risk / Gap Highlighting (Longer Term)

“Show artefacts that are used in final designs but never validated.”

“Flag geometry files that have no trace to test data.”

Report Generation

One command to generate a PDF/Markdown report of design lineage for audits, customers, or internal reviews.

8. Implementation Phases
Phase 0 – Skeleton

Repo scaffold (src/, tests/, README, cli/).

SQLite schema + migration script.

Minimal edna init + edna tag + edna show.

Phase 1 – Core Lineage

Implement edges and events.

Implement edna link, edna trace.

Basic tag + text search.

File embedding for common text formats.

Phase 2 – AI Summary

Simple Python module using an LLM.

edna explain and edna ask commands.

Config for model + API keys.

Phase 3 – Visualisation / UI

Simple web UI with:

Artefact list and search.

Node detail page with AI summary.

Lineage graph visualisation.

Phase 4 – Enterprise Features (optional)

Multi-repo support.

Shared network DB.

Export/import of lineage.

Integration hooks (Git pre-commit, CI scripts).

9. Possible Tech Stack

Language: Python 3.12+

DB: SQLite with SQLAlchemy or simple raw SQL.

CLI: typer or click.

Web API: FastAPI (later).

Graph Visualisation (UI): D3.js or a lightweight frontend.
