# Sentinel 2.0 Roadmap

The 2.0 surface is intentionally locked: `scan`, `report`, `watch`. Work after 2.0 should improve the quality of that one product, not add new surfaces.

## Calibration

- Run the rubric against at least ten real repositories.
- Record false positives and false negatives in the top five.
- Tune dimension weights, grade bands, size/complexity thresholds, and test-ratio thresholds.
- Flip `calibrated` to true only when the rubric is stable.

## Analysis depth

- Improve non-Python function spans and complexity heuristics.
- Add conservative import extraction for JavaScript/TypeScript path aliases.
- Improve test mapping for monorepos and namespace packages.
- Add duplicate-code detection only after it is proven low-noise.

## Watch performance

- Add an optional native filesystem watcher on Windows (`ReadDirectoryChangesW`) and Linux (inotify) behind the same polling fallback.
- Keep the polling implementation as the correctness baseline.
- Do not add a third-party watcher dependency.

## Reporting

- Add a small number of golden repositories with reviewed top-five findings.
- Add a CI-friendly `--baseline` comparison for finding deltas.
- Keep the JSON schema additive within version 1.

## Explicitly out of scope

- dashboard, HTTP server, MCP bridge, agent chat, retrieval, semantic search;
- LLM calls, embeddings, telemetry, cloud upload;
- test execution, coverage ingestion, autofix, PR generation;
- URL cloning and remote repository analysis;
- persistent agent memory, savings, timeline, ledger;
- a promise that 25k files are fully re-read in seconds.
