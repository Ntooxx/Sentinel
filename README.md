# Sentinel

**Fast repo intelligence for AI coding agents.**

Scan any repository. Get architecture, risks, hotspots, and a ready-to-use agent prompt — in seconds.

```bash
python sentinel.py scan . --fast --compact
```

---

## Speed

Sentinel analyses repos from 100 files to 6M lines in realistic time.

| Repo | Files | Lines | Scan Time | Health Score |
|---|---|---|---|---:|
| Small Python library | 234 | 42k | **0.16s** | 86% |
| FastAPI web framework | ~1k | ~200k | **4.56s** | 74% |
| Kubernetes (k8s.io/kubernetes) | 25,432 | 6,007,991 | **55s** | 74% |

> 25,000 files, 6 million lines analysed in under a minute. No external services. No cloud dependency.

---

## What It Does

Sentinel scans a repository and produces:

- **Architecture summary** — project type, language, frameworks, archetype, purpose, workflow
- **Hotspot detection** — runtime, build, test, docs, and vendor hotspots ranked by risk
- **Review signals** — oversized files, TODO density, documentation drift, test coverage gaps
- **Entry point mapping** — primary runtime entry points, build tools, generators, examples
- **Health score** — maintainability, runtime complexity, test signal, with breakdown
- **Project identity** — name, purpose, archetype, frameworks, main components
- **Next-step suggestions** — ranked by impact, effort, and confidence
- **Agent prompt** — project-aware prompt ready for Cline, Claude Code, Codex, Roo, or Continue
- **HTML report** — modern card-based design with SVG health ring, color-coded risks, terminal-style prompt
- **Dashboard GUI** — dark-theme browser command centre for all Sentinel workflows

---

## Benchmark Detail

| Metric | Small Library | FastAPI | Kubernetes |
|---|---|---|---|
| Total files | 234 | ~1k | 25,432 |
| Total lines | ~42k | ~200k | 6,007,991 |
| Scan duration | 0.16s | 4.56s | 55s |
| Health score | 86% | 74% | 74% |
| Runtime complexity | medium | medium | high |
| Test signal | strong | strong | strong |
| Review signals | 92 | 203 | 4,428 |
| TODOs found | 10 | 48 | 6,644 |
| Entry points detected | 2 | 1 | 48 |
| Confidence | high | high | low |

---

## What Makes Sentinel Different

| Area | Without Sentinel | With Sentinel |
|---|---|---|
| Project understanding | AI agent manually reads files | Compact overview generated automatically |
| Token waste | Large repo loaded repeatedly | Only relevant files and summaries |
| Onboarding | Manual structure explanation | Architecture, entry points, tests, hotspots in one command |
| Next action | The agent guesses | Sentinel ranks the highest-value next action |
| Risk awareness | High-risk files missed | Hotspots, oversized files, TODOs flagged |
| Testing | Broad or unclear test selection | Focused verification commands suggested |
| Agent safety | Edits made without full context | Validated focus files provided |
| Project memory | Every session starts from zero | Persistent memory in `.sentinel/` |
| Documentation | Manual report writing | Terminal, HTML, and Markdown reports |
| AI agent workflow | Context prepared manually | Agent-ready prompts and context packs |

---

## Core Idea

```
Repository → Sentinel Scan → [Project Understanding + Risk Detection + Architecture Summary]
                                  ↓
                   Knowledge Base → Context Pack → AI Coding Agent
                   Next-Step Suggestions → Focused Code Changes → Verification
```

Sentinel helps answer:
- What is this project?
- Where should I start?
- Which files matter most?
- What is risky?
- What should be fixed next?
- How should changes be verified?
- What context should an AI agent receive?

---

## Features

### Core Analysis
- **Project scanning**: Deep codebase analysis with AST parsing and import graph generation
- **Project identity resolution**: Smart 5-tier name extraction — knows FastAPI, TensorFlow, Kubernetes, Flask, React, PyTorch, and 20+ other projects by name
- **Purpose inference**: 5-step fallback from manifest descriptions through README body, summary, doc_title subtitle, and component-based generation
- **Codebase auditing**: Rule-based detection of risks, hotspots, and code smells
- **Architecture summarisation**: Project structure, entry points, dependencies, archetype
- **Entry point mapping**: Prioritises major Go binaries (kube-apiserver, kubectl, kubelet) and catches Go `cmd/<name>/<file>.go` patterns even when not named `main.go`
- **Persistent knowledge storage**: State kept in `.sentinel/` directory
- **Checkpoint tracking**: Diff-based change detection between scans

