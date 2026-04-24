# Project Sentinel Product Spec

Sentinel is a project-aware command line app for scanning a repository, building a compact knowledge base, and handing another agent the smallest useful context for the next engineering step.

## Current Product Surface

- `scan`, `brief`, `overview`, `context`, and `prompt` produce audit output, compact context, and task prompts.
- `retrieve`, `graph`, and `verify` help agents inspect only the relevant files and run focused checks.
- `memory`, `timeline`, and `savings` preserve scan history, task outcomes, decisions, risks, and token-saving events.
- `doctor`, `mcp-health`, `kilo-setup`, `kilo-bridge`, `kilo-refresh`, and `kilo-watch` validate integration health for Kilo/MCP/file-bridge workflows.
- `dashboard` serves a local live status page backed by periodic scans.
- `pr`, `coverage`, `cleanup-reports`, `autofix`, and `release-check` support open-source readiness and review hygiene.

## Architecture

```text
sentinel.py       CLI, orchestration, Kilo bridge, dashboard, release/PR modes
auditor.py        File scanning, metadata extraction, issues, risk scoring
knowledge.py      Persistent knowledge, scan history, task memory, token savings
suggester.py      Rule-based suggestions with confidence, impact, effort, ranking
retriever.py      Query-specific context retrieval
graph.py          AST symbols, imports, calls, dependency hotspots, runtime paths
verifier.py       Changed-file detection and focused test command selection
reporter.py       Terminal, markdown, compact, overview, status, and JSON output
sentinel_mcp.py   Stdio MCP tool surface
adapters.py       Agent-specific instruction documents
```

## Quality Gates

- Focus files written by the Kilo bridge are workspace-relative and validated before `CONTEXT.md` is written.
- Stale bridge context is marked in `.sentinel/kilo/status.json` and in `CONTEXT.md`.
- Suggestions include confidence evidence, uncertainty, impact, effort, and a ranking label.
- Risk scoring combines entry-point status, size, imports, executable surface, paired-test signals, and audit issues.
- Documentation drift detection flags scaffold-like docs, placeholder claims, and empty-looking headings.
- Performance budgets track scan duration, file count, and compact-context token count.
- Release readiness checks README, license, package metadata, version, tests, CLI help, and doctor status.

## Open-Source Readiness

Before publishing:

```bash
python sentinel.py release-check .
python -m pytest
python sentinel.py features
```

For a PR:

```bash
python sentinel.py pr .
python sentinel.py verify . --dry-run
```

For Kilo or another agent:

```bash
python sentinel.py kilo-refresh . --scan-root . --budget small --fast
```
