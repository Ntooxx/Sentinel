from __future__ import annotations

from typing import Any, Dict, Iterable


def _dedupe_list(items: list) -> list:
    """Deduplicate a list while preserving order."""
    seen = set()
    result = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _test_signal_label(risk_summary: Dict[str, Any]) -> str:
    value = str(risk_summary.get("test", {}).get("level", "unknown"))
    reason = risk_summary.get("test", {}).get("reason", "")
    label_map = {
        "high": "strong",
        "good": "strong",
        "medium": "present — coverage unknown",
        "low": "limited",
        "missing": "missing",
        "strong": "strong",
        "present": "present — coverage unknown",
        "unknown": "unknown",
    }
    mapped = label_map.get(value, value)
    if value == "strong" and reason:
        return f"strong — {reason.lower()}"
    if value == "present" and reason:
        return f"present — {reason.lower()}"
    return mapped


def _security_label(risk_summary: Dict[str, Any]) -> str:
    level = str(risk_summary.get("security", {}).get("level", "not_assessed"))
    if level in ("not_assessed", "none"):
        return "not assessed (planned module)"
    return level.replace("_", " ")


def _todo_category_label(category: str) -> str:
    return {
        "first_party_source": "first-party source",
        "tooling": "tooling",
        "tests_fixtures": "tests/fixtures",
        "docs": "documentation",
        "vendor_generated": "vendor/generated",
    }.get(category, category.replace("_", " "))


def _health_breakdown_lines(health_data: Dict[str, Any]) -> list[str]:
    breakdown = health_data.get("breakdown", {}) if isinstance(health_data, dict) else {}
    if not breakdown:
        return []
    documentation = f"Documentation: {breakdown.get('documentation_percent', 'unknown')}%"
    if breakdown.get("documentation_reason"):
        documentation += f" - {breakdown['documentation_reason']}"
    maintainability_pct = breakdown.get('maintainability_percent', 'unknown')
    if "maintainability_risk" in breakdown:
        maintainability_line = f"Maintainability: {maintainability_pct}% (risk: {breakdown['maintainability_risk']})"
    else:
        maintainability_line = f"Maintainability: {maintainability_pct}%"
    return [
        maintainability_line,
        f"Runtime complexity: {breakdown.get('runtime_complexity', 'unknown')}",
        f"Test signal: {breakdown.get('test_signal', 'unknown')}",
        documentation,
        f"Security: {'not assessed (planned module)' if str(breakdown.get('security', 'unknown')) in ('not_assessed', 'none') else str(breakdown.get('security', 'unknown')).replace('_', ' ')}",
    ]


def _confirmed_issue_count(issues: Iterable[Dict[str, Any]]) -> int:
    return sum(1 for issue in issues if issue.get("severity") in {"critical", "high"} and issue.get("type") not in {"todo", "large_file", "large_file_size", "doc_code_drift"})


def _scan_mode_label(result: Dict[str, Any]) -> str:
    metrics = result.get("audit", {}).get("metrics", {})
    perf = result.get("performance", {})
    files = int(metrics.get("total_files", 0) or result.get("files_scanned", 0) or 0)
    lines = int(metrics.get("total_lines", 0) or 0)
    if files >= 5_000 or lines >= 500_000:
        return "deep repo intelligence"
    if perf.get("fast_mode"):
        return "fast structure and risk scan"
    return "full project scan"


def _build_killer_insight(result: Dict[str, Any]) -> str:
    audit = result.get("audit", {})
    understanding = audit.get("understanding", {})
    risk_summary = audit.get("risk_summary", {})
    metrics = audit.get("metrics", {})
    issues = audit.get("issues", [])

    test_level = str(risk_summary.get("test", {}).get("level", "unknown"))
    runtime_level = str(risk_summary.get("runtime", {}).get("level", "unknown"))
    todos = metrics.get("open_todos", 0)
    high_risks = [i for i in audit.get("risk_scores", []) if i.get("level") == "high"]
    files_scanned = metrics.get("total_files", 0)

    scenarios = []

    if test_level in ("strong", "high") and runtime_level in ("high", "medium"):
        scenarios.append(
            "Strong test infrastructure but high runtime complexity — tests are your safety net, "
            "but complexity in core paths increases regression risk"
        )

    if todos > 100 and high_risks:
        scenarios.append(
            f"{todos} TODO/FIXME markers and {len(high_risks)} high-risk files suggest "
            f"accumulated technical debt in critical areas"
        )

    doc_issues = sum(1 for i in issues if i.get("type") == "doc_code_drift")
    if files_scanned > 1000 and doc_issues > 10:
        scenarios.append(
            f"Large project with {doc_issues} documentation drift signals — "
            f"docs may lag behind the actual code in several places"
        )

    if test_level == "strong" and runtime_level in ("low", "unknown") and not high_risks:
        scenarios.append(
            "Well-structured project with good test coverage and low hotspot density — "
            "ideal for safe, incremental changes"
        )

    if test_level in ("missing", "none", "low") and files_scanned > 50:
        scenarios.append(
            "Limited test infrastructure for a project of this size — "
            "regression risk is higher than necessary"
        )

    if scenarios:
        return scenarios[0]

    return (
        f"Project has {files_scanned} files across "
        f"{len(understanding.get('main_components', []))} main components. "
        f"Focus on the highest-risk files before broad changes."
    )
