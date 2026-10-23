"""Markdown report renderer."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..utils import format_bytes, format_ms


def _cell(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")


def render_markdown(report: Mapping[str, Any], *, all_findings: bool = False) -> str:
    project = report.get("project", {})
    scan = report.get("scan", {})
    summary = report.get("summary", {})
    metrics = report.get("metrics", {})
    git = project.get("git", {})
    name = str(project.get("name") or "repository")
    commit = git.get("commit")
    branch = git.get("branch")
    dirty = bool(git.get("dirty"))
    origin = ""
    if commit:
        origin = f" @ `{commit}`"
        if branch:
            origin += f" ({branch}{', dirty' if dirty else ''})"
    lines = [
        f"# Sentinel Quality Report - {name}",
        "",
        f"{origin} - {int(scan.get('files_total', 0)):,} files / "
        f"{int(metrics.get('source_lines', 0) or 0) + int(metrics.get('test_lines', 0) or 0):,} lines",
        "",
        f"Scan: {scan.get('mode', 'cold')} {format_ms(int(scan.get('duration_ms', 0) or 0))} | "
        f"Index {format_bytes(int(scan.get('index_size_bytes', 0) or 0))} | "
        f"Confidence {summary.get('confidence', 'unknown')}",
        "",
        f"## Quality band: {str(summary.get('band') or 'n/a').title()}"
        + (" (provisional)" if summary.get("provisional") else ""),
        "",
        str(summary.get("headline") or ""),
        "",
        "## What to fix first" if not all_findings else "## Findings",
        "",
    ]
    findings = report.get("findings", []) or []
    shown = findings if all_findings else findings[:5]
    if not shown:
        lines.append("No findings. This is either a clean repository or a very small surface.")
    for index, finding in enumerate(shown, start=1):
        location = finding.get("file") or "repository"
        line = int(finding.get("line", 0) or 0)
        if line > 1:
            location = f"{location}:{line}"
        lines.append(f"{index}. **{location}** - {finding.get('title', '')}")
        evidence = [str(item) for item in finding.get("evidence", []) or []]
        if evidence:
            lines.append(f"   - Evidence: {'; '.join(evidence[:5])}")
        if finding.get("why"):
            lines.append(f"   - Why: {finding['why']}")
        if finding.get("recommendation"):
            lines.append(f"   - Next: {finding['recommendation']}")
        lines.append("")
    lines.extend(["", "## Dimensions", "", "| Dimension | Grade | Summary |", "|---|---|---|"])
    for dimension in report.get("dimensions", []) or []:
        label = str(dimension.get("id", "")).replace("_", " ").title()
        lines.append(
            f"| {_cell(label)} | {_cell(dimension.get('grade', 'n/a'))} | {_cell(dimension.get('summary', ''))} |"
        )
    changes = report.get("changes", {})
    lines.extend(["", "## Changed since last scan", ""])
    if changes.get("since_scan_id") is None:
        lines.append("Initial scan - no previous snapshot to compare.")
    else:
        delta = changes.get("quality_delta", 0)
        delta_label = f"{delta:+d}" if isinstance(delta, int) else str(delta)
        lines.append(
            f"+{changes.get('added', 0)} files, ~{changes.get('modified', 0)} modified, "
            f"-{changes.get('deleted', 0)} deleted | quality {delta_label}"
        )
    lines.extend(["", "## Action plan", ""])
    actions = report.get("actions", []) or []
    if not actions:
        lines.append("No action items.")
    for action in actions:
        lines.append(
            f"{action.get('rank', 1)}. **{action.get('title')}** "
            f"({action.get('effort', 'medium')} effort, {action.get('impact', 'medium')} impact)"
        )
        if action.get("why"):
            lines.append(f"   - {action['why']}")
    coverage = scan.get("coverage", {})
    lines.extend(
        [
            "",
            "## Coverage and limitations",
            "",
            f"Source: {coverage.get('source', 'n/a')} | Tests: {coverage.get('tests', 'n/a')} | "
            f"Docs: {coverage.get('docs', 'n/a')} | Vendor: {coverage.get('vendor', 'excluded')} | "
            f"Generated: {coverage.get('generated', 'excluded')}",
            "",
            f"Security: basic pattern checks only, not an audit. Parse errors: {metrics.get('parse_errors', 0)}.",
        ]
    )
    limitations = report.get("limitations", []) or []
    if limitations:
        lines.append("")
        for limitation in limitations[:6]:
            lines.append(f"- {limitation}")
    return "\n".join(lines).rstrip() + "\n"
