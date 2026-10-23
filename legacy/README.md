# legacy/

Retired **Sentinel 1.x** code, kept for reference. Nothing here is part of the active product.

The active product is [`src/sentinel2`](../src/sentinel2) — three commands, one SQLite index,
standard library only. See [`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md).

## What is here

| Path | What it is |
|---|---|
| `src/` | The 1.x modules: `sentinel.py` (the `SentinelAgent`), `auditor.py`, `classify.py`, `knowledge.py`, `suggester.py`, `sentinel_mcp.py`, `dashboard.py`, `renderers/`, and the rest. ~17k lines. |
| `tests/` | The 1.x test suite (197 tests). |
| `github-action/sentinel/` | An unfinished GitHub Action built on the retired `SentinelAgent` API. Never wired into a workflow. |

## Why it was retired

1.x grew a wide feature set: a dashboard, an HTTP server, an MCP bridge, retrieval and chat,
PR summaries, coverage ingestion, autofix, memory, timeline, alerts. Each was a separate claim to
maintain, document and defend, and together they buried the one thing that worked well — a local
triage report.

Sentinel 2.0 deleted all of it and kept three commands: `scan`, `report`, `watch`. The runtime
dependency list went from a stack of packages to empty. The record of that contraction is in
[`docs/DESIGN_DECISIONS.md`](../docs/DESIGN_DECISIONS.md), decision 12.

The 1.x history is also in git. This directory exists so the code is readable without a checkout of
an old commit.

## Why it is not scanned or linted

`legacy` is in the default excluded directories (`exclude.default_dirs` in
[`src/sentinel2/config.py`](../src/sentinel2/config.py)), so `project-sentinel scan` skips it. That
keeps the quality report about the maintained product rather than the retired one — the same reason
`dist`, `build` and `target` are excluded. It is also excluded from `ruff`.

If you keep *active* code in a directory named `legacy` elsewhere, remove `legacy` from
`exclude.default_dirs` in your own `config/quality_rules.json`.

## Running the legacy tests

They still run, from this directory:

```bash
python -m pytest legacy/tests
```

The suite resolves its own module path (`parents[1] / "src"`), so no configuration is needed.

## Do not extend it

Per [`AGENTS.md`](../AGENTS.md): do not run, import, or extend the retired 1.x commands. Nothing
in `src/sentinel2` imports anything here, and nothing here should be made to.
