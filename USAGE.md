# edna CLI usage

## Quickstart

```
# Initialise the workspace database
$ edna init
```

## Practical examples

Tagging diverse files:

```
$ edna tag cad/wing.sldprt --type cad --project aero --tag baseline --description "Initial loft"
$ edna tag cfd/run1.jou --type cfd --project aero --tag mesh-v1
$ edna tag reports/loads.md --type report --tag review --project aero
$ edna tag scripts/cleanup.py --type script --tag util
```

Creating derived artefacts and linking parents:

```
# Post-processing script produces a CSV derived from the CFD journal output
$ edna tag results/run1_pressures.csv --type data --tag post --project aero
$ edna link results/run1_pressures.csv --from cfd/run1.jou --relation derived_from --reason "post-processing"
```

Unlinking mistaken lineage:

```
# Create a derived artefact and link it
$ edna tag results/run1_pressures.csv --type data --tag post --project aero
$ edna link results/run1_pressures.csv --from cfd/run1.jou --relation derived_from --reason "post-processing"

# If the link was made in error, remove it
$ edna unlink results/run1_pressures.csv --from cfd/run1.jou --relation derived_from

# Preview what would be unlinked without changing the DB
$ edna unlink results/run1_pressures.csv --from cfd/run1.jou --relation derived_from --dry-run
```

`unlink` removes edges from the lineage graph while recording an `unlinked` event on the child for auditability.

Showing artefacts by DNA token:

```
$ edna show edna_c13baf1c2c9a4c3f9d81a3b0d6b2123a
DNA: edna_c13baf1c...
Path: /workspace/cfd/run1.jou
...
```

Full example: modify a file → automatic version creation:

```
# Tag the original report
$ edna tag reports/day1.md --type report --tag baseline

# Edit the file in an editor, then retag
$ edna tag reports/day1.md --type report --tag revised
# Output shows a new DNA token; EDNA linked it to the original as a new version

# Iterating quickly without version spam (work-in-progress mode)
$ edna tag scripts/cleanup.py --type script --mode wip
# ...edit and save multiple times...
$ edna tag scripts/cleanup.py --type script --mode wip
# When the draft is meaningful again, capture a snapshot
$ edna tag scripts/cleanup.py --type script --mode snapshot
```

Full example: move a file → EDNA updates path:

```
$ mv results/run1_pressures.csv archive/run1_pressures.csv
$ edna show archive/run1_pressures.csv
# EDNA normalises the new path, logs a move event, and rewrites the sidecar
```

Searching across tags, types, and projects:

```
# Narrow by tag + project
$ edna search --tag baseline --project aero

# Narrow by type across projects
$ edna search --type cad
```

“Day in the life” – designer iterating a geometry:

```
$ edna init
$ edna project add aero --name "Wing Study"
$ edna tag cad/wing.sldprt --type cad --tag v1 --project aero
$ edna tag cfd/mesh.jou --type cfd --project aero --tag v1
$ edna link cfd/mesh.jou --from cad/wing.sldprt --relation derived_from --reason "meshing"
# Designer tweaks geometry
$ edna tag cad/wing.sldprt --type cad --tag v2
# Version automatically created; retag CFD input and link again
$ edna tag cfd/mesh.jou --type cfd --tag v2 --force-overwrite
$ edna link cfd/mesh.jou --from cad/wing.sldprt --relation derived_from --reason "meshing update"
$ edna graph cad/wing.sldprt --scope full --direction LR > lineage.mmd
```

## Project deletion (dangerous)

Delete a project and detach its artefacts:

```bash
# Preview (no changes)
edna project delete demo --dry-run

# Delete the project and detach artefacts (requires --force)
edna project delete demo --force
```

Optionally delete .edna sidecars for artefacts that belong only to this project:

```bash
# Preview which sidecars would be removed
edna project delete demo --purge-sidecars --dry-run

# Actually delete the project and the exclusive sidecars
edna project delete demo --purge-sidecars --force
```

Notes:

Artefacts are not deleted; they remain in the database.

Sidecars are only purged for artefacts that are not linked to any other project.

## Pathology cases and recovery

```
# Missing sidecar → automatic recreation on show/tag/rescan
$ rm reports/day1.md.edna
$ edna show reports/day1.md  # sidecar is rewritten and a restoration event is logged

# Retagging same content → same DNA (hash match, no new version)
$ edna tag reports/day1.md --tag review

# Forcing a hash overwrite (replace content but keep DNA)
$ edna tag reports/day1.md --force-overwrite --description "hotfix without new version"

# Work-in-progress saves keep the same DNA but log wip events (no new edges)
$ edna tag reports/day1.md --mode wip
# --mode wip already updates the current artefact; do not combine with --force-overwrite

# Corrupted sidecar (invalid JSON) → EDNA ignores it and rehydrates from DB/hash
$ printf '{bad json' > reports/day1.md.edna
$ edna show reports/day1.md  # repairs sidecar using DB data
```

## Lineage diagram (conceptual)

```
Projects: [aero]

edna_parent1 ---- derived_from ----> edna_childA
     |                                   |
     | linked (relation=notes)           | derived_from
     v                                   v
  events (created, moved, versioned)  edna_childB

Tags hang off artefacts; edges capture parent → child lineage.
```

## Export, trace, and graph

```
# Trace a file's ancestors (cycle-safe)
$ edna trace results/new.csv

# Visual lineage export
$ edna graph results/new.csv > lineage.mmd              # Mermaid (default)
$ edna graph results/new.csv --format dot --scope full \\
    --direction LR > lineage.dot                       # Graphviz DOT
$ edna graph results/new.csv --scope full --direction LR --view
    # Opens an interactive Mermaid view in your browser

# Export/import lineage bundles (portable JSON)
$ edna export --project demo --output edna_lineage_demo.json
$ edna import edna_lineage_demo.json --dry-run
$ edna import edna_lineage_demo.json
```

## Project management

```
$ edna project add demo --name "Demo Build"
$ edna project list
$ edna project show demo
$ edna project files demo
```

## Rescanning and repair

```
# Rescan a tree to repair sidecars, update paths, and detect missing files
$ edna rescan ./workspace
```

## FAQ

- Why do DNA tokens never change?  
  DNA tokens are immutable identifiers; new content produces a new DNA token linked to its parent so history stays auditable.
- Why is embedding metadata disabled?  
  Embedded markers would modify source files and skew hashes; EDNA keeps EMBED_ENABLED=False until hashing can ignore the marker or use canonical content.
- How does EDNA behave when files are moved?  
  On show/tag/rescan, EDNA normalises the new path, updates the database, rewrites the sidecar, and logs a `moved` event without changing the DNA.
- How do I quickly see the lineage graph?  
  Use `edna graph <file> --view` to generate a temporary HTML file with a Mermaid diagram and open it in your default browser. By default this uses an online Mermaid.js CDN, so you need basic internet access for the rendering script to load.
- How does EDNA detect versions?  
  A hash mismatch triggers versioning unless `--force-overwrite` is provided; the new artefact gets its own DNA and a derived_from edge back to the previous one.
