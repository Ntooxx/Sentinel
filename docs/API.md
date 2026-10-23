# API

Sentinel 2.0 has one supported entry point: the CLI. The Python functions below are used by the CLI and tests; they are stable enough for local automation, but the JSON report is the compatibility contract.

## CLI entry point

```python
from sentinel2.cli import main

raise SystemExit(main(["scan", ".", "--format", "json"]))
```

Console script: `project-sentinel = sentinel2.cli:main`

## Python helpers

```python
from pathlib import Path
from sentinel2.config import load_rules
from sentinel2.scanner import scan_project
from sentinel2.cli import load_latest_report
from sentinel2.renderers import render

rules = load_rules(Path("."))
report = scan_project(Path("."), rules=rules)          # scan and return report dict
latest = load_latest_report(Path("."))                 # no rescan; latest SQLite snapshot
text = render(latest, "text", color=False)
html = render(latest, "html")
json_text = render(latest, "json")
```

`scan_project` raises `sentinel2.scanner.ScanError` for invalid paths and runtime failures. `load_latest_report` raises `sentinel2.cli.ReportError` when the index is missing or unreadable.

## JSON report schema v1

Top-level keys:

| Key | Meaning |
|---|---|
| `schema_version` | `2` |
| `generated_at` | ISO timestamp for the scan |
| `project` | name, relative root, git commit/branch/dirty |
| `scan` | mode, duration, file counts, coverage, index size |
| `summary` | quality band/score, confidence, provisional flag, headline |
| `dimensions` | one object per dimension with `assessed`, `grade`, `score`, `summary`, `evidence` |
| `metrics` | source/test counts, ratios, TODOs, parse errors, complexity, archetype |
| `findings` | every finding, source-first ordering |
| `changes` | added/modified/deleted counts and quality delta |
| `architecture` | archetype, components, entry points, hotspots |
| `actions` | up to three ranked next actions |
| `exclusions` | translation/vendor/generated/example counts and reason |
| `limitations` | explicit caveats |
| `rules_version` | quality rules version |

Finding object:

```json
{
  "id": "MNT002:fastapi/routing.py",
  "rule_id": "MNT002",
  "dimension": "maintainability",
  "severity": "high",
  "confidence": "high",
  "title": "Very large source file",
  "file": "fastapi/routing.py",
  "line": 1,
  "evidence": ["4,956 lines", "197.0 KB"],
  "why": "Very large modules are hard to review, test and change safely.",
  "recommendation": "Map the relevant tests, then extract one cohesive concern at a time."
}
```

Rules:

- paths are project-relative;
- findings are sorted source-first, then by severity, confidence, dimension, path, and rule;
- JSON contains all findings even when text output shows the top five;
- timestamps and durations are the only intentionally non-deterministic fields.

## Removed 1.x API

`SentinelAgent`, `ProjectAuditor`, the dashboard action API, MCP bridge, retrieval/ask, memory, savings, timeline, ledger, verifier, coverage ingestion, autofix, PR generation, and URL analysis are retired. They remain in git history, not in the active API.
