"""Terminal report renderer."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..utils import format_bytes, format_ms

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
CYAN = "\033[36m"


def _grade_color(grade: str) -> str:
    if not grade or grade == "n/a":
        return DIM
    first = grade[0].upper()
    if first == "A":
        return GREEN
    if first == "B":
        return CYAN
    if first in {"C", "D"}:
        return YELLOW
    return RED


def _band_color(band: str) -> str:
    return {
        "maintained": GREEN,
        "adequate": CYAN,
        "weak": YELLOW,
        "unmaintained": RED,
    }.get(str(band or "").lower(), DIM)


def _paint(text: str, color: str, enabled: bool) -> str:
    return f"{color}{text}{RESET}" if enabled else text


def _project_line(report: Mapping[str, Any]) -> str:
    project = report.get("project", {})
    git = project.get("git", {})
    scan = report.get("scan", {})
    metrics = report.get("metrics", {})
    name = str(project.get("name") or "repository")
    commit = git.get("commit")
    branch = git.get("branch")
    dirty = bool(git.get("dirty"))
    suffix = ""
    if commit:
        suffix = f" @ {commit}"
        if branch:
            suffix += f" ({branch}{', dirty' if dirty else ''})"
    files = scan.get("files_total", 0)
    lines = int(metrics.get("source_lines", 0) or 0) + int(metrics.get("test_lines", 0) or 0)
    return f"{name}{suffix} - {int(files):,} files / {lines:,} lines"


def _scan_line(report: Mapping[str, Any]) -> str:
    scan = report.get("scan", {})
    summary = report.get("summary", {})
    mode = str(scan.get("mode") or "cold")
    duration = int(scan.get("duration_ms", 0) or 0)
    index_size = format_bytes(int(scan.get("index_size_bytes", 0) or 0))
    confidence = str(summary.get("confidence") or "unknown")
    return f"Scan: {mode} {format_ms(duration)} | Index: {index_size} | Confidence: {confidence}"


def _coverage_line(report: Mapping[str, Any]) -> str:
    scan = report.get("scan", {})
    coverage = scan.get("coverage", {})
    parts = [
        f"Source: {coverage.get('source', 'n/a')}",
        f"Tests: {coverage.get('tests', 'n/a')}",
        f"Docs: {coverage.get('docs', 'n/a')}",
        f"Vendor: {coverage.get('vendor', 'excluded')}",
    ]
    if coverage.get("generated"):
        parts.append(f"Generated: {coverage.get('generated')}")
    if coverage.get("examples"):
        parts.append(f"Examples: {coverage.get('examples')}")
    return "  " + " | ".join(parts)


def _finding_block(index: int, finding: Mapping[str, Any], color: bool) -> list[str]:
    file_label = finding.get("file") or "repository"
    line = int(finding.get("line", 0) or 0)
    location = f"{file_label}:{line}" if line > 1 else str(file_label)
    severity = str(finding.get("severity") or "medium")
    severity_color = {"high": RED, "medium": YELLOW, "low": CYAN}.get(severity, DIM)
    title = str(finding.get("title") or "")
    lines = [f"{index}. {location} - {_paint(title, severity_color, color)}"]
    evidence = [str(item) for item in finding.get("evidence", []) or []]
    if evidence:
        lines.append(f"   Evidence: {'; '.join(evidence[:4])}")
    if finding.get("why"):
        lines.append(f"   Why: {finding['why']}")
    if finding.get("recommendation"):
        lines.append(f"   Next: {finding['recommendation']}")
    return lines


def render_text(report: Mapping[str, Any], *, color: bool = True, all_findings: bool = False) -> str:
    summary = report.get("summary", {})
    dimensions = report.get("dimensions", [])
    findings = report.get("findings", []) or []
    shown = findings if all_findings else findings[:5]
    lines: list[str] = []
    lines.append(_paint("SENTINEL QUALITY REPORT", BOLD, color))
    lines.append(_project_line(report))
    lines.append(_scan_line(report))
    lines.append("")
    band = str(summary.get("band") or "n/a")
    provisional = " (provisional)" if summary.get("provisional") else ""
    lines.append(f"Quality band: {_paint(band.upper(), _band_color(band), color)}{provisional}")
    headline = str(summary.get("headline") or "")
    if headline:
        lines.append(f"  {headline}")
    lines.append("")
    lines.append(_paint("WHAT TO FIX FIRST" if not all_findings else "FINDINGS", BOLD, color))
    if not shown:
        lines.append("  No findings. This is either a clean repository or a very small surface.")
    for index, finding in enumerate(shown, start=1):
        lines.extend(_finding_block(index, finding, color))
        lines.append("")
    if len(shown) < len(findings) and not all_findings:
        lines.append(f"  ... {len(findings) - len(shown)} more finding(s) in the JSON report or --all-findings.")
        lines.append("")
    lines.append(_paint("DIMENSIONS", BOLD, color))
    for dimension in dimensions:
        label = str(dimension.get("id", "")).replace("_", " ").title()
        grade_label = str(dimension.get("grade") or "n/a")
        if not dimension.get("assessed", True):
            summary_text = "n/a - not assessed"
        else:
            summary_text = str(dimension.get("summary") or "")
        lines.append(f"  {label:<17} {_paint(f'{grade_label:<4}', _grade_color(grade_label), color)} {summary_text}")
    lines.append("")
    lines.append(_paint("CHANGED SINCE LAST SCAN", BOLD, color))
    changes = report.get("changes", {})
    if changes.get("since_scan_id") is None:
        lines.append("  Initial scan - no previous snapshot to compare.")
    else:
        delta = changes.get("quality_delta", 0)
        delta_label = f"{delta:+d}" if isinstance(delta, int) else str(delta)
        lines.append(
            f"  +{changes.get('added', 0)} files, ~{changes.get('modified', 0)} modified, "
            f"-{changes.get('deleted', 0)} deleted | quality {delta_label}"
        )
    lines.append("")
    lines.append(_paint("ACTION PLAN", BOLD, color))
    actions = report.get("actions", []) or []
    for action in actions:
        lines.append(
            f"  {action.get('rank', 1)}. {action.get('title')} "
            f"({action.get('effort', 'medium')} effort, {action.get('impact', 'medium')} impact)"
        )
        if action.get("why"):
            lines.append(f"     {action['why']}")
    if not actions:
        lines.append("  No action items.")
    lines.append("")
    lines.append(_paint("COVERAGE", BOLD, color))
    lines.append(_coverage_line(report))
    metrics = report.get("metrics", {})
    lines.append(
        f"  Security: basic checks only | Parse errors: {metrics.get('parse_errors', 0)} | "
        f"Todos: {metrics.get('source_todos', 0)}"
    )
    limitations = report.get("limitations", []) or []
    if limitations:
        lines.append("")
        lines.append(_paint("LIMITATIONS", BOLD, color))
        for limitation in limitations[:5]:
            lines.append(f"  - {limitation}")
    return "\n".join(lines).rstrip() + "\n"
