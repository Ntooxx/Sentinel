<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/Ntooxx/Sentinel/main/assets/logo-dark.svg">
  <img alt="Sentinel" src="https://raw.githubusercontent.com/Ntooxx/Sentinel/main/assets/logo-light.svg">
</picture>

*Repo intelligence for AI coding agents. Scan → Understand → Act.*

---

```bash
pip install -e .
python sentinel.py scan . --fast
```

**197 tests · 0 failures · 25k files / 6M lines in 55s · no external dependencies**

---

## Scan Performance

| Target | Files | Lines | Time | Health |
|---|---|---|---|---:|
| Python library | 234 | 42k | **0.16s** | 86% |
| FastAPI web framework | ~1k | ~200k | **4.56s** | 74% |
| Kubernetes (k8s.io/kubernetes) | 25,432 | 6,007,991 | **55s** | 74% |
| Ladybird browser engine | ~40k | ~1.4M | ~40s | — |

No cloud. No external services. Pure Python.

---

## What Sentinel Produces

| Output | Description |
|---|---|
| **Project identity** | Name, type, archetype, purpose, language, frameworks, workflow |
| **Health score** | Maintainability, runtime complexity, test signal, security, with breakdown |
| **Entry points** | Primary runtime, API surfaces, examples, build tools, generators |
| **Hotspots** | Runtime, build, test runner, documentation, vendor — ranked by risk |
| **Review signals** | Oversized files, TODO density, documentation drift, test gaps |
| **Next actions** | Suggestions ranked by impact, effort, and confidence |
| **Agent prompt** | Ready-to-use prompt for Cline, Claude Code, Codex, Roo, Continue |
| **Context pack** | Compact token-efficient project brief |
| **Architecture summary** | Components, dependencies, archetype, patterns |
| **Risk scores** | Per-file scoring with deduplicated factors and test coverage |

---

## Test Suite

**197 tests · 0 failures · 9.3s run time**

| Suite | Tests | Scope |
|---|---|---|
| `test_archetype_regressions` | 11 | Archetype detection, entry point filtering, vendor classification |
| `test_auditor` | 18 | Checkpoints, file classification, maintainability, test signals |
| `test_classification_regressions` | 36 | File roles, risk surfaces, generated code, i18n, monorepo detection |
| `test_knowledge` | 2 | Knowledge base storage and export |
| `test_knowledge_repo` | 1 | Artifact repo detection |
| `test_ladybird_regressions` | 37 | Risk surface classification, hotspot filtering, focus files |
| `test_mcp` | 4 | MCP server lifecycle and tool listing |
| `test_regression_fixtures` | 28 | Full pipeline, identity resolution, purpose inference, HTML cleaning |
| `test_report_quality` | 40 | Project name extraction, entry points, health scoring, LLVM/rust detection |
| `test_sentinel` | 14 | CLI commands, HTML report generation, dashboard, cache, scan lifecycle |
| `test_weighted_entry_points` | 1 | Entry point directory weighting |

---

## Feature Highlights

### Project Name Resolution

Sentinel resolves project names through a 5-tier ranked fallback:

1. **Known repo names** — 22 entries: FastAPI, Kubernetes, TensorFlow, Flask, Django, React, PyTorch, NumPy, Pandas, Vite, Express, Tailwind CSS, and more
2. **Package manifests** — Cargo.toml, pyproject.toml, package.json, setup.py, go.mod, CMakeLists.txt
3. **Manifest descriptions** — extracted from the same manifests
4. **README body** — first real paragraph after headings
5. **README heading** — validated against blocked section keywords (Installation, Usage, Sponsors, etc.)

> Prevents "Sponsors" from being used as a project name when scanning FastAPI repos.

### Purpose Inference

A 6-step fallback chain that never returns a placeholder:

1. **Manifest description** — stripped of HTML/badges
2. **README body** — first real paragraph, skip badges/tables/HTML
3. **README summary** — already-cleaned summary field
4. **README doc_title subtitle** — extracts subtitle after colon or em-dash ("Kubernetes: Production-Grade Container Orchestration" → "Production-Grade Container Orchestration")
5. **Component-based generation** — built from non-test/doc component roles
6. **Final fallback** — "Purpose could not be confidently inferred from README."

> Prevents `----` from appearing as project purpose in Kubernetes scans.

### Entry Point Detection

Go binaries are detected even when not named `main.go`:

- `cmd/kube-apiserver/apiserver.go` → runtime entry point
- `cmd/kubelet/kubelet.go` → runtime entry point  
- `cmd/cloud-controller-manager/main.go` → runtime entry point

Major Go binaries get a +80 score bonus: `kube-apiserver`, `kubelet`, `kube-controller-manager`, `kube-scheduler`, `kubectl`, `kube-proxy`, `kubeadm`.

### Identity Text Safety

Sentinel filters out HTML tags, markdown links, badges, images, sponsor keywords, section headings, table artifacts, and decorative separators (`----`, `====`, etc.) from all identity fields — project name, type, purpose, and summary.

---

## HTML Report

The generated HTML report is a single self-contained page:

