# Sentinel 2.0 - Focused Real-Time Repo Quality Report

**Status:** Draft v1 - implementation blueprint  
**Owner:** Sentinel maintainers  
**Created:** 2026-10-23  
**Supersedes:** the multi-surface Sentinel 1.x roadmap for the next milestone  
**Tagline:** One command. One page. Live repository quality with evidence.

---

## 0. Executive decision

Sentinel 2.0 does **one thing**:

> Read a repository, build a fast incremental index, and produce a concise, evidence-backed quality report that updates live as code changes.

The public product is three commands:

```text
project-sentinel scan  <path>    # build/update index and print the report
project-sentinel report <path>   # render from the index without rescanning
project-sentinel watch  <path>   # keep the index and report live
```

Everything else is out of scope for this milestone: dashboard, MCP, Kilo integration, agent memory, savings, timeline, ledger, retrieve/ask, graph, verify, autofix, PR summaries, coverage, release-check, URL analysis, adapters, watch alerts, bundled reports, and the legacy monolith.

This is not a cosmetic trim. The current app is multiple products in one, and that is directly causing:

- contradictory behavior between `python sentinel.py` and installed `project-sentinel`;
- a green 197-test suite that does not test the real CLI;
- dashboard actions failing with undefined names;
- a 431 MB runtime state file;
- a scan that crashes on first run;
- a "high-level" report that is 388 lines and 151 mostly-duplicate signals.

The focused version should be smaller, faster, easier to test, and much more useful.

---

## 1. Why this pivot

### 1.1 Measured baseline

All numbers were measured on Windows, Python 3.11, local SSD, using the current working tree.

| Scenario | Result |
|---|---:|
| FastAPI cold fast scan | 4.78s total / 4.53s scan |
| FastAPI warm fast scan | 2.08s total / 1.78s scan |
| FastAPI warm full scan | 4.36s total / 4.04s scan |
| Sentinel self-scan, 271 files, no checkpoint | 0.22s scan |
| Sentinel self-scan, 271 files, with checkpoint | 3.77s scan |
| `status` on FastAPI | 0.26s |
| `status` on Sentinel (431 MB checkpoints) | 1.56s |
| `retrieve` on FastAPI | 2.66s including AST rebuild |
| `ask` on FastAPI | 4.64s including rescan and AST rebuild |
| `context --budget small` | 2.21s, 979-token pack |
| Current test suite | 197 passed in 10.65s |

### 1.2 The current runtime state problem

`.sentinel/checkpoints.json` in the Sentinel repo is:

- **431,607,934 bytes** (431.6 MB);
- **282 checkpoints**;
- **182 checkpoints created on one day**;
- up to **4.35 MB per checkpoint** when a 13,000-file scan was recorded;
- parsed on every process startup even for `status`;
- rewritten in full whenever a significant change creates a new checkpoint.

There is no retention cap. This is the single largest speed and disk problem.

### 1.3 The current report is not high-level

The FastAPI report we generated is:

- **388 lines**
- **33,988 characters**
- **151 review signals**
- dominated by repeated "large documentation file" and "documentation drift" entries across translations
- topped by suggestions about documentation translations and large docs, not source risk.

FastAPI's actual composition shows why:

| Category | Files | Size |
|---|---:|---:|
| `fastapi/` source | 48 `.py` | 0.74 MB |
| `tests/` | 581 `.py` | 2.78 MB |
| `docs/` | 1,784 | 28.64 MB |
| `docs_src/` examples | 454 `.py` | 0.22 MB |
| `scripts/` | 70 | 0.23 MB |
| other | 37 | 1.18 MB |

The scanner spends most of its time on documentation and then lets documentation dominate the quality report. A source-first scan of source + tests is about **4.5x less data** and removes most of the noise.

### 1.4 Defects found during the evaluation

These are already observed, not hypothetical:

1. `src/orchestrator.py` crashed every scan with `TypeError: '<' not supported between instances of 'dict' and 'dict'` because `sorted()` was called on dicts without a key.
2. `src/dashboard.py` was missing `normalize_budget_name`, `datetime`, `re`, and more than 20 render/action imports.
3. Dashboard actions `graph`, `verify`, memory, savings, doctor, autofix, pr, timeline, mcp-health, coverage, insights, alerts, ledger, speed-plan, release-check, and adapters returned HTTP 500.
4. `retrieve` and `ask` crashed on default Windows CP-1252 output when content contained non-Latin characters such as `ı`.
5. The 197 tests import the legacy `src/sentinel.py`, not the new `orchestrator.py` or `dashboard.py`.
6. `setup.py` still installs the legacy module list and does not declare the new package/entry point.
7. `retrieve` ranked translated docs and docs examples above implementation files for queries such as "where is routing implemented".
8. The report claimed "Estimated Token Savings: 100%" because 99.975% was rounded.
9. FastAPI was classified as a "JavaScript/Python project" because docs/assets influenced language detection.
10. `.url-cache` and nested vendored checkouts were not consistently ignored by the AST graph.

Some local fixes were applied during evaluation:

- `src/orchestrator.py`: added a stable sort key.
- `src/dashboard.py`: added missing `datetime` and `normalize_budget_name` imports.

Those files are part of the surface being retired in Sentinel 2.0. Do not build the new product on them.

---

## 2. Product definition

### 2.1 One-sentence promise

> Point Sentinel at any repository and get a one-page quality report in seconds, with the top source risks, evidence, confidence, and a live view that updates as the code changes.

### 2.2 Target users

- A developer joining a repo who needs to know where the risk is before touching code.
- A tech lead who wants a repeatable, local quality snapshot for a repo.
- A CI pipeline that needs a deterministic Markdown/JSON report and an optional quality gate.
- A developer who wants a live report while refactoring.

### 2.3 Jobs to be done

1. "Tell me what this repo is and how healthy its source is in under five seconds."
2. "Show me the five source problems worth fixing first, with file and line evidence."
3. "Tell me what changed since the last scan and whether quality improved."
4. "Keep the report current while I edit, without a cloud service or a dashboard server."
5. "Give me stable JSON so I can automate it in CI."

### 2.4 In scope

- Local repository scanning, git and non-git.
- Source-first file classification.
- Python analysis as the first-class language.
- Basic JS/TS, Go, Rust, C/C++ analysis using safe heuristics.
- Test mapping and test-signal reporting.
- Documentation quality for README and canonical docs only.
- Dependency manifest and repository hygiene checks.
- Basic secret/dangerous-pattern checks, clearly labeled as basic.
- Incremental index and live report.
- Terminal, Markdown, HTML, and JSON output.
- Zero runtime dependencies from the Python standard library.
- Optional use of `git` when available.

### 2.5 Explicit non-goals

Do not build or keep in the active product:

- dashboard or local HTTP server;
- MCP server, Kilo bridge, adapters, or agent prompts;
- natural-language chat, `ask`, `retrieve`, or semantic search;
- persistent agent memory, savings, timeline, ledger, or task notes;
- autofix, test execution, verification, PR generation, coverage ingestion;
- URL cloning or remote repo analysis;
- release-readiness checklists beyond the quality report;
- code search UI, embeddings, LLM calls, or telemetry;
- a "code database" marketing claim. It is an index;
- a promise that a 25k-file repo is fully re-read in under two seconds.

### 2.6 Success metrics

| Metric | Target |
|---|---|
| FastAPI cold scan, source + tests | <= 2.0s |
| FastAPI warm scan, no changes | <= 0.4s |
| FastAPI report render from index | <= 0.1s |
| FastAPI text report size | <= 200 lines / <= 12 KB |
| FastAPI source findings shown | exactly top 5 by default |
| Documentation-translation noise in top findings | zero |
| 10k source/test files cold | <= 8s |
| 10k warm | <= 1s |
| 25k cold | <= 30s |
| 25k warm | <= 2s |
| Watch update after save | <= 1s p95 |
| SQLite index for 25k files | <= 50 MB |
| Peak memory for 25k files | <= 300 MB |
| Test suite | < 30s, deterministic, real CLI paths |
| Static analysis | zero F821 undefined names |

---

## 3. User experience

### 3.1 Commands

