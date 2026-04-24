# Project Sentinel Agent

Sentinel is a project-aware CLI that scans a codebase, understands how it is organized, stores persistent knowledge, and recommends the next highest-value action to take.

By default, Sentinel stores its runtime state inside the scanned project under `.sentinel/`, which makes it easy to vendor into another repository or install and run against an existing codebase.

## Why Teams Use Sentinel

Modern agents are powerful, but they are expensive when they rediscover the same repository from scratch every session. Sentinel gives them a working memory layer: a compact project map, verified focus files, current risks, and the next best action before the agent starts reading broadly.

Use Sentinel when you want:

- Less context waste: start from a small, ranked project brief instead of dumping a whole repository into an LLM.
- Faster onboarding: see the architecture, entry points, tests, hotspots, and likely workflow in one command.
- Better engineering judgment: get suggestions backed by concrete files, audit findings, confidence, impact, effort, and verification hints.
- Safer agent work: point Codex, Kilo, Cline, Claude Code, Roo, or Continue at validated focus files and narrow test commands.
- Durable memory: keep scan history, checkpoints, task memory, token savings, and reports inside `.sentinel/`.
- Vendor-friendly adoption: drop Sentinel into `tools/sentinel`, install it editable, or run it as a standalone CLI.

Sentinel is not another dashboard for humans to babysit. It is an operating layer for agentic development: small context in, focused action out, and a clear path to verify what changed.

The implementation in this repository follows the design from `sentinel.md` and ships as a working Python tool with:

- A scanning and audit pipeline
- Project understanding and architecture summarization
- Persistent knowledge and checkpoint storage
- A rule-based suggestion engine
- Suggestion confidence, impact/effort labels, and verification hints
- Risk scoring for high-blast-radius files
- Documentation drift detection for placeholder or stale docs
- Low-token context packs and next-step prompt generation
- Path-validated Kilo file bridge output with stale-context detection
- Terminal and markdown report generation
- A local live dashboard
- PR, coverage, release-readiness, MCP-health, autofix, and stale-report cleanup modes
- Continuous monitoring support
- Configuration, packaging, and tests

## Features

- Scans important project files and captures metadata such as line counts, imports, symbols, TODO markers, and entry points
- Understands project purpose, major components, hotspots, and likely workflow hints
- Audits project structure, documentation presence, test coverage signals, oversized files, and current hotspots
- Persists knowledge between runs in `.sentinel/knowledge_base.json`
- Tracks changes between scans in `.sentinel/checkpoints.json`
- Produces prioritized next-step suggestions with ready-to-run prompts
- Adds confidence evidence, uncertainty, impact, effort, and ranking labels to suggestions
- Emits compact context packs and prompt packs to reduce LLM token usage dramatically
- Validates generated focus-file paths before writing Kilo bridge context
- Retrieves task-specific context with ranked files, symbols, snippets, import hints, and call hints
- Builds a Python AST symbol index plus import graph, call graph, dependency hotspots, and runtime paths
- Verifies patches by choosing narrow related tests for changed files
- Records task memory: changes, tests, decisions, and remaining risks
- Records a scan timeline with health, changes, top suggestions, and performance-budget alerts
- Tracks estimated token savings over time
- Reads `coverage.xml` and flags high-risk files with weak coverage
- Generates PR summaries and open-source release-readiness checklists
- Serves a local dashboard with health, suggestions, timeline, and token-saving status
- Generates adapter prompts for Cline, Claude Code, Codex, Roo, and Continue
- Generates both console output and full markdown reports
- Uses only the Python standard library

## Quick Start

Install locally from this repository:

```bash
python -m pip install -e .
```

Then scan any project:

```bash
project-sentinel scan /path/to/project --fast --compact
```

If you are running directly from a checkout, this also works:

```bash
python sentinel.py scan . --fast --compact
```

Watch a project continuously:

```bash
python sentinel.py watch /path/to/project --interval 30
```

Generate a full report:

```bash
python sentinel.py report /path/to/project
```

Get a high-level explanation of how the project works:

```bash
python sentinel.py overview /path/to/project --fast
```

Generate a compact context pack for another LLM or agent:

```bash
python sentinel.py context /path/to/project --budget small --fast
```

Generate a focused next-step prompt:

```bash
python sentinel.py prompt /path/to/project --goal next --budget small --fast
```

Retrieve task-specific context:

```bash
python sentinel.py retrieve /path/to/project --query "scheduler timeout bug" --goal debug --fast
```

Inspect symbols, imports, and call hints:

```bash
python sentinel.py graph /path/to/project
```

Verify a patch with narrow tests:

```bash
python sentinel.py verify /path/to/project --dry-run
```

Open the live dashboard:

