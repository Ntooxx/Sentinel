from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional
import re

from classify import classifyFile, classifyRiskSurface, classifySurface, riskFromScore
from utils import DEFAULT_AUDIT_RULES, read_json
from scan_engine import classify_path_context, _categorize_entry_point

_KNOWN_MAJOR_GO_BINARIES = {
    "kube-apiserver", "kubelet", "kube-controller-manager",
    "kube-scheduler", "kubectl", "kube-proxy", "kubeadm",
}


def _calculate_entry_point_score(filepath: str, parts: tuple[str, ...], info: Any = None) -> int:
    score = 100
    lower_parts = tuple(part.lower() for part in parts)
    fp = Path(filepath)
    name_lower = fp.stem.lower()
    parent_name = fp.parent.name.lower() if fp.parent and fp.parent.name.lower() != name_lower else name_lower
    category = _categorize_entry_point(filepath)

    if any(part in {"src", "source", "app", "cmd", "main"} for part in lower_parts):
        score += 50

    binary_name = parent_name if parent_name != name_lower else name_lower
    if binary_name in _KNOWN_MAJOR_GO_BINARIES:
        score += 80

    if any(part == "libraries" for part in lower_parts):
        score += 40

    if any(part in {"runtime", "bin", "sbin"} for part in lower_parts):
        score += 20

    if category == "runtime":
        score += 25
    elif category == "runtime_surface":
        score += 20
    elif category == "example":
        score -= 35
    elif category == "build":
        score += 5
    elif category == "generator":
        score -= 15
    elif category == "test":
        score -= 45
    elif category == "environment":
        score -= 55
    elif category == "packaging":
        score -= 5

    if any(part in {"tests", "test", "spec"} for part in lower_parts):
        score -= 50

    if any(part in {"docs", "documentation"} for part in lower_parts):
        score -= 30

    if any(part in {".devcontainer", "scripts", "tools"} for part in lower_parts):
        score -= 20

    if any(part in {"build", "meta", "generators"} for part in lower_parts):
        score -= 10

    if any(part in {"examples", "example", "samples", "sample", "demo", "demos"} for part in lower_parts):
        score -= 60

    if any(part in {"assets", "asset", "static"} for part in lower_parts):
        score -= 80

    return max(0, score)


