"""Dimension scoring, confidence and overall grade."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .utils import band_for, bands_from_rules, grade_for, grade_score, is_data_blob

DIMENSION_LABELS = {
    "architecture": "Architecture",
    "maintainability": "Maintainability",
    "tests": "Tests",
    "docs": "Documentation",
    "dependencies": "Dependencies",
    "security": "Security",
}

DIMENSION_ORDER = ["architecture", "maintainability", "tests", "docs", "dependencies", "security"]
SOURCE_DIMENSIONS = {"architecture", "maintainability", "tests"}


def _get(row: Any, key: str, default: Any = None) -> Any:
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return getattr(row, key, default)


def _grade_label(score: int | None, confidence: str) -> str:
    if score is None:
        return "n/a"
    label = grade_for(float(score))
    return f"{label}?" if confidence == "low" else label


def _confidence(
    files_rows: Sequence[Any],
    coverage: Mapping[str, Any],
    data_blob_min: int = 1000,
) -> tuple[str, list[str]]:
    source_files = sum(1 for row in files_rows if int(_get(row, "is_source", 0) or 0))
    parse_errors = sum(
        1
        for row in files_rows
        if int(_get(row, "is_source", 0) or 0) and _get(row, "parse_error")
    )
    sampled = sum(
        1
        for row in files_rows
        if int(_get(row, "is_source", 0) or 0)
        and int(_get(row, "sampled", 0) or 0)
        and not is_data_blob(
            int(_get(row, "lines", 0) or 0),
            int(_get(row, "function_count", 0) or 0),
            int(_get(row, "class_count", 0) or 0),
            data_blob_min,
        )
    )
    source_pct = float(coverage.get("source_pct", 1.0))
    test_pct = float(coverage.get("test_pct", 1.0))
    parse_success = 1.0 - (parse_errors / source_files) if source_files else 1.0
    reasons: list[str] = []
    if source_pct < 1.0:
        reasons.append(f"source coverage {source_pct:.0%}")
    if test_pct < 1.0:
        reasons.append(f"test coverage {test_pct:.0%}")
    if parse_errors:
        reasons.append(f"{parse_errors} source parse error(s)")
    if sampled:
        reasons.append(f"{sampled} source file(s) sampled")
    if source_pct >= 0.95 and test_pct >= 0.95 and parse_success >= 0.99 and sampled == 0:
        return "high", reasons
    if source_pct >= 0.80 and test_pct >= 0.80 and parse_success >= 0.95:
        return "medium", reasons
    return "low", reasons or ["analysis coverage below 80%"]


# How much one finding contributes to its family's "affected" measure before
# context and confidence weighting. Severity is the only per-rule signal left;
# the family penalty is then driven by how much of the codebase is affected.
_SEVERITY_WEIGHT = {"critical": 1.4, "high": 1.0, "medium": 0.6, "low": 0.3, "info": 0.0}


def _dimension_penalties(
    findings: Sequence[Any],
    rules: Mapping[str, Any],
    source_todos: int,
    source_lines: int,
    test_ratio: float = 0.0,
    expected_ratio: float = 0.0,
    hotspot_total: int = 0,
    hotspot_unmapped: int = 0,
    counts: Mapping[str, float] | None = None,
    peak_complexity: int = 0,
) -> dict[str, float]:
    """Penalties proportional to the share of code affected.

    Absolute counts flatten quality: with a fixed cap and ``1/sqrt(n)`` damping a
    repository with one oversized file and one with eighty both sit at the cap,
    while a 40k-file repository is punished for having more absolute problems
    than a 50-file one. The blueprint asks for ratios instead, so each family
    saturates on ``affected / denominator`` where the denominator is the relevant
    population (source files, test files) or a plain count for defects that are
    severe however large the repository is, such as unparseable files and leaked
    credentials.
    """

    counts = counts or {}
    family_by_rule = rules.get("family_by_rule", {})
    family_caps = rules.get("penalty_caps", {})
    denominators = rules.get("penalty_denominator", {})
    halves = rules.get("penalty_half_share", {})
    thresholds = rules.get("thresholds", {})
    context_factors = rules.get("context_penalty_factor", {"live": 1.0, "fixture": 0.1})
    confidence_factors = rules.get("confidence_penalty_factor", {"high": 1.0, "medium": 0.65, "low": 0.35})

    affected: dict[str, float] = {}
    family_dimension: dict[str, str] = {}
    family_hit: set[str] = set()
    # Files that are both oversized and very complex compound: the difficulty of
    # changing a module is not the sum of two independent findings scored against
    # the whole file population, it is the product of the two risks landing on the
    # same code. The same file being the largest module and the hardest to follow
    # is the worst single hotspot a codebase can carry.
    complex_paths: set[str] = set()
    oversized_paths: set[str] = set()
    for finding in findings:
        if finding.rule_id in {"MNT005", "MNT006", "MNT007"} and finding.file:
            complex_paths.add(str(finding.file))
        elif finding.rule_id in {"MNT001", "MNT002"} and finding.file:
            oversized_paths.add(str(finding.file))
    compounding = complex_paths & oversized_paths

    for finding in findings:
        weight = _SEVERITY_WEIGHT.get(finding.severity, 0.6)
        if weight <= 0:
            continue
        family = family_by_rule.get(finding.rule_id, finding.rule_id)
        family_dimension[family] = finding.dimension
        context_factor = float(context_factors.get(str(getattr(finding, "context", "live")), 1.0))
        confidence_factor = float(confidence_factors.get(finding.confidence, 1.0))

        if finding.rule_id == "MNT008":
            # TODOs are scored on density, never on raw count.
            density = (source_todos / source_lines * 1000.0) if source_lines else 0.0
            target = float(thresholds.get("todo_density_per_1000_lines", 8.0))
            affected[family] = affected.get(family, 0.0) + min(3.0, density / max(0.1, target))
            continue
        if finding.rule_id == "TST002":
            # A ratio shortfall is already a proportion: 2% of the expected tests
            # must cost far more than one sitting at 90%.
            shortfall = max(0.0, 1.0 - (test_ratio / expected_ratio)) if expected_ratio > 0 else 0.0
            affected[family] = affected.get(family, 0.0) + shortfall * weight * confidence_factor
            continue
        if finding.rule_id in {"MNT005", "MNT006", "MNT007"}:
            # Complexity is charged on peak severity, not on how many files the
            # repository happens to have. A single function with complexity 60 is
            # a genuine change-safety risk wherever it sits; dividing it by a
            # 16-file population as if it were one vote among sixteen buried that
            # signal in small codebases. The per-finding weight still scales with
            # the number of distinct complex functions, and the family cap still
            # bounds the total, so this measures intensity while the denominator
            # governs how widely the problem is spread.
            peak_reference = float(thresholds.get("complexity_peak_reference", 50) or 50)
            intensity = 1.0 + max(0.0, (float(peak_complexity) - peak_reference) / peak_reference)
            if finding.file and str(finding.file) in compounding:
                intensity *= 1.5
            affected[family] = affected.get(family, 0.0) + weight * context_factor * confidence_factor * intensity
            continue
        if finding.rule_id == "TST003":
            # Unmapped hotspots are charged on the share of the riskiest modules
            # left uncovered, not once per finding. A repository whose most
            # complex file has no test is meaningfully riskier to change than one
            # whose fringe does, even when both report a handful of findings; a
            # flat per-finding weight cannot tell them apart. This fires once,
            # when the first unmapped hotspot is seen, so repeated findings do not
            # stack on the same root cause (section 4.2).
            if hotspot_total > 0 and hotspot_unmapped > 0 and "test_mapping" not in family_hit:
                family_hit.add("test_mapping")
                share = min(1.0, float(hotspot_unmapped) / float(hotspot_total))
                affected[family] = affected.get(family, 0.0) + share * confidence_factor
            continue
        if finding.rule_id in {"MNT001", "MNT002", "MNT003", "MNT004"}:
            # An oversized file that is also very complex is harder to change than
            # one that is merely long, so the same compounding applies on the
            # maintainability side of the same module.
            weight = weight * 1.5 if finding.file and str(finding.file) in compounding else weight

        affected[family] = affected.get(family, 0.0) + weight * context_factor * confidence_factor

    dimension_penalties: dict[str, float] = {}
    for family, value in affected.items():
        if value <= 0:
            continue
        cap = float(family_caps.get(family, 100.0))
        denominator = str(denominators.get(family, "count"))
        if denominator == "count":
            share = value
        else:
            total = float(counts.get(denominator, 0) or 0)
            # Blend absolute burden with density. Pure density punishes a tiny
            # repository for one bad function in twenty and forgives a huge one
            # its hundreds; pure counts do the opposite. Adding a reference scale
            # to the population means small repositories are judged on how many
            # problems they actually have, and large ones on how much of their
            # code is affected.
            population = total + float(rules.get("penalty_reference_scale", 150.0))
            share = (value / population) if population > 0 else value
        half = float(halves.get(family, 0.15))
        # Families whose population is large enough to matter are judged on that
        # population alone; small ones fall back to the reference scale so a
        # single problem in a five-file repository is not catastrophic. The
        # reference scale is what made a repository with 84 very large test files
        # score the same tests dimension as one with 3: 84 / (2341 + 150) is a
        # 3.4% share that the half-share curve flattened to a rounding error.
        population_floor = float(rules.get("penalty_population_floor", 0) or 0)
        if denominator == "count":
            share = value
        else:
            total = float(counts.get(denominator, 0) or 0)
            # Blend absolute burden with density. Pure density punishes a tiny
            # repository for one bad function in twenty and forgives a huge one
            # its hundreds; pure counts do the opposite. Adding a reference scale
            # to the population means small repositories are judged on how many
            # problems they actually have, and large ones on how much of their
            # code is affected.
            population = (
                total
                if total >= population_floor
                else total + float(rules.get("penalty_reference_scale", 150.0))
            )
            share = (value / population) if population > 0 else value
        penalty = cap * min(1.0, share) if half <= 0 else cap * share / (share + half)
        if penalty <= 0:
            continue
        dimension = family_dimension[family]
        dimension_penalties[dimension] = dimension_penalties.get(dimension, 0.0) + penalty
    return dimension_penalties


def compute_quality(
    files_rows: Sequence[Any],
    findings: Sequence[Any],
    rules: Mapping[str, Any],
    manifest_meta: Mapping[str, Any],
    coverage: Mapping[str, Any],
    archetype: str,
    entry_evidence: Sequence[str] = (),
) -> dict[str, Any]:
    source_rows = [row for row in files_rows if int(_get(row, "is_source", 0) or 0)]
    test_rows = [row for row in files_rows if int(_get(row, "is_test", 0) or 0)]
    doc_rows = [row for row in files_rows if int(_get(row, "is_doc", 0) or 0)]
    config_rows = [row for row in files_rows if int(_get(row, "is_config", 0) or 0)]
    source_files = len(source_rows)
    test_files = len(test_rows)
    source_lines = sum(int(_get(row, "lines", 0) or 0) for row in source_rows)
    test_lines = sum(int(_get(row, "lines", 0) or 0) for row in test_rows)
    source_todos = sum(int(_get(row, "todos", 0) or 0) for row in source_rows)
    parse_errors = sum(1 for row in source_rows if _get(row, "parse_error"))
    max_complexity = max((int(_get(row, "max_complexity", 0) or 0) for row in source_rows), default=0)
    medium_lines = int(rules.get("thresholds", {}).get("file_lines_medium", 500))
    data_blob_min = int(rules.get("data_blob_min_lines", 1000))
    code_rows = [
        row
        for row in source_rows
        if not is_data_blob(
            int(_get(row, "lines", 0) or 0),
            int(_get(row, "function_count", 0) or 0),
            int(_get(row, "class_count", 0) or 0),
            data_blob_min,
        )
    ]
    code_lines = sum(int(_get(row, "lines", 0) or 0) for row in code_rows)
    oversized = sum(1 for row in code_rows if int(_get(row, "lines", 0) or 0) > medium_lines)

    confidence, confidence_reasons = _confidence(files_rows, coverage, data_blob_min)
    ratio_thresholds = rules.get("thresholds", {}).get("test_ratio", {})
    expected_ratio = float(ratio_thresholds.get(archetype, ratio_thresholds.get("default", 0.30)))
    test_ratio = (test_files / source_files) if source_files else 0.0
    # How much of the riskiest surface has no statically mapped test. This is the
    # quality of coverage, which a raw file-count ratio cannot see: a project can
    # ship many small tests and still leave its most complex module unexercised.
    hotspots = sorted(
        (row for row in source_rows if str(_get(row, "path", "")).endswith(".py")),
        key=lambda row: -(float(_get(row, "lines", 0) or 0) + 4.0 * float(_get(row, "max_complexity", 0) or 0)),
    )[: int(rules.get("thresholds", {}).get("hotspot_count", 10))]
    mapped_paths = {
        str(finding.file)
        for finding in findings
        if finding.rule_id == "TST003" and finding.file
    }
    hotspot_total = len(hotspots)
    hotspot_unmapped = len(mapped_paths)
    dimension_penalties = _dimension_penalties(
        findings,
        rules,
        source_todos,
        source_lines,
        test_ratio=test_ratio,
        expected_ratio=expected_ratio,
        peak_complexity=max_complexity,
        hotspot_total=hotspot_total,
        hotspot_unmapped=hotspot_unmapped,
        counts={
            "source_files": source_files,
            "test_files": test_files,
            "doc_files": len(doc_rows),
            "config_files": len(config_rows),
            "source_lines": source_lines,
        },
    )
    weights = rules.get("weights", {})

    assessed: set[str] = set()
    if source_files:
        assessed.update(SOURCE_DIMENSIONS)
    if doc_rows or (files_rows and any(finding.dimension == "docs" for finding in findings)):
        assessed.add("docs")
    if files_rows:
        assessed.add("dependencies")
    if source_files or config_rows or test_files:
        assessed.add("security")

    total_weight = sum(float(weights.get(dimension, 0.0)) for dimension in assessed)
    dimensions: list[dict[str, Any]] = []
    scores: dict[str, int | None] = {}
    for dimension in DIMENSION_ORDER:
        is_assessed = dimension in assessed
        if is_assessed:
            raw = 100.0 - dimension_penalties.get(dimension, 0.0)
            score: int | None = max(0, min(100, int(round(raw))))
            grade = _grade_label(score, confidence)
        else:
            score = None
            grade = "n/a"
        scores[dimension] = score
        evidence = sorted(
            {
                str(finding.file)
                for finding in findings
                if finding.dimension == dimension and finding.file
            }
        )[:3]
        summary = _dimension_summary(
            dimension,
            files_rows=files_rows,
            findings=findings,
            source_files=source_files,
            test_files=test_files,
            source_todos=source_todos,
            parse_errors=parse_errors,
            max_complexity=max_complexity,
            oversized=oversized,
            manifest_meta=manifest_meta,
            archetype=archetype,
            entry_evidence=entry_evidence,
        )
        dimensions.append(
            {
                "id": dimension,
                "grade": grade,
                "score": score,
                "confidence": confidence if is_assessed else "n/a",
                "assessed": is_assessed,
                "summary": summary,
                "evidence": evidence,
            }
        )

    if total_weight > 0:
        weighted = sum(float(scores[dimension] or 0) * float(weights.get(dimension, 0.0)) for dimension in assessed)
        overall_score = int(round(weighted / total_weight))
        # A weighted average lets healthy dimensions rescue a failing one. A
        # repository whose tests or maintainability genuinely fail cannot be good
        # overall, whatever its README looks like, so a weak assessed dimension
        # pulls the overall score toward it.
        weak_threshold = float(rules.get("weak_dimension_threshold", 70))
        weak_pull = float(rules.get("weak_dimension_pull", 0.5))
        assessed_scores = [int(scores[dimension]) for dimension in assessed if scores[dimension] is not None]
        weakest_score = min(assessed_scores) if assessed_scores else None
        if weakest_score is not None and weakest_score < weak_threshold and weak_pull > 0:
            overall_score = int(round(overall_score * (1.0 - weak_pull) + weakest_score * weak_pull))
        # Top grades require evidence of sustained quality at scale. A codebase too
        # small to demonstrate that is capped rather than rated exemplary. Only a
        # ceiling rule that actually matched may cap; the default is no ceiling.
        analysed_lines = code_lines + test_lines
        ceiling: str | None = None
        for rule in sorted(rules.get("grade_ceiling", []), key=lambda item: int(item.get("min_analysed_lines", 0))):
            if analysed_lines >= int(rule.get("min_analysed_lines", 0)):
                ceiling = str(rule.get("max_grade", "A+"))
        if ceiling is not None:
            limit = grade_score(ceiling)
            if limit is not None and overall_score > limit:
                overall_score = limit
        overall_band = band_for(overall_score, bands_from_rules(rules)) if overall_score is not None else "n/a"
    else:
        overall_score = None
        overall_band = "n/a"
        ceiling = None

    provisional = bool(not rules.get("calibrated", False) or confidence == "low")
    weakest = _weakest_assessed(dimensions)
    assessed_scores = [int(dimension["score"]) for dimension in dimensions if dimension.get("assessed") and dimension.get("score") is not None]
    if overall_band == "n/a":
        headline = "No source or documentation content was available to score."
    elif findings and weakest:
        headline = f"{len(findings)} finding(s); weakest dimension: {DIMENSION_LABELS.get(weakest, weakest)}."
    elif not findings and assessed_scores and min(assessed_scores) >= 95:
        headline = "All assessed dimensions are healthy; no high-signal findings were detected."
    elif weakest:
        headline = f"Weakest assessed dimension: {DIMENSION_LABELS.get(weakest, weakest)}."
    else:
        headline = "The repository has no assessed quality signals."

    return {
        "summary": {
            "band": overall_band,
            "overall_score": overall_score,
            "confidence": confidence,
            "provisional": provisional,
            "headline": headline,
            "confidence_reasons": confidence_reasons,
            "grade_ceiling": ceiling,
        },
        "dimensions": dimensions,
        "metrics": {
            "source_files": source_files,
            "test_files": test_files,
            "source_lines": source_lines,
            "test_lines": test_lines,
            "test_to_source_file_ratio": round(test_files / source_files, 2) if source_files else 0.0,
            "test_to_source_line_ratio": round(test_lines / source_lines, 2) if source_lines else 0.0,
            "source_todos": source_todos,
            "parse_errors": parse_errors,
            "max_complexity": max_complexity,
            "oversized_source_files": oversized,
            "archetype": archetype,
        },
    }


def _weakest_assessed(dimensions: Sequence[Mapping[str, Any]]) -> str | None:
    candidates = [dimension for dimension in dimensions if dimension.get("assessed") and dimension.get("score") is not None]
    if not candidates:
        return None
    return str(min(candidates, key=lambda dimension: (int(dimension["score"]), str(dimension["id"])))["id"])


def _dimension_summary(
    dimension: str,
    *,
    files_rows: Sequence[Any],
    findings: Sequence[Any],
    source_files: int,
    test_files: int,
    source_todos: int,
    parse_errors: int,
    max_complexity: int,
    oversized: int,
    manifest_meta: Mapping[str, Any],
    archetype: str,
    entry_evidence: Sequence[str],
) -> str:
    def count(rule_ids: Iterable[str]) -> int:
        wanted = set(rule_ids)
        return sum(1 for finding in findings if finding.rule_id in wanted)

    if dimension == "architecture":
        cycles = count({"ARCH002"})
        entry = entry_evidence[0] if entry_evidence else "no clear entry point"
        return f"{archetype.replace('_', ' ')}; {entry}; {cycles} import cycle(s)."
    if dimension == "maintainability":
        return (
            f"{source_files} source files; {oversized} over 500 lines; "
            f"{source_todos} TODOs; max complexity {max_complexity}; {parse_errors} parse error(s)."
        )
    if dimension == "tests":
        ratio = (test_files / source_files) if source_files else 0.0
        return f"{test_files} test files; ratio {ratio:.2f}; {count({'TST001', 'TST003', 'TST006'})} test-signal finding(s)."
    if dimension == "docs":
        if not any(int(_get(row, "is_doc", 0) or 0) for row in files_rows):
            return "No canonical documentation files detected."
        return f"{count({'DOC001', 'DOC002', 'DOC003', 'DOC004', 'DOC005'})} canonical documentation finding(s); translations excluded from scoring."
    if dimension == "dependencies":
        manifests = count({"DEP001", "DEP002", "DEP003", "DEP004", "HYG001", "HYG002", "HYG003", "HYG004"})
        unpinned = len(manifest_meta.get("unpinned", []) or [])
        has_manifest = bool(manifest_meta.get("manifest_kind"))
        if not has_manifest:
            lock = "no manifest"
        elif any(finding.rule_id == "DEP002" for finding in findings):
            lock = "no lockfile"
        else:
            lock = "lockfile"
        return f"{manifests} dependency/hygiene finding(s); {lock}; {unpinned} unpinned dependency string(s)."
    if dimension == "security":
        secrets = count({"SEC001"})
        dangerous = count({"SEC002"})
        debug = count({"SEC003"})
        return f"Basic pattern checks only; {secrets} secret-like match(es); {dangerous} dynamic-execution and {debug} debug finding(s)."
    return ""
