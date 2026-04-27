# Sentinel Archetype-Aware Improvements

## Summary
Made Sentinel archetype-aware so it handles huge framework/library repositories, monorepos, CLI apps, browser engines, desktop apps, and vendor-heavy repositories without forcing every project into a single runtime-entry workflow.

## Changes

### 1. Added repo archetype detection
- Expanded `detectRepoArchetype()` to return `primaryArchetype`, `secondaryArchetypes`, `confidence`, and `workflowStrategy`.
- Supported archetypes: `app`, `cli_server`, `desktop_app`, `browser_engine`, `framework_library`, `monorepo`, `vendor_heavy`, `generated_heavy`, `documentation_heavy`, `test_heavy`, `mixed_language`.
- Archetype now controls workflow hints and report wording.

### 2. Added/centralized file classification
- `classifyFile()` is the single canonical classifier used across all report surfaces.
- New flags added: `isRuntimeSource`, `isRuntimeEntryCandidate`, `isRuntimeHotspotCandidate`, `isBuildTooling`, `isGenerator`, `isTest`, `isTestRunner`, `isFixture`, `isDocumentation`, `isSpecification`, `isVendor`, `isGenerated`, `isGeneratedSdk`, `isLocalization`, `isDependencyLock`, `isConfig`, `isEnvironmentSetup`.
- Classification is consistent across: Main Components, Entry Points, Focus Files, Hotspots, Top File Risks, Review Signals, Recommended Next Actions, Compact Context, Agent Prompt, HTML report, and Markdown report.

### 3. Improved project name and purpose extraction
- Project name now skips raw HTML (`<div`, `<p`, `<img`, badges, comments) and falls back to the repository folder name.
- Purpose extraction blocks generic filler like "application logic, application logic, application logic" and raw HTML/config lines.
- If no useful purpose is found, reports "Purpose could not be confidently inferred from README."

### 4. Improved project type detection
- Type strings now include archetype signals and multiple languages.
- Examples:
  - Python + C++ + Bazel + APIs + core/compiler/lite => "Python/C++ framework/library with build tooling and test suite"
  - Go + C++ backend + CLI/server => "Go-based CLI/server application with native backend components"
  - C++ browser engine + WPT tests => "C++ browser engine / web browser project with test suite"

### 5. Improved main component splitting
- Large top-level directories (>200 files) with meaningful subdirectories are now split one level deeper in `_summarize_components()`.
- Common split labels added: `core`, `python`, `compiler`, `lite`, `c`, `cc`, `java`, `go`, `tools`, `examples`, `tests`, `ci`, `api`, `server`, `cmd`, `app`, `desktop`, `sdk`.
- Top 10–15 components are shown; tiny directories (<=2 files) are filtered out.

### 6. Fixed entry point classification
- New categories: `runtime_surface`, `example`.
- Examples under `examples/` or `api/examples/` are classified as example entry points, not primary runtime.
- `*_test.*` files are classified as tests, not runtime.
- `gen/generate` files are classified as generators.
- Build scripts and CI scripts are classified as build/tooling.
- Framework/library repos may have runtime/API surfaces instead of a single runtime entry point.
- Random generator/tool main files are no longer chosen as primary runtime entry points.

### 7. Fixed top runtime risks
- Top runtime risks now only include first-party runtime source files.
- Excluded from runtime risks: tests, examples, generators, generated code, vendor/third_party, docs/specs, build scripts, CI scripts, environment setup, dependency locks, requirements files, linters, fuzzers.
- Excluded files are moved to correct sections: build/tooling risks, generator risks, test risks, example risks, documentation risks, dependency/lockfile signals, vendor/third-party hotspots.

### 8. Fixed dependency/requirements classification
- Lockfiles classified as `dependency_lock`: `requirements*.txt`, `requirements_lock*.txt`, `package-lock.json`, `pnpm-lock.yaml`, `yarn.lock`, `Cargo.lock`, `go.sum`, `poetry.lock`, `Pipfile.lock`.
- Large lockfile message: "Large dependency/requirements file; review only when dependency generation or upgrade process changes."
- Lockfiles never trigger "consider reviewing module boundaries".

### 9. Improved large-file messages by file role
- Source: "File is X lines; consider reviewing module boundaries."
- Documentation/spec: "Large documentation/specification file; review for readability and drift, not source module boundaries."
- Generated: "Generated file; regenerate from source/schema instead of editing manually."
- Vendor: "Vendor/third-party file; track only, do not refactor by default."
- Dependency: "Large dependency/requirements file; review only when dependency generation or upgrade process changes."
- Test: "Large test file; review test structure only if frequently edited or flaky."
- Config/data: "Large config/data file; validate schema before editing. Do not refactor like source code."

### 10. Deduplicated risk factors
- Risk factors are deduplicated before rendering in `_score_file_risks()`, HTML report, Markdown report, compact context, and agent prompt.
- Duplicate strings like "moderate size, moderate size" are now rendered as "moderate size".

### 11. Improved agent prompt generation
- Archetype-aware prompts:
  - `app`/`cli_server`: "Trace the execution flow starting at ..."
  - `framework_library`: "Map the relevant API/runtime surface before editing."
  - `monorepo`: "Select the affected package/app/service first, then trace locally."
- Current risks include file names and meaningful reasons, not vague TODO counts.

### 12. Added regression tests
- New test file: `tests/test_archetype_regressions.py`
- Fixtures cover: raw HTML README, generic purpose blocking, framework/library repo, vendor-heavy repo, dependency lockfile, browser engine, desktop app, Go + native backend, monorepo component splitting, example entry points, risk factor deduplication.
- All 130 tests pass.

## Performance
- Scan speed preserved.
- No AST parsing, type-checking, or dependency graph building added.
- All new logic uses path rules, filenames, extensions, README cleanup, manifest hints, line counts, file sizes, TODO counts, and import counts already collected.
- Estimated overhead: <5%.