```text
project-sentinel scan [PATH] [options]     Build/update index and print report.
project-sentinel report [PATH] [options]   Render from existing index; no scan.
project-sentinel watch [PATH] [options]    Scan, then keep report live.
project-sentinel --version
project-sentinel --help
```

A hidden/internal `doctor` may remain for support, but it is not part of the public three-command promise.

### 3.2 Flags

`scan`:

```text
--format text|md|html|json   Output format. Default: text.
--out PATH                   Write output to a file.
--full                       Analyze all source files fully; no fast sampling.
--include-docs               Analyze documentation content, not only metadata.
--all-findings               Print all findings, not only the top 5.
--fail-under BAND            Exit 3 if the quality band is below maintained/adequate/weak.
--ignore PATH                Repeatable; exclude a project-relative path.
--no-color                   Disable ANSI color.
--quiet                      Suppress logs and progress.
```

`report`:

```text
--format text|md|html|json
--out PATH
--open                       Open HTML report in the default browser.
--refresh                    Run a scan first, then render.
```

`watch`:

```text
--format text|html|md|json   Default: html.
--out PATH                   Default: .sentinel/quality.html
--interval SECONDS           Default: 1.0
--debounce MILLISECONDS      Default: 300
--include-docs
--full
--quiet
```

### 3.3 Exit codes

| Code | Meaning |
|---:|---|
| 0 | Success |
| 1 | Runtime error, invalid path, unreadable DB, or no index for `report` |
| 2 | Invalid CLI usage |
| 3 | Quality gate failed (`--fail-under`) |

### 3.4 Output rules

- Default terminal output is a one-page report.
- Markdown is suitable for PR comments or local files.
- HTML is a single self-contained file with inline CSS. No external assets.
- JSON is the stable contract for automation.
- No output contains absolute paths by default; use project-relative paths.
- No source code is embedded in the report except short evidence snippets; secrets are redacted.
- The report states what was read, sampled, skipped, and excluded.

### 3.5 Example terminal report

> Illustrative output. The FastAPI file/line counts come from the baseline scan; individual findings and the 92% mapping figure are examples of the intended format, not claims about the current build.

```text
SENTINEL QUALITY REPORT
FastAPI @ 6d5d3f2 (main, dirty) - 2,736 files / 356,919 lines
Scan: cold 4.5s / warm 1.8s | Index: 4.9 MB | Confidence: high

Quality band: MAINTAINED (provisional)
  Healthy tests and architecture; three large source files dominate risk.

WHAT TO FIX FIRST
1. fastapi/routing.py - 4,956 lines, 197 KB, high churn, 4 TODOs
   Why: largest source file and central routing path; risky to change blind.
   Next: map tests, then extract one cohesive concern at a time.

2. fastapi/applications.py - 4,691 lines, 181 KB, high import fan-in
   Why: large application surface with many responsibilities.
   Next: identify one boundary to extract with test coverage first.

3. fastapi/param_functions.py - 2,460 lines
   Why: oversized source module with repeated parameter patterns.
   Next: consider generated/declarative helpers only if tests prove parity.

4. fastapi/dependencies/utils.py - 1,057 lines
   Why: core dependency resolution path; high impact if changed.
   Next: add/confirm tests around edge cases before editing.

5. fastapi/openapi/utils.py - 606 lines
   Why: medium-size source file with high fan-in.
   Next: verify schema tests cover the paths you plan to change.

DIMENSIONS
  Architecture      A-    clear package boundaries; one CLI entry point
  Maintainability   B     3 source files over 2,000 lines; 14 source TODOs
  Tests             A-    581 test files; 92% of hotspots mapped to tests
  Documentation     B-    README good; 64 oversized docs, mostly translations
  Dependencies      B     lockfile present; 3 manifests
  Security          n/a   basic checks only; not a security audit

CHANGED SINCE LAST SCAN
  +3 files, ~12 modified, -0 deleted | quality -1

ACTION PLAN
1. Add tests around the top 3 hotspots before refactoring. (low effort, high impact)
2. Reduce routing.py and applications.py in small, test-backed moves. (high effort, high impact)
3. Group and archive translation-doc warnings; do not treat them as source quality. (low effort, medium impact)

COVERAGE
  Source: 100% read | Tests: 100% read | Docs: metadata only | Vendor/generated: excluded
  Security: basic checks only | Parse errors: 0
```

### 3.6 JSON contract

`--format json` emits a stable schema. Version 2 (version 1 published `summary.overall_grade`;
version 2 replaces it with `summary.band`):

```json
{
  "schema_version": 2,
  "generated_at": "2026-10-23T14:00:00+01:00",
  "project": {
    "name": "FastAPI",
    "root": ".",
    "git": {"commit": "6d5d3f2", "branch": "main", "dirty": true}
  },
  "scan": {
    "mode": "incremental",
    "duration_ms": 1800,
    "files_total": 2736,
    "files_analyzed": 12,
    "files_cached": 2724,
    "files_skipped": 143,
    "coverage": {
      "source": "100%",
      "tests": "100%",
      "docs": "metadata",
      "vendor": "excluded"
    }
  },
  "summary": {
    "band": "maintained",
    "overall_score": 87,
    "confidence": "high",
    "provisional": true,
    "headline": "Healthy tests and architecture; three large source files dominate risk."
  },
  "dimensions": [
    {
      "id": "architecture",
      "grade": "A-",
      "score": 91,
      "confidence": "high",
      "assessed": true,
      "summary": "Clear package boundaries and one CLI entry point.",
      "evidence": ["fastapi/__init__.py", "fastapi/cli.py"]
    }
  ],
  "metrics": {
    "source_files": 48,
    "test_files": 581,
    "source_lines": 19335,
    "test_lines": 74543,
    "test_to_source_file_ratio": 12.1,
    "test_to_source_line_ratio": 3.85,
    "source_todos": 14,
    "parse_errors": 0
  },
  "findings": [
    {
      "id": "MNT001:fastapi/routing.py",
      "rule_id": "MNT001",
      "dimension": "maintainability",
      "severity": "high",
      "confidence": "high",
      "title": "Oversized source file",
      "file": "fastapi/routing.py",
      "line": 1,
      "evidence": ["4,956 lines", "197 KB", "4 TODO markers", "high churn"],
      "why": "Large central module is harder to review and test safely.",
      "recommendation": "Map tests, then extract one cohesive concern at a time."
    }
  ],
  "changes": {
    "since_scan_id": 41,
    "added": 3,
    "modified": 12,
    "deleted": 0,
    "quality_delta": -1
  },
  "architecture": {
    "archetype": "framework_library",
    "components": [],
    "entry_points": [],
    "hotspots": []
  },
  "actions": [
    {
      "rank": 1,
      "title": "Add tests around the top 3 hotspots",
      "why": "The riskiest files lack obvious direct test coverage.",
      "impact": "high",
      "effort": "low"
    }
  ],
  "exclusions": {
    "vendor": 0,
    "generated": 0,
    "docs_translations": 1784,
    "reason": "Documentation translations and generated assets do not affect source quality."
  },
  "limitations": [
    "Security is a basic check, not a full audit.",
    "Documentation content is sampled unless --include-docs is used.",
    "Churn requires git history."
  ],
  "rules_version": "quality-rules-1"
}
```

The JSON is the source of truth. Text, Markdown, and HTML are renderings of this object.

---

## 4. Quality model

### 4.1 Dimensions

Use six dimensions maximum. Weights are initial and must be calibrated against real repos before being treated as precise.

| ID | Dimension | Weight | What it measures |
|---|---|---:|---|
| `architecture` | Architecture and structure | 20% | components, entry points, boundaries, cycles, layering |
| `maintainability` | Maintainability | 25% | file/function size, complexity, TODOs, parse errors |
| `tests` | Test confidence | 20% | test presence, test mapping, test/source ratio, hotspot coverage |
| `docs` | Documentation | 15% | README, canonical docs, drift, placeholder content |
| `dependencies` | Dependency and repo hygiene | 10% | manifests, lockfiles, pinning, license, ignore/CI files |
| `security` | Basic security checks | 10% | secrets and dangerous patterns only; explicitly not an audit |

