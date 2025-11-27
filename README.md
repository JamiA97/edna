# EDNA (Engineering Design DNA)

EDNA is a CLI for tracking the lineage of engineering artefacts (CAD, code, reports, data) with tamper-evident DNA tokens. It records how files evolve, links parent/child derivations, and lets you explore ancestry in text, Mermaid, or DOT.

## Why EDNA?

- Audit-ready lineage: every derived artefact keeps an immutable DNA token with recorded parent links.
- Lightweight + local-first: SQLite database, optional sidecar files, zero external services.
- Practical workflows: tag files, link derivations, browse projects, render graphs, and export/import bundles.
- Safety: avoids modifying source content; metadata lives beside files.

Example lineage (conceptual):

```text
raw/geom_v1.step      ──┐
                        ├──> cfd/mesh_v1.jou ──> results/run_001_surface.csv
raw/bc_template.yaml  ──┘
```

## EDNA – Engineering Design DNA

For full project details, documentation, and examples, visit the EDNA webpage:

[Project Website](https://jamia97.github.io/edna/)

Email: [edna.dev.tool@gmail.com](mailto:edna.dev.tool@gmail.com)

## Installation

```bash
pip install eng-dna
# or from the repo
pip install -e .
```

Requirements: Python 3.10+ and a writable workspace. No non-stdlib runtime dependencies.

## Quick start

```bash
# Initialise (creates eng_dna.db in the working tree)
edna init

# Tag raw and cleaned datasets
edna tag raw/source.csv       --type dataset -d "Raw sensor data"
edna tag results/clean.csv    --type dataset -d "Cleaned dataset"

# Link derivations (child from parents)
edna link results/clean.csv --from raw/source.csv --relation derived_from

# Inspect metadata and lineage
edna show  results/clean.csv
edna trace results/clean.csv

# Visualise lineage (Mermaid to stdout)
edna graph results/clean.csv

# Open an interactive browser view (Mermaid)
edna graph results/clean.csv --scope full --direction LR --view

# Export/import a project bundle
edna export --project demo --output edna_lineage_demo.json
edna import edna_lineage_demo.json
```

## Core concepts

- **DNA token**: stable identifier for a file version; changes when content changes.
- **Sidecar**: optional `.edna` file beside artefacts to mirror DB metadata.
- **Lineage edges**: parent → child links with relation and optional reason.
- **Projects/Tags**: lightweight grouping and filtering.

## Commands (overview)

- `edna init [--path DIR]` — create or reuse `eng_dna.db`.
- `edna tag FILE [--type ...] [-d ...] [--tag ...] [--project ...] [--mode snapshot|wip]`
- `edna show TARGET` — display metadata for a file or DNA token.
- `edna link CHILD --from PARENT [--relation ...] [--reason ...]`
- `edna unlink CHILD --from PARENT [--relation ...] [--dry-run]`
- `edna trace TARGET` — ancestor walk (cycle-safe).
- `edna graph TARGET [--format mermaid|dot] [--scope ancestors|descendants|full] [--direction TB|LR] [--view]`
- `edna search [--tag ...] [--type ...] [--project ...]`
- `edna project ...` — manage projects (add/list/show/files/delete).
- `edna export --project ID [-o FILE]` / `edna import FILE [--dry-run]`
- `edna rescan [ROOT]` — reconcile sidecars/paths/hashes under a tree.

## Typical workflow

1) `edna init`  
2) `edna tag file.ext --type ...`  
3) `edna link derived.ext --from source.ext --relation derived_from`  
4) `edna show/trace/graph derived.ext`  
5) Share lineage: `edna export --project demo` (and `edna import ...` elsewhere)

## Example: CAD → mesh → CFD results

Here’s a minimal, realistic scenario for tracking a CFD study:

```bash
# 1) Initialise the database in your study folder
edna init

# 2) Tag the source geometry and boundary conditions
edna tag geom/rotor_v3.step      --type cad     -d "Rotor v3 baseline"
edna tag bc/rotor_v3_bc.yaml     --type config  -d "Rotor v3 BC template"

# 3) Tag the mesh setup and link it back to its inputs
edna tag cfd/mesh_v3.jou         --type script  -d "Mesh script for rotor v3"
edna link cfd/mesh_v3.jou \
    --from geom/rotor_v3.step \
    --from bc/rotor_v3_bc.yaml \
    --relation derived_from

# 4) After running CFD, tag the results and link them to the mesh
edna tag results/rotor_v3_run001_surface.csv \
    --type data \
    -d "Surface fields for rotor v3, run 001"

edna link results/rotor_v3_run001_surface.csv \
    --from cfd/mesh_v3.jou \
    --relation derived_from

# 5) Inspect and visualise the lineage
edna trace  results/rotor_v3_run001_surface.csv
edna graph  results/rotor_v3_run001_surface.csv --scope full --direction LR --view
```

## Philosophy & safety notes

- Metadata lives outside content; EDNA won’t rewrite your artefacts.
- Lineage is explicit: links are created by you, never inferred silently.
- Sidecars are recoverable from the DB; corrupt sidecars are ignored and repaired.
- Browser graph view uses Mermaid from a public CDN; offline use still works via Mermaid/DOT text output.

## FAQ (short)

- **How do I see the graph fast?** `edna graph <file> --view` opens a temp HTML in your browser; requires internet for the Mermaid CDN.
- **What if files move?** `edna show/tag/rescan` updates stored paths and logs a `moved` event; DNA tokens stay stable.
- **Can I overwrite DNA tokens?** No—new content gets a new DNA and edges preserve history.
- **How to work offline?** Use `edna graph ...` (Mermaid/DOT text) and render with your own toolchain.

## Contributing

- Run tests: `pytest`
- Style: standard library only; keep CLI thin and delegate to `operations`/`sync`.
- Open issues/PRs with clear repros and expected behaviour.
