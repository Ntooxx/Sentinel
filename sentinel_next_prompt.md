You are working on Sentinel, a fast repo intelligence scanner that generates HTML/Markdown reports, compact project context, and AI-agent prompts.

Goal:
Improve report accuracy and scoring quality for monorepos and mixed-language projects without losing scan speed.

Current problem:
Recent reports are structurally good, but score is reduced by these recurring issues:
1. Main components are too broad in monorepos, e.g. "packages" becomes one huge "application logic" component.
2. Some generated SDK files are classified as vendor/third-party instead of generated project-owned files.
3. i18n/localization/resource files are treated like oversized source modules.
4. Markdown spec/design docs are treated like oversized source modules.
5. Runtime entry points are too broad; important runtime modules are sometimes labelled as entry points.
6. Maintainability score and maintainability risk can contradict each other, e.g. "Maintainability: 95% risk low" but "Maintainability risk: high".
7. Need to preserve current speed. Do not add expensive AST parsing, dependency graph building, or full-file semantic analysis.

Important constraint:
Keep the scanner fast. Prefer path-based, filename-based, extension-based, and cached lightweight heuristics. Avoid reading entire large files unless the scanner already does so. Reuse existing scan metadata where possible.

Tasks:

1. Add better monorepo component splitting
- If a top-level directory like packages/, apps/, services/, crates/, modules/, or libs/ contains many immediate subdirectories, classify meaningful second-level components instead of collapsing everything into the parent.
- Example:
  packages/opencode -> CLI / AI coding agent core
  packages/app -> frontend application
  packages/desktop -> Tauri desktop shell
  packages/desktop-electron -> Electron desktop shell
  packages/console -> console/web app
  packages/sdk/js -> generated/client SDK
  packages/containers -> container/build tooling
- Keep the old top-level grouping only if second-level grouping would create too many tiny components.
- Add a cap, e.g. show top 12 components by lines/files.

2. Add resource/i18n classification
Detect paths like:
- **/i18n/**
- **/locales/**
- **/translations/**
- files named en.ts, fr.ts, de.ts, ar.ts, zh.ts, etc. inside i18n/locales folders
Classify them as:
"localization/resource files"
Do not generate "review module boundaries" warnings for these unless they contain executable logic beyond simple exported dictionaries/objects.
For large i18n files, use wording:
"Large localization/resource file; large by design. Review only if translation loading or schema changes."

3. Add generated SDK classification
Detect:
- **/gen/**
- **/generated/**
- **/*.gen.ts
- **/*.generated.ts
- sdk.gen.ts
- types.gen.ts
- client.gen.ts
- OpenAPI-generated SDK folders
Classify as:
"generated SDK/client code"
Do not recommend manual refactoring.
Use wording:
"Generated SDK/client file; regenerate from source schema instead of editing manually."

4. Add spec/design/document classification
Detect markdown files in:
- **/specs/**
- **/adr/**
- **/docs/**
- filenames containing spec, design, architecture, adr, proposal
Classify as:
"documentation/specification"
Do not say "consider reviewing module boundaries" for markdown files.
Use wording:
"Large documentation/specification file; review for readability and drift, not source module boundaries."

5. Refine entry-point detection
Split files into:
- True runtime entry points
- Runtime hotspots
- Build/tooling entry points
- Generator entry points
- Test runners
- Environment/setup scripts
Rules:
- main.rs, main.go, index.ts, cli entry files, app bootstrap files can be runtime entry points.
- Files like provider.ts, models.ts, parsing.rs, cache.rs, session.ts are usually runtime hotspots, not entry points, unless package metadata/scripts directly reference them.
- build.rs, script/build.ts, install.sh, Docker build scripts are build/tooling.
- files under gen/generated are not runtime entry points unless explicitly referenced by package scripts or known app bootstraps.
Update the report so "Runtime entry points" is smaller and more accurate, while "Primary hotspots" includes important non-entry runtime modules.

6. Fix maintainability score/risk consistency
Create one function that maps maintainability score to risk:
- 85-100 => low
- 65-84 => medium
- 0-64 => high
Ensure the report cannot say "Maintainability: 95% risk low" and also "Maintainability risk: high".
Update all summary sections to use the same computed value.

7. Improve test signal wording
Use:
- "strong" when real test directories and test files are detected.
- "present — coverage unknown" when tests exist but coverage is not measured.
- "missing" only when no tests are detected.
Avoid "unknown" when test files/directories are clearly present.

8. Preserve performance
Do not add full AST parsing.
Do not recursively re-read files after initial scan.
Implement classification using existing file metadata:
- path
- extension
- size
- line count
- basename
- directory segments
- already-detected imports/TODO/executable flags if available
Add a small benchmark/regression check:
- Run existing fixture scans or lightweight tests.
- Ensure classification changes do not increase scan time meaningfully.
- Target: no more than 5-10% overhead on current scan pipeline.

9. Add regression expectations
Add/update tests or snapshot fixtures for a YOLO-style monorepo:
Expected improvements:
- packages is split into meaningful subcomponents.
- packages/app/src/i18n/*.ts is classified as localization/resource, not oversized source module.
- packages/sdk/js/src/**/gen/*.ts is classified as generated SDK/client code.
- packages/app/create-effect-simplification-spec.md is classified as documentation/specification.
- Runtime entry points do not include provider/models-style files unless clearly referenced as bootstraps.
- Maintainability risk matches maintainability score.

Acceptance criteria:
- Report score should improve for YOLO-style monorepos due to better classification, without hiding real risks.
- Generated, i18n, docs, and test files are still visible but downgraded or moved to the correct category.
- Primary runtime hotspots focus on real application/runtime code.
- Speed remains close to current fast scan performance.
- Existing strong Ladybird/Ollama behavior does not regress.

Deliverables:
- Code changes implementing the classifiers and scoring consistency.
- Updated tests/snapshots.
- A short summary explaining:
  1. What changed
  2. Why it improves report quality
  3. How speed was preserved
  4. Any remaining known limitations