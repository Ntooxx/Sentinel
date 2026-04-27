You are working on Sentinel, a fast repo intelligence scanner that generates HTML reports, Markdown reports, compact project context, and AI-agent prompts.

Goal:
Improve Sentinel’s general report quality across very large repositories, monorepos, frameworks, CLI/server apps, browser engines, desktop apps, and vendor-heavy projects without losing scan speed.

Core principle:
Sentinel should generate trustworthy repo intelligence, not pretend every repository is a normal app with one runtime entry point.

Hard constraints:
- Preserve scan speed.
- Do not add expensive full AST parsing.
- Do not build full dependency graphs.
- Do not type-check projects.
- Do not install dependencies.
- Use path rules, filenames, extensions, directory structure, README cleanup, manifest/build-file hints, file sizes, line counts, TODO counts, import counts already collected, and existing scan metadata.

Target:
Raise report quality toward 8.5+/10 while keeping current scan speed within 5–10% overhead.

Main problems to fix:
1. Project names can be extracted from raw README HTML.
2. Purpose extraction can output generic filler like “application logic, application logic”.
3. Repositories are sometimes forced into “app runtime tracing” even when they are frameworks/libraries.
4. Main components can be too broad, such as “packages”, “tensorflow”, or “third_party”.
5. Runtime entry points can include generators, examples, tests, build tools, or helper scripts.
6. Top runtime risks can include test files, generators, linters, CI scripts, and tooling.
7. Large lockfiles, requirements files, generated files, i18n files, docs, and fixtures can be treated like source modules.
8. Risk factors can duplicate, e.g. “moderate size, moderate size”.
9. The same file can be classified differently in report sections, compact context, and agent prompt.

==================================================
1. ADD A CENTRAL FILE CLASSIFIER
==================================================

Create or refactor one canonical classifier:

classifyFile(path, metadata) -> {
  role,
  surface,
  label,
  isRuntimeSource,
  isRuntimeEntryCandidate,
  isRuntimeHotspotCandidate,
  isBuildTooling,
  isGenerator,
  isTest,
  isTestRunner,
  isFixture,
  isDocumentation,
  isSpecification,
  isVendor,
  isGenerated,
  isGeneratedSdk,
  isLocalization,
  isDependencyLock,
  isConfig,
  isEnvironmentSetup,
  manualEditPolicy,
  largeFilePolicy
}

Surfaces:
- runtime
- build_tooling
- generator
- test
- test_runner
- test_data
- documentation
- specification
- vendor
- generated
- generated_sdk
- localization
- dependency_lock
- config
- environment_setup
- unknown

This classifier must be used everywhere:
- project summary
- main components
- entry points
- focus files
- hotspots
- top risks by surface
- review signals
- recommended actions
- compact context
- generated agent prompt
- HTML report
- Markdown report

Do not allow old independent logic such as “entry point + executable = runtime” to override the central classifier.

==================================================
2. ADD REPOSITORY ARCHETYPE DETECTION
==================================================

Detect repo archetypes before choosing workflow wording.

Archetypes:
- app
- cli_server
- desktop_app
- browser_engine
- framework_library
- monorepo
- test_suite
- vendor_heavy
- generated_heavy
- documentation_heavy
- mixed_language

Rules:
If a repo has no single obvious application entry point and has large API/runtime directories, classify it as framework_library or library-style repo.

For framework/library repos, do not force:
“Start runtime tracing from random main.go”.

Instead say:
“Framework/library repo: choose the relevant API/runtime surface before editing.”

Example workflow wording:
- For API changes, start from the API layer.
- For runtime/core changes, start from the runtime/core directories.
- For compiler/build changes, start from compiler/build tooling.
- Use existing tests before broad changes.

For app/CLI repos, runtime tracing is appropriate.

For browser engines, runtime tracing from a real runtime/browser entry point is appropriate.

For monorepos, split by product/package first.

==================================================
3. FIX PROJECT NAME AND PURPOSE EXTRACTION
==================================================

Project name:
- Prefer repository folder name, package metadata name, or first meaningful README heading.
- Skip raw HTML blocks.
- Skip badges, logos, images, centered divs/p tags, empty headings.

Never allow project name to be:
- <div align="center">
- <p align="center">
- an image tag
- a badge line

Purpose:
- Prefer first meaningful README paragraph after badges/logo.
- Skip raw HTML.
- Skip config lines like cmake_minimum_required.
- Skip generic generated fallback text.
- Prefer package/repo description if available.

Never output:
- “application logic, application logic, application logic”
- “<p align='center'>”
- “cmake_minimum_required(...)”

If no confident purpose is found, output:
“Purpose could not be confidently inferred from README.”

==================================================
4. IMPROVE MAIN COMPONENT SPLITTING
==================================================

