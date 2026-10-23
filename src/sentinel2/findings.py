"""Quality rules, evidence, ranking and noise control."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .classify import detect_archetype, entry_points, is_fixture_path, manifest_kind
from .utils import (
    CONFIDENCE_ORDER,
    DIMENSION_ORDER,
    SEVERITY_ORDER,
    format_bytes,
    is_data_blob,
    module_name_for_path,
)


@dataclass
class Finding:
    rule_id: str
    dimension: str
    severity: str
    confidence: str
    title: str
    file: str | None
    line: int
    evidence: list[str] = field(default_factory=list)
    why: str = ""
    recommendation: str = ""
    evidence_key: str = "project"
    category: str = "source"
    context: str = "live"

    @property
    def id(self) -> str:
        return f"{self.rule_id}:{self.evidence_key}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "rule_id": self.rule_id,
            "dimension": self.dimension,
            "severity": self.severity,
            "confidence": self.confidence,
            "title": self.title,
            "file": self.file,
            "line": int(self.line),
            "evidence": list(self.evidence),
            "why": self.why,
            "recommendation": self.recommendation,
        }


def _get(row: Any, key: str, default: Any = None) -> Any:
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return getattr(row, key, default)


def _meta(row: Any) -> dict[str, Any]:
    raw = _get(row, "meta_json")
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else {}
    except (TypeError, ValueError):
        return {}


def _basename(path: str) -> str:
    return path.replace("\\", "/").rsplit("/", 1)[-1]


def _stem(path: str) -> str:
    name = _basename(path)
    return name.rsplit(".", 1)[0] if "." in name else name


def _severity(rules: Mapping[str, Any], rule_id: str) -> str:
    return str(rules.get("severity_by_rule", {}).get(rule_id, "medium"))


def _add(
    findings: list[Finding],
    rules: Mapping[str, Any],
    rule_id: str,
    dimension: str,
    title: str,
    file: str | None,
    line: int,
    evidence: Iterable[str],
    why: str,
    recommendation: str,
    *,
    confidence: str = "high",
    category: str = "source",
    evidence_key: str | None = None,
    context: str = "live",
    severity: str | None = None,
) -> None:
    key = evidence_key or (file or "project")
    if any(existing.rule_id == rule_id and existing.evidence_key == key for existing in findings):
        return
    findings.append(
        Finding(
            rule_id=rule_id,
            dimension=dimension,
            severity=severity if severity is not None else _severity(rules, rule_id),
            confidence=confidence,
            title=title,
            file=file,
            line=int(line),
            evidence=list(evidence),
            why=why,
            recommendation=recommendation,
            evidence_key=key,
            category=category,
            context=context,
        )
    )


def _module_map(source_rows: Iterable[Any]) -> dict[str, str]:
    modules: dict[str, str] = {}
    for row in source_rows:
        module = module_name_for_path(str(_get(row, "path")))
        if module:
            modules[module] = str(_get(row, "path"))
    return modules


def _resolve_module(imported: str, modules: Mapping[str, str]) -> str | None:
    imported = imported.strip()
    if not imported or imported.startswith((".", "/")):
        return None
    if imported in modules:
        return imported
    parts = imported.split(".")
    for end in range(len(parts) - 1, 0, -1):
        candidate = ".".join(parts[:end])
        if candidate in modules:
            return candidate
    return None


def _import_cycles(
    source_rows: Sequence[Any],
    import_rows: Sequence[Any],
    modules: Mapping[str, str] | None = None,
) -> list[list[str]]:
    modules = dict(modules) if modules is not None else _module_map(source_rows)
    if not modules:
        return []
    graph: dict[str, set[str]] = {module: set() for module in modules}
    for row in import_rows:
        source_module = module_name_for_path(str(_get(row, "file")))
        if source_module not in graph:
            continue
        target = _resolve_module(str(_get(row, "imported")), modules)
        if target and target != source_module or target == source_module:
            graph[source_module].add(target)

    # Kosaraju SCC, iterative to avoid recursion limits on large repositories.
    visited: set[str] = set()
    order: list[str] = []
    for start in sorted(graph):
        if start in visited:
            continue
        stack: list[tuple[str, bool]] = [(start, False)]
        while stack:
            node, processed = stack.pop()
            if processed:
                order.append(node)
                continue
            if node in visited:
                continue
            visited.add(node)
            stack.append((node, True))
            for neighbour in sorted(graph.get(node, ()), reverse=True):
                if neighbour not in visited:
                    stack.append((neighbour, False))

    reverse: dict[str, set[str]] = {module: set() for module in graph}
    for source, targets in graph.items():
        for target in targets:
            reverse.setdefault(target, set()).add(source)

    assigned: set[str] = set()
    cycles: list[list[str]] = []
    for start in reversed(order):
        if start in assigned:
            continue
        component: list[str] = []
        stack = [start]
        while stack:
            node = stack.pop()
            if node in assigned:
                continue
            assigned.add(node)
            component.append(node)
            for neighbour in sorted(reverse.get(node, ())):
                if neighbour not in assigned:
                    stack.append(neighbour)
        if len(component) > 1:
            cycles.append(sorted(component))
        elif component and component[0] in graph.get(component[0], set()):
            cycles.append(component)
    cycles.sort()
    return cycles


def _test_mapping(
    source_rows: Sequence[Any],
    test_rows: Sequence[Any],
    import_rows: Sequence[Any],
    modules: Mapping[str, str] | None = None,
) -> set[str]:
    modules = dict(modules) if modules is not None else _module_map(source_rows)
    stem_map: dict[str, list[str]] = {}
    prefix_map: dict[str, set[str]] = {}
    for row in source_rows:
        path = str(_get(row, "path"))
        stem = _stem(path)
        if stem and stem != "__init__":
            stem_map.setdefault(stem, []).append(path)
        module = module_name_for_path(path)
        if module:
            parts = module.split(".")
            for end in range(1, len(parts) + 1):
                prefix_map.setdefault(".".join(parts[:end]), set()).add(path)

    test_paths = {str(_get(test, "path")) for test in test_rows}
    mapped: set[str] = set()
    for row in import_rows:
        file_path = str(_get(row, "file"))
        if file_path not in test_paths:
            continue
        imported = str(_get(row, "imported"))
        if imported in modules:
            mapped.add(modules[imported])
            continue
        # An import may name an attribute of a module (pkg.mod.attr); map the
        # longest module prefix without scanning every source module.
        parts = imported.split(".")
        for end in range(len(parts) - 1, 0, -1):
            candidate = ".".join(parts[:end])
            if candidate in modules:
                mapped.add(modules[candidate])
                break
        # A package import (pkg) maps every module below that package.
        mapped.update(prefix_map.get(imported, ()))

    test_stems = {_stem(str(_get(row, "path"))) for row in test_rows}
    for stem in test_stems:
        if not stem.startswith("test_"):
            continue
        target_stem = stem[5:]
        candidates = stem_map.get(target_stem, [])
        if len(candidates) == 1:
            mapped.add(candidates[0])
    return mapped


def _hotspots(source_rows: Sequence[Any]) -> list[Any]:
    def score(row: Any) -> float:
        return (
            float(_get(row, "lines", 0)) / 100.0
            + float(_get(row, "max_complexity", 0))
            + float(_get(row, "size", 0)) / 50000.0
            + float(_get(row, "churn", 0)) * 2.0
        )

    return sorted(source_rows, key=lambda row: (-score(row), str(_get(row, "path"))))


def detect_findings(
    conn: sqlite3.Connection,
    root: Path,
    rules: Mapping[str, Any],
    files_rows: Sequence[Any],
    manifest_meta: Mapping[str, Any],
    excluded_counts: Mapping[str, int],
    churn_available: bool,
    include_docs: bool = False,
) -> list[Finding]:
    """Run every quality rule against the current index snapshot."""

    thresholds = rules.get("thresholds", {})
    source_rows = [row for row in files_rows if int(_get(row, "is_source", 0) or 0)]
    test_rows = [row for row in files_rows if int(_get(row, "is_test", 0) or 0)]
    doc_rows = [
        row
        for row in files_rows
        if int(_get(row, "is_doc", 0) or 0) and not int(_get(row, "is_translation", 0) or 0)
    ]
    config_rows = [row for row in files_rows if int(_get(row, "is_config", 0) or 0)]
    all_paths = {str(_get(row, "path")) for row in files_rows}
    source_paths = [str(_get(row, "path")) for row in source_rows]
    source_lines = sum(int(_get(row, "lines", 0) or 0) for row in source_rows)
    archetype = detect_archetype(root, files_rows, manifest_meta)
    entry_evidence = entry_points(root, [str(_get(row, "path")) for row in files_rows], manifest_meta)
    import_rows = list(conn.execute("SELECT file, imported, kind FROM imports ORDER BY file, imported"))
    modules = _module_map(source_rows)
    mapped_sources = _test_mapping(source_rows, test_rows, import_rows, modules)
    findings: list[Finding] = []

    # --- Architecture -----------------------------------------------------
    if source_rows and archetype in {"cli", "web_app", "application"} and not entry_evidence:
        _add(
            findings,
            rules,
            "ARCH001",
            "architecture",
            "Application has no clear entry point",
            None,
            1,
            [f"{len(source_rows)} source files", "no main/CLI entry point found"],
            "Without an entry point it is harder to see how the application starts and where behaviour begins.",
            "Add or document a single entry point (for example main.py, __main__.py or a console script).",
            category="project",
        )
    cycles = _import_cycles(source_rows, import_rows, modules)
    for cycle in cycles[:5]:
        paths = sorted({modules.get(module, module) for module in cycle})
        _add(
            findings,
            rules,
            "ARCH002",
            "architecture",
            f"Import cycle between {len(cycle)} source modules",
            paths[0] if paths else None,
            1,
            [f"{len(cycle)} modules in cycle", " -> ".join(cycle + [cycle[0]])],
            "Import cycles make startup order fragile and prevent clean layering.",
            "Break the cycle by moving shared contracts into a lower-level module.",
            evidence_key="cycle:" + ",".join(cycle),
        )
    if (
        archetype in {"library", "framework_library"}
        and len(source_rows) > 1
        and not any(path.endswith("__init__.py") for path in source_paths)
    ):
        _add(
            findings,
            rules,
            "ARCH003",
            "architecture",
            "Source package has no clear public surface",
            source_paths[0] if source_paths else None,
            1,
            [f"{len(source_rows)} source files", "no package __init__.py"],
            "A library without a package initializer has no obvious public API boundary.",
            "Add a package __init__.py that explicitly exports the supported surface.",
            category="project",
        )
    if len(entry_evidence) > 2 and len(source_rows) < int(thresholds.get("source_files_tiny", 20)):
        _add(
            findings,
            rules,
            "ARCH004",
            "architecture",
            "Multiple competing entry points in a small application",
            entry_evidence[0],
            1,
            entry_evidence[:5],
            "Competing entry points duplicate startup behaviour and confuse users.",
            "Choose one supported entry point and document the rest as internal tools.",
            category="project",
        )
    # Architectural load: the real cost of a large, intricate codebase. The
    # other architecture rules only fire on defects (cycles, missing entry
    # point), so they stay quiet for well-formed but sprawling projects, which
    # leaves Architecture pinned near A+ and lets healthy dimensions rescue a
    # huge repository's overall grade. Module count and peak complexity measure
    # how hard the codebase is to hold in one head; both are charged as a share
    # of the code affected so a small project pays nothing and a very large one
    # approaches - but never exceeds - the family cap.
    arch_modules = len(source_rows)
    arch_threshold = int(thresholds.get("arch_load_modules", 60))
    max_complexity = max((int(_get(row, "max_complexity", 0) or 0) for row in source_rows), default=0)
    if source_rows and arch_modules >= arch_threshold:
        _add(
            findings,
            rules,
            "ARCH005",
            "architecture",
            "High architectural load across a large, intricate codebase",
            None,
            1,
            [
                f"{arch_modules} source modules",
                f"threshold {arch_threshold}",
                f"max complexity {max_complexity}",
            ],
            "A codebase this large and intricate is expensive to change safely: coordination, "
            "review latency and blast radius all grow with structural load even when no single "
            "defect is present.",
            "Split cohesive subpackages behind clear interfaces, raise test coverage on the "
            "highest-churn modules, and document the module boundaries.",
            category="project",
        )

    # --- Maintainability --------------------------------------------------
    medium_lines = int(thresholds.get("file_lines_medium", 500))
    high_lines = int(thresholds.get("file_lines_high", 2000))
    medium_size = int(thresholds.get("file_size_medium", 100 * 1024))
    high_size = int(thresholds.get("file_size_high", 250 * 1024))
    for row in source_rows:
        path = str(_get(row, "path"))
        lines = int(_get(row, "lines", 0) or 0)
        size = int(_get(row, "size", 0) or 0)
        if is_data_blob(
            lines,
            int(_get(row, "function_count", 0) or 0),
            int(_get(row, "class_count", 0) or 0),
            int(rules.get("data_blob_min_lines", 1000)),
        ):
            # Generated tables and benchmark payloads are data, not maintainable
            # code. Their size is not a reviewability defect.
            continue
        evidence = [f"{lines:,} lines", format_bytes(size)]
        if lines > high_lines:
            _add(
                findings,
                rules,
                "MNT002",
                "maintainability",
                "Very large source file",
                path,
                1,
                evidence,
                "Very large modules are hard to review, test and change safely.",
                "Map the relevant tests, then extract one cohesive concern at a time.",
            )
        elif lines > medium_lines:
            _add(
                findings,
                rules,
                "MNT001",
                "maintainability",
                "Large source file",
                path,
                1,
                evidence,
                "Large modules accumulate responsibilities and slow down review.",
                "Identify one cohesive boundary to extract, with tests first.",
            )
        if size > high_size and lines <= high_lines:
            _add(
                findings,
                rules,
                "MNT004",
                "maintainability",
                "Very large source file by byte size",
                path,
                1,
                evidence,
                "A very large source file is costly to parse, review and merge.",
                "Check for generated content, then split or regenerate the file.",
            )
        elif size > medium_size and lines <= medium_lines:
            _add(
                findings,
                rules,
                "MNT003",
                "maintainability",
                "Large source file by byte size",
                path,
                1,
                evidence,
                "Large byte size often signals generated or duplicated content.",
                "Verify the file is hand-written and split it if it is.",
            )

    function_rows = list(
        conn.execute(
            "SELECT symbols.file, symbols.name, symbols.qualname, symbols.kind, symbols.line, "
            "symbols.length, symbols.complexity FROM symbols JOIN files ON files.path = symbols.file "
            "WHERE files.is_source = 1 AND (symbols.length > ? OR symbols.complexity > ?) "
            "ORDER BY symbols.complexity DESC, symbols.length DESC",
            (int(thresholds.get("function_lines", 100)), int(thresholds.get("complexity_medium", 15))),
        )
    )
    chosen_symbols: dict[str, sqlite3.Row] = {}
    for row in function_rows:
        chosen_symbols.setdefault(str(row["file"]), row)
    for path, row in sorted(chosen_symbols.items()):
        complexity = int(row["complexity"] or 0)
        length = int(row["length"] or 0)
        evidence = [
            f"{row['qualname']} at line {row['line']}",
            f"{length} lines",
            f"complexity {complexity}",
        ]
        if complexity > int(thresholds.get("complexity_high", 25)):
            _add(
                findings,
                rules,
                "MNT007",
                "maintainability",
                "Very high cyclomatic complexity",
                path,
                int(row["line"]),
                evidence,
                "Very high branching complexity makes a function hard to test exhaustively.",
                "Split the function by responsibility and add regression tests for each branch.",
            )
        elif complexity > int(thresholds.get("complexity_medium", 15)):
            _add(
                findings,
                rules,
                "MNT006",
                "maintainability",
                "High cyclomatic complexity",
                path,
                int(row["line"]),
                evidence,
                "High branching complexity raises the cost of safe changes.",
                "Extract a helper or simplify branches, then add edge-case tests.",
            )
        elif length > int(thresholds.get("function_lines", 100)):
            _add(
                findings,
                rules,
                "MNT005",
                "maintainability",
                "Long function",
                path,
                int(row["line"]),
                evidence,
                "Long functions mix steps that can usually be named and tested separately.",
                "Extract one named step at a time, preserving behaviour with tests.",
            )

    source_todos = sum(int(_get(row, "todos", 0) or 0) for row in source_rows)
    density = (source_todos / source_lines * 1000.0) if source_lines else 0.0
    if source_todos >= int(thresholds.get("todo_count_min", 5)) and density > float(
        thresholds.get("todo_density_per_1000_lines", 8.0)
    ):
        top_todos = sorted(source_rows, key=lambda row: (-int(_get(row, "todos", 0) or 0), str(_get(row, "path"))))[:5]
        _add(
            findings,
            rules,
            "MNT008",
            "maintainability",
            "High TODO/FIXME density in source",
            None,
            1,
            [f"{source_todos} markers", f"{density:.1f} per 1,000 source lines"]
            + [f"{_get(row, 'path')}: {int(_get(row, 'todos', 0) or 0)}" for row in top_todos if int(_get(row, 'todos', 0) or 0)],
            "A dense backlog of inline TODOs signals unfinished or uncertain code.",
            "Triage the markers: convert real work into issues and delete stale notes.",
            category="project",
        )
    for row in source_rows:
        parse_error = _get(row, "parse_error")
        if not parse_error or int(_get(row, "sampled", 0) or 0):
            continue
        _add(
            findings,
            rules,
            "MNT009",
            "maintainability",
            "Python parse error",
            str(_get(row, "path")),
            1,
            [str(parse_error)],
            "A file that does not parse cannot be analysed or imported safely.",
            "Fix the syntax error and re-scan to restore full analysis coverage.",
        )
    for row in source_rows + config_rows:
        if not int(_get(row, "encoding_replaced", 0) or 0):
            continue
        _add(
            findings,
            rules,
            "MNT010",
            "maintainability",
            "File contains invalid UTF-8",
            str(_get(row, "path")),
            1,
            ["decoded with replacement characters"],
            "Mixed encodings hide characters from tools and reviewers.",
            "Convert the file to UTF-8 and verify the affected text.",
            category="source" if int(_get(row, "is_source", 0) or 0) else "config",
        )

    # --- Tests ------------------------------------------------------------
    if source_rows and not test_rows:
        _add(
            findings,
            rules,
            "TST001",
            "tests",
            "No test files detected",
            None,
            1,
            [f"{len(source_rows)} source files", "0 test files"],
            "Without tests, changes to source behaviour cannot be verified automatically.",
            "Add a small test suite around the main entry path and the riskiest modules.",
            category="project",
        )
    ratio = (len(test_rows) / len(source_rows)) if source_rows else 0.0
    ratio_thresholds = thresholds.get("test_ratio", {})
    expected_ratio = float(ratio_thresholds.get(archetype, ratio_thresholds.get("default", 0.30)))
    if len(source_rows) >= int(thresholds.get("min_source_files_for_ratio", 5)) and ratio < expected_ratio:
        _add(
            findings,
            rules,
            "TST002",
            "tests",
            "Test/source ratio below expected for this repository shape",
            None,
            1,
            [f"{len(test_rows)} test files", f"{len(source_rows)} source files", f"ratio {ratio:.2f} (expected >= {expected_ratio:.2f})"],
            "A low test/source ratio suggests large parts of the codebase have no direct tests.",
            "Add focused tests for the highest-risk modules before broad refactoring.",
            category="project",
        )
    if test_rows:
        missing = [row for row in _hotspots(source_rows)[: int(thresholds.get("hotspot_count", 10))] if str(_get(row, "path")) not in mapped_sources]
        for row in missing[: int(thresholds.get("hotspot_no_test_count", 5))]:
            _add(
                findings,
                rules,
                "TST003",
                "tests",
                "High-risk hotspot has no statically mapped test",
                str(_get(row, "path")),
                1,
                [f"{int(_get(row, 'lines', 0) or 0):,} lines", f"complexity {int(_get(row, 'max_complexity', 0) or 0)}", "no importing test found"],
                "No test file imports or names this module, so a change here is poorly covered by static evidence.",
                "Add or identify a direct test before editing this module.",
            )
        entry_source_paths = [path for path in entry_evidence if path in all_paths and path in source_paths]
        unmapped_entry = [path for path in entry_source_paths if path not in mapped_sources]
        if unmapped_entry:
            _add(
                findings,
                rules,
                "TST006",
                "tests",
                "No test exercises the main entry path",
                unmapped_entry[0],
                1,
                unmapped_entry[:5],
                "The main entry path is the first thing users run, but no test imports it.",
                "Add one smoke test that exercises the documented entry point.",
            )
        for row in test_rows:
            if int(_get(row, "lines", 0) or 0) <= int(thresholds.get("test_file_lines", 1000)):
                continue
            _add(
                findings,
                rules,
                "TST004",
                "tests",
                "Very large test file",
                str(_get(row, "path")),
                1,
                [f"{int(_get(row, 'lines', 0) or 0):,} lines"],
                "Very large test files are harder to navigate and selectively run.",
                "Group related tests and move shared setup into fixtures.",
            )
        test_todos = sum(int(_get(row, "todos", 0) or 0) for row in test_rows)
        if test_todos >= int(thresholds.get("test_todo_count", 10)):
            _add(
                findings,
                rules,
                "TST005",
                "tests",
                "Many TODO/FIXME markers in tests",
                None,
                1,
                [f"{test_todos} markers across {len(test_rows)} test files"],
                "A large number of test TODOs suggests deferred coverage or flaky cases.",
                "Resolve or convert test TODOs into tracked issues.",
                category="project",
            )

    # --- Documentation ----------------------------------------------------
    readme_rows = [
        row
        for row in doc_rows
        if _basename(str(_get(row, "path"))).lower().startswith("readme")
        and "/" not in str(_get(row, "path")).strip("/")
    ]
    readme = sorted(readme_rows, key=lambda row: (len(str(_get(row, "path"))), str(_get(row, "path"))))[0] if readme_rows else None
    if not readme:
        _add(
            findings,
            rules,
            "DOC001",
            "docs",
            "No README found",
            None,
            1,
            ["no README.md, README.rst or README.txt at the repository root"],
            "A repository without a README gives a new developer no starting point.",
            "Add a short README that states what the project is, how to run it and how to test it.",
            category="project",
        )
    else:
        meta = _meta(readme)
        readme_path = str(_get(readme, "path"))
        placeholders = int(meta.get("placeholders", 0) or 0)
        if placeholders >= int(thresholds.get("readme_placeholder_min", 1)):
            _add(
                findings,
                rules,
                "DOC002",
                "docs",
                "README contains placeholder content",
                readme_path,
                1,
                [f"{placeholders} placeholder marker(s)"],
                "Placeholder text in the front door makes the project look unfinished.",
                "Replace placeholders with real setup, usage and testing instructions.",
                category="docs",
            )
        readme_size = int(_get(readme, "size", 0) or 0)
        headings = int(meta.get("headings", 0) or 0)
        if readme_size > int(thresholds.get("readme_large_bytes", 50 * 1024)) or (
            int(_get(readme, "lines", 0) or 0) > int(thresholds.get("readme_unstructured_lines", 200)) and headings < 2
        ):
            _add(
                findings,
                rules,
                "DOC005",
                "docs",
                "README is extremely large or unstructured",
                readme_path,
                1,
                [format_bytes(readme_size), f"{headings} headings"],
                "A long, unstructured README is difficult to scan for the information a newcomer needs.",
                "Keep the README short and move deep material into focused docs.",
                category="docs",
            )
        broken: list[str] = []
        for row in doc_rows:
            if int(_get(row, "is_translation", 0) or 0):
                continue
            if not include_docs and row is not readme:
                continue
            row_meta = _meta(row)
            for target in row_meta.get("broken_links", [])[: int(thresholds.get("broken_links_max", 10))]:
                broken.append(f"{_get(row, 'path')}: {target}")
        if broken:
            _add(
                findings,
                rules,
                "DOC004",
                "docs",
                "Canonical documentation has broken relative links",
                readme_path,
                1,
                broken[: int(thresholds.get("broken_links_max", 10))],
                "Broken links send readers to files that no longer exist.",
                "Fix or remove the broken links and add a docs link check to CI.",
                category="docs",
            )
    translation_count = int(excluded_counts.get("doc_translation", 0) or 0)
    if translation_count and not doc_rows:
        _add(
            findings,
            rules,
            "DOC006",
            "docs",
            "Documentation translations are the only documentation",
            None,
            1,
            [f"{translation_count} translation files", "no canonical docs detected"],
            "Translations without a canonical source are hard to keep accurate.",
            "Add canonical English documentation and treat translations as derived content.",
            confidence="high",
            category="docs",
        )

    # --- Dependencies and repository hygiene ------------------------------
    manifests = [row for row in config_rows if manifest_kind(str(_get(row, "path")))]
    lock_names = {str(item).lower() for item in rules.get("lockfile_names", [])}
    license_names = {str(item).lower() for item in rules.get("license_names", [])}
    has_lock = any(_basename(path).lower() in lock_names for path in all_paths)
    has_license = any(_basename(path).lower() in license_names for path in all_paths)
    if source_rows and not manifests:
        _add(
            findings,
            rules,
            "DEP001",
            "dependencies",
            "No dependency manifest",
            None,
            1,
            [f"{len(source_rows)} source files", "no supported manifest found"],
            "Without a manifest, dependencies cannot be installed reproducibly.",
            "Add pyproject.toml, package.json or the manifest appropriate to the project.",
            category="project",
        )
    elif manifests and not has_lock:
        _add(
            findings,
            rules,
            "DEP002",
            "dependencies",
            "Manifest present but no lockfile",
            str(_get(manifests[0], "path")),
            1,
            [f"{len(manifests)} manifest(s)", "no lockfile found"],
            "A lockfile makes installs reproducible across machines and CI.",
            "Generate and commit the lockfile for the package manager in use.",
            category="config",
        )
    unpinned = list(manifest_meta.get("unpinned", []) or [])
    if unpinned:
        _add(
            findings,
            rules,
            "DEP003",
            "dependencies",
            "Unpinned direct dependencies",
            str(manifest_meta.get("manifest_path") or (str(_get(manifests[0], "path")) if manifests else "")) or None,
            1,
            [f"{len(unpinned)} unpinned dependency string(s)"] + [str(item) for item in unpinned[:5]],
            "Loose dependency ranges make builds drift over time.",
            "Pin direct dependencies to tested versions and let the lockfile handle transitives.",
            category="config",
        )
    if source_rows and not has_license:
        _add(
            findings,
            rules,
            "DEP004",
            "dependencies",
            "No license file",
            None,
            1,
            ["no LICENSE, COPYING or NOTICE file found"],
            "A repository without a license is not legally reusable by others.",
            "Add the license you intend to use and reference it from the README.",
            category="project",
        )
    if ".gitignore" not in all_paths:
        _add(
            findings,
            rules,
            "HYG001",
            "dependencies",
            "No .gitignore",
            None,
            1,
            ["no .gitignore at the repository root"],
            "Without ignore rules, local state and build output can be committed by accident.",
            "Add a .gitignore for the toolchain in use.",
            category="project",
        )
    ci_paths = [str(item).replace("\\", "/") for item in rules.get("ci_paths", [])]
    has_ci = any(
        path == ci_path or path.startswith(ci_path + "/")
        for path in all_paths
        for ci_path in ci_paths
    )
    if not has_ci:
        _add(
            findings,
            rules,
            "HYG002",
            "dependencies",
            "No CI configuration",
            None,
            1,
            ["no GitHub Actions, GitLab CI or equivalent configuration"],
            "Automated checks catch regressions that a local scan cannot.",
            "Add a minimal CI job that runs tests and quality checks.",
            category="project",
        )
    generated_or_vendor = int(excluded_counts.get("generated", 0) or 0) + int(excluded_counts.get("vendor", 0) or 0)
    if generated_or_vendor >= int(thresholds.get("large_excluded_count", 100)):
        _add(
            findings,
            rules,
            "HYG004",
            "dependencies",
            "Large generated or vendored surface excluded from scoring",
            None,
            1,
            [f"{excluded_counts.get('generated', 0)} generated files", f"{excluded_counts.get('vendor', 0)} vendor files"],
            "A large excluded surface is healthy to exclude, but it affects the real repository size.",
            "Keep generated and vendored content out of reviews and keep regeneration reproducible.",
            category="project",
        )

    # --- Security basics --------------------------------------------------
    secret_findings = 0
    max_security = int(thresholds.get("security_max_findings", 5))
    for row in files_rows:
        path = str(_get(row, "path"))
        if not (int(_get(row, "is_source", 0) or 0) or int(_get(row, "is_test", 0) or 0) or int(_get(row, "is_config", 0) or 0)):
            continue
        meta = _meta(row)
        # Test, fixture, certificate and sample paths almost always hold deliberate
        # test material. They are still reported, but weighted down so they cannot
        # dominate the security grade the way a leaked credential in live code does.
        in_fixture = bool(
            int(_get(row, "is_test", 0) or 0)
            or int(_get(row, "is_example", 0) or 0)
            or is_fixture_path(path)
        )
        context = "fixture" if in_fixture else "live"
        for secret in meta.get("secrets", []):
            if secret_findings >= max_security:
                break
            # A provider-shaped token (AKIA..., ghp_..., sk-...) is unambiguous;
            # a name-based "assigned credential" match is only a heuristic, so it
            # carries less weight and a lower reported confidence.
            specific = secret.get("kind") != "assigned credential"
            _add(
                findings,
                rules,
                "SEC001",
                "security",
                "Secret-like string in a test fixture" if in_fixture else "Secret-like string in a tracked file",
                path,
                int(secret.get("line", 1) or 1),
                [f"{secret.get('kind', 'possible secret')}: {secret.get('redacted', '[REDACTED]')}"]
                + (["likely deliberate test material"] if in_fixture else []),
                "A credential committed to source should be treated as compromised.",
                "Remove the value from history, rotate the credential and load it from the environment."
                if not in_fixture
                else "Confirm the value is a fake test fixture; otherwise remove it, rotate the credential and load it from the environment.",
                confidence="low" if in_fixture else ("high" if specific else "medium"),
                category="test" if in_fixture and int(_get(row, "is_test", 0) or 0) else ("source" if int(_get(row, "is_source", 0) or 0) else "config"),
                context=context,
                severity="low" if in_fixture else ("high" if specific else "medium"),
                evidence_key=f"{path}:{secret.get('line', 1)}:{secret.get('kind', 'secret')}",
            )
            secret_findings += 1
        dangerous = meta.get("dangerous", [])
        if dangerous:
            _add(
                findings,
                rules,
                "SEC002",
                "security",
                "Dangerous dynamic execution pattern",
                path,
                int(dangerous[0].get("line", 1) or 1),
                [f"line {item.get('line')}: {item.get('kind')}" for item in dangerous[:5]]
                + (["in test or fixture context"] if in_fixture else []),
                "Dynamic execution is a common path for injection bugs.",
                "Prefer explicit calls or strict allow-lists; document why dynamic execution is required.",
                confidence="low" if in_fixture else "medium",
                category="test" if in_fixture and int(_get(row, "is_test", 0) or 0) else ("source" if int(_get(row, "is_source", 0) or 0) else "config"),
                context=context,
                severity="low" if in_fixture else None,
            )
        debug = meta.get("debug", [])
        if debug:
            _add(
                findings,
                rules,
                "SEC003",
                "security",
                "Hardcoded debug mode",
                path,
                int(debug[0].get("line", 1) or 1),
                [f"line {item.get('line')}: {item.get('kind')}" for item in debug[:5]]
                + (["in test or fixture context"] if in_fixture else []),
                "Debug mode enabled in tracked code can leak stack traces and internal state.",
                "Drive debug settings from environment configuration and default to off.",
                confidence="low" if in_fixture else "medium",
                category="test" if in_fixture and int(_get(row, "is_test", 0) or 0) else ("source" if int(_get(row, "is_source", 0) or 0) else "config"),
                context=context,
                severity="low" if in_fixture else None,
            )

    # --- Hotspots and churn ----------------------------------------------
    if churn_available:
        churn_min = int(thresholds.get("churn_min", 3))
        changed_hotspots = [
            row
            for row in _hotspots(source_rows)
            if int(_get(row, "churn", 0) or 0) >= churn_min
            and (
                float(_get(row, "max_complexity", 0) or 0) > int(thresholds.get("complexity_medium", 15))
                or int(_get(row, "lines", 0) or 0) > medium_lines
            )
        ]
        for row in changed_hotspots[:5]:
            path = str(_get(row, "path"))
            _add(
                findings,
                rules,
                "HOT001",
                "maintainability",
                "High-churn hotspot",
                path,
                1,
                [f"{int(_get(row, 'churn', 0) or 0)} recent changes", f"{int(_get(row, 'lines', 0) or 0):,} lines", f"complexity {int(_get(row, 'max_complexity', 0) or 0)}"],
                "Frequent changes in a complex file increase the chance of regressions.",
                "Review this file with tests first and consider extracting a stable boundary.",
            )
            if test_rows and path not in mapped_sources:
                _add(
                    findings,
                    rules,
                    "HOT002",
                    "tests",
                    "High-churn hotspot has no mapped test",
                    path,
                    1,
                    [f"{int(_get(row, 'churn', 0) or 0)} recent changes", "no importing test found"],
                    "A frequently changed file without a mapped test is a regression risk.",
                    "Add direct tests before the next change.",
                )

    return sort_findings(findings)


def sort_findings(findings: Sequence[Finding]) -> list[Finding]:
    return sorted(
        findings,
        key=lambda finding: (
            _category_rank(finding.category),
            SEVERITY_ORDER.get(finding.severity, 9),
            CONFIDENCE_ORDER.get(finding.confidence, 9),
            DIMENSION_ORDER.get(finding.dimension, 9),
            finding.file or "",
            finding.rule_id,
            finding.evidence_key,
        ),
    )


def _category_rank(category: str) -> int:
    return {
        "source": 0,
        "test": 0,
        "config": 1,
        "project": 2,
        "docs": 3,
        "dependencies": 4,
    }.get(category, 5)


def rank_top_findings(findings: Sequence[Finding], limit: int = 5) -> list[Finding]:
    """Source-first top findings with per-rule and per-dimension noise caps."""

    ordered = sort_findings(findings)
    selected: list[Finding] = []
    rule_counts: dict[str, int] = {}
    dimension_counts: dict[str, int] = {}
    seen_paths: set[tuple[str | None, str]] = set()
    for finding in ordered:
        if finding.severity == "info":
            continue
        if rule_counts.get(finding.rule_id, 0) >= 2:
            continue
        if dimension_counts.get(finding.dimension, 0) >= 3 and len(ordered) > limit:
            continue
        key = (finding.file, finding.rule_id)
        if key in seen_paths:
            continue
        selected.append(finding)
        rule_counts[finding.rule_id] = rule_counts.get(finding.rule_id, 0) + 1
        dimension_counts[finding.dimension] = dimension_counts.get(finding.dimension, 0) + 1
        seen_paths.add(key)
        if len(selected) >= limit:
            break
    return selected


def build_actions(findings: Sequence[Finding]) -> list[dict[str, Any]]:
    """Turn the highest-value findings into a short, ranked action plan."""

    if not findings:
        return [
            {
                "rank": 1,
                "title": "Keep the quality baseline and re-scan after changes",
                "why": "No high-signal findings were detected in the current snapshot.",
                "impact": "low",
                "effort": "low",
            }
        ]

    by_rule = {finding.rule_id for finding in findings}
    actions: list[dict[str, Any]] = []

    def add(title: str, why: str, impact: str, effort: str) -> None:
        if any(action["title"] == title for action in actions):
            return
        actions.append(
            {
                "rank": len(actions) + 1,
                "title": title,
                "why": why,
                "impact": impact,
                "effort": effort,
            }
        )

    if by_rule & {"SEC001"}:
        add(
            "Remove and rotate hardcoded secrets",
            "Committed credentials must be treated as compromised before any other refactor.",
            "high",
            "low",
        )
    if by_rule & {"TST001", "TST003", "TST006", "HOT002"}:
        add(
            "Add tests around the top hotspots",
            "The riskiest source paths have no direct static test evidence.",
            "high",
            "medium",
        )
    if by_rule & {"MNT001", "MNT002", "MNT003", "MNT004"}:
        add(
            "Reduce oversized source files in small, test-backed moves",
            "Large files dominate review cost and make changes riskier.",
            "high",
            "high",
        )
    if by_rule & {"MNT005", "MNT006", "MNT007"}:
        add(
            "Split high-complexity functions and add branch tests",
            "Complex branching is the most expensive code to change safely.",
            "medium",
            "high",
        )
    if by_rule & {"ARCH002"}:
        add(
            "Break import cycles before adding features",
            "Cycles make startup order and layering fragile.",
            "medium",
            "medium",
        )
    if by_rule & {"DOC001", "DOC002", "DOC004", "DOC005"}:
        add(
            "Fix canonical documentation and archive translation noise",
            "The README and canonical docs are the front door for new developers.",
            "medium",
            "low",
        )
    if by_rule & {"DEP002", "DEP003"}:
        add(
            "Pin dependencies and commit a lockfile",
            "Reproducible installs prevent quality drift between machines.",
            "low",
            "low",
        )
    if not actions:
        add(
            "Address the highest-ranked finding first",
            "Start with the top finding and re-scan after the change.",
            "medium",
            "medium",
        )
    return actions[:3]
