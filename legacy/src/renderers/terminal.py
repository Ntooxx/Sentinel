from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any, Dict, Iterable

from utils import ensure_parent_dir
from renderers._utils import (
    _build_killer_insight,
    _confirmed_issue_count,
    _health_breakdown_lines,
    _scan_mode_label,
    _security_label,
    _test_signal_label,
    _todo_category_label,
)


class TerminalRenderer:
    """Renders Sentinel reports for terminal output with ASCII formatting."""

    def __init__(self, width: int = 60):
        self.width = width

    def render(self, result: Dict[str, Any], include_all_suggestions: bool = False) -> str:
        lines = []
        lines.append("")
        lines.append("=" * self.width)
        lines.append(f"SENTINEL REPORT - Scan #{result['scan_number']}")
        lines.append(f"  {result['timestamp']}")
        if "performance" in result:
            perf = result["performance"]
            lines.append(f"  Duration: {perf.get('duration_seconds', 0):.3f}s | Mode: {_scan_mode_label(result)}")
        lines.append("=" * self.width)
        lines.append("")

        health = result["audit"]["health_score"]
        health_data = result["audit"].get("health_score_data", {})
        health_text = f"Health: {self._health_bar(health)} {health}%"
        if health_data.get("security_assessed") is False:
            health_text += " (excluding security review)"
        confidence = health_data.get("confidence_label", "")
        if confidence in ("low_confidence", "moderate_confidence"):
            label = "low" if confidence == "low_confidence" else "moderate"
            reason = health_data.get("confidence_reason", "")
            if reason:
                health_text += f" [confidence: {label} ({reason})]"
            else:
                health_text += f" [confidence: {label}]"
        lines.append(health_text)
        risk_summary = result["audit"].get("risk_summary", {})
        if risk_summary:
            lines.append(
                "Risk: "
                f"Maintainability risk: {risk_summary.get('maintainability', {}).get('level', 'unknown')} | "
                f"Runtime complexity: {risk_summary.get('runtime', {}).get('level', 'unknown')} | "
                f"Test signal: {_test_signal_label(risk_summary)} | "
                f"Security review: {_security_label(risk_summary)}"
            )
        if health_data.get("breakdown"):
            lines.append("Why this score:")
            for line in _health_breakdown_lines(health_data):
                lines.append(f"  {line}")
        lines.append("")

        understanding = result["audit"].get("understanding", {})
        coverage = result["audit"].get("scan_coverage", {}) or understanding.get("scan_coverage", {})
        if understanding:
            lines.append("Project:")
            if understanding.get("project_name"):
                lines.append(f"  Name: {understanding['project_name']}")
            if understanding.get("project_type"):
                lines.append(f"  Type: {understanding['project_type']}")
            if understanding.get("purpose"):
                lines.extend(
                    f"  {line}" for line in textwrap.wrap(
                        f"Purpose: {understanding['purpose']}",
                        width=self.width - 2,
                    )
                )
            if understanding.get("frameworks"):
                lines.append(f"  Frameworks: {', '.join(understanding['frameworks'][:6])}")
            lines.append("")
        if coverage.get("warning"):
            lines.append(f"Coverage Warning: {coverage['warning']}")
            lines.append("")

        metrics = result["audit"]["metrics"]
        lines.append("Metrics:")
        lines.append(
            f"  Files: {metrics['total_files']} | "
            f"Lines: {metrics['total_lines']} | "
            f"TODOs: {metrics['open_todos']}"
        )
        todo_categories = metrics.get("todo_categories", {})
        if todo_categories:
            lines.append("  TODO Categories:")
            for category, count in todo_categories.items():
                lines.append(f"    {_todo_category_label(category)}: {count}")
        budget_alerts = result.get("performance", {}).get("budget_alerts", [])
        if budget_alerts:
            lines.append("  Budget alerts: " + "; ".join(alert["message"] for alert in budget_alerts[:3]))

        diff = result["diff"]
        lines.append("")
        lines.append("Changes:")
        if diff.get("is_first_scan"):
            lines.append("  Baseline scan: all files are treated as new because no previous checkpoint exists.")
        else:
            lines.append(
                f"  +{diff.get('new_count', 0)} new | "
                f"~{diff.get('modified_count', 0)} modified | "
                f"-{diff.get('deleted_count', 0)} deleted"
            )
            lines.extend(self._render_file_list("  New", diff.get("new_files", [])))
            lines.extend(self._render_file_list("  Modified", diff.get("modified_files", [])))
            lines.extend(self._render_file_list("  Deleted", diff.get("deleted_files", [])))

        issues = result["audit"]["issues"]
        lines.append("")
        lines.append(f"Confirmed issues: {_confirmed_issue_count(issues)}")
        lines.append(f"Review signals ({len(issues)}):")
        if issues:
            for issue in issues[:5]:
                location = f" [{issue['file']}]" if issue.get("file") else ""
                lines.append(
                    f"  [{issue['severity'].upper()}] {issue['message']}{location}"
                )
        else:
            lines.append("  None")

        components = understanding.get("main_components", [])
        if components:
            lines.append("")
            lines.append("Main Components:")
            for component in components[:4]:
                lines.append(
                    f"  - {component['path']} | {component['role']} | "
                    f"{component['file_count']} files / {component['line_count']} lines"
                )

        suggestions = result.get("suggestions", [])
        if suggestions:
            lines.append("")
            lines.append("Next Steps:")
            visible = suggestions if include_all_suggestions else suggestions[:3]
            for index, suggestion in enumerate(visible, start=1):
                lines.append(
                    f"  {index}. [{suggestion['priority'].upper()}] {suggestion['title']}"
                )
                rank = suggestion.get("ranking_label")
                confidence = suggestion.get("confidence", {})
                if rank or confidence:
                    lines.append(
                        "     "
                        f"{rank or 'ranked'} | impact={suggestion.get('impact', 'medium')} "
                        f"effort={suggestion.get('effort', 'medium')} "
                        f"confidence={confidence.get('level', 'unknown')}"
                    )
                lines.extend(
                    f"     {line}" for line in textwrap.wrap(suggestion["reason"], width=self.width - 8)
                )
            if not include_all_suggestions and len(suggestions) > len(visible):
                remaining = len(suggestions) - len(visible)
                lines.append(f"  ... and {remaining} more suggestion(s)")

            prompt = suggestions[0]["suggested_prompt"]
            lines.append("")
            lines.append("Suggested Prompt:")
            for wrapped in textwrap.wrap(prompt, width=self.width - 6):
                lines.append(f"  {wrapped}")

        llm = result.get("llm", {})
        if llm:
            lines.append("")
            lines.append("LLM Readiness:")
            lines.append(
                "  "
                f"Full: {llm.get('estimated_full_context_tokens', 0)} tok | "
                f"Compact: {llm.get('estimated_compact_context_tokens', 0)} tok | "
                f"Saved: {llm.get('estimated_token_savings_percent', 0)}%"
            )
            focus_files = llm.get("focus_files", [])
            if focus_files:
                lines.append("  Focus Files:")
                lines.extend(f"    - {path}" for path in focus_files[:5])

        lines.append("")
        lines.append("=" * self.width)
        return "\n".join(lines)

    def render_brief(self, result: Dict[str, Any]) -> str:
        metrics = result["audit"]["metrics"]
        issues = result["audit"]["issues"]
        perf = result.get("performance", {})
        top = result.get("suggestions", [{}])[0] if result.get("suggestions") else None

        lines = [
            f"health={result['audit']['health_score']}% "
            f"files={metrics['total_files']} "
            f"review_signals={len(issues)} "
            f"duration={perf.get('duration_seconds', 0):.3f}s "
            f"mode={'fast' if perf.get('fast_mode') else 'full'}"
        ]
        if top:
            lines.append(f"next: [{top['priority']}] {top['title']}")
            lines.append(f"why: {top['reason']}")
            lines.append(f"do: {top['action']}")
            lines.append(
                f"rank: {top.get('ranking_label', 'ranked')} | "
                f"impact={top.get('impact', 'medium')} effort={top.get('effort', 'medium')} "
                f"confidence={top.get('confidence', {}).get('level', 'unknown')}"
            )
        understanding = result["audit"].get("understanding", {})
        if understanding.get("project_type"):
            lines.append(f"type: {understanding['project_type']}")
        return "\n".join(lines)

    def _health_bar(self, health: int) -> str:
        total = 30
        filled = int(total * max(0, min(health, 100)) / 100)
        return "[" + ("#" * filled) + ("-" * (total - filled)) + "]"

    def _render_file_list(self, label: str, items: Iterable[str]) -> list[str]:
        values = list(items)[:5]
        if not values:
            return []
        lines = [f"{label}:"]
        lines.extend(f"    - {item}" for item in values)
        return lines
