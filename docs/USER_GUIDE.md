# Sentinel 2.0 User Guide

## Install

```bash
python -m pip install .
project-sentinel --version
```

Python 3.10 or newer is required. There are no runtime dependencies.

## The three commands

### 1. Scan

```bash
project-sentinel scan .
```

This discovers files, classifies them before reading, analyses changed files, updates `.sentinel/quality.db`, computes quality, and prints the report.

Useful variants:

```bash
project-sentinel scan . --format md --out quality.md
project-sentinel scan . --format html --out quality.html
project-sentinel scan . --format json --out quality.json
project-sentinel scan . --all-findings
project-sentinel scan . --include-docs
project-sentinel scan . --full
project-sentinel scan . --ignore generated --ignore vendor
project-sentinel scan . --fail-under B
```

`--full` re-analyses source/test files that would otherwise be cached. `--include-docs` reads documentation content; by default only README and manifest content is read and other docs contribute metadata.

### 2. Report

```bash
project-sentinel report .
```

Renders the latest scan snapshot without rescanning. This is the fast path for CI artifacts and local viewing.

```bash
project-sentinel report . --format json
project-sentinel report . --format md --out report.md
project-sentinel report . --open
project-sentinel report . --refresh
```

`--open` forces HTML output and opens it in the default browser. `--refresh` runs a scan first and then renders.

If no index exists, `report` exits 1 with a message telling you to run `scan` first.

### 3. Watch

```bash
project-sentinel watch .
```

Scans once, then polls for changes. Defaults:

- format: `html`
- output: `.sentinel/quality.html`
- interval: `1.0` seconds
- debounce: `300` milliseconds

```bash
project-sentinel watch . --out quality.html --interval 0.5
project-sentinel watch . --format md --out quality.md
project-sentinel watch . --quiet
```

The HTML report is self-contained. It uses an inline meta refresh so a browser can follow the file without a local server. Watch ignores its own index and output file.

## Output formats

| Format | Intended use |
|---|---|
| `text` | Terminal, one page, ANSI color when attached to a TTY |
| `md` | PR comments, repository docs, CI artifacts |
| `html` | Self-contained local report, live watch view |
| `json` | Automation and the stable schema contract |

The JSON report is the source of truth. The terminal report shows the top five findings by default; JSON contains every finding.

## What gets scanned

Default discovery uses git when available:

1. `git ls-files -co --exclude-standard -z` for tracked and untracked, non-ignored files.
2. deterministic filesystem walk otherwise.
3. default excludes such as `.git`, `.sentinel`, `node_modules`, `__pycache__`, `.venv`, `dist`, `build`, caches, and IDE directories.
4. user `--ignore` paths.
5. generated, vendor, examples, and documentation-translation folders are excluded from source quality.

Source and test files are analysed. Documentation content is metadata-only unless `--include-docs`; README and manifests are always read because the quality rules need them. Binary files are detected and skipped with a reason. Files larger than the configured maximum are skipped and recorded in coverage.

## Quality model

Six dimensions are scored from 100 using rule penalties:

- Architecture and structure (20%)
- Maintainability (25%)
- Tests (20%)
- Documentation (15%)
- Dependencies and hygiene (10%)
- Basic security (10%)

A dimension that cannot be assessed is shown as `n/a` and removed from the weighted average. A docs-only repository therefore gets documentation/dependency signals, not an invented source score.

The overall result is a coarse band (`maintained`, `adequate`, `weak`, `unmaintained`); dimension grades use the letter bands in `sentinel_2.md` (A+ through F) as evidence. The band is provisional until the rubric is calibrated. Low-confidence reports append `?` to dimension grades and list the reason in limitations.

### Tests are mapped, not measured

Sentinel does not execute tests and does not ingest coverage. A source module is "mapped" when a test file imports or names it. The report must not call this runtime coverage.

### Security is basic

Rules look for provider-shaped tokens, assigned credentials, dangerous dynamic execution, and debug flags. Matches are redacted. This is not a security audit and should not be presented as one.

## CI usage

A typical job:

```yaml
- name: Install Sentinel
  run: python -m pip install .
- name: Quality report
  run: project-sentinel scan . --format json --out sentinel.json
- name: Quality gate
  run: project-sentinel scan . --fail-under maintained --quiet
```

The quality gate exits 3 when the quality band is below the requested band (a legacy letter grade also works). Because the band is provisional, prefer explicit finding rules or human review for hard release gates.

To publish a PR comment, render Markdown:

```bash
project-sentinel scan . --format md --out sentinel.md
```

## Troubleshooting

### `report` says there is no index

Run `project-sentinel scan .` first. The index lives at `.sentinel/quality.db` relative to the scanned root.

### The scan is slower than expected

- Confirm documentation is not being read: default is metadata-only.
- Confirm the second scan reports `files_analyzed: 0`; if not, something is rewriting mtimes or the rules version changed.
- Run `project-sentinel scan . --format json` and inspect `scan.files_skipped`.
- Use `python tools/benchmark.py --repo . --mode all` to measure cold, warm, and report separately.

### Non-ASCII output on Windows

Sentinel reconfigures stdout/stderr to UTF-8 with replacement at startup. If a parent process forces a legacy code page, set `PYTHONIOENCODING=utf-8`.

### Old 1.x commands are gone

`status`, `retrieve`, `ask`, `graph`, `dashboard`, `mcp`, `kilo-*`, `verify`, `pr`, `coverage`, `autofix`, `memory`, `savings`, `timeline`, `ledger`, `alerts`, `insights`, `adapters`, and `analyze-url` are intentionally removed. `status` is replaced by `report`; `brief`/`overview` by the report summary; `benchmark` by `tools/benchmark.py`.
