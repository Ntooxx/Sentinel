# CHANGELOG

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