### AI Agent Support
- **Prioritised next-step suggestions**: Ranked by impact, effort, and confidence
- **Confidence scoring**: Quantified reliability of each recommendation
- **Impact and effort labels**: Clear triage for developers and agents
- **Verification hints**: Focused test commands per suggestion
- **Agent prompt generation**: Project-aware prompts for Cline, Claude Code, Codex, Roo, Continue
- **Low-token context packs**: Compact briefs for token-sensitive workflows

### Risk & Quality
- **Health scoring**: Weighted metric combining maintainability, runtime complexity, test signal, documentation quality, and TODO density
- **Risk scoring**: File-level risk with coverage tracking and deduplicated factors
- **Documentation drift detection**: Regex-based placeholder detection (TBD, "coming soon", empty brackets, stubs)
- **Coverage hotspot analysis**: Weakly tested areas from `coverage.xml`
- **Dependency hotspot detection**: Risky dependency patterns
- **Release-readiness checks**: Open-source checklist

### Reports
- **HTML reports**: Modern card-based layout with SVG health ring, color-coded severity badges, terminal-style agent prompt, responsive design
- **Dashboard GUI**: Dark-theme local browser command centre with tool cards, toggle pills, live status, stats row, and action runner
- **Markdown reports**: Detailed project documentation
- **Terminal reports**: Quick CLI summaries
- **Repo URL analysis**: Clone, scan, and bundle a complete report from any git URL

### Advanced Analysis
- **Python AST symbol indexing**: Class, function, and variable extraction
- **Import graph generation**: Dependency visualisation
- **Call graph generation**: Function call relationships
- **Runtime path analysis**: Execution flow tracing
- **Task memory recording**: Work history and token savings tracking

### Integration
- **Kilo file bridge**: No-MCP integration with Kilo
- **MCP server**: Model Context Protocol server mode
- **Continuous monitoring**: Interval-based watch mode
- **Autofix planning**: Small safe fix generation
- **PR summary generation**: Changes, risks, and suggested tests
- **Adapter prompts**: Tool-specific prompts for various AI coding assistants

### Standard Library Only
- **Pure Python**: No external dependencies beyond the standard library

---

## Quick Start

Install:
```bash
python -m pip install -e .
```

Scan a project:
```bash
python sentinel.py scan . --fast --compact
```

Open the dashboard:
```bash
python sentinel.py dashboard . --port 8765 --fast
```

Generate an HTML report:
```bash
python sentinel.py report . --format html
```

Generate a next-step prompt:
```bash
python sentinel.py prompt . --goal next --budget small --fast
```

Ask a question:
```bash
python sentinel.py ask . --question "where is authentication handled?" --fast
```

Analyse a GitHub repo:
```bash
python sentinel.py analyze-url https://github.com/user/repo --fast
```

---

## Dashboard

Open the GUI at `http://127.0.0.1:8765`:

- Stats dashboard with live health, files, lines, issues, signals, TODOs
- Tool cards for scan, ask, overview, retrieve, prompts, reports, verification, PR, memory, autofix
- Terminal output panel with artifact links
- Suggestions list and agent prompt panel
- Focus files, hotspots, and framework pills
- File risks and review signals tables
- Health timeline

All actions use shared inputs — query, repo URL, budget, goal, flags — so you don't re-enter context for every command.

---

## HTML Report

The HTML report is a single self-contained page with:

- **Health ring**: SVG donut chart color-coded by score
- **Stats bar**: Files, lines, issues, signals, TODOs at a glance
- **Project identity**: Type, archetype, purpose, workflow, recent changes
- **Risk summary**: Maintainability, runtime complexity, test signal, security
- **Top risk insight**: Most important single finding
- **Next actions**: Ranked suggestions with impact, effort, confidence
- **Hotspots**: Primary runtime, build, generator, test runner, documentation groups
- **Entry points**: Runtime, API surface, examples, build, generator categories
- **Components table**: Path, role, file count, line count
- **File risks**: By surface with level, score, and factors
- **Review signals**: Severity, message, file
- **Agent prompt**: Terminal-styled `$`-prefixed prompt block

