from __future__ import annotations

from typing import Any, Dict

from renderers._utils import (
    _confirmed_issue_count,
    _health_breakdown_lines,
    _scan_mode_label,
    _security_label,
    _test_signal_label,
    _todo_category_label,
)


class MarkdownRenderer:
    """Renders Sentinel reports as Markdown documents for saved reports."""

    def __init__(self, width: int = 60):
        self.width = width

    def render(self, result: Dict[str, Any], knowledge_context: str = "") -> str:
        metrics = result["audit"]["metrics"]
        diff = result["diff"]
        issues = result["audit"]["issues"]
        suggestions = result.get("suggestions", [])
        coverage = result["audit"].get("scan_coverage", {})

        lines = ["# Sentinel Report", ""]
        health = result["audit"]["health_score"]
        health_data = result["audit"].get("health_score_data", {})
        health_text = f"{health}%"
        if health_data.get("security_assessed") is False:
            health_text += " (excluding security review)"
        lines.append(f"- Scan: {result['scan_number']}")
        lines.append(f"- Timestamp: {result['timestamp']}")
        lines.append(f"- Scan Mode: {_scan_mode_label(result)}")
        lines.append(f"- Duration: {result.get('performance', {}).get('duration_seconds', 0)}s")
        confidence_label = health_data.get("confidence_label", "")
        confidence_reason = health_data.get("confidence_reason", "")
        if confidence_label == "low_confidence":
            reason_text = f" ({confidence_reason})" if confidence_reason else ""
            lines.append(f"- Health Score: {health_text} (confidence: low{reason_text})")
        elif confidence_label == "moderate_confidence":
            reason_text = f" ({confidence_reason})" if confidence_reason else ""
            lines.append(f"- Health Score: {health_text} (confidence: moderate{reason_text})")
        else:
            lines.append(f"- Health Score: {health_text}")
        risk_summary = result["audit"].get("risk_summary", {})
        if risk_summary:
            lines.append(
                "- Risk Summary: "
                f"Maintainability risk: {risk_summary.get('maintainability', {}).get('level', 'unknown')}; "
                f"Runtime complexity: {risk_summary.get('runtime', {}).get('level', 'unknown')}; "
                f"Test signal: {_test_signal_label(risk_summary)}; "
                f"Security review: {_security_label(risk_summary)}"
            )
        if health_data.get("breakdown"):
            lines.append("- Why this score: " + health_data.get("explanation", ""))
            for line in _health_breakdown_lines(health_data):
                lines.append(f"  - {line}")
        lines.append("")

        understanding = result["audit"].get("understanding", {})
        if understanding:
            lines.append("## Project Understanding")
            lines.append(f"- Project: {understanding.get('project_name', 'unknown')}")
            lines.append(f"- Type: {understanding.get('project_type', 'unknown')}")
            archetype = understanding.get("archetype", "")
            if archetype:
                lines.append(f"- Archetype: {archetype}")
            if understanding.get("purpose"):
                lines.append(f"- Purpose: {understanding['purpose']}")
            if understanding.get("frameworks"):
                lines.append(f"- Frameworks: {', '.join(understanding['frameworks'])}")
            if understanding.get("workflow_hints"):
                lines.append(f"- Workflow Hints: {', '.join(understanding['workflow_hints'])}")

            confidence_reasons = understanding.get("confidence_reasons", {})
            if confidence_reasons:
                lines.append("")
                lines.append("### Confidence Reasons")
                for aspect, data in confidence_reasons.items():
                    if data.get("level") and data.get("reason"):
                        lines.append(f"- {aspect.capitalize()} confidence: {data['level']}")
                        lines.append(f"  - Reason: {data['reason']}")
            lines.append("")
        if coverage.get("warning"):
            lines.append("## Scan Coverage")
            lines.append(f"- Warning: {coverage['warning']}")
            if coverage.get("underrepresented_directories"):
                lines.append(
                    "- Underrepresented directories: "
                    + ", ".join(coverage["underrepresented_directories"][:6])
                )
            lines.append("")

        lines.append("## Metrics")
        lines.append(f"- Files Scanned: {metrics['total_files']}")
        lines.append(f"- Total Lines: {metrics['total_lines']}")
        lines.append(f"- Total Size Bytes: {metrics['total_size_bytes']}")
        lines.append(f"- Open TODOs: {metrics['open_todos']}")
        todo_categories = metrics.get("todo_categories", {})
        if todo_categories:
            lines.append("- TODO Categories:")
            for category, count in todo_categories.items():
                lines.append(f"  - {_todo_category_label(category)}: {count}")
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

        lines.append("## Review Signals")
        lines.append(f"- Confirmed issues: {_confirmed_issue_count(issues)}")
        lines.append(f"- Review signals: {len(issues)}")
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
        coverage = result["audit"].get("scan_coverage", {}) or understanding.get("scan_coverage", {})
        lines = [
            "SENTINEL OVERVIEW",
            f"Project: {understanding.get('project_name', 'unknown')}",
            f"Type: {understanding.get('project_type', 'unknown')}",
            f"Archetype: {understanding.get('archetype', 'unknown')}",
        ]
        if understanding.get("summary"):
            lines.append(f"Summary: {understanding['summary']}")
        lines.append(f"Health: {result['audit']['health_score']}%")
        risk_summary = result["audit"].get("risk_summary", {})
        if risk_summary:
            lines.append(
                "Risk Summary: "
                f"Maintainability {risk_summary.get('maintainability', {}).get('level', 'unknown')}; "
                f"Runtime {risk_summary.get('runtime', {}).get('level', 'unknown')}; "
                f"Tests {_test_signal_label(risk_summary)}; "
                f"Security {_security_label(risk_summary)}"
            )
        lines.append("")

        if understanding.get("frameworks"):
            lines.append(f"Frameworks: {', '.join(understanding['frameworks'])}")
            lines.append("")

        if coverage.get("warning"):
            lines.append(f"Scan Coverage: {coverage['warning']}")
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
            lines.append("Primary Hotspots:")
            for hotspot in hotspots[:4]:
                lines.append(f"- {hotspot['path']}: {hotspot['reason']}")
            lines.append("")

        hotspot_groups = understanding.get("hotspot_groups", {})
        for name, label in [
            ("runtime", "Primary Runtime Hotspots"),
            ("runtime_surface", "Runtime/API Surface Hotspots"),
            ("build_tooling", "Build/Tooling Hotspots"),
            ("generator", "Generator Hotspots"),
            ("test_runner", "Test Runner Hotspots"),
            ("vendor", "Vendor/Third-Party Hotspots — track only, do not refactor by default"),
            ("generated_sdk", "Generated SDK/client code — regenerate from schema instead of editing manually"),
            ("dependency_lock", "Dependency/lockfile signals"),
            ("test_data", "Test/Data Hotspots"),
            ("documentation", "Documentation Hotspots"),
            ("specification", "Specification Hotspots"),
            ("example", "Example Hotspots"),
        ]:
            group_items = hotspot_groups.get(name, [])
            if not group_items:
                continue
            lines.append(f"{label}:")
            for hotspot in group_items[:3]:
                lines.append(f"- {hotspot['path']}: {hotspot['reason']}")
            lines.append("")

        entry_points_by_category = understanding.get("entry_points_by_category", {})
        if entry_points_by_category:
            lines.append("Entry Points:")
            for name, label in [
                ("runtime", "Primary runtime entry points"),
                ("runtime_surface", "Runtime/API surfaces"),
                ("example", "Example entry points"),
                ("build", "Build/tooling entry points"),
                ("generator", "Generator entry points"),
                ("test", "Test runners"),
                ("environment", "Environment setup"),
                ("documentation", "Documentation"),
            ]:
                values = entry_points_by_category.get(name, [])
                if values:
                    lines.append(f"- {label}: {', '.join(values[:4])}")
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

        from renderers._utils import _dedupe_list

        risk_scores = result["audit"].get("risk_scores", [])
        if risk_scores:
            lines.append("Top Risks:")
            for item in risk_scores[:5]:
                cov = item.get("coverage", {})
                coverage_text = cov.get("status", "unknown")
                if cov.get("test_file"):
                    coverage_text += f" via {cov['test_file']}"
                deduped = _dedupe_list(item.get("factors", []))
                lines.append(
                    f"- [{item.get('level')}] {item.get('file')} score={item.get('score')} "
                    f"({', '.join(deduped[:3])}; coverage={coverage_text})"
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
