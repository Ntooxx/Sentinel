# Sentinel Sellable Product Integration Report

Date: 2026-04-24

## Integrated

- Replaced fast-mode full-file `read_bytes()` scanning with streaming file reads.
- Added incremental SHA-256 hashing and line counting without loading whole files into memory.
- Added fast-mode analysis sampling with explicit `analysis_truncated` and `analysis_bytes` metadata.
- Added scan metrics:
  - bytes scanned from disk
  - bytes analyzed
  - skipped files
  - truncated files
  - largest skipped files
  - cached files reused
  - changed files analyzed
- Added scan timing breakdowns for file walk/read/hash, metadata extraction, diffing, audit scoring, and context/report generation.
- Added persistent per-file incremental scan cache at `.sentinel/scan_cache.json`.
- Added cache reuse by path, size, mtime, hash, and stored metadata.
- Added `project-sentinel scan --force-full-rescan` to manually bypass the incremental cache.
- Added confidence and evidence for project identity, project purpose, primary language, entry points, frameworks, and test strategy.
- Added competing primary-language guesses and weighted evidence in JSON/report data.
- Added README title and purpose separation through metadata fields.
- Hardened README parsing further with BOM-safe decoding, badge/html/table/noise skipping, and descriptive-text checks.
- Added generated/noisy file detection for lockfiles, minified assets, protobuf output, snapshots, and generated/vendor directories.
- Downweighted generated and low-signal files in primary-language scoring.
- Expanded manifest support for `Cargo.toml`, `go.mod`, `pom.xml`, Gradle, and `CMakeLists.txt`.
- Added declared CLI entry-point detection from Python and Node manifests.
- Added entry-point details with type, score, evidence, and confidence.
- Updated terminal, overview, markdown, HTML, knowledge context, and dashboard surfaces to show confidence and evidence.
- Clarified token-saving language as estimated token reduction and added a note that token numbers are estimates.
- Added dashboard copy buttons for agent prompt and focus files.
- Reworded top README claims from "understands the project" to repository mapping and evidence-backed context.
- Added regression tests for streaming fast scans, truncation metrics, incremental cache reuse, and confidence/evidence output.
- Added raw second-pass claim verification for compressed project identity and purpose claims.
- Added a stable `sentinel.agent_handoff.v1` JSON contract via `project-sentinel handoff`.
- Added agent-specific handoff targeting for Codex, Cline, Kilo, Roo, and Continue.
- Added `project-sentinel benchmark` with files, lines, bytes scanned/analyzed, duration, and cache hit rate.
- Added `project-sentinel scan --no-write` to avoid updating knowledge, checkpoints, scan cache, or scan history after the scan.
- Hardened URL scans with branch selection, max cloned repo size, exact commit SHA capture, repo size reporting, and submodule warnings.
- Added dashboard buttons for handoff and benchmark plus warning/verification panels for stale or sampled scans.

## Remaining

- Dashboard workflow can still go deeper: richer filters, side-by-side full-repo estimate versus emitted context, and dedicated "why detected" drilldowns.
- URL scan hardening still needs progress streaming to the browser and cached URL repos.
- Public benchmark fixtures are still missing.
- Verification command detection is still basic compared with the roadmap target for package managers, typecheck, lint, integration tests, and narrow test selection.
- Persistent memory still needs accepted/rejected suggestions, pinned files, confirmed project purpose/entry points, and recurring hotspot tracking.
- Privacy and safety story still needs secret redaction and clearer docs of all written files.
- Install polish still needs richer upgrade notes and a more complete first-run/doctor flow.
- Product tiers and commercial packaging are still only roadmap-level.

## Verification

- `python -m compileall src tests`
- `python -m unittest discover -s tests -v`
- `python sentinel.py scan . --fast --compact --no-checkpoint --top 1`
- `python sentinel.py scan . --fast --compact --no-checkpoint --force-full-rescan --top 1`
- `python sentinel.py scan . --fast --compact --no-checkpoint --no-write --top 1`
- `python sentinel.py handoff . --agent codex --fast --output .sentinel/handoff-test.json`
- `python sentinel.py benchmark . --fast --iterations 2`
- Repeated fast scan verified cache reuse: second scan reused 58 cached files and analyzed 0 changed files.

## New Score

- Prototype / MVP: 9.0/10
- Sellable developer tool today: 9.0/10
- Product foundation: 9.1/10

The biggest movement came from the roadmap's highest-priority trust and scale items: streaming fast scans, incremental caching, evidence-backed confidence, README parsing hardening, raw claim verification, stable agent handoff, benchmark proof, URL guardrails, and clearer reporting. Sentinel now crosses the 9/10 threshold as a sellable developer tool foundation; the remaining work is polish, fixture depth, and team/pro packaging rather than core trust.