---

## Project Layout

```
src/
  sentinel.py      Main orchestrator, CLI, dashboard GUI, MCP tools
  auditor.py       Scanning, auditing, identity, entry points, checkpoints
  reporter.py      Terminal, HTML, Markdown, JSON report generation
  classify.py      File classification, archetype detection, risk surface
  suggester.py     Prioritised next-step suggestion engine
  knowledge.py     Persistent knowledge base storage
  graph.py         AST symbols, imports, calls, dependencies, runtime paths
  verifier.py      Changed-file detection and focused test selection
  monitor.py       Continuous monitoring loop
  retriever.py     Context retrieval and project Q&A
  sentinel_mcp.py  MCP server surface
  utils.py         Shared helpers and defaults

config/
  config.json          Runtime configuration
  audit_rules.json     Risk and hotspot detection rules
  patterns.json        Code pattern matching rules

tests/                 Comprehensive regression test suite
docs/                  Architecture, API, user guide, install guide
```

---

## CLI Commands

| Command | Description |
|---|---|
| `scan` | Run a single project scan |
| `brief` | Tiny summary with the top suggestion |
| `overview` | Project structure, hotspots, workflow |
| `context` | Compact low-token context pack |
| `prompt` | Focused next-step prompt |
| `retrieve` | Query-specific files, symbols, snippets |
| `ask` | Answer a project question |
| `analyze-url` | Clone a git URL and write report bundle |
| `graph` | AST symbols, import graph, call graph |
| `verify` | Preview or run focused checks |
| `memory` | Record or list task memory |
| `savings` | Tracked token savings |
| `doctor` | Validate config and runtime paths |
| `dashboard` | Local live GUI |
| `autofix` | Plan or apply small safe fixes |
| `pr` | Summarise changes, risks, tests |
| `timeline` | Scan history, memory, savings |
| `mcp-health` | Validate MCP tool availability |
| `coverage` | Coverage.xml hotspot analysis |
| `cleanup-reports` | Archive old reports |
| `release-check` | Open-source readiness checklist |
| `features` | List commands with terminal animation |
| `adapters` | Tool-specific adapter prompts |
| `mcp` | Run as stdio MCP server |
| `kilo-setup` | Kilo configuration and rules |
| `kilo-bridge` | No-MCP file bridge setup |
| `kilo-refresh` | Refresh Kilo context files |
| `kilo-watch` | Continuous Kilo refresh |
| `watch` | Continuous monitoring |
| `report` | Save report (Markdown or HTML) |
| `status` | Latest saved status without rescan |

---

## Useful Flags

| Flag | Purpose |
|---|---|
| `--fast` | Trades scan depth for speed |
| `--compact` | Shorter output |
| `--quiet` | Suppresses log noise |
| `--top 1` | Keep only the most important suggestion |
| `--budget small` | Minimal context for token-sensitive workflows |
| `--budget medium` | More detail while controlling context |
| `--format json` | JSON output for scripts |
| `--ignore-path` | Exclude vendored or irrelevant paths |

---

## Token-Saving Workflow

```bash
project-sentinel overview . --fast --quiet
project-sentinel context . --budget small --fast --quiet
project-sentinel prompt . --goal next --budget small --fast --quiet
```

This gives an AI agent:
1. A clear project overview
2. A compact context pack
3. A focused next-step prompt
4. A small list of relevant files
5. A clearer verification path

---

## Development

```bash
python -m unittest discover -s tests -v
```

---

## Limitations

Sentinel produces review signals and AI-agent context, not guaranteed bug findings. It is not a replacement for SonarQube, Semgrep, or CodeQL. Always review recommendations before applying changes.

---

## Summary

Sentinel gives AI coding agents **memory, focus, and engineering judgement** before they touch your codebase.

It turns this:
```
Read everything. Guess what matters. Try a change. Hope the tests pass.
```

Into this:
```
Read the project brief. Inspect the right files. Take the highest-value next action. Verify with focused tests. Store what changed.
```

25,000 files, 6 million lines, one command, under a minute.
