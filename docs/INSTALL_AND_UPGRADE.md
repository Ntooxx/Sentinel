# Install and Upgrade

## Requirements

- Python 3.10 or newer
- No runtime dependencies
- Optional: `git` on PATH for git-first discovery and churn signals

## Install

From a checkout:

```bash
python -m pip install .
project-sentinel --version
```

Editable development install:

```bash
python -m pip install -e .
python -m pip install pytest ruff
```

Run without installing:

```bash
python -m sentinel2 --version
python sentinel.py --version
```

## First run

```bash
project-sentinel scan .
```

This creates `.sentinel/quality.db`. A second scan with no changes should report `files_analyzed: 0` and be much faster.

## Upgrade from 1.x

Sentinel 2.0 is a breaking change.

- The public surface is only `scan`, `report`, and `watch`.
- Old JSON state (`.sentinel/checkpoints.json`, `.sentinel/knowledge_base.json`, `src/scan_cache.json`) is not migrated. The new SQLite index rebuilds from source.
- Old state is safe to archive or delete after the first 2.0 scan. Do not delete it automatically from a script; back it up if you need history.
- `status` becomes `report`; `brief`/`overview` become the report summary; `benchmark` becomes `tools/benchmark.py`.
- Dashboard, MCP/Kilo, retrieval, ask, graph, verify, PR, coverage, autofix, memory, savings, timeline, ledger, alerts, insights, adapters, URL analysis, and release-check commands are removed.

## Clean rebuild

If the index is corrupt or rules changed in a way that requires a full re-analysis:

```bash
project-sentinel scan . --full
```

To force a completely new index, stop any watch process and remove only the runtime database files:

```bash
rm .sentinel/quality.db .sentinel/quality.db-wal .sentinel/quality.db-shm
project-sentinel scan .
```

Do not remove source or configuration files.

## CI install

```bash
python -m pip install .
project-sentinel scan . --format json --out sentinel.json
```

A wheel contains only the `sentinel2` package and its packaged quality rules. It does not include tests, fixtures, benchmarks, `.sentinel`, or legacy modules.

## Troubleshooting

- **`report` exits 1**: run `scan` first; `report` does not create an index.
- **Warm scan analyses everything**: rules `analysis_version` changed, mtimes are being rewritten, or the index cannot be written.
- **Non-ASCII console errors**: set `PYTHONIOENCODING=utf-8`; Sentinel also reconfigures stdout/stderr with replacement.
- **Unreadable index**: remove `.sentinel/quality.db*` and rescan.
