# Sentinel 2.0 Architecture

## One implementation

The active product is the `sentinel2` package under `src/`. Its only entry point is `sentinel2.cli:main`, exposed as `project-sentinel` and as `python -m sentinel2`. `python sentinel.py` is a compatibility launcher for the same function.

The retired 1.x code lives under [`legacy/`](../legacy) for history only. The active package does not import it, it is excluded from `ruff`, and it is in the scan's default excluded directories.

## Data flow

```text
CLI
  -> discover candidates (git-first, deterministic walk fallback)
  -> classify before reading (source/test/doc/config/vendor/generated/example/translation)
  -> stat + cache check in SQLite
  -> read and analyse only changed files (bounded thread pool)
  -> write facts, symbols, imports to SQLite
  -> run quality rules against the indexed snapshot
  -> build one report object
  -> render text / Markdown / HTML / JSON
```

`report` skips everything except reading the latest `scans.report_json` and rendering it.

`watch` polls a cheap stat snapshot, waits for the debounce, then runs the same scan pipeline. It writes the report atomically with a temporary file and `os.replace`.

## Module responsibilities

| Module | Responsibility |
|---|---|
| `cli.py` | argparse surface, exit codes, output routing |
| `config.py` | defaults and project-level rule overrides |
| `discover.py` | git/walk discovery, ignore rules, binary/oversized skips |
| `classify.py` | language, role, translation/example/vendor detection, archetypes |
| `analyze.py` | Python AST facts, heuristics for other languages, manifests, README |
| `index.py` | SQLite schema, migrations, upserts, scans, retention |
| `findings.py` | rule catalog, evidence, deduplication, ranking, actions |
| `quality.py` | dimension penalties, confidence, grades, metrics |
| `report.py` | stable report object |
| `renderers/` | pure text/Markdown/HTML/JSON renderers |
| `scanner.py` | orchestration of one incremental scan |
| `watch.py` | polling loop and atomic live output |

## SQLite index

Database: `.sentinel/quality.db`

Pragmas: WAL, `synchronous=NORMAL`, foreign keys on, 5-second busy timeout, in-memory temp store.

Tables:

- `meta` - rule/analysis version metadata
- `files` - one row per indexed path, including lightweight skipped rows
- `symbols` - functions/classes with line spans, length and complexity
- `imports` - static import evidence used for cycles and test mapping
- `findings` - deduplicated current findings with first/last seen scan
- `scans` - one row per scan, including the full report JSON
- `scan_files` - per-scan file status, for change detection and history

Retention keeps the latest 20 scans and cascades `scan_files`. Current facts reflect the latest scan. Findings that disappear are removed from the current table; their history remains in older `scans.report_json` rows until retention removes them.

## Incremental algorithm

For each candidate:

1. `stat` size and mtime.
2. Look up `files.path`.
3. If size, mtime, and analysis version match, reuse the cached row.
4. If changed, read and hash the file.
5. If the hash matches and the analysis version is current, update metadata only.
6. Otherwise analyse and upsert facts and symbols/imports.
7. Delete rows for paths no longer present.

Skipped binary/unreadable files get a lightweight `files` row with `skipped_reason`, so warm scans do not re-read them. Oversized files are recorded in discovery skips and coverage.

Analysis uses up to 32 workers from a thread pool. SQLite writes stay on the main thread, in batches of 200.

## Quality rules

Rules live in `findings.py`; thresholds and weights live in `config/quality_rules.json` (with defaults in `config.py` and a packaged copy at `src/sentinel2/quality_rules.json`).

Each finding has:

- stable rule ID and dimension;
- severity and confidence;
- file and line evidence;
- why it matters;
- a concrete next step;
- a stable evidence key so repeats are deduplicated.

Penalties are capped per rule family. Count-based signals use diminishing penalties and ratios so a 10,000-file repository is not punished merely for having more files. Source findings sort ahead of documentation, dependency, and hygiene findings.

## Determinism

Reports sort files, findings, dimensions, components, entry points, and actions. JSON includes `generated_at` and scan durations, which are intentionally variable; all other content is deterministic for the same commit, rules version, and options.

## Performance choices

- docs metadata by default;
- binary probe and cache-aware warm scans;
- no re-hash for unchanged files;
- no AST rebuild for report rendering;
- SQL aggregates for metrics;
- single SQLite writer;
- atomic report writes in watch mode;
- no third-party runtime packages.

## Testing

The suite uses real CLI subprocesses for integration and exercises the same package the user runs. It covers classification, discovery, SQLite integrity/retention, analysis, rules, quality, renderers, CLI exit codes, watch updates, privacy (no network), Windows/unicode paths, and performance budgets.