If `security` is not assessed, exclude it and renormalize the remaining weights. Never show a security score based on no checks.

For docs-only or data-only repos, mark source dimensions as `n/a`; do not invent a source score.

### 4.2 Scoring

- Each dimension starts at 100.
- Apply rule penalties with per-rule caps.
- Scale absolute-count rules by repo maturity and use ratios/percentiles where possible.
- A repo with 10,000 source files must not fail simply because it has more TODOs than a 50-file repo.
- Findings are capped: the same root cause contributes once to a dimension, even if it affects many files.
- Overall score = weighted average of assessed dimensions.
- Overall band = the configured score band for that score. The band, not a letter grade, is the
  published overall claim.
- `provisional` is true until the rubric has been calibrated on at least 10 repositories and the `rules.calibrated` flag is true.
- No quality gate may use the overall score while `provisional` is true. Gates may use explicit finding rules instead.

Suggested penalty caps:

| Rule family | Max dimension penalty |
|---|---:|
| Oversized files | 15 |
| Complexity | 15 |
| TODOs | 5 |
| Parse errors | 20 |
| Missing tests | 30 |
| Test mapping gaps | 15 |
| Docs drift | 10 |
| Dependencies | 10 |
| Security basics | 15 |

### 4.3 Quality bands, grades and confidence

The overall result is a coarse band, not a letter grade. Bands allow ties and describe
maintenance evidence, not commit activity:

| Band | Score floor | Meaning |
|---|---:|---|
| `maintained` | 80 | Tests, docs and dependency hygiene are in place. |
| `adequate` | 70 | Usable, but carries real debt worth fixing. |
| `weak` | 60 | Significant structural problems; risky to depend on. |
| `unmaintained` | 0 | Little or no evidence of maintenance practice. |

The band is derived from the overall score, which remains available in the JSON contract for
trends and `--fail-under`. Letter grades are still used for **individual dimensions**, where they
are evidence for the band rather than a headline claim:

| Grade | Score |
|---|---:|
| A+ | 97-100 |
| A | 93-96 |
| A- | 90-92 |
| B+ | 87-89 |
| B | 83-86 |
| B- | 80-82 |
| C+ | 77-79 |
| C | 73-76 |
| C- | 70-72 |
| D | 60-69 |
| F | <60 |

Confidence:

- **high:** >=95% of source and tests analyzed, parse success >=99%, no source file sampling.
- **medium:** >=80% coverage, or docs sampled, or 95-99% parse success.
- **low:** <80% coverage, many parse errors, or a large unsupported-language surface.

If confidence is low:

- append `?` to dimension grades, e.g. `B?`, and never present the band as precise;
- mark `provisional: true`;
- list the reason in `limitations`.

### 4.4 Findings catalog

Each finding has a stable rule ID, dimension, default severity, evidence, and recommendation. Rules may be disabled or tuned in `config/quality_rules.json`.

#### Architecture

| ID | Rule | Default |
|---|---|---|
| ARCH001 | Application has no clear entry point | medium |
| ARCH002 | Import cycle between source modules | medium |
| ARCH003 | Source package has no clear public surface | low |
| ARCH004 | Multiple competing entry points in a small app | low |

#### Maintainability

| ID | Rule | Default |
|---|---|---|
| MNT001 | Source file > 500 lines | medium |
| MNT002 | Source file > 2,000 lines | high |
| MNT003 | Source file > 100 KB | medium |
| MNT004 | Source file > 250 KB | high |
| MNT005 | Function > 100 lines | medium |
| MNT006 | Cyclomatic complexity > 15 | medium |
| MNT007 | Cyclomatic complexity > 25 | high |
| MNT008 | TODO/FIXME density above threshold in source | low |
| MNT009 | Python parse error | high |
| MNT010 | File unreadable or encoding-replaced | low |
| MNT011 | Duplicate code block (optional, later) | low |

#### Tests

| ID | Rule | Default |
|---|---|---|
| TST001 | No test files detected | high |
| TST002 | Test/source ratio below archetype threshold | medium |
| TST003 | High-risk hotspot has no mapped test | medium |
| TST004 | Test file > 1,000 lines | low |
| TST005 | Many TODOs/FIXMEs in tests | low |
| TST006 | Test suite exists but no tests exercise the main entry path | medium |

"Test mapping" means static evidence that a test file imports or names a source module. It is not runtime coverage and must not be labeled as coverage.

#### Documentation

| ID | Rule | Default |
|---|---|---|
| DOC001 | No README | medium |
| DOC002 | README contains placeholders/TODOs | medium |
| DOC003 | Canonical docs show drift indicators | low |
| DOC004 | Canonical docs have broken relative links | low |
| DOC005 | README is extremely large or unstructured | low |
| DOC006 | Docs translations are the only documentation | info, not scored |

Only README and canonical docs are scored. Translation folders are excluded from source and docs scores; they may be summarized as a hygiene note.

#### Dependencies and hygiene

| ID | Rule | Default |
|---|---|---|
| DEP001 | No dependency manifest | medium |
| DEP002 | Manifest present but no lockfile | low |
| DEP003 | Unpinned direct dependencies | low |
| DEP004 | No license file | medium for release profile |
| HYG001 | No `.gitignore` | low |
| HYG002 | No CI configuration | low |
| HYG003 | Generated/vendor files are tracked as source | low |
| HYG004 | Very large generated or vendored file count | info |

#### Security basics

| ID | Rule | Default |
|---|---|---|
| SEC001 | Secret-like string in a tracked file | high, low-confidence |
| SEC002 | Dangerous dynamic execution pattern | medium |
| SEC003 | Hardcoded debug mode or credentials | medium |

Security rules must redact the matched value in all output. The report must state: "Basic pattern checks only; not a security audit."

#### Hotspots and churn

| ID | Rule | Default |
|---|---|---|
| HOT001 | High-churn + high-complexity source file | medium/high |
| HOT002 | High-churn hotspot with no mapped test | medium |
| HOT003 | Recently changed hotspot with no recent test change | low |

Churn is optional and requires git. If git is unavailable, skip HOT rules and say so.

### 4.5 Noise control

The report must not repeat near-identical findings. Rules:

- Group findings by `rule_id + root cause`.
- For repeated documentation issues, emit one summary finding with counts and examples.
- Limit the default report to **top 5 findings**.
- Full findings are available only with `--all-findings` or in JSON.
- Never let translations, vendor, generated, or examples outrank source.
- Deduplicate evidence paths.
- Cap each category in the top 5 to at most 2 findings unless there is no other signal.
- If only one finding exists, show one; do not pad.

### 4.6 Determinism

- Same commit + same rules version + same options = byte-identical JSON (excluding `generated_at` and duration fields).
- Sort findings by `(severity, confidence, dimension, path, rule_id)` with explicit tie-breakers.
- Sort components, files, and actions.
- No randomness, no clock-dependent scoring.
- Golden tests compare reports with timestamps normalized.

### 4.7 Report contract

Every report must answer:

1. What is this repo?
2. How healthy is the source?
3. What are the top risks, with evidence?
4. What changed since last time?
5. What should I do next?

Every report must state:

- scan mode and duration;
- cold vs warm;
- what was read, sampled, skipped, and excluded;
- confidence and its reason;
- limitations, especially security and non-Python languages.

---

## 5. Architecture

### 5.1 Principles

1. **One implementation.** No duplicate CLI, dashboard, or scanner paths.
2. **Index once, render many.** Report generation never rescans.
3. **Incremental by default.** Only changed files are analyzed.
4. **Source-first.** Docs and vendor do not drive source quality.
5. **Standard library only at runtime.** `sqlite3`, `ast`, `hashlib`, `concurrent.futures`, `json`, `pathlib`.
6. **Optional git.** Use git for discovery and churn when present; degrade cleanly.
7. **Deterministic output.** Reports are diffable and testable.
8. **Boring, small surface.** Three commands, one package, one entry point.

### 5.2 Module layout

Recommended package layout:

