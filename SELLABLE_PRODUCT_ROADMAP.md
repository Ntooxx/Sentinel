# Sentinel Sellable Product Roadmap

Sentinel is already useful as a fast repo scout and AI-agent context layer. To make it feel like a sellable developer tool today, the work should focus less on adding flashy features and more on making the current promise reliable, explainable, and hard to misinterpret.

Target positioning:

> Sentinel scans a repository before your AI coding agent starts work, then produces a compact, trustworthy map of project structure, entry points, risks, tests, docs drift, and high-value focus files.

## Current Honest Rating

- Prototype / MVP: 8/10
- Sellable developer tool today: 6.5/10
- Product foundation: very promising

Goal:

- Sellable developer tool: 8/10 to 9/10

## 1. Make The Claims Honest And Precise

Sentinel should not claim that it deeply understands an entire codebase. Its current strength is high-speed structural and metadata analysis.

Needed changes:

- Replace vague claims like "understands your codebase" with "maps your repository for AI-assisted development."
- Rename or clarify "Token Savings: 100%" because it can sound fake.
- Show the actual numbers behind token savings:
  - estimated full repo tokens
  - compact context tokens
  - percentage reduction
  - explanation that this is an estimate
- Label fast-mode output as "sampled metadata analysis" when file bodies are truncated.
- Add a "Confidence" field to project identity, primary language, purpose, and entry point guesses.

Why it matters:

Developers forgive limitations when the tool is honest. They lose trust fast when a dashboard looks more certain than the underlying heuristic.

## 2. Fix Fast Mode So It Is Actually Fast At Scale

Current issue:

Fast mode samples only the first chunk for analysis, but still reads each full file into memory with `read_bytes()` to hash and count lines.

Needed changes:

- Stream file reads instead of loading whole files.
- Hash incrementally.
- Count lines incrementally.
- Decode only the analysis sample.
- Add scan metrics:
  - bytes scanned
  - bytes analyzed
  - skipped files
  - truncated files
  - largest skipped files
- Add separate timings for:
  - filesystem walk
  - file read/hash
  - metadata extraction
  - audit scoring
  - report generation

Why it matters:

The current speed is good, but for huge repos the implementation still pays unnecessary I/O and memory cost. This is the difference between impressive demo and dependable daily tool.

## 3. Add Incremental Scans

Current issue:

Every scan reprocesses the repository. That is fine for demos, but a daily developer tool should feel instant after the first scan.

Needed changes:

- Cache per-file metadata by path, size, mtime, and hash.
- Reuse cached metadata for unchanged files.
- Recompute only changed, new, and deleted files.
- Add dashboard labels:
  - full scan
  - incremental scan
  - changed files analyzed
  - cached files reused
- Add a manual "force full rescan" action.

Why it matters:

First scan can take 99 seconds on a huge repo. Second scan should usually take 1-5 seconds.

## 4. Strengthen Project Identity Detection

Current issue:

Sentinel can still mistake README decorations, badges, HTML placeholders, or resource-file volume for product identity.

Needed changes:

- Extract README title separately from README purpose.
- Strip BOM characters before Markdown parsing.
- Skip:
  - badges
  - SVG/HTML alignment tags
  - tables of contents
  - install commands
  - license boilerplate
  - status badges
- Prefer manifest metadata when available:
  - `pyproject.toml`
  - `package.json`
  - `Cargo.toml`
  - `go.mod`
  - `pom.xml`
  - `CMakeLists.txt`
- Produce identity evidence:
  - project name source
  - purpose source
  - primary language evidence
  - entry point evidence

Why it matters:

The first sentence a tool says about a repo sets user trust. If that sentence is wrong, everything else feels suspect.

## 5. Upgrade Language And Entry-Point Heuristics

Current issue:

The latest patch improved this, but it is still heuristic and should be made more transparent and extensible.

Needed changes:

- Keep weighted scoring, but expose evidence in JSON and dashboard.
- Cap low-signal file classes:
  - tests
  - fixtures
  - generated files
  - vendored files
  - resources
  - docs
  - CI scripts
