# Install And Upgrade

## Install

Editable local install:

```bash
python -m pip install -e .
```

Recommended isolated install once published:

```bash
pipx install sentinel-agent
```

CLI command:

```bash
project-sentinel --help
```

## First Run

```bash
project-sentinel doctor .
project-sentinel scan . --fast --compact
project-sentinel dashboard . --fast
```

## Upgrade Notes

- Re-run `project-sentinel doctor .` after upgrading.
- Delete `.sentinel/scan_cache.json` if you want a clean incremental-cache rebuild.
- Re-run `project-sentinel kilo-refresh . --fast` after upgrading Kilo bridge files.

## Troubleshooting

- Dashboard port busy: pass `--port 8766`.
- URL scans need Git on PATH.
- Use `--ignore-path` for generated, private, or irrelevant directories.
- Use `--no-write` for read-only scan checks.
