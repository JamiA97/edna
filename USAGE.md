# edna CLI usage

```
# Initialise the workspace database
$ edna init

# Tag a file with metadata and tags/projects
$ edna tag reports/day1.md --type report --tag baseline --project demo

# Show metadata for a DNA token or file path
$ edna show edna_c13baf...
$ edna show reports/day1.md

# Link a derived artefact to its parent(s)
$ edna link results/new.csv --from reports/day1.md --relation derived_from --reason "post-processing"

# Trace a file's ancestors
$ edna trace results/new.csv

# Search by tag/type/project
$ edna search --tag baseline --project demo

# Manage projects
$ edna project add demo --name "Demo Build"
$ edna project files demo

# Rescan a tree to repair sidecars and update paths
$ edna rescan ./workspace
```