- **SVG health ring** — donut chart color-coded by score (green/gold/red)
- **Stats bar** — files, lines, issues, signals, TODOs
- **Project identity + risk** — definition lists in two-column card layout
- **Top risk insight** — accent-bordered card with the single most important finding
- **Next actions** — grid of suggestion cards with impact/effort/confidence badges
- **Hotspots + entry points** — grouped file pills by category
- **Components table** — path, role, file count, line count
- **File risks** — by surface with level, score, and factors
- **Review signals** — severity, message, file
- **Agent prompt** — terminal-styled `$`-prefixed block on dark background
- **Responsive** — degrades gracefully from desktop to 500px viewport

---

## Dashboard GUI

Dark-theme browser command centre at `http://127.0.0.1:8765`:

- **Stats row** — 6 metrics at a glance with color-coded values
- **Project identity + risk** — definition list cards
- **Shared inputs** — query, repo URL, budget, goal, flags — reused across all actions
- **Toggle pills** — fast scan, dry-run, apply, verify, adapters — border-fill toggle animation
- **Tool cards** — Understand, Ask, Reports, Quality, Memory, Maintenance, Analyze URL — with compact button grids
- **Output terminal** — monospace result panel with artifact links
- **Suggestions + prompt** — styled suggestion list and prompt block
- **Focus / hotspots / frameworks** — three-column pill row
- **File risks + review signals** — scrollable tables
- **Health timeline** — scan history with scores
- **Auto-refresh** — polls every 3 seconds

---

## Architecture

```
                          ┌─────────────────┐
                          │   sentinel.py    │  CLI, dashboard, MCP server
                          │   (4149 lines)   │
                          └────────┬────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              ▼                    ▼                    ▼
    ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
    │   auditor.py    │  │  reporter.py    │  │   suggester.py  │
    │   (2686 lines)  │  │  (1059 lines)   │  │   (738 lines)   │
    │ scanning, ids,  │  │ terminal, html, │  │ suggestion eng  │
    │ entry points,   │  │ markdown, json  │  │ confidence,     │
    │ checkpoints     │  │ reports         │  │ impact, effort  │
    └────────┬────────┘  └─────────────────┘  └─────────────────┘
             │
    ┌────────┴────────┐
    │  classify.py    │
    │  (1036 lines)   │
    │ file roles,     │
    │ archetypes,     │
    │ risk surfaces   │
    └─────────────────┘
```

---

## Commands

| Command | What It Does |
|---|---|
| `scan` | Analyse project structure, risks, hotspots |
| `brief` | One-line summary with the top suggestion |
| `overview` | Full project description with components, hotspots, workflow |
| `context` | Token-efficient project brief for AI agents |
| `prompt` | Focused next-step prompt with goal selection |
| `retrieve` | Find files, symbols, and snippets matching a query |
| `ask` | Answer a natural-language question about the project |
| `analyze-url` | Clone a git URL and generate a complete report bundle |
| `graph` | Extract AST symbols, import graph, call graph |
| `verify` | Preview or run focused tests for changed files |
| `dashboard` | Launch the live browser GUI |
| `report` | Save a Markdown or HTML report |
| `pr` | Summarise changes, risks, and suggested tests |
| `release-check` | Open-source readiness checklist |
| `coverage` | Identify weakly tested areas from coverage.xml |
| `timeline` | Show scan history, task memory, and token savings |
| `memory` | Record or list task memory |
| `savings` | Show estimated token savings |
| `autofix` | Plan or apply small safe fixes |
| `doctor` | Validate configuration and paths |
| `mcp` | Run as a stdio MCP server |
| `mcp-health` | Validate MCP tool availability |
| `kilo-setup` | Configure Kilo with Sentinel-first rules |
| `kilo-bridge` | Set up the no-MCP file bridge |
| `kilo-refresh` | Refresh Kilo context files before a task |
| `watch` | Continuously scan at an interval |

---

## Quick Start

```bash
# Install
python -m pip install -e .

# Scan
python sentinel.py scan . --fast

# Dashboard
python sentinel.py dashboard . --fast

# HTML report
python sentinel.py report . --format html

# Agent prompt
python sentinel.py prompt . --goal next --budget small --fast

# Ask a question
python sentinel.py ask . --question "where is authentication handled?" --fast

# Analyse a GitHub repo
python sentinel.py analyze-url https://github.com/user/repo --fast
```

---

## Product Flow

```
Open GUI ──> Scan ──> Ask / Retrieve ──> Generate Report ──> Export Prompt ──> Verify Changes
     │                     │                    │                   │
     └────── Repeat ───────┴───── iterate ──────┴──── iterate ──────┘
```

---

## Token-Saving Workflow

```bash
project-sentinel overview . --fast --quiet
project-sentinel context . --budget small --fast --quiet
project-sentinel prompt . --goal next --budget small --fast --quiet
```

Delivers to the AI agent:
1. Project overview
2. Compact context pack (~2500 tokens)
3. Focused next-step prompt
4. High-value focus files
5. Narrowed verification path

---

## Development

```bash
python -m unittest discover -s tests -v
```

**197 tests · 0 failures · 9.3 seconds.**

---

## Limitations

Sentinel produces review signals and AI-agent context, not guaranteed bug findings. It is not a replacement for SonarQube, Semgrep, or CodeQL. Always review recommendations before applying changes.

---

*25,000 files. 6 million lines. One command. Under a minute. No cloud.*