Do not collapse huge repos into broad generic components.

If a top-level directory contains many meaningful subdirectories, split deeper.

Monorepo roots:
- packages
- apps
- services
- crates
- modules
- libs

For these, group by second-level path:
- packages/app
- packages/desktop
- packages/console
- packages/sdk
- packages/opencode

Framework/library roots:
If a large top-level source directory contains meaningful subcomponents, split by second-level path:
- core
- python
- compiler
- lite
- c
- cc
- java
- go
- tools
- examples
- server
- api
- cmd
- model
- convert

Vendor roots:
- third_party
- vendor
- external
Split special large vendor subprojects when useful:
- third_party/xla
- third_party/llvm
- third_party/boringssl

Show top 10–15 components by lines/files.
Avoid flooding the report with tiny components.

Improve labels:
- core -> runtime/core library
- python -> Python API layer
- compiler -> compiler/lowering components
- lite -> lightweight/mobile runtime
- c -> C API
- cc -> C++ API
- tools -> developer/build tooling
- examples -> examples/sample apps
- tests/e2e/integration/smoke -> test suite
- sdk/gen/generated -> generated SDK/client code
- third_party/vendor -> vendor dependency
- ci -> CI/build infrastructure
- docs -> documentation
- app -> frontend/application
- desktop -> desktop application shell
- server -> server internals
- api -> API layer
- cmd -> CLI commands

Avoid generic labels when a better path-based label exists.

==================================================
5. FIX ENTRY POINT CLASSIFICATION
==================================================

Split entry points into:
- Primary runtime entry points
- Secondary/runtime feature entry points
- Example entry points
- Runtime hotspots
- Build/tooling entry points
- Generator entry points
- Test runners
- Environment setup

