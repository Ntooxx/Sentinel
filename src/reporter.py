from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any, Dict, Iterable

from utils import ensure_parent_dir


class ReportGenerator:
    """Formats Sentinel scan results for terminals and saved reports."""

    def __init__(self, width: int = 60):
        self.width = width

    def render_terminal(self, result: Dict[str, Any], include_all_suggestions: bool = False) -> str:
        lines = []
        lines.append("")
        lines.append("=" * self.width)
        lines.append(f"SENTINEL REPORT - Scan #{result['scan_number']}")
        lines.append(f"  {result['timestamp']}")
        if "performance" in result:
            perf = result["performance"]
            mode = "fast" if perf.get("fast_mode") else "full"
            lines.append(f"  Duration: {perf.get('duration_seconds', 0):.3f}s | Mode: {mode}")
        lines.append("=" * self.width)
        lines.append("")

        health = result["audit"]["health_score"]
        lines.append(f"Health: {self._health_bar(health)} {health}%")
        lines.append("")

        understanding = result["audit"].get("understanding", {})
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

        metrics = result["audit"]["metrics"]
        lines.append("Metrics:")
        lines.append(
            f"  Files: {metrics['total_files']} | "
            f"Lines: {metrics['total_lines']} | "
            f"TODOs: {metrics['open_todos']}"
        )
        budget_alerts = result.get("performance", {}).get("budget_alerts", [])
        if budget_alerts:
            lines.append("  Budget alerts: " + "; ".join(alert["message"] for alert in budget_alerts[:3]))

        diff = result["diff"]
        lines.append("")
        lines.append("Changes:")
        if diff.get("is_first_scan"):
            lines.append(f"  {diff['summary']}")
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
        lines.append(f"Issues ({len(issues)}):")
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

    def render_compact(self, result: Dict[str, Any]) -> str:
        metrics = result["audit"]["metrics"]
        diff = result["diff"]
        top = result.get("suggestions", [{}])[0] if result.get("suggestions") else None
        perf = result.get("performance", {})

        lines = [
            f"scan={result['scan_number']} health={result['audit']['health_score']}% "
            f"files={metrics['total_files']} lines={metrics['total_lines']} "
            f"changes=+{diff.get('new_count', 0)}/~{diff.get('modified_count', 0)}/-{diff.get('deleted_count', 0)} "
            f"duration={perf.get('duration_seconds', 0):.3f}s mode={'fast' if perf.get('fast_mode') else 'full'}"
        ]
        if top:
            lines.append(f"next=[{top['priority']}] {top['title']}")
            if top.get("ranking_label"):
                lines.append(
                    f"rank={top['ranking_label']} impact={top.get('impact')} "
                    f"effort={top.get('effort')} confidence={top.get('confidence', {}).get('level')}"
                )
        budget_alerts = result.get("performance", {}).get("budget_alerts", [])
        if budget_alerts:
            lines.append(f"budget_alerts={len(budget_alerts)}")
        return "\n".join(lines)

    def render_brief(self, result: Dict[str, Any]) -> str:
        metrics = result["audit"]["metrics"]
        issues = result["audit"]["issues"]
        perf = result.get("performance", {})
        top = result.get("suggestions", [{}])[0] if result.get("suggestions") else None

        lines = [
            f"health={result['audit']['health_score']}% "
            f"files={metrics['total_files']} "
            f"issues={len(issues)} "
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

    def render_markdown(self, result: Dict[str, Any], knowledge_context: str = "") -> str:
        metrics = result["audit"]["metrics"]
        diff = result["diff"]
        issues = result["audit"]["issues"]
        suggestions = result.get("suggestions", [])

        lines = ["# Sentinel Report", ""]
        lines.append(f"- Scan: {result['scan_number']}")
        lines.append(f"- Timestamp: {result['timestamp']}")
        lines.append(f"- Health Score: {result['audit']['health_score']}%")
        lines.append("")

        understanding = result["audit"].get("understanding", {})
        if understanding:
            lines.append("## Project Understanding")
            lines.append(f"- Project: {understanding.get('project_name', 'unknown')}")
            lines.append(f"- Type: {understanding.get('project_type', 'unknown')}")
            if understanding.get("purpose"):
                lines.append(f"- Purpose: {understanding['purpose']}")
            if understanding.get("frameworks"):
                lines.append(f"- Frameworks: {', '.join(understanding['frameworks'])}")
            if understanding.get("workflow_hints"):
                lines.append(f"- Workflow Hints: {', '.join(understanding['workflow_hints'])}")
            lines.append("")

        lines.append("## Metrics")
        lines.append(f"- Files Scanned: {metrics['total_files']}")
        lines.append(f"- Total Lines: {metrics['total_lines']}")
        lines.append(f"- Total Size Bytes: {metrics['total_size_bytes']}")
        lines.append(f"- Open TODOs: {metrics['open_todos']}")
        lines.append("")

        lines.append("## Changes")
        lines.append(f"- Summary: {diff['summary']}")
        if diff.get("new_files"):
            lines.append(f"- New Files: {', '.join(diff['new_files'])}")
        if diff.get("modified_files"):
            lines.append(f"- Modified Files: {', '.join(diff['modified_files'])}")
        if diff.get("deleted_files"):
            lines.append(f"- Deleted Files: {', '.join(diff['deleted_files'])}")
        lines.append("")

        lines.append("## Issues")
        if issues:
            for issue in issues:
                location = f" ({issue['file']})" if issue.get("file") else ""
                lines.append(f"- [{issue['severity'].upper()}] {issue['message']}{location}")
        else:
            lines.append("- None")
        lines.append("")

        lines.append("## Suggestions")
        if suggestions:
            for index, suggestion in enumerate(suggestions, start=1):
                lines.append(f"### {index}. {suggestion['title']}")
                lines.append(f"- Priority: {suggestion['priority']}")
                lines.append(f"- Category: {suggestion['category']}")
                lines.append(f"- Reason: {suggestion['reason']}")
                lines.append(f"- Action: {suggestion['action']}")
                lines.append(f"- Impact: {suggestion.get('impact', 'medium')}")
                lines.append(f"- Effort: {suggestion.get('effort', 'medium')}")
                if suggestion.get("ranking_label"):
                    lines.append(f"- Ranking: {suggestion['ranking_label']}")
                confidence = suggestion.get("confidence", {})
                if confidence:
                    lines.append(f"- Confidence: {confidence.get('level', 'unknown')}")
                    for evidence in confidence.get("evidence", [])[:3]:
                        lines.append(f"  - Evidence: {evidence}")
                    lines.append(f"  - Uncertainty: {confidence.get('uncertainty', 'unknown')}")
                lines.append("")
                lines.append("```text")
                lines.append(suggestion["suggested_prompt"])
                lines.append("```")
                lines.append("")
        else:
            lines.append("- None")
            lines.append("")

        if knowledge_context:
            lines.append("## Knowledge Context")
            lines.append("")
            lines.append("```text")
            lines.append(knowledge_context.strip())
            lines.append("```")
            lines.append("")

        return "\n".join(lines).rstrip() + "\n"

    def render_overview(self, result: Dict[str, Any]) -> str:
        understanding = result["audit"].get("understanding", {})
        llm = result.get("llm", {})
        suggestions = result.get("suggestions", [])
        lines = [
            "SENTINEL OVERVIEW",
            f"Project: {understanding.get('project_name', 'unknown')}",
            f"Type: {understanding.get('project_type', 'unknown')}",
        ]
        if understanding.get("summary"):
            lines.append(f"Summary: {understanding['summary']}")
        lines.append(f"Health: {result['audit']['health_score']}%")
        lines.append("")

        if understanding.get("frameworks"):
            lines.append(f"Frameworks: {', '.join(understanding['frameworks'])}")
            lines.append("")

        components = understanding.get("main_components", [])
        if components:
            lines.append("Main Components:")
            for component in components[:5]:
                lines.append(
                    f"- {component['path']}: {component['role']} "
                    f"({component['file_count']} files / {component['line_count']} lines)"
                )
            lines.append("")

        hotspots = understanding.get("hotspots", [])
        if hotspots:
            lines.append("Hotspots:")
            for hotspot in hotspots[:4]:
                lines.append(f"- {hotspot['path']}: {hotspot['reason']}")
            lines.append("")

        important_files = understanding.get("important_files", [])
        if important_files:
            lines.append("Important Files:")
            for item in important_files[:6]:
                lines.append(f"- {item['path']}: {item.get('reason', 'important')}")
            lines.append("")

        if suggestions:
            lines.append("Suggested Next Steps:")
            for suggestion in suggestions[:3]:
                lines.append(
                    f"- [{suggestion['priority']}] {suggestion['title']}: {suggestion['action']} "
                    f"({suggestion.get('ranking_label', 'ranked')}, "
                    f"confidence={suggestion.get('confidence', {}).get('level', 'unknown')})"
                )
            lines.append("")

        risk_scores = result["audit"].get("risk_scores", [])
        if risk_scores:
            lines.append("Top Risks:")
            for item in risk_scores[:5]:
                lines.append(
                    f"- [{item.get('level')}] {item.get('file')} score={item.get('score')} "
                    f"({', '.join(item.get('factors', [])[:3])})"
                )
            lines.append("")

        if llm:
            lines.append("Token Strategy:")
            lines.append(f"- Full context estimate: {llm.get('estimated_full_context_tokens', 0)} tokens")
            lines.append(f"- Compact context estimate: {llm.get('estimated_compact_context_tokens', 0)} tokens")
            lines.append(f"- Estimated savings: {llm.get('estimated_token_savings_percent', 0)}%")
            lines.append(f"- Recommended budget: {llm.get('recommended_budget', 'small')}")
            lines.append("")

        return "\n".join(lines).rstrip() + "\n"

    def render_context_pack(self, context_pack: Dict[str, Any]) -> str:
        lines = [
            "SENTINEL CONTEXT PACK",
            f"Budget: {context_pack.get('budget')}",
            f"Compact Tokens: {context_pack.get('estimated_context_tokens', 0)}",
            f"Full Tokens: {context_pack.get('estimated_full_context_tokens', 0)}",
            f"Estimated Savings: {context_pack.get('estimated_token_savings_percent', 0)}%",
            "",
            context_pack.get("context", "").rstrip(),
        ]
        return "\n".join(lines).rstrip() + "\n"

    def render_prompt_pack(self, prompt_pack: Dict[str, Any]) -> str:
        return prompt_pack.get("prompt_text", "")

    def render_json(self, result: Dict[str, Any]) -> str:
        return json.dumps(result, indent=2, sort_keys=True)

    def render_status(self, status: Dict[str, Any]) -> str:
        summary = status.get("summary", {})
        lines = [
            "SENTINEL STATUS",
            f"Project: {status.get('project_dir')}",
            f"Project Name: {summary.get('project_name')}",
            f"Project Type: {summary.get('project_type')}",
            f"Last Scan: {summary.get('last_scan')}",
            f"Last Checkpoint: {summary.get('last_checkpoint')}",
            f"Files: {summary.get('total_files', 0)}",
            f"Lines: {summary.get('total_lines', 0)}",
            f"Open Issues: {summary.get('open_issues', 0)}",
        ]
        if summary.get("top_suggestion"):
            lines.append(f"Top Suggestion: {summary['top_suggestion']}")
        if summary.get("estimated_token_savings_percent") is not None:
            lines.append(f"Estimated Token Savings: {summary['estimated_token_savings_percent']}%")
        return "\n".join(lines)

    def save_markdown(self, report_text: str, destination: str | Path) -> Path:
        target = ensure_parent_dir(destination)
        target.write_text(report_text, encoding="utf-8")
        return target

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