```text
src/sentinel2/
  __init__.py
  __main__.py
  cli.py              # argparse, command dispatch, exit codes
  config.py           # defaults, config loading, rules loading
  discover.py         # git/walk discovery, ignore rules
  classify.py         # file roles, archetypes, language detection
  analyze.py          # per-file facts, AST, complexity, metadata
  index.py            # SQLite schema, migrations, queries, retention
  quality.py          # dimension scoring, confidence, quality band
  findings.py         # rules, ranking, grouping, evidence
  report.py           # build report object from index
  renderers/
    __init__.py
    text.py
    markdown.py
    html.py
    json.py
  watch.py            # polling/watch loop, debounce, atomic writes
  utils.py
config/
  quality_rules.json
tests/
  fixtures/
  golden/
  test_cli.py
  ...
```

The old flat modules (`src/sentinel.py`, `src/auditor.py`, `src/orchestrator.py`, `src/dashboard.py`, `src/cli.py`, `src/dashboard.html`, etc.) are retired. Port only the useful logic into `sentinel2`.

### 5.3 Data flow

```text
CLI
  -> discover candidates
  -> classify before reading
  -> stat/hash/cache check
  -> analyze changed files (parallel)
  -> write facts/findings to SQLite
  -> compute quality from SQL
  -> build report JSON
  -> render text/md/html/json
```

`report` skips the first five steps and renders the latest scan snapshot from SQLite.

`watch` runs the scan pipeline for changed paths, then re-renders.

### 5.4 Discovery

Priority order:

1. If `.git` exists and `git` is available: `git ls-files -z` and `git status --porcelain -z`.
2. Otherwise: `os.scandir` walk with a deterministic sorted order.
3. Always apply: default excludes, `.gitignore` rules when git is available, user `--ignore` paths, and generated/vendor rules.
4. Skip by default: `.git`, `.sentinel`, `node_modules`, `__pycache__`, `.venv`, `venv`, `dist`, `build`, `.tox`, `.nox`, `.mypy_cache`, `.ruff_cache`, `.pytest_cache`, `.idea`, `.vscode`, `coverage`, `htmlcov`, `sentinel-url-reports`.
5. Do not follow symlinks by default. Count skipped symlinks.
6. Respect `max_file_size`; skip oversized files and record them in coverage.
7. Binary detection: null byte in the first 8 KB.
8. Encoding: UTF-8 with replacement; count replacements.

### 5.5 Analysis

Per-file facts to store:

- language, role, size, line count, mtime, content hash;
- source/test/doc/vendor/generated flags;
- parse success/failure;
- imports and symbols;
- function count, class count, max function length, max complexity;
- TODO/FIXME count;
- test names and mapped source modules;
- metadata for manifests and README.

Language strategy:

| Language | Strategy |
|---|---|
| Python | `ast` for imports, symbols, function length, complexity, test mapping |
| JS/TS | line/regex heuristics; optional conservative import extraction |
| Go | regex for package, funcs, imports |
| Rust | regex for fn/struct/enum/trait/use |
| C/C++ | regex for includes, functions, classes |
| Markdown | headings, links, placeholders, size |
| JSON/YAML/TOML | manifest keys only |
| Other | metadata only, excluded from source quality |

Do not claim deep semantic analysis for languages beyond Python.

### 5.6 SQLite schema

Database path: `.sentinel/quality.db`.

```sql
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;
PRAGMA temp_store = MEMORY;

CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS files (
  path TEXT PRIMARY KEY,
  language TEXT,
  role TEXT,
  size INTEGER NOT NULL,
  lines INTEGER NOT NULL,
  mtime_ns INTEGER NOT NULL,
  content_hash TEXT,
  is_source INTEGER NOT NULL DEFAULT 0,
  is_test INTEGER NOT NULL DEFAULT 0,
  is_doc INTEGER NOT NULL DEFAULT 0,
  is_vendor INTEGER NOT NULL DEFAULT 0,
  is_generated INTEGER NOT NULL DEFAULT 0,
  is_config INTEGER NOT NULL DEFAULT 0,
  analysis_version INTEGER NOT NULL DEFAULT 0,
  parse_error TEXT,
  encoding_replaced INTEGER NOT NULL DEFAULT 0,
  last_analyzed_at TEXT,
  skipped_reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_files_source ON files(is_source);
CREATE INDEX IF NOT EXISTS idx_files_language ON files(language);
CREATE INDEX IF NOT EXISTS idx_files_role ON files(role);

CREATE TABLE IF NOT EXISTS symbols (
  file TEXT NOT NULL,
  name TEXT NOT NULL,
  qualname TEXT NOT NULL,
  kind TEXT NOT NULL,
  line INTEGER NOT NULL,
  end_line INTEGER NOT NULL,
  complexity INTEGER NOT NULL DEFAULT 0,
  length INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (file, qualname, line),
  FOREIGN KEY (file) REFERENCES files(path) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(file);
CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);

CREATE TABLE IF NOT EXISTS imports (
  file TEXT NOT NULL,
  imported TEXT NOT NULL,
  kind TEXT NOT NULL,
  PRIMARY KEY (file, imported, kind),
  FOREIGN KEY (file) REFERENCES files(path) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS findings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  rule_id TEXT NOT NULL,
  evidence_key TEXT NOT NULL,
  file TEXT,
  dimension TEXT NOT NULL,
  severity TEXT NOT NULL,
  confidence TEXT NOT NULL,
  title TEXT NOT NULL,
  evidence_json TEXT NOT NULL,
  why TEXT NOT NULL,
  recommendation TEXT NOT NULL,
  first_seen_scan INTEGER,
  last_seen_scan INTEGER,
  UNIQUE (rule_id, evidence_key)
);

CREATE INDEX IF NOT EXISTS idx_findings_scan ON findings(last_seen_scan);
CREATE INDEX IF NOT EXISTS idx_findings_file ON findings(file);
CREATE INDEX IF NOT EXISTS idx_findings_rule ON findings(rule_id);

CREATE TABLE IF NOT EXISTS scans (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT NOT NULL,
  finished_at TEXT NOT NULL,
  mode TEXT NOT NULL,
  commit_hash TEXT,
  branch TEXT,
  dirty INTEGER NOT NULL DEFAULT 0,
  duration_ms INTEGER NOT NULL,
  files_total INTEGER NOT NULL,
  files_analyzed INTEGER NOT NULL,
  files_cached INTEGER NOT NULL,
  files_skipped INTEGER NOT NULL,
  rules_version TEXT NOT NULL,
  report_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scan_files (
  scan_id INTEGER NOT NULL,
  path TEXT NOT NULL,
  status TEXT NOT NULL,
  PRIMARY KEY (scan_id, path),
  FOREIGN KEY (scan_id) REFERENCES scans(id) ON DELETE CASCADE
);
```

Retention:

- Keep the latest **20** scans.
- Delete older `scans` rows; cascade `scan_files`.
- Current `files`, `symbols`, `imports`, and `findings` reflect the latest scan.
- Findings that disappear are deleted or marked resolved; v1 deletes them from current findings and keeps history in `scans.report_json`.

### 5.7 Incremental algorithm

For each candidate path:

1. `stat` the file.
2. Look up `files.path`.
3. If size and mtime match, reuse cached facts and mark cache hit.
4. If changed, hash the file.
5. If hash matches, update mtime only.
6. If hash differs, analyze and upsert facts.
7. Delete rows for files no longer present.
8. Recompute findings for changed files and for global rules.
9. Insert a scan row with report JSON.
10. Delete old scans beyond retention.

Race handling:

- Re-stat after reading; if size/mtime changed during analysis, retry once.
- If still changing, skip with `skipped_reason = "changed_during_scan"`.

Batch writes:

- Use one transaction per batch of 200 files.
- `executemany` for symbols/imports.
- Commit before rendering.

### 5.8 Watch design

MVP:

- Poll every 1.0s by default.
- If git is available, use `git status --porcelain -z`; else walk source/test directories only.
- Debounce changes for 300ms.
- Re-analyze only changed paths.
- Recompute quality and render.
- Write report atomically: write `quality.html.tmp`, then `os.replace`.
- Ignore `.sentinel/quality.db`, `-wal`, `-shm`, and the report file itself.
- On error, keep the last good report and print a warning.
- Ctrl+C exits cleanly.

HTML live update:

- Single self-contained HTML with inline CSS.
- Use `<meta http-equiv="refresh" content="2">` for a simple live file view, or a small inline script that reloads.
- No server, no WebSocket, no dashboard.
- `watch --out quality.html` is the live view.

Later optimization:

- Windows `ReadDirectoryChangesW` via `ctypes`; Linux inotify.
- Keep polling fallback.
- Do not add a third-party watcher dependency.

### 5.9 Report generation

- Read one scan snapshot from `scans.report_json`.
- If `report --refresh`, run the scan pipeline first.
- Render pure functions; no file I/O during rendering except writing output.
- HTML must escape all repository-controlled strings.
- Text must be readable at 80-120 columns.
- Markdown must be valid CommonMark.
- JSON must validate against the schema in `sentinel_2.md`.

### 5.10 Performance design

- Skip docs content by default; read metadata only.
- Read full source files up to 1 MB; sample larger files and flag them.
- Parallelize analysis with `ThreadPoolExecutor`, max 32 workers.
- Do not re-hash unchanged files.
- Do not rebuild the AST for report rendering.
- Compute aggregates with SQL `SUM`/`COUNT`.
- Avoid JSON state files entirely.
- Keep one SQLite writer; readers see WAL snapshots.
- Measure cold, warm, report, and watch separately.

---

## 6. Implementation plan

### M0 - Safety and baseline (0.5 day)

Tasks:

- [ ] Create branch `sentinel-2`.
- [ ] Tag or commit the current working tree as `legacy-wip` so the cut is reversible.
- [ ] Record baseline numbers from section 1.1.
- [ ] Back up `.sentinel/checkpoints.json`; then remove it from the working tree after approval.
- [ ] Add `.sentinel/` to `.gitignore`.
- [ ] Write the new package skeleton and a failing CLI smoke test.

Acceptance:

- Work is recoverable from a branch/tag.
- The 431 MB state file is no longer part of the active runtime.
- A test proves `project-sentinel --version` works.

### M1 - CLI cut (0.5-1 day)

Tasks:

- [ ] Create `src/sentinel2/cli.py` with `scan`, `report`, `watch`.
- [ ] Remove all other commands from the help and dispatch.
- [ ] Add `--format`, `--out`, `--ignore`, `--quiet`, `--no-color`.
- [ ] Add `--version`.
- [ ] Update packaging to install `sentinel2` and the `project-sentinel` console script.
- [ ] Remove the legacy setup/module list.
- [ ] Ensure `python -m sentinel2` works.

Acceptance:

- `pip install .` in a clean venv gives a working `project-sentinel`.
- `project-sentinel --help` lists only the supported commands.
- `python sentinel.py` and `project-sentinel` run the same code.

### M2 - Discovery and SQLite index (1-2 days)

Tasks:

- [ ] Implement `discover.py` with git and walk modes.
- [ ] Implement `classify.py` by porting the useful role/archetype logic.
- [ ] Implement `index.py` with the schema, migrations, WAL, retention.
- [ ] Implement stat/hash/upsert/delete.
- [ ] Implement `scan` end-to-end without quality scoring.
- [ ] Add integration tests for cold, warm, changed, deleted, renamed, ignored, binary, unicode.
- [ ] Add `PRAGMA integrity_check` test.

Acceptance:

- FastAPI cold scan < 5s at this stage.
- FastAPI warm scan < 1s.
- Second scan analyzes zero unchanged files.
- Deleting a file removes it from the index.
- Database integrity check passes.

### M3 - Analysis and quality model (2-3 days)

Tasks:

- [ ] Implement Python AST facts: imports, symbols, function length, complexity.
- [ ] Implement basic non-Python heuristics.
- [ ] Implement test mapping.
- [ ] Implement findings rules and ranking.
- [ ] Implement dimension scoring and confidence.
- [ ] Implement top-5 grouping and noise control.
- [ ] Add rule fixtures and golden tests.
- [ ] Add source-first filters and docs translation exclusion.

Acceptance:

- FastAPI report shows five source findings, not doc translations.
- A known-bad fixture produces the expected findings.
- A docs-only fixture produces no source score.
- Same fixture produces identical JSON twice.

### M4 - Report renderers (1 day)

Tasks:

- [ ] Implement JSON schema v1.
- [ ] Implement text renderer with ANSI color.
- [ ] Implement Markdown renderer.
- [ ] Implement self-contained HTML renderer.
- [ ] Add golden tests for all formats.
- [ ] Add HTML escaping test.
- [ ] Enforce report size budget.

Acceptance:

- Text report <= 200 lines / 12 KB on FastAPI.
- HTML is self-contained and opens offline.
- JSON validates against the schema.
- All repository-controlled strings are escaped.

### M5 - Watch mode (1 day)

Tasks:

- [ ] Implement polling discovery for source/test paths.
- [ ] Implement debounce and changed-path collection.
- [ ] Implement incremental analysis and atomic report write.
- [ ] Add subprocess-based watch test.
- [ ] Add Ctrl+C cleanup test if feasible.

Acceptance:

- Modify a source file; report updates within 1s p95.
- No full rescan on each change.
- Report is never partially written.
- Watch ignores its own DB and output files.

### M6 - Performance and hardening (1-2 days)

Tasks:

- [ ] Optimize cold/warm paths against budgets.
- [ ] Add synthetic 10k/25k repo generator.
- [ ] Add benchmark harness and baseline JSON.
- [ ] Add Windows path/encoding tests.
- [ ] Run privacy audit (no network, no telemetry).
- [ ] Run SQLite integrity and retention audit.
- [ ] Remove dead code and legacy modules.
- [ ] Run `ruff`, `compileall`, and full tests.

Acceptance:

- All budgets in section 10 met or explicitly waived.
- Windows default console does not crash on non-ASCII output.
- No F821 undefined names.
- No network calls during scan/report/watch.

### M7 - Release (0.5 day)

Tasks:

- [ ] Rewrite README for the three-command product.
- [ ] Update CHANGELOG to 2.0.0.
- [ ] Update AGENTS.md and contributor docs.
- [ ] Verify clean install from wheel in a fresh venv.
- [ ] Verify `project-sentinel` and `python -m sentinel2`.
- [ ] Confirm no `.sentinel` or test fixtures are included in the wheel.
- [ ] Tag `v2.0.0`.

Acceptance:

- Fresh install works on Windows and Linux.
- CI is green.
- Release audit checklist signed off.

### Work breakdown summary

| Milestone | Deliverable | Estimate |
|---|---|---:|
| M0 | Safety, branch, baseline | 0.5 day |
| M1 | Three-command CLI | 0.5-1 day |
| M2 | SQLite incremental index | 1-2 days |
| M3 | Quality model and findings | 2-3 days |
| M4 | One-page renderers | 1 day |
| M5 | Live watch mode | 1 day |
| M6 | Performance, tests, audits | 1-2 days |
| M7 | Release | 0.5 day |
| **Total** | | **7-10 days** |

---

## 7. Testing strategy

### 7.1 Test pyramid

- **Unit:** classification, hashing, SQLite, scoring, rules, renderers.
- **Integration:** real CLI on fixture repos, cold/warm/deleted/changed.
- **Golden:** full report snapshots for representative repos.
- **Performance:** cold/warm/report/watch budgets.
- **Audit:** privacy, determinism, packaging, Windows.

No test may import the legacy monolith. Tests must exercise the same entry point users run.

### 7.2 Unit tests

| File | Covers |
|---|---|
| `test_discover.py` | git/non-git, ignores, symlinks, unicode, max size, binary |
| `test_classify.py` | source/test/doc/vendor/generated/config roles; archetypes |
| `test_index.py` | schema, migrations, upsert, delete, retention, WAL, integrity |
| `test_analyze.py` | AST imports/symbols/complexity, parse errors, non-Python heuristics |
| `test_quality.py` | dimension scores, weights, caps, maturity scaling, confidence |
| `test_findings.py` | each rule positive/negative, grouping, ranking, top 5 |
| `test_report.py` | JSON schema, text/md/html rendering, escaping, size |
| `test_cli.py` | help, version, flags, exit codes, invalid paths, report without index |
| `test_watch.py` | debounce, changed paths, atomic write, stop |
| `test_privacy.py` | no network, no writes outside allowed paths, secret redaction |
| `test_windows.py` | drive paths, CRLF, unicode filenames, default console encoding |
| `test_perf.py` | budgets and regression baseline |