```bash
python sentinel.py dashboard /path/to/project --port 8765 --fast
```

Summarize a pull request before publishing:

```bash
python sentinel.py pr /path/to/project
```

Run the open-source release checklist:

```bash
python sentinel.py release-check /path/to/project
```

Inspect coverage hotspots after generating `coverage.xml`:

```bash
python sentinel.py coverage /path/to/project
```

Record task memory:

```bash
python sentinel.py memory record /path/to/project --goal "fixed retrieve command" --changed-file src/sentinel.py --test "python -m pytest tests"
```

Show all commands with a small terminal animation:

```bash
python sentinel.py features --animate
```

Run Sentinel as an MCP server for Kilo Code and similar agents:

```bash
python sentinel.py mcp /path/to/project --budget small --fast
```

MCP uses stdio, so a healthy server normally waits silently for the client to send framed MCP messages. If you start it by hand in a terminal, no startup banner is expected; press `Ctrl+C` to stop it.

Bootstrap a project-local Kilo Code integration:

```bash
python sentinel.py kilo-setup /path/to/workspace --scan-root app --force
```

Set up Kilo plus the no-MCP file bridge:

```bash
python sentinel.py kilo-bridge /path/to/workspace --scan-root app --budget small --fast --force
```

Refresh the shared workspace context files before a Kilo task:

```bash
python sentinel.py kilo-refresh /path/to/workspace --scan-root app --budget small --goal next --fast
```

Keep those files refreshed in the background:

```bash
python sentinel.py kilo-watch /path/to/workspace --scan-root app --budget small --fast --interval 30
```

Show the latest saved status without rescanning:

```bash
python sentinel.py status /path/to/project
```

Fast and compact scan:

```bash
python sentinel.py scan /path/to/project --fast --compact
```

Tiny AI-friendly summary:

```bash
python sentinel.py brief /path/to/project --fast --quiet
```

JSON output for scripts or low-token sharing:

```bash
python sentinel.py scan /path/to/project --format json
```

## Project Layout

```text
src/
  sentinel.py    Main orchestrator and CLI entrypoint
  auditor.py     Scanning, auditing, and checkpoints
  knowledge.py   Persistent knowledge base
  suggester.py   Prioritized next-step suggestions
  graph.py       AST symbol, import, call, dependency, and runtime-path graph
  verifier.py    Changed-file detection and focused test command selection
  monitor.py     Continuous monitoring loop
  reporter.py    Terminal and markdown reports
  utils.py       Shared helpers and defaults
config/
  config.json
  audit_rules.json
  patterns.json
.sentinel/
  knowledge_base.json
  checkpoints.json
  reports/
tests/
  test_sentinel.py
  test_auditor.py
  test_knowledge.py
docs/
  ARCHITECTURE.md
  API.md
  USER_GUIDE.md
```

## Configuration

The default configuration lives in `config/config.json`.

Supported environment variables:

- `SENTINEL_CONFIG_PATH` overrides the config file location
- `SENTINEL_LOG_LEVEL` sets the log level such as `INFO` or `DEBUG`
- `SENTINEL_DEBUG_MODE` enables debug logging when set to a truthy value
- `SENTINEL_HOME` overrides where Sentinel stores its runtime state

## Development

Run the test suite:

```bash
python -m unittest discover -s tests -v
```

Install as a package locally:

```bash
python -m pip install .
```

## Output Files

- `.sentinel/knowledge_base.json` stores discovered project knowledge
- `.sentinel/checkpoints.json` stores scan checkpoints for diffs
- `.sentinel/reports/` stores archived markdown reports
- `SENTINEL_REPORT.md` is written into the scanned project when the `report` command is used

## CLI Commands

- `scan` runs a single scan
- `brief` runs a tiny summary with the top suggestion
- `overview` explains the project structure, hotspots, and likely workflow
- `context` emits a compact low-token context pack for another LLM or agent
- `prompt` generates a focused prompt for the next step, review, debug pass, or plan
- `retrieve` returns query-specific files, symbols, snippets, import hints, and call hints
- `graph` builds a Python AST symbol index plus import graph and call graph
- `verify` runs or previews narrow checks for changed files
- `memory` records or lists task memory
- `savings` shows tracked estimated token savings
- `doctor` validates config and runtime paths with clearer failure messages
- `dashboard` serves a local live dashboard with health, timeline, suggestions, and budget alerts
- `autofix` plans or applies small safe fixes for config and bridge hygiene
- `pr` summarizes changed files, risk focus, and suggested tests
- `timeline` shows scan history, task memory, and token savings
- `mcp-health` validates MCP tool availability and file-bridge freshness
- `coverage` reads `coverage.xml` and identifies high-risk files with weak coverage
- `cleanup-reports` marks old archived reports as historical
- `release-check` runs an open-source readiness checklist
- `features` lists every command and can play a short terminal animation
- `adapters` prints or writes adapter prompts for Cline, Claude Code, Codex, Roo, and Continue
- `mcp` runs Sentinel as a stdio MCP server
- `kilo-setup` writes modern `.kilo/kilo.jsonc`, Sentinel-first Kilo rules, a Sentinel Kilo agent profile, and a legacy `.kilocode/mcp.json` fallback
- `watch` continuously monitors a project
- `report` saves a markdown report
- `status` prints the latest saved knowledge summary without doing a new scan