- Add generated-file detection:
  - minified JS
  - lockfiles
  - protobuf outputs
  - bundled assets
  - snapshots
- Expand entry point detection for:
  - Python console scripts from `pyproject.toml`
  - Node bin entries from `package.json`
  - Go `cmd/*/main.go`
  - Rust `src/main.rs` and workspace bins
  - Java/Kotlin application classes
  - CMake/Meson executable targets
- Separate entry point types:
  - application entry point
  - CLI entry point
  - service entry point
  - build entry point
  - CI entry point

Why it matters:

Developers need Sentinel to know the difference between "how the repo builds" and "where the app starts."

## 6. Add A Real Confidence Model

Current issue:

Sentinel reports many guesses as facts.

Needed changes:

- Add confidence scores:
  - primary language confidence
  - project purpose confidence
  - entry point confidence
  - framework confidence
  - test strategy confidence
- Include top competing guesses.
- Explain evidence briefly:
  - "C++ selected because CMakeLists.txt, src/main.cpp, and executable target were found."
  - "JavaScript downweighted because most JS files were under tests/resources."
- Use dashboard badges:
  - high confidence
  - medium confidence
  - low confidence
  - needs review

Why it matters:

Confidence turns a heuristic product into a trustworthy assistant.

## 7. Improve Dashboard For Real Developer Workflow

Current issue:

The dashboard is useful, but it should become the primary product experience, not just a report viewer.

Needed changes:

- Add a scan timeline.
- Add "why this was detected" panels for:
  - primary language
  - purpose
  - entry points
  - hotspots
  - suggested next action
- Add filters:
  - source files
  - tests
  - docs
  - CI/build
  - generated/vendor
  - high risk
- Add copy buttons for:
  - compact AI context
  - focus files
  - suggested prompt
  - verification commands
- Add warning states:
  - stale scan
  - large repo sampled
  - low-confidence identity
  - ignored huge directories
- Add side-by-side "Full repo estimate vs emitted context."

Why it matters:

A sellable tool needs a product surface that makes the value obvious without reading docs.

## 8. Add URL Scan Hardening

Current issue:

The URL workflow is compelling, but it needs guardrails to be trusted.

Needed changes:

- Show clone/fetch progress.
- Enforce max repo size or ask for confirmation.
- Add timeout and cleanup behavior.
- Cache previously scanned URL repos.
- Display exact commit SHA scanned.
- Support branch selection.
- Warn when submodules are skipped.
- Keep scan outputs isolated from Sentinel's own repo state.

Why it matters:

"Paste a GitHub URL and get a repo map" is a killer demo. It must be reliable and predictable.

## 9. Add Benchmarks And Public Test Fixtures

Current issue:

The speed claims are interesting, but they need repeatable proof.

Needed changes:

- Add benchmark command:
  - `project-sentinel benchmark /path/to/repo`
- Report:
  - files scanned
  - lines counted
  - bytes read
  - bytes analyzed
  - duration
  - memory peak if available
  - cache hit rate
- Maintain benchmark fixtures:
  - Python app
  - Node app
  - Go service
  - Rust CLI
  - C++ repo with JS fixtures
  - monorepo
  - docs-heavy repo
  - generated-files-heavy repo
- Add regression snapshots for expected identity and entry point output.

Why it matters:

Benchmarks convert "this feels fast" into a defensible product claim.

## 10. Add Better Test And Verification Suggestions

Current issue:

Sentinel can detect tests, but verification commands should be more actionable.

Needed changes:

- Detect test commands from:
  - `package.json`
  - `pyproject.toml`
  - `tox.ini`
  - `noxfile.py`
  - `pytest.ini`
  - `Makefile`
  - `Cargo.toml`
  - `go test ./...`
  - Gradle/Maven files
- Suggest narrow test commands based on focus files.
- Distinguish:
  - unit tests
  - integration tests
  - lint
  - typecheck
  - build
- Add confidence to each suggested command.

Why it matters:

Agents need not only context, but also the right way to verify changes.

## 11. Build A Better "Agent Handoff" Contract

Current issue:

Sentinel produces context, prompts, and focus files, but the contract could be sharper.