### 7.3 Integration tests

Use real subprocesses, not mocked CLI internals:

1. `scan` a clean fixture; assert DB exists, JSON parses, report contains the expected grade.
2. Run `scan` again; assert `files_analyzed == 0` and duration is lower.
3. Modify a source file; assert finding appears and quality changes.
4. Delete a file; assert it disappears from the index and report.
5. Rename a file; assert old path is removed and new path is present.
6. Add a huge file; assert MNT002/MNT004 finding.
7. Add a test file; assert TST001 resolves and test mapping improves.
8. Run `report` without a DB; assert exit 1 and a clear message.
9. Run `report` after `scan`; assert it does not modify the DB mtime or run analysis.
10. Run `watch` as a subprocess; touch a file; poll the HTML output; assert content changes within 1s.

### 7.4 Golden reports

Store normalized snapshots in `tests/golden/`:

- `fastapi.json`, `fastapi.md`, `fastapi.html`
- `requests.json`
- `messy_python.json`
- `clean_python_app.json`
- `docs_only.json`
- `monorepo.json`

Normalize timestamps, durations, absolute paths, and machine-specific values before comparing.

Golden rules:

- Top 5 findings must be source findings on FastAPI.
- No translation docs in top 5.
- Docs-only repo must not get a source grade.
- Clean fixture must not produce high-severity findings.
- Messy fixture must produce the expected top 5 in the expected order.

### 7.5 Fixtures

Create under `tests/fixtures/`:

| Fixture | Purpose |
|---|---|
| `clean_python_app` | healthy app with tests and README |
| `messy_python` | huge file, complex function, TODOs, no tests |
| `library_package` | library without runtime entry point |
| `monorepo` | packages/apps layout |
| `docs_only` | README/docs, no source |
| `generated_vendor` | generated and vendored code that must be excluded |
| `unicode_names` | non-ASCII paths and content |
| `broken_syntax` | Python parse error |
| `minified_js` | large minified JS file |
| `empty_repo` | empty directory |
| `symlink_loop` | symlink cycle, skipped by default |
| `large_10k` | generated at test time, not committed |

### 7.6 Performance tests

- Run 3 times, take the median.
- Report cold, warm, report-only, and watch latency.
- Store results in `benchmarks/results/`.
- Fail if a budget is exceeded by more than 20% over baseline.
- Keep performance tests out of the default unit test run; run in CI on a dedicated job.
- Machine-independent assertions use generous upper bounds, not exact timings.

Benchmark targets:

| Repo | Cold | Warm | Report |
|---|---:|---:|---:|
| fixtures (2-3 files) | < 0.2s | < 0.1s | < 0.05s |
| requests (136 files) | < 0.8s | < 0.2s | < 0.05s |
| FastAPI source+tests (~630 files) | < 2.0s | < 0.4s | < 0.1s |
| synthetic 10k | < 8s | < 1s | < 0.1s |
| synthetic 25k | < 30s | < 2s | < 0.1s |

### 7.7 Windows and encoding

- Never rely on the default console encoding for output.
- Configure `sys.stdout`/`sys.stderr` with `errors="replace"` or UTF-8 at startup.
- Test with repository content containing Turkish, Chinese, Japanese, emoji, and accented filenames.
- Test paths with spaces, drive letters, backslashes, and long names.
- Test CRLF and LF files.
- A `retrieve`-style output must never crash with `charmap`.

### 7.8 Privacy and security tests

- Monkeypatch `socket.socket` and `urllib` to raise; scan/report/watch must not call the network.
- Assert no telemetry or analytics code.
- Assert no writes outside `.sentinel/`, the requested output path, and explicit user paths.
- Assert secret-like values are redacted in text/MD/HTML/JSON.
- Assert report paths are relative, not absolute, unless the user requests otherwise.
- Assert HTML escapes `<script>` and quotes from filenames.

### 7.9 CI

Matrix:

- OS: Windows and Ubuntu.
- Python: 3.10, 3.12, 3.13.
- Steps: install, ruff, unit/integration, golden, wheel install smoke test.
- Performance job: Ubuntu only, Python 3.12, fastapi + synthetic 10k.
- Windows job: encoding and path tests.
- Nightly: synthetic 25k performance.

Commands:

```bash
python -m pytest -q -m "not perf"
python -m pytest -q -m perf
ruff check .
python -m compileall -q src
python -m build
python -m pip install dist/*.whl
project-sentinel --version
project-sentinel scan tests/fixtures/clean_python_app --format json
```

### 7.10 Definition of done

- [ ] Three commands only.
- [ ] All tests pass on Windows and Linux.
- [ ] No F821 undefined names.
- [ ] FastAPI warm scan <= 0.4s.
- [ ] FastAPI cold source+tests <= 2.0s.
- [ ] Report <= 200 lines / 12 KB.
- [ ] Top 5 findings are source-first.
- [ ] No translation-doc noise in top findings.
- [ ] SQLite only; no checkpoint JSON.
- [ ] Watch updates within 1s.
- [ ] Clean install from wheel works.
- [ ] Privacy audit passes.
- [ ] Release audit signed off.

---

## 8. Auditing

Auditing is a scheduled activity, not a final cleanup. Run the relevant audit at the end of every milestone.

### 8.1 Audit cadence

| When | Audit |
|---|---|
| Before M0 | Repository snapshot and baseline |
| After M1 | CLI/packaging audit |
| After M2 | Data/index audit |
| After M3 | Quality/report audit |
| After M4 | Rendering/security audit |
| After M5 | Watch/incremental audit |
| After M6 | Performance/privacy audit |
| Before release | Full release audit |

### 8.2 Code audit

Run:

```bash
ruff check . --select E,F,W,I,N,UP,B,SIM,ARG,F821
python -m compileall -q src tests
python -m pytest -q
```

Check:

- [ ] No `F821` undefined names.
- [ ] No duplicate implementations.
- [ ] No dead modules imported.
- [ ] No broad `except Exception` that hides scan failures.
- [ ] No silent skips; every skipped file has a reason.
- [ ] No timestamp/clock use in scoring.
- [ ] All public functions have type hints.
- [ ] CLI help matches dispatch.
- [ ] Errors go to stderr with a non-zero exit code.

### 8.3 Architecture audit

- [ ] One entry point: `sentinel2.cli:main`.
- [ ] No `dashboard`, `mcp`, `kilo`, `retriever`, `graph`, `verifier` imports in the active package.
- [ ] `report` has no scan side effects.
- [ ] `watch` uses the same scan pipeline as `scan`.
- [ ] SQLite is the only persistent state.
- [ ] No JSON cache or checkpoint files.
- [ ] Import graph has no cycle.
- [ ] Package installs without `src` on `PYTHONPATH`.

### 8.4 Data audit

- [ ] `PRAGMA integrity_check` returns `ok`.
- [ ] WAL mode is active.
- [ ] Foreign keys are on.
- [ ] Retention keeps at most 20 scans.
- [ ] Deleting a scan cascades `scan_files`.
- [ ] Deleting a file cascades symbols/imports.
- [ ] Findings are deduplicated by `rule_id + evidence_key`.
- [ ] Index size stays under budget.
- [ ] No unbounded table growth.

### 8.5 Quality audit

Run the report on at least:

- FastAPI
- requests
- this repository
- `clean_python_app`
- `messy_python`
- `docs_only`
- `monorepo`

For each:

- [ ] Top 5 findings are source findings where source exists.
- [ ] Docs translations do not appear in top findings.
- [ ] Findings cite real file/line evidence.
- [ ] No obvious false positive in the top 5.
- [ ] Score/grade matches a manual review.
- [ ] Confidence matches coverage.
- [ ] Report states limitations.
- [ ] Report fits the size budget.
- [ ] Same input produces the same output.

Manual review questions:

1. Would a senior engineer agree with finding #1?
2. Is finding #2 actionable?
3. Is anything important missing?
4. Is anything shown that should be excluded?
5. Does the report answer "what should I do next?"

