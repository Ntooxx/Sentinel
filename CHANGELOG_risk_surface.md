# Sentinel Risk Surface Classification Fix — Changelog

## Why Tooling Files Were Leaking Into Top Runtime Risks

The `_categorize_hotspot` method used a separate `_classify_path_context` classifier that had different category mappings from the canonical `classifyRiskSurface` function. For example, `_classify_path_context` mapped all `Meta/` files to `build_tooling` regardless of subtype, but `_categorize_hotspot` mapped `generator`, `lint_tooling`, and `tooling` contexts all to `build_tooling` surface — while `classifyRiskSurface` distinguished `generator` as its own surface. This inconsistency meant some files could appear in "Top runtime risks" because the hotspot and risk scoring paths used different classification logic.

Additionally, `classifyRiskSurface` was missing path rules for Ladybird-specific patterns like `Meta/Lagom/Fuzzers/`, `Libraries/LibMedia/`, `Libraries/LibWebView/`, and `Libraries/LibMain/`, causing these files to fall through to the generic source extension check and get classified as `runtime` when they should have been `test_runner` or had explicit runtime recognition.

## How the Surface Classifier Fixes It

1. **Canonical classifier alignment**: `_categorize_hotspot` now delegates to `classifyRiskSurface` instead of `_classify_path_context`, ensuring all risk grouping, hotspot display, and risk scoring use the same surface classification logic.

2. **New surface-aware group keys**: Hotspot groups expanded from `{runtime, build_tooling, vendor, test_data, documentation}` to `{runtime, build_tooling, generator, test_runner, vendor, test_data, documentation}`, matching the risk groups structure. Reporter sections now include "Generator Hotspots" and "Test Runner Hotspots".

3. **Added path rules** for `Meta/Lagom/Fuzzers/` → `test_runner`, `Meta/CMake/` → `build_tooling`, `/fuzzers/` and `/fuzzer/` → `test_runner`, `Libraries/LibMedia/` and `Libraries/LibWebView/` and `Libraries/LibMain/` → `runtime`, `TIFFGenerator` → `generator`, and `3rdparty/` → `vendor`.

4. **`_classify_path_context`** in auditor.py also updated with `/lagom/`, `/cmake/`, `/fuzzers/` patterns, and `Libraries/LibMain` → `runtime_entry` to stay consistent with `classifyRiskSurface`.

## How Duplicate Factors Were Removed

Risk factors were already deduplicated in `_score_file_risks`, but the `_dedupe_list` utility in `reporter.py` was applied in the `risk_rows()` and `grouped_risk_sections()` functions. The `build_prompt_pack` in `suggester.py` now also deduplicates factors when building the "Current risks" section of the agent prompt, using case-insensitive key matching.

## How Speed Was Preserved

All changes are path/metadata classification — no new file I/O, no AST parsing, no dependency graph building. The `classifyRiskSurface` function processes paths using string operations and regex patterns in O(1) per file. The `_categorize_hotspot` change from `_classify_path_context` to `classifyRiskSurface` is a pure substitution with no additional computation. The timing impact is negligible (well under 5%).