# eng-dna

`eng-dna` (CLI: `edna`) is a lightweight engineering memory store as described in `Background.md`. It keeps a SQLite lineage database plus DNA tokens embedded in files or neighbouring sidecars.

## Portable lineage export/import

You can sync lineage between machines (or via Git) without a server:

- Export a project lineage closure to JSON:

  ```
  edna export --project demo --output edna_lineage_demo.json
  ```

- Import on another machine (use `--dry-run` to preview):

  ```
  edna import edna_lineage_demo.json --dry-run
  edna import edna_lineage_demo.json
  ```

The bundle uses DNA tokens as stable identifiers and merges safely into the local DB (idempotent; does not overwrite local paths).

See `USAGE.md` for command examples.
