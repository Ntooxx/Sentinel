# CHANGELOG

## 2.0.0 (2026-10-23)

### Breaking
- Public surface reduced to three commands: `scan`, `report`, `watch`.
- Removed dashboard, local HTTP server, MCP/Kilo bridge, retrieval/chat, verify, PR summaries, coverage ingestion, autofix, memory, savings, timeline, ledger, alerts, insights, adapters, URL analysis, and release-check from the active product.
- Packaging now installs `src/sentinel2` and the `project-sentinel = sentinel2.cli:main` entry point. The legacy flat-module install list was retired.
- Runtime state moved from JSON checkpoints/knowledge bases to one SQLite index at `.sentinel/quality.db`.
- `status` is replaced by `report`; `brief`/`overview` by the report summary; `benchmark` by `tools/benchmark.py`.
- The overall result is a coarse quality band (`maintained`, `adequate`, `weak`, `unmaintained`) instead of a letter grade. `summary.overall_grade` is removed from the JSON contract and `summary.band` replaces it; the report schema is now version 2. Dimension grades are unchanged.

### Changed
- Retired 1.x code moved from `src/` to [`legacy/`](legacy): 22 modules, the 1.x renderers, and the 1.x test suite. It is excluded from `ruff` and from the scan's default excluded directories, but preserved in full and still runnable with `python -m pytest legacy/tests`. `src/` now contains only the active `sentinel2` package.
- Added `legacy` to `exclude.default_dirs`, so an archived tree no longer drives a repository's quality score — the same treatment `dist`, `build` and `target` already receive.
- README now leads with the logo, status badges and a real report screenshot. The 1.x promotional diagrams moved to `legacy/assets/` so they no longer advertise retired features.

### Added
- Incremental SQLite index with WAL, foreign keys, scan history, and 20-scan retention.
- Source-first classification with docs translations, examples, generated files, and vendor trees excluded from source quality.
- Python AST analysis for imports, symbols, function length, and complexity; conservative heuristics for JS/TS, Go, Rust, C/C++, and other languages.
- Six-dimension quality model with confidence, provisional grading, capped penalties, and maturity-aware scaling.
- Evidence-backed findings catalog, top-5 noise control, and a three-item action plan.
- Text, Markdown, self-contained HTML, and stable JSON renderers.
- Polling watch mode with debounce and atomic report writes.
- Local-only privacy guardrails and redacted security checks.
- CLI, integration, privacy, Windows/unicode, watch, and performance tests that exercise the real package.
- Benchmark and synthetic repository tools.
- A committed demo under `docs/demo/`: a real self-contained HTML report and a rendered screenshot, regenerable with `tools/capture_screenshots.ps1` (headless Chrome, no server, deterministic for a commit).
- Rewritten README and user/architecture/privacy guides.

### Fixed
- Warm scans no longer re-hash or re-parse unchanged files.
- Binary and unreadable files are cached as skipped rows instead of being re-read every scan.
- Report rendering never rescans or rebuilds ASTs.
- Windows console output no longer depends on the default code page.
- Report paths are project-relative and secrets are redacted in every format.
- HTML and CSS assets are no longer classified as source, which manufactured unmapped-hotspot findings for web templates and stylesheets.
- Large finding families are judged on their real population instead of being diluted by the small-repository offset.

## 1.1.0 (2026-04-28)

### Added
- Archetype-aware scanning for framework/library, monorepo, CLI/server, browser engine, desktop app, and vendor-heavy repositories
- Risk surface classification with deduplicated factors and test coverage signals
- Release check command (`release-check`) for open-source readiness
- HTML report generation with SVG health ring, stats bar, and responsive layout
- Dashboard GUI with live metrics, suggestion cards, and health timeline
- `analyze-url` command for cloning and scanning remote repositories
- `ask` command for natural-language project questions
- `retrieve` command for query-specific context retrieval
- `verify` command for focused test detection on changed files
- `coverage` command for coverage.xml gap analysis
- `pr` command for changed-file summaries with risk and suggested tests
- `autofix` command for small safe fix planning and application
- Kilo/MCP integration bridge for AI agent workflows
- Voice note transcription support (Discord/Telegram — local Whisper and NVIDIA NIM)
- Persistent task memory, scan timeline, and token savings tracking

### Changed
- Project name resolution: 5-tier ranked fallback (known repos, manifests, README heading, dir name)
- Purpose inference: 6-step chain never returns placeholder
- Entry point detection: Go binaries with major-binary bonus scoring
- Identity text: all HTML, markdown links, badges, images, sponsors filtered out
- Maintainability scoring: large files penalized separately from health failures

### Fixed
- HTML badges/sponsors no longer leak into project name/purpose
- Decorative separators (`----`, `====`) no longer appear as purpose
- Section headings (Installation, Usage, Sponsors) blocked as project names
- Test runner files excluded from runtime hotspots
- Generated SDK files classified separately from source

## 1.0.0 (2026-04-24)

### Added
- Initial release
- Project scanning with fast mode
- Health scoring (maintainability, runtime complexity, test signal, documentation, security)
- Entry point detection and risk surface classification
- Review signals: oversized files, TODO density, documentation drift, test gaps
- Suggestions with impact, effort, confidence metadata
- Agent-ready prompt and context pack generation
- Terminal and Markdown report output
- JSON report export
