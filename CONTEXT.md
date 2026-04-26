# Sentinel Context

Generated: 2026-04-24T23:40:34+01:00
Target: `.`
Budget: `small`
Prompt Tokens: 1590

Kilo should start from this generated Sentinel context before broad file reads.

## Freshness
- Context Fresh: yes
- Path Validation: all focus files exist

## Files
- Overview: `.sentinel/kilo/overview.md`
- Context Pack: `.sentinel/kilo/context.md`
- Prompt Pack: `.sentinel/kilo/prompt.md`
- Focus Files: `.sentinel/kilo/focus-files.txt`

## Focus Files
Paths are relative to the workspace root.
- `src/auditor.py`
- `src/sentinel.py`
- `setup.py`
- `benchmarks/fixtures/cpp_repo/src/main.cpp`
- `benchmarks/fixtures/go_service/cmd/service/main.go`
- `benchmarks/fixtures/rust_cli/src/main.rs`

## Recommended Next Step
- Title: Address 3390 first-party TODO/FIXME markers
- Priority: medium
- Action: Review and resolve first-party TODO items; test/imported TODOs can be deferred
- Reason: Total: 3609 TODOs (first-party engine: 3390, tooling: 121, test/fixture: 70, documentation: 28). Focus on first-party engine TODOs.

## Working Rule
- Read focus files first.
- Only open extra files if the focus files are insufficient.
- Refresh Sentinel after meaningful edits with `project-sentinel kilo-refresh . --scan-root . --budget small --fast`.

## Compact Context

# Project Knowledge Base

## Summary
- Project: sentinel-agent
- Type: cpp project with a CLI or script entry point with a service/API layer and a test suite
- Primary Language: cpp
- Files: 12971
- Total Lines: 1475664
- Open Issues: 524
- Patterns Found: 5
- Top Suggestion: Address 3390 first-party TODO/FIXME markers
- Estimated Token Savings: 100%
- Last Scan: 2026-04-24T23:40:53+01:00
- Last Checkpoint: 2026-04-24T23:40:25+01:00

## Project Understanding
- Summary: sentinel-agent appears to be cpp project with a CLI or script entry point with a service/API layer and a test suite. Autonomous project monitor, auditor, and suggestion engine.
- Purpose: Autonomous project monitor, auditor, and suggestion engine.
- Confidence: identity=high; language=high; entry=high
- Frameworks: fastapi, pytest, unittest, pydantic, python_packaging, test_suite
- Workflow: Start execution tracing from src/auditor.py, Use the test suite as the fastest regression signal, Read the project manifest before changing dependencies or startup flow, Use the README as the first source of product intent

## Main Components
- src: application logic (12 files / 10748 lines)
- tests: test suite / test infrastructure (6 files / 1358 lines)
- docs: documentation (7 files / 527 lines)
- config: configuration and defaults (3 files / 175 lines)
- benchmarks/fixtures: test suite / test infrastructure (16 files / 100 lines)

## Important Files
- src/auditor.py: entry point
- src/sentinel.py: entry point
- setup.py: entry point
- benchmarks/fixtures/cpp_repo/src/main.cpp: entry point
- benchmarks/fixtures/go_service/cmd/service/main.go: entry point
- benchmarks/fixtures/rust_cli/src/main.rs: entry point

## Architecture
- Entry Points: src/auditor.py, src/sentinel.py, setup.py, benchmarks/fixtures/cpp_repo/src/main.cpp, benchmarks/fixtures/go_service/cmd/service/main.go, benchmarks/fixtures/rust_cli/src/main.rs
- Patterns: automated_tests, documentation, command_line_interface, containerization, packaging
- Directories: benchmarks/fixtures, benchmarks/fixtures/cpp_repo, benchmarks/fixtures/cpp_repo/src, benchmarks/fixtures/docs_heavy, benchmarks/fixtures/docs_heavy/docs, benchmarks/fixtures/generated_heavy/generated

## Dependencies
- python: sentinel-url-reports/.url-cache/ladybird-default/Tests/ClangPlugins/requirements.txt, sentinel-url-reports/.url-cache/ladybird-default/pyproject.toml, setup.py
- node: benchmarks/fixtures/node_app/package.json
- containers: sentinel-url-reports/.url-cache/ladybird-default/.devcontainer/fedora-ci/Dockerfile

## Patterns
- automated_tests: The project includes automated tests
- documentation: The project includes markdown documentation
- command_line_interface: The project exposes a command-line entry point
- containerization: The project includes container-related assets
- packaging: The project includes package or dependency manifests

## Recent Issues
- [high] 3609 TODO/FIXME markers (first-party engine: 3390, tooling: 121, test/fixture: 70, documentation: 28)
- [medium] File is 6352 lines; consider reviewing module boundaries (directory_structure.txt)
- [medium] File is 750KB (directory_structure.txt)
- [medium] File is 888 lines; consider reviewing module boundaries (README.md)

## Suggested Next Move
- [medium] Address 3390 first-party TODO/FIXME markers: Review and resolve first-party TODO items; test/imported TODOs can be deferred
  Reason: Total: 3609 TODOs (first-party engine: 3390, tooling: 121, test/fixture: 70, documentation: 28). Focus on first-party engine TODOs.
- [medium] Trace the main execution path before editing: Map the runtime path through entry points and current hotspots
  Reason: Start from src/auditor.py and trace into sentinel-url-reports/.url-cache/ladybird-default/Libraries/LibWeb/Crypto/CryptoAlgorithms.cpp, sentinel-url-reports/.url-cache/ladybird-default/Libraries/LibJS/Rust/src/bytecode/codegen.rs

## LLM Strategy
- Recommended Budget: large
- Full Context Tokens: 16506857
- Compact Context Tokens: 2386
- Estimated Savings: 100%

## Task Memory
- patch verification: changed README.md, SELLABLE_PRODUCT_INTEGRATION_REPORT.md, SELLABLE_PRODUCT_ROADMAP.md, benchmarks/; tests C:\Users\anton\AppData\Local\Programs\Python\Python310\python.exe -m pytest tests/test_auditor.py tests/test_knowledge.py tests/test_knowledge_repo.py tests/test_mcp.py tests/test_sentinel.py tests/test_weighted_entry_points.py; risks none recorded