class HealthCalculator:
    """Computes health scores, risk scores, and metrics."""

    def __init__(self, audit_rules_path: Optional[str] = None):
        if not audit_rules_path:
            self.rules = dict(DEFAULT_AUDIT_RULES)
        else:
            loaded = read_json(audit_rules_path, DEFAULT_AUDIT_RULES)
            if isinstance(loaded, dict):
                from utils import merge_dicts  # noqa: F811
                self.rules = merge_dicts(DEFAULT_AUDIT_RULES, loaded)
            else:
                self.rules = dict(DEFAULT_AUDIT_RULES)

    def _compute_metrics(self, files: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        total_lines = sum(info["line_count"] for info in files.values())
        total_size = sum(info["size"] for info in files.values())
        todos = sum(info["todo_count"] for info in files.values())
        ext_counts: Dict[str, int] = {}

        todo_categories = {
            "first_party_source": 0,
            "tooling": 0,
            "tests_fixtures": 0,
            "docs": 0,
            "vendor_generated": 0,
        }

        for path, info in files.items():
            ext = info["extension"]
            ext_counts[ext] = ext_counts.get(ext, 0) + 1

            todo_count = info.get("todo_count", 0)
            if todo_count > 0:
                fc = classifyFile(path, info)
                if fc.isTest or fc.isTestRunner or fc.isFixture:
                    todo_categories["tests_fixtures"] += todo_count
                elif fc.isDocumentation or fc.isSpecification:
                    todo_categories["docs"] += todo_count
                elif fc.isVendor or fc.isGenerated or fc.isGeneratedSdk:
                    todo_categories["vendor_generated"] += todo_count
                elif fc.isBuildTooling or fc.isGenerator or fc.isConfig:
                    todo_categories["tooling"] += todo_count
                elif fc.isLocalization or fc.isDependencyLock:
                    todo_categories["vendor_generated"] += todo_count
                elif fc.isEnvironmentSetup:
                    todo_categories["tooling"] += todo_count
                elif fc.isRuntimeSource:
                    todo_categories["first_party_source"] += todo_count
                else:
                    todo_categories["first_party_source"] += todo_count

        largest_files = sorted(
            (
                {"file": path, "lines": info["line_count"], "size": info["size"]}
                for path, info in files.items()
            ),
            key=lambda item: (item["lines"], item["size"]),
            reverse=True,
        )[:5]

        return {
            "total_files": len(files),
            "total_lines": total_lines,
            "total_size_bytes": total_size,
            "open_todos": todos,
            "todo_categories": todo_categories,
            "file_types": dict(sorted(ext_counts.items())),
            "avg_lines_per_file": total_lines // max(len(files), 1),
            "largest_files": largest_files,
        }

    def _calculate_health_score(
        self,
        issues: List[Dict[str, Any]],
        metrics: Dict[str, Any],
        risk_summary: Dict[str, Any],
    ) -> Dict[str, Any]:
        severity_penalties = self.rules.get("health_penalties", DEFAULT_AUDIT_RULES["health_penalties"])
        type_penalties = self.rules.get(
            "health_penalties_by_type",
            DEFAULT_AUDIT_RULES["health_penalties_by_type"],
        )
        score = 100.0

        security_assessed = any(issue.get("category") == "security" for issue in issues)

        total_lines = max(1, metrics.get("total_lines", 0))
        total_files = max(1, metrics.get("total_files", 0))
        is_mature = total_lines >= 50000 or total_files >= 2000

        issue_type_counts: Dict[str, int] = {}
        for issue in issues:
            itype = issue.get("type", "")
            if itype == "todo":
                continue
            issue_type_counts[itype] = issue_type_counts.get(itype, 0) + 1

        for itype, count in issue_type_counts.items():
            if itype in type_penalties:
                penalty = float(type_penalties[itype]) * min(count, 10)
                if itype in {"large_file", "large_file_size"} and is_mature:
                    penalty *= 0.5
            else:
                sevs = [issue.get("severity", "low") for issue in issues if issue.get("type") == itype]
                median_sev = "medium" if len(sevs) > 5 else (sevs[0] if sevs else "low")
                penalty = float(severity_penalties.get(median_sev, 1)) * min(count, 5)
            score -= penalty

        todo_categories = metrics.get("todo_categories", {})

        todo_density = metrics.get("open_todos", 0) / total_lines * 1000
        density_penalty = min(35, todo_density * 6) if not is_mature else min(25, todo_density * 4)

        maintainability = max(55, min(100, round(100 - density_penalty - min(20, len(issues) / 60))))
        documentation_todos = int(todo_categories.get("docs", 0) or 0)
        documentation_drift = sum(1 for issue in issues if issue.get("type") == "doc_code_drift")
        large_docs = sum(
            1
            for issue in issues
            if issue.get("type") == "large_file" and classify_path_context(str(issue.get("file") or "")) == "documentation"
        )
        documentation = max(0, min(100, 80 - min(30, documentation_todos * 3)))
        runtime_level = risk_summary.get("runtime", {}).get("level", "unknown")
        test_signal = risk_summary.get("test", {}).get("level", "unknown")

        modifiers = 0
        if test_signal == "strong":
            modifiers += 5
        elif test_signal in ("missing", "none", "low"):
            modifiers -= 10
        if runtime_level == "high":
            modifiers -= 5
        elif runtime_level == "medium":
            modifiers -= 2
        if documentation <= 30:
            modifiers -= 5
        if not security_assessed:
            modifiers -= 5

        if is_mature and test_signal == "strong":
            modifiers += 3

        final_score = max(30, min(100, round(maintainability + modifiers)))

        has_scan_coverage_warning = any(issue.get("type") == "scan_coverage" for issue in issues)
        if has_scan_coverage_warning:
            explanation = (
                "Health score may be unreliable due to incomplete scan coverage. "
                "Maintainability appears strong, but test presence and runtime complexity "
                "could not be fully assessed."
            )
        else:
            if final_score >= 70:
                explanation = (
                    "Score reflects repo maintenance risk, not project quality. "
                    "Strong test and architecture signals improve confidence, while TODO volume, oversized files, "
                    "runtime complexity, and unassessed security reduce it."
                )
            elif final_score >= 50:
                explanation = (
                    "Score reflects repo maintenance risk, not project quality. "
                    "TODO density and runtime complexity weigh on the score, "
                    "while test infrastructure and documentation quality provide some confidence."
                )
            else:
                explanation = (
                    "Score reflects repo maintenance risk, not project quality. "
                    "Significant TODO volume, runtime complexity, or documentation gaps "
                    "reduce confidence. Review the highest-risk areas before broad changes."
                )

        maintainability_risk_level = riskFromScore(maintainability)

        confidence_label = None
        confidence_reason = ""
        if total_lines >= 100000 or total_files >= 10000:
            confidence_label = "low_confidence"
            confidence_reason = "large repo — scan samples, does not read every file fully"
        elif total_lines >= 50000:
            confidence_label = "moderate_confidence"
            confidence_reason = "large repo — scan samples may miss some patterns"

        has_confidence_reasons = any(
            reason.get("level") == "medium"
            for reason in (risk_summary.get("entry_points_confidence", {}) or {}).values()
        )
        if has_confidence_reasons and not confidence_reason:
            confidence_reason = "entry-point uncertainty"
            confidence_label = confidence_label or "moderate_confidence"

        return {
            "score": final_score,
            "security_assessed": security_assessed,
            "reason": f"Security {'was assessed' if security_assessed else 'was not assessed'}",
            "explanation": explanation,
            "confidence_label": confidence_label or "normal",
            "confidence_reason": confidence_reason,
            "breakdown": {
                "maintainability_percent": maintainability,
                "maintainability_risk": maintainability_risk_level,
                "runtime_complexity": runtime_level,
                "test_signal": test_signal,
                "todo_density": round(todo_density, 2),
                "lines_scanned": total_lines,
                "documentation_percent": documentation,
                "documentation_reason": (
                    f"{documentation_drift} documentation file(s) show heuristic drift indicators; "
                    f"sample matches before treating them as stale. "
                    f"{large_docs} large documentation file(s) may need review; {documentation_todos} documentation TODO(s)"
                ),
                "security": "assessed" if security_assessed else "not_assessed",
            },
        }

    def _score_file_risks(
        self,
        files: Dict[str, Dict[str, Any]],
        structure: Dict[str, Any],
        issues: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        entry_points = set(structure.get("entry_points", []))
        test_files = set(structure.get("test_files", []))
        test_index = self._build_test_index(test_files)
        issue_map: Dict[str, list[str]] = {}
        for issue in issues:
            if issue.get("file"):
                issue_map.setdefault(issue["file"], []).append(issue.get("type", "issue"))

        risks: list[dict[str, Any]] = []
        for path, info in files.items():
            fc = classifyFile(path, info)

            if path in test_files:
                continue
            if fc.isTest or fc.isTestRunner or fc.isFixture:
                continue
            if fc.isGeneratedSdk or fc.isGenerated:
                continue
            if fc.isVendor:
                continue
            if fc.isDependencyLock:
                continue
            if fc.isLocalization:
                continue
            if fc.isDocumentation or fc.isSpecification:
                continue
            if fc.isGenerator:
                continue
            if fc.isEnvironmentSetup:
                continue
            if fc.isBuildTooling:
                continue
            lower_path = path.replace("\\", "/").lower()
            if any(part in lower_path for part in {"/examples/", "/example/", "/samples/", "/sample/", "/demo/", "/demos/", "/assets/", "/asset/", "/static/"}):
                continue
            name_lower = Path(path).name.lower()
            ext_lower = Path(path).suffix.lower()
            if ext_lower in {".cc", ".cpp", ".cxx"} and name_lower.endswith(("_test.cc", "_test.cpp", "_test.cxx")):
                continue
            if ext_lower in {".cc", ".cpp", ".cxx"} and name_lower.endswith(("_gen.cc", "_gen.cpp", "_gen.cxx")):
                continue
            if ext_lower == ".go" and name_lower.endswith("_test.go"):
                continue
            if ext_lower == ".py" and name_lower.endswith("_test.py"):
                continue
            if name_lower.startswith("gen_") or "/gen_" in lower_path:
                continue
            if "/genop/" in lower_path:
                continue
            if name_lower.startswith("requirements") and name_lower.endswith(".txt"):
                continue

            risk_surface = classifyRiskSurface(path, info)

            score = 0
            factors: list[str] = []

            if path in entry_points:
                score += 30
                factors.append("entry point")
            line_count = int(info.get("line_count", 0))
            if line_count > 500 and (fc.isConfig or fc.isDependencyLock or fc.isDocumentation or fc.isLocalization):
                score += 8
                factors.append("large data/config file")
            elif line_count > 250:
                score += 10
                factors.append("moderate size")
            import_count = int(info.get("import_count", 0))
            if import_count >= 12:
                score += 15
                factors.append("many imports")
            elif import_count >= 6:
                score += 8
                factors.append("several imports")
            if info.get("has_main"):
                score += 10
                factors.append("runtime surface")
            if info.get("has_class") or info.get("has_function"):
                if not fc.isDocumentation:
                    score += 6
                    factors.append("executable code")
                else:
                    score += 2
                    factors.append("contains code examples")
            coverage = self._classify_test_coverage(path, test_index)
            if Path(path).suffix in {".py", ".go"} and coverage["status"] == "none":
                score += 12
                factors.append("no obvious paired test")
            if issue_map.get(path):
                score += 8 * len(issue_map[path])
                factors.extend(issue_map[path][:2])

            if score <= 0:
                continue
            if score >= 55:
                level = "high"
            elif score >= 30:
                level = "medium"
            else:
                level = "low"
            seen_factors: set[str] = set()
            deduped_factors: list[str] = []
            for f in factors:
                if f not in seen_factors:
                    seen_factors.add(f)
                    deduped_factors.append(f)
            risks.append(
                {
                    "file": path,
                    "score": min(score, 100),
                    "level": level,
                    "factors": deduped_factors[:6],
                    "risk_categories": self._risk_categories(path, info, deduped_factors),
                    "surface": risk_surface,
                    "coverage": coverage,
                }
            )

        risks.sort(key=lambda item: item["score"], reverse=True)
        return risks[:80]

    def _group_risk_scores(
        self,
        risk_scores: List[Dict[str, Any]],
        files: Dict[str, Dict[str, Any]],
    ) -> Dict[str, List[Dict[str, Any]]]:
        groups: Dict[str, List[Dict[str, Any]]] = {
            "runtime": [],
            "runtime_surface": [],
            "build_tooling": [],
            "generator": [],
            "test_runner": [],
            "test_data": [],
            "documentation": [],
            "specification": [],
            "vendor": [],
            "generated_sdk": [],
            "dependency_lock": [],
            "environment_setup": [],
            "config": [],
            "example": [],
            "other": [],
        }
        for risk in risk_scores:
            path = risk.get("file", "")
            surface = risk.get("surface") or classifyRiskSurface(path, files.get(path, {}))
            if surface not in groups:
                surface = "other"
            groups[surface].append(risk)
        return {key: values[:10] for key, values in groups.items() if values}

    def _summarize_risk_categories(
        self,
        issues: List[Dict[str, Any]],
        risk_scores: List[Dict[str, Any]],
        structure: Dict[str, Any] = None,
        maintainability_percent: Optional[int] = None,
    ) -> Dict[str, Any]:
        summary: Dict[str, Any] = {
            "structural": {"level": "low", "signals": 0},
            "runtime": {"level": "unknown", "signals": 0},
            "test": {"level": "unknown", "signals": 0},
            "security": {"level": "not_assessed", "signals": 0},
            "maintainability": {"level": "low", "signals": 0},
        }

        for issue in issues:
            category = issue.get("category") or "maintainability"
            if category not in summary:
                summary[category] = {"level": "low", "signals": 0}
            summary[category]["signals"] += 1
            if issue.get("type") in {"large_file", "large_file_size"} and category != "maintainability":
                summary["maintainability"]["signals"] += 1

        for risk in risk_scores:
            for category in risk.get("risk_categories", []):
                if category not in summary:
                    summary[category] = {"level": "low", "signals": 0}
                summary[category]["signals"] += 1

        for category, data in summary.items():
            signals = int(data.get("signals", 0))
            if category == "security" and signals == 0:
                continue
            if category == "test":
                continue
            if category == "maintainability" and maintainability_percent is not None:
                data["level"] = riskFromScore(maintainability_percent)
            else:
                high_threshold = 20 if category == "maintainability" else 8
                if signals >= high_threshold:
                    data["level"] = "high"
                elif signals >= 3:
                    data["level"] = "medium"
                elif signals >= 1:
                    data["level"] = "low"

        has_test_files = structure is not None and bool(structure.get("has_tests"))
        test_files_count = len(structure.get("test_files", [])) if structure else 0

        e2e_count = 0
        fixture_count = 0
        if structure:
            for tf in structure.get("test_files", []):
                lower_tf = tf.lower()
                if "/e2e/" in lower_tf or "e2e" in lower_tf.split("/")[-1]:
                    e2e_count += 1
                if "/fixtures/" in lower_tf or "/testdata/" in lower_tf or "/wpt-import/" in lower_tf:
                    fixture_count += 1

        has_coverage_warning = any(issue.get("type") == "scan_coverage" for issue in issues)

        if any(issue.get("type") == "no_tests" for issue in issues):
            if has_coverage_warning:
                summary["test"]["level"] = "missing"
                summary["test"]["reason"] = "No automated test suite was detected — scan coverage may be incomplete or test detection uncertain"
            else:
                summary["test"]["level"] = "missing"
                summary["test"]["reason"] = "No automated test suite was detected"
        elif has_test_files and test_files_count > 5:
            parts = []
            if e2e_count > 0:
                parts.append(f"{e2e_count} end-to-end test file(s)")
            if fixture_count > 0:
                parts.append(f"{fixture_count} test fixture file(s)")
            if parts:
                summary["test"]["level"] = "strong"
                summary["test"]["reason"] = "Several real test directories/files detected including " + "; ".join(parts)
            else:
                summary["test"]["level"] = "strong"
                summary["test"]["reason"] = f"Several real test directories/files detected ({test_files_count} test files)"
        elif has_test_files or test_files_count > 0:
            summary["test"]["level"] = "present"
            summary["test"]["reason"] = "present — coverage unknown"
        else:
            summary["test"]["level"] = "missing"
            summary["test"]["reason"] = "missing"

        if any(issue.get("type") == "no_entry_point" for issue in issues):
            summary["runtime"]["level"] = "medium"

        return summary

    def _build_test_index(self, test_files: set[str]) -> Dict[str, Any]:
        entries = []
        for test_path in sorted(test_files):
            stem = Path(test_path).stem.lower()
            ext = Path(test_path).suffix.lower()
            if ext == ".go":
                normalized = stem.removesuffix("_test") if stem.endswith("_test") else stem
            else:
                normalized = stem.removeprefix("test_").removesuffix("_test")
            entries.append({"path": test_path, "stem": stem, "normalized": normalized})
        return {"entries": entries}

    def _classify_test_coverage(self, path: str, test_index: Dict[str, Any]) -> Dict[str, Any]:
        ext = Path(path).suffix.lower()

        if ext not in {".py", ".go"}:
            return {"status": "not_applicable", "test_file": None}

        if ext == ".go":
            stem = Path(path).stem.lower()
            expected_test = f"{stem}_test.go"
            for entry in test_index.get("entries", []):
                if entry["path"].endswith(expected_test) or entry["normalized"] == stem:
                    return {"status": "paired", "test_file": entry["path"]}
            return {"status": "none", "test_file": None}

        stem = Path(path).stem.lower()
        for entry in test_index.get("entries", []):
            if entry["normalized"] == stem:
                return {"status": "paired", "test_file": entry["path"]}

        for entry in test_index.get("entries", []):
            normalized = entry["normalized"]
            if stem and (stem in normalized.split("_") or normalized in stem):
                return {"status": "related", "test_file": entry["path"]}

        return {"status": "none", "test_file": None}

    def _risk_categories(self, path: str, info: Dict[str, Any], factors: List[str]) -> List[str]:
        fc = classifyFile(path, info)
        categories = set()

        if any(factor in factors for factor in {"large file", "moderate size", "many imports", "several imports"}):
            categories.add("structural")

        if "large data/config file" in factors:
            categories.add("maintainability")

        if any(factor in factors for factor in {"entry point", "runtime surface"}):
            categories.add("runtime")

        if "no obvious paired test" in factors:
            categories.add("test")

        surface = classifySurface(path, info)
        if surface == "runtime" or (Path(path).suffix == ".py" and (info.get("has_class") or info.get("has_function"))):
            categories.add("runtime")

        if fc.role == "documentation":
            categories.add("maintainability")

        return sorted(categories) or ["maintainability"]

    def _risk_surface(self, path: str, info: Dict[str, Any]) -> str:
        return classifyRiskSurface(path, info)