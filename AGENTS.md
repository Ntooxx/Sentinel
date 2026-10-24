# Project Agent Instructions

Sentinel 2.0 is the active product. It has exactly three public commands:

```text
project-sentinel scan [PATH]
project-sentinel report [PATH]
project-sentinel watch [PATH]
```

Use it before broad project exploration:

- `project-sentinel report . --format text`: read the latest one-page quality snapshot without rescanning.
- `project-sentinel scan . --format text`: refresh the incremental index and print the report.
- `project-sentinel scan . --format json`: machine-readable findings for automation.
- `project-sentinel watch .`: keep `.sentinel/quality.html` current while editing.

The active package is `src/sentinel2`. Tests for it are `tests/test_s2_*.py`.

Rules for agents working in this repository:

- Do not run, import, or extend the retired 1.x commands (`retrieve`, `ask`, `graph`, `verify`, `dashboard`, `mcp`, `kilo-*`, `pr`, `coverage`, `autofix`, `memory`, `savings`, `timeline`, `ledger`, `alerts`, `insights`, `adapters`, `analyze-url`).
- Do not add dashboard, MCP, chat, retrieval, test-runner, autofix, or cloud features to the active product.
- Keep the runtime dependency list empty and stay compatible with Python 3.10+.
- `scan` may write only `.sentinel/` and the requested output path.
- `report` must not rescan or rebuild ASTs.
- After changing quality rules or analysis logic, bump `analysis_version` in `config.py` and regenerate `config/quality_rules.json` and `src/sentinel2/quality_rules.json`.
- Run `python -m pytest -q -m "not perf"` and `ruff check .` before finishing.
- Keep new thresholds in `config.py`/the rules JSON, not scattered through modules.

The product contract and acceptance criteria are in `sentinel_2.md`. Architecture notes are in `docs/ARCHITECTURE.md`.