Runtime entry points:
- main.go, main.rs, main.ts, main.cpp when in app/cli/server/cmd roots
- package.json bin/main/module entry files when metadata is available
- Tauri src-tauri/src/main.rs
- Go cmd/*/main.go
- CLI bootstrap files
- server bootstrap files

Not runtime entry points by default:
- generators
- *_gen.*
- gen_*
- build.rs
- build scripts
- install scripts
- CI scripts
- examples
- tests
- fuzzers
- linters
- fixtures
- docs
- lockfiles
- helper modules like provider.ts, models.ts, parsing.rs, cache.rs, session.ts unless manifest evidence proves they are bootstraps

Example files:
Files under examples/ or api/examples/ should be “Example entry points”, not primary runtime entry points.

Framework/library repos:
If no true app entry point exists, use:
“Runtime/API surfaces”
instead of forcing a runtime entry point.

==================================================
6. FIX TOP RUNTIME RISKS
==================================================

Top runtime risks must include only files classified as first-party runtime source.

Exclude:
- tests
- test runners
- testdata
- fixtures
- examples
- generators
- generated code
- generated SDK files
- vendor/third-party
- docs/specs
- build scripts
- CI scripts
- environment setup
- lockfiles
- dependency files
- linters
- fuzzers unless intentionally classified as runtime security/testing surface

If a high-score file is not runtime, move it to the correct section:
- Top build/tooling risks
- Top generator risks
- Top test runner risks
- Top test/data risks
- Top documentation risks
- Vendor/third-party hotspots
- Generated SDK/client code
- Dependency/lockfile signals

The same central classifier must decide this.

==================================================
7. FIX LARGE FILE WARNING POLICIES
==================================================

Large file messages must depend on classification.

Source file:
“File is X lines; consider reviewing module boundaries.”

Documentation/specification:
“Large documentation/specification file; review for readability and drift, not source module boundaries.”

Localization/resource:
“Large localization/resource file; large by design. Review only if translation loading, schema, or resource generation changes.”

Generated SDK/client:
“Generated SDK/client file; regenerate from schema/source instead of editing manually.”

Vendor/third-party:
“Vendor/third-party file; track only, do not refactor by default.”

Dependency lock/requirements:
“Large dependency/requirements file; review only when dependency generation or upgrade process changes.”

Config/data:
“Large config/data file; validate schema before editing. Do not refactor like source code.”

Test file:
“Large test file; review test structure only if frequently edited or flaky.”

Never say “review module boundaries” for:
- docs
- specs
- markdown
- lockfiles
- requirements files
- i18n/localization
- generated SDK
- vendor
- fixtures

==================================================
8. ADD DEPENDENCY/LOCKFILE CLASSIFICATION
==================================================

Classify these as dependency_lock:
- package-lock.json
- pnpm-lock.yaml
- yarn.lock
- Cargo.lock
- go.sum
- requirements*.txt
- requirements_lock*.txt
- Pipfile.lock
- poetry.lock
- conda env lock files

They should not be runtime hotspots or module-boundary issues.

==================================================
9. FIX GENERATED AND VENDOR CLASSIFICATION
==================================================

Generated code:
- **/gen/**
- **/generated/**
- *.gen.*
- *.generated.*
- *_pb2.py
- *.pb.go
- generated SDK/client files

Vendor:
- vendor/**
- third_party/**
- external/**
- 3rdparty/**
- minified files
- vendored headers/libraries

Separate:
- “Generated SDK/client code — regenerate instead of editing manually”
from
- “Vendor/third-party hotspots — track only, do not refactor by default”

Do not mix generated project-owned SDK files with vendor dependencies.

==================================================
10. FIX TEST CLASSIFICATION
==================================================

Classify:
- *_test.go
- *_test.cc
- *_test.cpp
- *_test.py
- *.test.ts
- *.spec.ts
- tests/**
- test/**
- e2e/**
- integration/**
- smoke/**
as tests.

Classify:
- testdata/**
- fixtures/**
- wpt-import/**
as test/data fixtures.

Tests must not appear under Top runtime risks.

Test signal wording:
- “strong” when many real tests are detected.
- “present — coverage unknown” when some tests exist.
- “missing” only when no tests are detected.

==================================================
11. FIX RISK FACTOR DEDUPLICATION
==================================================

Before rendering factors, deduplicate while preserving order.

Bad:
“entry point, moderate size, moderate size, many imports”

Good:
“entry point, moderate size, many imports”

Apply to:
- top risks
- recommended actions
- compact context
- HTML report
- Markdown report

==================================================
12. FIX SCAN COVERAGE WARNINGS
==================================================

Only show coverage warnings when specific evidence exists.

Bad:
“Major source directories appear underrepresented.”

Good:
“Scan coverage note: expected directories cmd/, api/, server/ are below previous baseline.”

If there is no baseline, avoid baseline warnings unless obvious:
- tests dominate >70%
- source lines extremely low
- expected dirs exist but are excluded
- known required language files are absent

If a directory is present and large, do not warn that it is underrepresented unless showing baseline numbers.

==================================================
13. IMPROVE AGENT PROMPTS
==================================================

Agent prompt focus files should match the selected task.

For runtime tracing:
Primary focus should be runtime entry/surface + runtime hotspots.
Build files should go under “Build context if needed,” not primary focus.

For TODO triage:
Primary focus should be top TODO-bearing first-party source files.

For docs:
Primary focus should be documentation hotspots.

Current risks must include file names and useful reasons, not vague lines like:
“Contains 1 TODO/FIXME markers.”

Better:
- AK/Math.h: 31 TODO/FIXME markers and large core utility surface.
- src/foo.ts: runtime hotspot with TODOs and many imports.
- docs/ARCHITECTURE.md: documentation drift indicator.

==================================================
14. SNAPSHOT / REGRESSION TESTS
==================================================

Add fixture/snapshot tests for these repo patterns:

1. Browser engine repo
Expected:
- Correct browser engine identity.
- Runtime risks are source files, not Meta tools.
- Tests/WPT fixtures separated.
- Devcontainer not runtime.

2. Go + native backend repo
Expected:
- Go CLI/server identity.
- Native backend files are backend hotspots.
- Examples separated from primary runtime.
- Purpose is not raw HTML or generic fallback.

3. TypeScript/Rust monorepo
Expected:
- packages split into meaningful subcomponents.
- generated SDK files classified correctly.
- i18n files classified correctly.
- markdown specs classified correctly.

4. Framework/library repo
Expected:
- Framework/library archetype.
- No forced random runtime entry point.
- Components split deeper than top-level source dir.
- tests/generators/requirements not runtime risks.

5. Dependency lockfile fixture
Expected:
- lockfiles/requirements files get dependency warning, not module-boundary warning.

6. README extraction fixture
Expected:
- raw HTML skipped.
- badges skipped.
- meaningful title/purpose extracted.
- no generic “application logic” purpose.

Acceptance:
- No runtime risk section contains files classified as tests, generators, docs, vendor, generated, lockfiles, examples, build tooling, or environment setup.
- Risk factors are deduplicated.
- Component labels are specific where path rules allow.
- Speed overhead <= 5–10%.

==================================================
15. DELIVERABLES
==================================================

Deliver:
1. Code changes.
2. Updated snapshots/tests.
3. Before/after examples for at least:
   - one browser engine
   - one Go/native-backend project
   - one monorepo
   - one framework/library style project
4. Short changelog explaining:
   - central classifier added/used
   - entry/risk classification improved
   - purpose extraction fixed
   - large file policies improved
   - speed preserved

Definition of done:
Sentinel should produce reports that are fast and trustworthy across repo archetypes. It should not overclaim, should not force every repo into app-style runtime tracing, and should use consistent classification across the report, compact context, and agent prompt.