Needed changes:

- Create a stable JSON schema for agent handoff.
- Include:
  - project summary
  - confidence fields
  - focus files
  - entry points
  - relevant tests
  - risks
  - suggested first commands
  - ignored paths
  - scan limitations
- Add `--agent codex`, `--agent cline`, `--agent kilo`, etc. output modes.
- Make context packs deterministic for the same repo state.

Why it matters:

This is Sentinel's strongest wedge: making AI coding agents less wasteful and less lost.

## 12. Add Persistent Project Memory That Is Actually Useful

Current issue:

Sentinel has a knowledge base, but memory should clearly improve future scans and agent handoffs.

Needed changes:

- Store accepted/rejected suggestions.
- Store manually confirmed entry points and project purpose.
- Let users pin important files.
- Let users mark generated/vendor directories.
- Track recurring hotspots over time.
- Track files frequently edited together.
- Add "what changed since last scan" as a first-class dashboard panel.

Why it matters:

Persistent memory is what makes Sentinel more than a one-shot scanner.

## 13. Improve Risk Scoring

Current issue:

Risk scoring is useful but still basic.

Needed changes:

- Separate risk categories clearly:
  - runtime risk
  - test risk
  - maintainability risk
  - documentation drift
  - security surface
  - generated/vendor noise
- Add risk evidence.
- Avoid over-penalizing large generated/data files.
- Detect risky patterns:
  - auth/security code
  - payment/billing paths
  - migration scripts
  - deployment configs
  - public API handlers
  - low-test high-churn files
- Add "why this file is a hotspot."

Why it matters:

Risk is only valuable if developers believe the ranking.

## 14. Add Install And Packaging Polish

Current issue:

A sellable developer tool needs frictionless install and predictable commands.

Needed changes:

- Provide `pipx install sentinel-agent` path.
- Publish a clean package name and CLI name.
- Add version command.
- Add doctor command that validates:
  - config
  - permissions
  - dashboard port
  - Git availability
  - URL scan dependencies
- Add upgrade notes.
- Add first-run message with next command.

Why it matters:

Great tools lose users during installation.

## 15. Add Privacy And Safety Story

Current issue:

Developers will ask what leaves their machine.

Needed changes:

- Clearly state local-only behavior.
- Document exactly what files are written:
  - `.sentinel/knowledge_base.json`
  - reports
  - checkpoints
  - dashboard state
- Add `--no-write` or `--dry-run` scan mode.
- Add config for excluding sensitive paths.
- Redact secrets from summaries and reports.
- Detect possible secret files and avoid surfacing their contents.

Why it matters:

Trust is a product feature, especially for codebase tools.

## 16. Add Clear Product Tiers

Possible free/open-source tier:

- local scan
- dashboard
- context pack
- markdown/html reports
- URL scan
- basic memory

Possible paid/pro tier:

- team reports
- benchmark history
- CI integration
- PR risk summaries
- saved repo profiles
- advanced agent handoff
- organization-wide architecture/debt trends

Why it matters:

The product needs a business shape, not just features.

## 17. Most Important Near-Term Fixes

If only five things are done next, do these:

1. Stream fast-mode scanning instead of full `read_bytes()`.
2. Add confidence and evidence for identity, language, and entry points.
3. Fix README parsing edge cases, including BOM and Markdown decorations.
4. Add incremental scan caching.
5. Improve dashboard explainability and copyable agent handoff.

These five changes would likely move Sentinel from 6.5/10 to around 8/10 as a sellable developer tool.

## 18. What Would Make It 9/10

To reach 9/10, Sentinel needs to become not just fast, but dependably right.

That means:

- robust multi-language project detection
- incremental scans that feel instant
- transparent confidence and evidence
- strong dashboard workflow
- stable agent handoff schema
- repeatable benchmarks
- trustworthy privacy story
- low-friction install
- polished URL scanning

At 9/10, the product promise becomes:

> Before an AI agent edits your repo, Sentinel gives it a fast, evidence-backed project map and verification plan, so it starts with context instead of guessing.

That is a product developers can understand, trust, and pay for.