`--fast` trades some scan depth for speed, so it is best for quick health checks rather than the most detailed audit.
`--top 1` keeps only the most important suggestion in the output.
`--quiet` suppresses log noise so the output is easier to pass into another tool or LLM.
`--budget small` or `--budget medium` helps control how much context Sentinel emits for token-sensitive workflows.

## Bringing It Into Another Project

Recommended approach:

```bash
python -m pip install -e path/to/sentinel
project-sentinel overview . --fast --quiet --ignore-path tools/sentinel
project-sentinel context . --budget small --fast --quiet --ignore-path tools/sentinel
project-sentinel prompt . --goal next --budget small --fast --quiet --ignore-path tools/sentinel
```

This works well when Sentinel is copied into a `tools/` or `vendor/` folder inside another repository. The scan targets your current project, and Sentinel keeps its own state under that project's `.sentinel/` directory by default. If Sentinel lives inside the same repository you are scanning, use `--ignore-path` to exclude the vendored folder itself.

## Token-Saving Workflow

Sentinel is designed to reduce wasteful LLM context loading.

Use this pattern:

```bash
project-sentinel overview . --fast --quiet --ignore-path tools/sentinel
project-sentinel context . --budget small --fast --quiet --ignore-path tools/sentinel
project-sentinel prompt . --goal next --budget small --fast --quiet --ignore-path tools/sentinel
```

That gives you:

- A high-level project explanation
- A compact context pack instead of pasting the whole repo
- A focused next-step prompt anchored to the current hotspots and workflow

The token savings are only real if your agent starts from the Sentinel context or prompt pack instead of reopening large parts of the repository from scratch.

## Kilo Code Integration

Sentinel can integrate with Kilo in two ways:

- No-MCP file bridge: writes compact context into normal workspace files that Kilo can read directly.
- MCP bridge: exposes Sentinel as tools when Kilo's MCP dispatcher is working correctly.

Recommended bootstrap for the strongest setup:

```bash
project-sentinel kilo-bridge . --scan-root axiom --budget small --fast --force
```

This writes:

- `CONTEXT.md` as the auto-readable compact project brief
- `.sentinel/kilo/prompt.md` as the task prompt Kilo should follow
- `.sentinel/kilo/focus-files.txt` as the first files to inspect, using workspace-relative paths
- `.sentinel/kilo/status.json` with token, health, freshness, and path-validation metadata
- `.kilo/kilo.jsonc` with the current Kilo `mcp` config format
- `.kilo/rules/sentinel-file-bridge.md` with the no-MCP workflow
- `.kilo/rules/sentinel-first.md` referenced from `instructions`
- `.kilo/agents/sentinel-code.md` as a selectable Sentinel-first Kilo agent profile
- `.kilocode/mcp.json` and `.kilocode/rules/sentinel-first.md` as compatibility fallbacks for older Kilo builds

Daily no-MCP workflow:

```bash
project-sentinel kilo-refresh . --scan-root axiom --budget small --goal next --fast
```

Then tell Kilo: read `CONTEXT.md`, follow `.sentinel/kilo/prompt.md`, and start with `.sentinel/kilo/focus-files.txt`.

If any focus file disappears or a nested scan root moves, Sentinel marks the bridge stale and prints the exact `kilo-refresh` command to regenerate it.

Optional background workflow:

```bash
project-sentinel kilo-watch . --scan-root axiom --budget small --fast --interval 30
```

If MCP is healthy, the high-value tools are:

- `sentinel_sentinel_context` for compact project context
- `sentinel_sentinel_overview` for architecture and hotspots
- `sentinel_sentinel_prompt` for a focused next-step prompt grounded in compact context

Important limitation:

- Sentinel does not magically reduce token usage by itself
- The savings happen when Kilo uses `CONTEXT.md`, `.sentinel/kilo/prompt.md`, or Sentinel MCP first and follows the focus files instead of rereading the whole repo

Legacy forms still work:

- `python sentinel.py --once`
- `python sentinel.py /path/to/project --full-report`
- `python sentinel.py /path/to/project --interval 30`