If the answer to #1 or #2 is no, fix the rubric before release.

### 8.6 Performance audit

Measure:

- cold scan;
- warm scan with no changes;
- warm scan after 10 changed files;
- report-only;
- watch latency after save;
- DB size;
- peak RSS;
- CPU during idle watch.

Check:

- [ ] Budgets met.
- [ ] No O(n^2) behavior.
- [ ] No full re-hash on warm scan.
- [ ] No AST rebuild for report.
- [ ] No full docs read by default.
- [ ] Watch does not rescan the world on every event.
- [ ] SQLite queries use indexes (`EXPLAIN QUERY PLAN`).

### 8.7 Privacy and security audit

- [ ] No network imports or calls during scan/report/watch.
- [ ] No telemetry.
- [ ] No upload or remote logging.
- [ ] Secret matches are redacted.
- [ ] HTML escapes repository-controlled strings.
- [ ] Path traversal cannot escape the project root.
- [ ] Symlink loops are skipped.
- [ ] Oversized files are skipped safely.
- [ ] Crash dumps do not include source contents.
- [ ] `.sentinel` state is ignored by discovery.
- [ ] Report paths are relative by default.

This is not a security audit of the scanned repository. It is an audit of Sentinel's own behavior.

### 8.8 Release audit

- [ ] Version bumped to 2.0.0.
- [ ] CHANGELOG updated.
- [ ] README describes only the three-command product.
- [ ] LICENSE present.
- [ ] Python support documented.
- [ ] Wheel contains no tests, fixtures, `.sentinel`, or checkpoints.
- [ ] Fresh venv install works.
- [ ] `project-sentinel --help` and `--version` work.
- [ ] `python -m sentinel2` works.
- [ ] CI green on Windows and Linux.
- [ ] No legacy commands in help.
- [ ] No 431 MB state files in the repo or wheel.
- [ ] Performance report attached to the release.

### 8.9 Audit report template

Copy this into the milestone PR or audit note:

```markdown
# Sentinel 2.0 Audit - M<N>

Date:
Auditor:
Commit:
Scope:

## Build
- [ ] ruff clean
- [ ] compileall clean
- [ ] tests pass
- [ ] clean install passes

## Correctness
- [ ] scan cold/warm works
- [ ] report has no scan side effects
- [ ] watch updates incrementally
- [ ] deleted files removed
- [ ] changed files re-analyzed

## Data
- [ ] integrity_check ok
- [ ] retention works
- [ ] DB size within budget

## Quality
- [ ] top 5 source-first
- [ ] no translation noise
- [ ] evidence is real
- [ ] confidence matches coverage

## Performance
- cold:
- warm:
- report:
- watch:
- DB size:
- peak RSS:

## Risks / waivers
-

## Verdict
- [ ] pass
- [ ] pass with waivers
- [ ] fail
```

---

## 9. Migration and cut

### 9.1 Keep / cut

| Current module | Decision | Reason |
|---|---|---|
| `src/classify.py` | Keep, port | file roles, archetypes, risk surfaces |
| `src/scan_engine.py` | Keep, rewrite cache | discovery and parallel analysis logic |
| `src/health.py` | Keep, trim | scoring heuristics |
| `src/issue_detector.py` | Keep, trim | issue rules |
| `src/suggester.py` | Keep, trim to top 3 | action ranking |
| `src/utils.py` | Keep, slim | shared helpers |
| `src/sentinel.py` (legacy monolith) | Retire | duplicate implementation |
| `src/auditor.py` | Retire after port | superseded by discover/analyze/quality |
| `src/orchestrator.py` | Retire | multi-surface orchestration |
| `src/cli.py` | Replace | too many commands |
| `src/dashboard.py`, `dashboard.html` | Delete | out of scope |
| `src/kilo.py` | Delete | out of scope |
| `src/sentinel_mcp.py` | Delete | out of scope |
| `src/retriever.py` | Delete | out of scope |
| `src/graph.py` | Delete | out of scope; re-add only if a quality rule needs imports |
| `src/verifier.py` | Delete | out of scope |
| `src/url_reports.py` | Delete | out of scope |
| `src/adapters.py` | Delete | out of scope |
| `src/monitor.py` | Replace with `watch.py` | single watch implementation |
| `src/knowledge.py` | Replace | JSON state replaced by SQLite |
| `src/reporter.py`, `src/renderers/` | Replace/port | one-page report only |
| `src/cli_renderers.py` | Retire | old command renderers |
| `src/health.py` dependency on `scan_engine` | Refactor | remove circular coupling |
| `src/scan_cache.json` | Delete | runtime artifact |
| `.sentinel/checkpoints.json` | Archive then delete | 431 MB, unbounded |
| `sentinel-url-reports/` | Delete/ignore | cloned artifacts, not product data |

### 9.2 Branch and history

1. Commit all current work on a branch named `legacy-wip`.
2. Tag the last 1.x state as `v1.1.0-legacy`.
3. Create `sentinel-2` from `main`.
4. Make the cut in one commit so it is reviewable.
5. Keep the old code in git history, not in the active tree.

### 9.3 State cleanup

- Add `.sentinel/` to `.gitignore`.
- Back up `.sentinel/checkpoints.json` if it contains anything needed for history.
- Delete it after approval; it is runtime state, not source.
- Add `.sentinel/quality.db`, `-wal`, `-shm`, and `quality.html` to `.gitignore`.
- Remove `src/scan_cache.json` and any nested `.sentinel` directories from the source tree.
- Do not migrate old JSON state; the new index rebuilds from source.

### 9.4 Packaging

`pyproject.toml` should be the single source of packaging truth:

```toml
[build-system]
requires = ["setuptools>=64"]
build-backend = "setuptools.build_meta"

[project]
name = "sentinel-agent"
version = "2.0.0"
description = "Local real-time repository quality report"
requires-python = ">=3.10"
dependencies = []

[project.scripts]
project-sentinel = "sentinel2.cli:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

Remove or neutralize the legacy `setup.py` module list to avoid a second entry point.

### 9.5 Documentation

Rewrite:

- `README.md` - three commands, one-page report, speed, privacy, limitations.
- `docs/USER_GUIDE.md` - scan/report/watch, formats, CI usage.
- `docs/ARCHITECTURE.md` - index, incremental scan, quality model.
- `docs/PRIVACY_AND_SAFETY.md` - local only, no network, basic security checks.
- `AGENTS.md` - update to the new commands; remove old Kilo/retrieve instructions.
- `CHANGELOG.md` - 2.0.0 breaking change: removed multi-surface commands.

Delete or archive docs for removed features.

### 9.6 Legacy command mapping

| Old command | New path |
|---|---|
| `scan` | `scan` |
| `report` | `report` |
| `watch` | `watch` |
| `status` | `report --format text` |
| `brief` / `overview` | report summary |
| `context` / `prompt` | removed |
| `retrieve` / `ask` / `graph` | removed |
| `verify` / `pr` / `coverage` / `autofix` | removed |
| `memory` / `savings` / `timeline` / `ledger` | removed |
| `alerts` / `insights` | removed |
| `dashboard` / `mcp` / `kilo-*` / `adapters` | removed |
| `analyze-url` | removed |
| `benchmark` | `tools/benchmark.py`, not a public command |
| `release-check` | release audit checklist |
| `doctor` | internal diagnostic, optional |

---

## 10. Performance budgets

These are release gates. Measure on a warm OS cache and a cold start; record both.

| Workload | Budget |
|---|---:|
| CLI startup (`--version`) | <= 200 ms |
| FastAPI cold source+tests | <= 2.0 s |
| FastAPI warm, no changes | <= 0.4 s |
| FastAPI report-only | <= 0.1 s |
| FastAPI watch update | <= 1.0 s p95 |
| requests cold | <= 0.8 s |
| 10k source/test cold | <= 8 s |
| 10k warm | <= 1 s |
| 25k source/test cold | <= 30 s |
| 25k warm | <= 2 s |
| DB size, 25k files | <= 50 MB |
| Peak RSS, 25k files | <= 300 MB |
| Text report, FastAPI | <= 200 lines / 12 KB |
| JSON report, FastAPI | <= 150 KB |

If a budget is not met:

1. Profile before optimizing.
2. Check docs are not being read by default.
3. Check warm scans are not re-hashing unchanged files.
4. Check the report is not rebuilding AST.
5. Check SQLite queries use indexes.
6. Only then consider native acceleration.

Do not trade correctness for speed silently. If a scan samples, the report must say so.

---

## 11. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Scope creep brings back dashboard/MCP | High | Locked three-command surface; reject additions |
| Quality score is arbitrary | High | Calibrate on 10+ repos; provisional flag; no gates until calibrated |
| False positives erode trust | High | Source-first, evidence required, top-5 review, fixture tests |
| Cold scan still slow on huge repos | Medium | Source-only default, git discovery, parallel analysis, honest budgets |
| SQLite concurrency issues in watch | Medium | Single writer, WAL, busy timeout, retry |
| Windows encoding regressions | Medium | UTF-8/replace stdout, Windows CI tests |
| Symlinks/permissions/huge files crash scan | Medium | Skip with reasons, count coverage |
| Non-Python languages produce weak analysis | Medium | Label as basic heuristics, do not overclaim |
| Docs noise returns | Medium | Exclude translations from scoring; group findings |
| Tests drift back to legacy code | High | Integration tests run the real CLI; delete legacy modules |
| Report grows unbounded | Medium | Top-5 default, size budget, `--all-findings` optional |
| Security checks overclaim | High | Label as basic checks; no "security audit" language |
| Churn data expensive on huge repos | Low | Optional, cached, one git pass |
| Users expect instant 25k cold scan | Medium | Document cold/warm clearly; no false promise |

---

## 12. Locked decisions

These are decided for v1. Do not reopen without a measured reason.

- [x] Three public commands: `scan`, `report`, `watch`.
- [x] SQLite index, no JSON checkpoints.
- [x] Source-first quality scoring.
- [x] Docs metadata by default; `--include-docs` for deep docs.
- [x] Top 5 findings by default.
- [x] One-page report contract.
- [x] Zero runtime dependencies.
- [x] Python 3.10+.
- [x] Optional git, graceful fallback.
- [x] No dashboard, MCP, chat, retrieval, test runner, or autofix.
- [x] Overall score is provisional until calibrated.
- [x] Security is basic checks, not an audit.
- [x] No claim that 25k files are fully re-read in seconds.

Decisions to validate after calibration:

- exact dimension weights;
- grade bands;
- complexity and size thresholds;
- test/source ratio thresholds by archetype;
- top-N grouping rules;
- churn weight.

---

## 13. First three days

### Day 1 - Safety and cut

- [ ] Commit current work; tag `legacy-wip`.
- [ ] Back up and remove the 431 MB checkpoint file from the active tree.
- [ ] Create the `sentinel2` package skeleton.
- [ ] Implement `--version`, `--help`, and the three-command parser.
- [ ] Update packaging so a clean install uses `sentinel2`.
- [ ] Add CLI smoke tests.

### Day 2 - Index

- [ ] Implement discovery (git + walk).
- [ ] Implement classification.
- [ ] Implement SQLite schema and migrations.
- [ ] Implement stat/hash/upsert/delete.
- [ ] Implement cold and warm `scan`.
- [ ] Add integration tests for changed/deleted/ignored files.

### Day 3 - First report

- [ ] Implement Python AST facts.
- [ ] Implement top-5 findings for size, complexity, TODOs, and tests.
- [ ] Implement dimension grades and confidence.
- [ ] Implement JSON and text renderers.
- [ ] Run against FastAPI and the fixtures.
- [ ] Verify: no doc translation noise, top findings are source, report <= 12 KB.

At the end of day 3, the project should already be more useful than the current 388-line report.

---

## 14. Appendices

### Appendix A - Current baseline (for regression comparison)

| Test | Current |
|---|---:|
| FastAPI cold fast | 4.78s |
| FastAPI warm fast | 2.08s |
| FastAPI warm full | 4.36s |
| FastAPI `status` | 0.26s |
| Sentinel `status` | 1.56s |
| Sentinel scan no checkpoint | 0.22s |
| Sentinel scan with checkpoint | 3.77s |
| FastAPI `retrieve` | 2.66s |
| FastAPI `ask` | 4.64s |
| FastAPI `context` | 2.21s / 979 tokens |
| Legacy test suite | 197 passed / 10.65s |
| Checkpoint file | 431.6 MB / 282 entries |
| FastAPI report | 388 lines / 33,988 chars / 151 signals |

### Appendix B - Report template

```markdown
# Sentinel Quality Report - <project>

<commit/branch/dirty> - <files> files / <lines> lines
Cold <x>s | Warm <y>s | Index <z> MB | Confidence <level>

## Quality band: <maintained|adequate|weak|unmaintained> (<provisional>)

<one-line headline>

## What to fix first

1. **<file>** - <title>
   - Evidence: ...
   - Why: ...
   - Next: ...

## Dimensions

| Dimension | Grade | Summary |
|---|---|---|
| Architecture | ... | ... |
| Maintainability | ... | ... |
| Tests | ... | ... |
| Documentation | ... | ... |
| Dependencies | ... | ... |
| Security | n/a | basic checks only |

## Changed since last scan

+<n> files, ~<n> modified, -<n> deleted | quality <delta>

## Action plan

1. ...
2. ...
3. ...

## Coverage and limitations

Source: ... | Tests: ... | Docs: ... | Vendor: excluded
Security: basic checks only. Churn: <available/unavailable>.
```

### Appendix C - Quality rules file

`config/quality_rules.json` should contain:

- `rules_version`;
- `calibrated: false`;
- `weights`;
- `thresholds` for size, complexity, TODOs, test ratio;
- `severity_by_rule`;
- `confidence` thresholds;
- `exclude` patterns for docs translations, vendor, generated;
- `archetype_overrides` for library/app/cli/monorepo/docs-only.

Do not hardcode thresholds in more than one place.

### Appendix D - CLI help target

```text
usage: project-sentinel [-h] [--version] {scan,report,watch} ...

Sentinel 2.0 - local real-time repository quality report.

commands:
  scan    Build/update the index and print a quality report.
  report  Render the latest report from the index without scanning.
  watch   Keep the index and report live as files change.
```

### Appendix E - Audit checklist (copy into every milestone)

```markdown
- [ ] Branch/tag exists and work is recoverable
- [ ] No legacy modules imported by the active package
- [ ] ruff clean, F821 zero
- [ ] compileall clean
- [ ] unit tests pass
- [ ] integration tests pass
- [ ] golden tests pass
- [ ] Windows encoding test passes
- [ ] cold/warm/report budgets measured
- [ ] SQLite integrity_check ok
- [ ] retention works
- [ ] top 5 findings reviewed manually
- [ ] no docs translation noise
- [ ] no network calls
- [ ] secrets redacted
- [ ] clean wheel install works
- [ ] README/help match the three-command surface
```

### Appendix F - Benchmark commands

```bash
# CLI smoke
python -m sentinel2 --version
python -m sentinel2 scan tests/fixtures/clean_python_app --format json
python -m sentinel2 report tests/fixtures/clean_python_app --format text

# Cold/warm measurement
python tools/benchmark.py --repo test-repos/fastapi --mode cold
python tools/benchmark.py --repo test-repos/fastapi --mode warm
python tools/benchmark.py --repo test-repos/fastapi --mode report

# Synthetic scale
python tools/gen_repo.py --files 10000 --out .tmp/repo-10k
python tools/benchmark.py --repo .tmp/repo-10k --mode cold

# Watch latency
python -m sentinel2 watch test-repos/fastapi --out .sentinel/quality.html --interval 1
# In another shell, touch a source file and measure the report update.
```

### Appendix G - What "done" looks like

A developer runs:

```bash
project-sentinel scan .
```

and gets a one-page report in about a second that correctly says:

- what the repo is;
- how healthy the source is;
- the five source risks that matter;
- what changed;
- what to do next;
- what was excluded and why.

Then they run:

```bash
project-sentinel watch .
```

and the same report updates within a second when they edit a source file.

That is Sentinel 2.0. Nothing else is required.
