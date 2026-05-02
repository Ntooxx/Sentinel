from __future__ import annotations

import html
import json
import textwrap
from pathlib import Path
from typing import Any, Dict, Iterable

from utils import ensure_parent_dir


def _dedupe_list(items: list) -> list:
    """Deduplicate a list while preserving order."""
    seen = set()
    result = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


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
            lines.append(f"  Duration: {perf.get('duration_seconds', 0):.3f}s | Mode: {self._scan_mode_label(result)}")
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
                f"Test signal: {self._test_signal_label(risk_summary)} | "
                f"Security review: {self._security_label(risk_summary)}"
            )
        if health_data.get("breakdown"):
            lines.append("Why this score:")
            for line in self._health_breakdown_lines(health_data):
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
        # Show categorized TODOs if available
        todo_categories = metrics.get("todo_categories", {})
        if todo_categories:
            lines.append("  TODO Categories:")
            for category, count in todo_categories.items():
                lines.append(f"    {self._todo_category_label(category)}: {count}")
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
        lines.append(f"Confirmed issues: {self._confirmed_issue_count(issues)}")
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

    def render_markdown(self, result: Dict[str, Any], knowledge_context: str = "") -> str:
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
        lines.append(f"- Scan Mode: {self._scan_mode_label(result)}")
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
                f"Test signal: {self._test_signal_label(risk_summary)}; "
                f"Security review: {self._security_label(risk_summary)}"
            )
        if health_data.get("breakdown"):
            lines.append("- Why this score: " + health_data.get("explanation", ""))
            for line in self._health_breakdown_lines(health_data):
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

            # Show confidence reasons if available
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
        # Show categorized TODOs if available
        todo_categories = metrics.get("todo_categories", {})
        if todo_categories:
            lines.append("- TODO Categories:")
            for category, count in todo_categories.items():
                lines.append(f"  - {self._todo_category_label(category)}: {count}")
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
        lines.append(f"- Confirmed issues: {self._confirmed_issue_count(issues)}")
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

    def render_html(self, result: Dict[str, Any], knowledge_context: str = "") -> str:
        metrics = result["audit"]["metrics"]
        diff = result["diff"]
        issues = result["audit"]["issues"]
        suggestions = result.get("suggestions", [])
        understanding = result["audit"].get("understanding", {})
        risk_scores = result["audit"].get("risk_scores", [])
        risk_groups = result["audit"].get("risk_groups", {})
        llm = result.get("llm", {})
        perf = result.get("performance", {})
        risk_summary = result["audit"].get("risk_summary", {})
        health_data = result["audit"].get("health_score_data", {})
        components = understanding.get("main_components", [])
        hotspots = understanding.get("hotspots", [])
        hotspot_groups = understanding.get("hotspot_groups", {})
        entry_points_by_category = understanding.get("entry_points_by_category", {})
        coverage = result["audit"].get("scan_coverage", {}) or understanding.get("scan_coverage", {})
        focus_files = llm.get("focus_files", [])
        project_name = understanding.get("project_name") or "Sentinel Project"
        health = int(result["audit"].get("health_score", 0) or 0)
        health_class = "good" if health >= 80 else "warn" if health >= 55 else "bad"

        def esc(value: Any) -> str:
            return html.escape(str(value), quote=True)

        def pills(values: Iterable[Any]) -> str:
            items = [f"<span class=\"pill\">{esc(value)}</span>" for value in values if str(value)]
            return "".join(items) or "<span class=\"muted\">None detected</span>"

        def maybe_pills(values: Iterable[Any], fallback: str = "") -> str:
            items = [f"<span class=\"pill\">{esc(value)}</span>" for value in values if str(value)]
            if items:
                return "".join(items)
            return f"<span class=\"muted\">{esc(fallback)}</span>" if fallback else ""

        def grouped_pills(groups: Dict[str, Iterable[Any]], *, label_map: Dict[str, str]) -> str:
            chunks = []
            for key, label in label_map.items():
                values = list(groups.get(key, []))
                if not values:
                    continue
                display_values = [item.get("path", item) if isinstance(item, dict) else item for item in values]
                chunks.append(
                    f"<div style=\"margin-bottom:12px\"><h3>{esc(label)}</h3><div class=\"file-list\">{pills(display_values[:6])}</div></div>"
                )
            return "".join(chunks) or "<p class=\"muted\">None detected.</p>"

        def issue_rows() -> str:
            if not issues:
                return "<tr><td colspan=\"3\" class=\"muted\">No review signals detected.</td></tr>"
            rows = []
            for issue in issues[:40]:
                rows.append(
                    "<tr>"
                    f"<td><span class=\"badge\">{esc(issue.get('severity', 'unknown')).upper()}</span></td>"
                    f"<td>{esc(issue.get('message', ''))}</td>"
                    f"<td><code>{esc(issue.get('file', ''))}</code></td>"
                    "</tr>"
                )
            return "".join(rows)

        def suggestion_cards() -> str:
            if not suggestions:
                return "<p class=\"muted\">No suggestions yet.</p>"
            cards = []
            for index, suggestion in enumerate(suggestions[:8], start=1):
                confidence = suggestion.get("confidence", {})
                focus = suggestion.get("focus_files", [])
                fallback = ""
                if not focus and suggestion.get("category") == "cleanup":
                    fallback = "Affected files: grouped list available in TODO detail view"
                focus_html = (
                    f"<div class=\"file-list\">{maybe_pills(focus[:5], fallback)}</div>"
                    if focus or fallback
                    else ""
                )
                cards.append(
                    "<article class=\"item\">"
                    f"<div class=\"item-kicker\">#{index} {esc(suggestion.get('priority', 'medium')).upper()}</div>"
                    f"<h3>{esc(suggestion.get('title', 'Untitled suggestion'))}</h3>"
                    f"<p>{esc(suggestion.get('reason', ''))}</p>"
                    f"<p><strong>Action:</strong> {esc(suggestion.get('action', ''))}</p>"
                    "<div class=\"meta\">"
                    f"<span>impact {esc(suggestion.get('impact', 'medium'))}</span>"
                    f"<span>effort {esc(suggestion.get('effort', 'medium'))}</span>"
                    f"<span>confidence {esc(confidence.get('level', 'unknown'))}</span>"
                    "</div>"
                    f"{focus_html}"
                    "</article>"
                )
            return "".join(cards)

        def component_rows() -> str:
            if not components:
                return "<tr><td colspan=\"4\" class=\"muted\">No component map available.</td></tr>"
            rows = []
            for component in components[:12]:
                rows.append(
                    "<tr>"
                    f"<td><code>{esc(component.get('path', ''))}</code></td>"
                    f"<td>{esc(component.get('role', ''))}</td>"
                    f"<td>{esc(component.get('file_count', 0))}</td>"
                    f"<td>{esc(component.get('line_count', 0))}</td>"
                    "</tr>"
                )
            return "".join(rows)

        def risk_rows() -> str:
            if not risk_scores:
                return "<tr><td colspan=\"4\" class=\"muted\">No file risk scores available.</td></tr>"
            rows = []
            for item in risk_scores[:15]:
                deduped = _dedupe_list(item.get("factors", []))
                rows.append(
                    "<tr>"
                    f"<td><span class=\"badge\">{esc(item.get('level', 'unknown')).upper()}</span></td>"
                    f"<td><code>{esc(item.get('file', ''))}</code></td>"
                    f"<td>{esc(item.get('score', 0))}</td>"
                    f"<td>{esc(', '.join(deduped[:4]))}</td>"
                    "</tr>"
                )
            return "".join(rows)

        def grouped_risk_sections() -> str:
            label_map = {
                "runtime": "Top runtime risks",
                "build_tooling": "Top build/tooling risks",
                "generator": "Top generator risks",
                "test_runner": "Top test runner risks",
                "test_data": "Top test/data risks",
                "documentation": "Top documentation risks",
                "specification": "Top specification risks",
                "vendor": "Vendor/third-party hotspots — track only, do not refactor by default",
                "generated_sdk": "Generated SDK/client code — regenerate from schema instead of editing manually",
                "dependency_lock": "Dependency/lockfile signals",
                "environment_setup": "Environment/setup signals",
                "config": "Config/data signals",
                "other": "Other risks",
            }
            sections = []
            for key, label in label_map.items():
                items = list(risk_groups.get(key, []))
                if not items:
                    continue
                rows = []
                for item in items[:8]:
                    deduped = _dedupe_list(item.get("factors", []))
                    rows.append(
                        "<tr>"
                        f"<td><span class=\"badge\">{esc(item.get('level', 'unknown')).upper()}</span></td>"
                        f"<td><code>{esc(item.get('file', ''))}</code></td>"
                        f"<td>{esc(item.get('score', 0))}</td>"
                        f"<td>{esc(', '.join(deduped[:4]))}</td>"
                        "</tr>"
                    )
                sections.append(
                    f"<div style=\"margin-bottom:18px\"><h3>{esc(label)}</h3>"
                    "<table><thead><tr><th>Level</th><th>File</th><th>Score</th><th>Factors</th></tr></thead>"
                    f"<tbody>{''.join(rows)}</tbody></table></div>"
                )
            return "".join(sections) or "<p class=\"muted\">No grouped risk scores available.</p>"

        health_breakdown = "<br>".join(esc(line) for line in self._health_breakdown_lines(health_data))
        scan_mode = self._scan_mode_label(result)
        confirmed_issues = self._confirmed_issue_count(issues)

        markdown_prompt = suggestions[0].get("suggested_prompt", "") if suggestions else ""
        if diff.get("is_first_scan"):
            timeline_hint = "Baseline scan: all files are treated as new because no previous checkpoint exists."
        else:
            timeline_hint = (
                f"{diff.get('new_count', 0)} new, "
                f"{diff.get('modified_count', 0)} modified, "
                f"{diff.get('deleted_count', 0)} deleted"
            )

        return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sentinel Report – {esc(project_name)}</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 64 64%22><rect x=%2212%22 y=%2216%22 width=%2240%22 height=%2232%22 rx=%226%22 fill=%22%230f6b9e%22/><polygon points=%2232,20 24,30 28,30 24,44 32,36 40,44 36,30 40,30%22 fill=%22%23fff%22/></svg>">
<style>
:root{{color-scheme:light;--bg:#f0f4f8;--surface:#ffffff;--ink:#0b1b2b;--muted:#5d6f83;--line:#d8e2ec;--accent:#0f6b9e;--accent-glow:#0f6b9e0f;--good:#1a7f4c;--warn:#a86800;--bad:#c62828;--shadow:0 1px 3px #0000000d,0 1px 2px #0000000a;--shadow-lg:0 4px 16px #00000012;--radius:10px;--radius-sm:6px}}
*{{box-sizing:border-box}}
body{{margin:0;font-family:Inter,system-ui,-apple-system,Segoe UI,Roboto,Noto Sans,sans-serif;background:var(--bg);color:var(--ink);line-height:1.55;-webkit-font-smoothing:antialiased}}
main{{max-width:1160px;margin:0 auto;padding:32px 28px}}
h1{{font-size:30px;line-height:1.2;margin:0 0 6px;font-weight:700;letter-spacing:-.02em}}
h2{{font-size:18px;margin:0 0 12px;font-weight:600;letter-spacing:-.01em;color:var(--ink)}}
h3{{font-size:14px;margin:0 0 6px;font-weight:600;color:var(--ink)}}
p{{margin:0 0 8px}}
a{{color:var(--accent);text-decoration:none}}
.muted{{color:var(--muted);font-size:13px}}
.good{{color:var(--good)}}.warn{{color:var(--warn)}}.bad{{color:var(--bad)}}
.cap{{font-size:11px;text-transform:uppercase;letter-spacing:.06em;font-weight:700;color:var(--muted)}}

/* header hero */
.hero{{display:grid;grid-template-columns:1fr 280px;gap:24px;align-items:start;padding:32px 0 28px;border-bottom:1px solid var(--line);margin-bottom:24px}}
.hero-info h1{{font-size:32px}}.hero-tagline{{font-size:15px;color:var(--ink);opacity:.75;max-width:680px;line-height:1.5;margin:8px 0 12px}}
.health-card{{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:20px;box-shadow:var(--shadow)}}
.health-score{{display:flex;align-items:center;gap:16px;margin-bottom:10px}}
.health-ring{{position:relative;width:64px;height:64px;flex-shrink:0}}
.health-ring svg{{transform:rotate(-90deg);width:64px;height:64px}}
.health-ring .bg{{fill:none;stroke:#e8edf2;stroke-width:5}}
.health-ring .fg{{fill:none;stroke:currentColor;stroke-width:5;stroke-linecap:round;stroke-dasharray:{{max(0.5, health * 1.88)}} 188.5}}
.health-ring .label{{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-size:16px;font-weight:800}}
.health-meta{{font-size:12px;color:var(--muted);line-height:1.6}}

/* stats bar */
.stats{{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:22px}}
.stat{{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:14px 16px;box-shadow:var(--shadow)}}
.stat .label{{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:700}}
.stat .value{{font-size:22px;font-weight:800;margin-top:2px;letter-spacing:-.02em}}

/* cards */
.card{{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:18px;box-shadow:var(--shadow)}}
.card-accent{{border-left:3px solid var(--accent)}}
.grid-2{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:18px}}
.grid-3{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px;margin-bottom:18px}}

/* suggestion cards */
.suggestions{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:6px}}
.item{{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:18px;box-shadow:var(--shadow);display:flex;flex-direction:column}}
.item-kicker{{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:700;margin-bottom:2px}}
.item h3{{font-size:15px;margin:2px 0 8px;font-weight:600}}
.item p{{font-size:14px;color:var(--ink);opacity:.8}}
.meta{{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0 8px}}
.meta span{{font-size:11px;padding:2px 8px;border:1px solid var(--line);border-radius:999px;background:var(--bg);color:var(--muted);font-weight:600}}

/* pills & badges */
.pill,.badge{{display:inline-block;font-size:12px;padding:3px 9px;border:1px solid var(--line);border-radius:999px;background:var(--bg);color:var(--ink);font-weight:500;white-space:nowrap}}
.badge{{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;border-radius:var(--radius-sm);padding:2px 8px;white-space:nowrap}}
.badge.high{{background:#fef2f2;border-color:#fecaca;color:#b91c1c}}
.badge.medium{{background:#fffbeb;border-color:#fde68a;color:#92400e}}
.badge.low{{background:#f0f9ff;border-color:#bae6fd;color:#0369a1}}
.pill.high{{}}.pill.medium{{}}.pill.low{{}}
.file-list{{display:flex;flex-wrap:wrap;gap:5px;margin-top:8px}}

/* table */
table{{width:100%;border-collapse:collapse;font-size:13.5px}}
th,td{{text-align:left;padding:9px 10px;vertical-align:top;border-bottom:1px solid var(--line)}}
th{{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:700;background:var(--bg)}}
tr:last-child td{{border-bottom:none}}

/* code */
code{{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:12.5px;background:var(--bg);padding:1px 5px;border-radius:4px;word-break:break-all}}
pre{{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:13px;line-height:1.5;white-space:pre-wrap;overflow:auto;background:#0e1a2b;color:#e2ecf9;border-radius:var(--radius);padding:18px;max-height:440px;box-shadow:var(--shadow-lg);margin:4px 0 0}}
pre::before{{content:"$";color:#5a7a9a;margin-right:10px;user-select:none}}

/* insight block */
.insight{{font-size:17px;font-weight:600;margin:4px 0;line-height:1.45;padding:4px 0}}
.insight-block{{padding:2px 0 0}}

/* sections */
.section{{margin-bottom:22px}}
.section-header{{margin-bottom:14px}}

/* prompt section */
.prompt-wrap{{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:18px;box-shadow:var(--shadow)}}

/* divider */
.divider{{border:none;border-top:1px solid var(--line);margin:18px 0}}

/* scan coverage callout */
.callout{{background:#fffbeb;border:1px solid #fde68a;border-radius:var(--radius);padding:14px 16px;margin-bottom:18px}}

/* repo desc list */
.dl dt{{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:700;margin-top:10px}}
.dl dt:first-child{{margin-top:0}}
.dl dd{{margin:1px 0 0 0;font-size:14px;line-height:1.45}}

/* badges row */
.badge-row{{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}}

@media(max-width:820px){{
main{{padding:20px 16px}}
.hero{{grid-template-columns:1fr;gap:16px}}
.stats{{grid-template-columns:repeat(3,1fr)}}
.grid-2,.grid-3,.suggestions{{grid-template-columns:1fr}}
}}
@media(max-width:500px){{
.stats{{grid-template-columns:repeat(2,1fr)}}
}}
</style>
</head>
<body>
<main>

<!-- header -->
<header class="hero">
  <div class="hero-info">
    <p class="cap" style="margin-bottom:4px">Sentinel report</p>
    <h1>{esc(project_name)}</h1>
    <p class="hero-tagline">{esc(understanding.get('summary') or understanding.get('purpose') or understanding.get('project_type') or 'Project scan and engineering guidance.')}</p>
    <div class="badge-row">{pills(understanding.get('frameworks', [])[:8])}</div>
  </div>
  <div class="health-card">
    <div class="health-score">
      <div class="health-ring {health_class}">
        <svg viewBox="0 0 64 64"><circle class="bg" cx="32" cy="32" r="30"/><circle class="fg" cx="32" cy="32" r="30"/></svg>
        <div class="label">{health}%</div>
      </div>
      <div>
        <div class="cap">Health score</div>
        {'<p class="muted" style="margin:2px 0 0">(excluding security review)</p>' if result['audit'].get('health_score_data', {}).get('security_assessed') is False else ''}
        <p class="muted" style="margin:2px 0 0">Scan #{esc(result.get('scan_number'))}</p>
      </div>
    </div>
    <div class="health-meta">
      {f'<p>confidence: {esc(health_data.get("confidence_label", "normal"))}{" (" + esc(health_data.get("confidence_reason", "")) + ")" if health_data.get("confidence_reason") else ""}</p>' if health_data.get("confidence_label") in ("low_confidence", "moderate_confidence") else ''}
      {f'<p>{esc(health_data.get("explanation", ""))}</p>' if health_data.get("explanation") else ''}
      {health_breakdown}
    </div>
    <div class="health-meta" style="margin-top:8px;padding-top:8px;border-top:1px solid var(--line)">
      <p>{esc(scan_mode)} &middot; {esc(perf.get('duration_seconds', 0))}s</p>
      <p>{esc(result.get('timestamp'))}</p>
    </div>
  </div>
</header>

<!-- stats row -->
<section class="stats">
  <div class="stat"><div class="label">Files</div><div class="value">{esc(metrics.get('total_files', 0))}</div></div>
  <div class="stat"><div class="label">Lines</div><div class="value">{esc(metrics.get('total_lines', 0))}</div></div>
  <div class="stat"><div class="label">Issues Found</div><div class="value">{esc(confirmed_issues)}</div></div>
  <div class="stat"><div class="label">Review Signals</div><div class="value">{esc(len(issues))}</div></div>
  <div class="stat"><div class="label">TODOs</div><div class="value">{esc(metrics.get('open_todos', 0))}</div></div>
</section>

<!-- identity + risk -->
<section class="grid-2">
  <div class="card">
    <h2>Project Identity</h2>
    <dl class="dl">
      <dt>Type</dt><dd>{esc(understanding.get('project_type', 'unknown'))}</dd>
      <dt>Archetype</dt><dd>{esc(understanding.get('archetype', 'unknown'))}</dd>
      <dt>Purpose</dt><dd>{esc(understanding.get('purpose', 'unknown'))}</dd>
      <dt>Workflow</dt><dd>{esc(', '.join(understanding.get('workflow_hints', [])[:6]) or 'not detected')}</dd>
      <dt>Recent changes</dt><dd>{esc(timeline_hint)}</dd>
    </dl>
  </div>
  <div class="card">
    <h2>Risk Summary</h2>
    <dl class="dl">
      <dt>Maintainability risk</dt><dd>{esc(risk_summary.get('maintainability', {}).get('level', 'unknown'))}</dd>
      <dt>Runtime complexity</dt><dd>{esc(risk_summary.get('runtime', {}).get('level', 'unknown'))}</dd>
      <dt>Test signal</dt><dd>{esc(self._test_signal_label(risk_summary))}</dd>
      <dt>Security review</dt><dd>{esc(self._security_label(risk_summary))}</dd>
    </dl>
  </div>
</section>

<!-- top risk insight -->
<section class="card card-accent">
  <h2>Top Risk Insight</h2>
  <p class="insight">{esc(self._build_killer_insight(result))}</p>
  <p class="muted" style="margin-top:2px">Generated by Sentinel from scan signals. Covers the most important single finding.</p>
</section>

<!-- scan coverage warning -->
{f'''<section class="callout">
  <p><strong>Scan Coverage Warning:</strong> {esc(coverage.get("warning", ""))}</p>
  <p><strong>Source lines:</strong> {esc(coverage.get("category_lines", {}).get("source", 0))} &middot; <strong>Test lines:</strong> {esc(coverage.get("category_lines", {}).get("tests", 0))}</p>
  <div class="file-list">{pills(coverage.get("underrepresented_directories", [])[:8])}</div>
</section>''' if coverage.get("warning") else ''}

<!-- next actions -->
<section class="section">
  <h2>Recommended Next Actions</h2>
  <div class="suggestions">{suggestion_cards()}</div>
</section>

<!-- focus + hotspots -->
<section class="grid-2">
  <div class="card">
    <h2>Focus Files</h2>
    <div class="file-list">{pills(focus_files[:12])}</div>
  </div>
  <div class="card">
    <h2>Primary Hotspots</h2>
    <div class="file-list">{pills([item.get('path', '') for item in hotspots[:12]])}</div>
  </div>
</section>

<!-- entry points + hotspot groups -->
<section class="grid-2">
  <div class="card">
    <h2>Entry Points</h2>
    {grouped_pills(
        entry_points_by_category,
        label_map={
            "runtime": "Primary runtime entry points",
            "runtime_surface": "Runtime/API surfaces",
            "example": "Example entry points",
            "build": "Build/tooling entry points",
            "generator": "Generator entry points",
            "test": "Test runners",
            "environment": "Environment setup",
            "documentation": "Documentation/specs",
        },
    )}
  </div>
  <div class="card">
    <h2>Other Hotspots</h2>
    {grouped_pills(
        hotspot_groups,
        label_map={
            "runtime": "Primary runtime hotspots",
            "runtime_surface": "Runtime/API surface hotspots",
            "build_tooling": "Build/tooling hotspots",
            "generator": "Generator hotspots",
            "test_runner": "Test runner hotspots",
            "vendor": "Vendor/third-party hotspots",
            "generated_sdk": "Generated SDK/client code",
            "dependency_lock": "Dependency/lockfile signals",
            "test_data": "Test/data hotspots",
            "documentation": "Documentation hotspots",
            "specification": "Specification hotspots",
            "environment_setup": "Environment/setup",
            "example": "Example hotspots",
        },
    )}
  </div>
</section>

<!-- components table -->
<section class="section">
  <h2>Main Components</h2>
  <div class="card" style="padding:0;overflow:hidden">
  <table><thead><tr><th>Path</th><th>Role</th><th>Files</th><th>Lines</th></tr></thead><tbody>{component_rows()}</tbody></table>
  </div>
</section>

<!-- file risks -->
<section class="section">
  <h2>Top File Risks By Surface</h2>
  {grouped_risk_sections()}
</section>

<!-- review signals -->
<section class="section">
  <h2>Review Signals</h2>
  <p class="muted" style="margin-bottom:10px"><strong>Confirmed issues:</strong> {esc(confirmed_issues)} &middot; <strong>Total signals:</strong> {esc(len(issues))}</p>
  <div class="card" style="padding:0;overflow:hidden">
  <table><thead><tr><th>Severity</th><th>Message</th><th>File</th></tr></thead><tbody>{issue_rows()}</tbody></table>
  </div>
</section>

<!-- agent prompt -->
<section class="section">
  <div class="prompt-wrap">
    <h2 style="margin-bottom:4px">Agent Prompt</h2>
    <p class="muted" style="margin-bottom:8px">Recommended prompt for an AI agent working on this codebase.</p>
    <pre>{esc(markdown_prompt or 'No prompt generated — run a full scan to generate one.')}</pre>
  </div>
</section>

<!-- footer -->
<hr class="divider">
<footer class="muted" style="font-size:12px;padding-bottom:12px;text-align:center">Generated by Sentinel &middot; {esc(scan_mode)} &middot; {esc(perf.get('duration_seconds', 0))}s</footer>

</main>
</body>
</html>
"""

    def render_ask_answer(self, answer: Dict[str, Any]) -> str:
        retrieval = answer.get("retrieval", {})
        scan = answer.get("scan", {})
        understanding = scan.get("audit", {}).get("understanding", {})
        files = retrieval.get("files", [])
        symbols = retrieval.get("symbols", [])
        snippets = retrieval.get("snippets", [])
        suggestions = scan.get("suggestions", [])

        lines = [
            "SENTINEL ASK",
            f"Question: {answer.get('question', '')}",
            "",
            "Short Answer:",
            answer.get("short_answer", "Sentinel found local context, but no direct answer could be inferred."),
            "",
            "Project Context:",
            f"- Project: {understanding.get('project_name', 'unknown')}",
            f"- Type: {understanding.get('project_type', 'unknown')}",
        ]
        if understanding.get("purpose"):
            lines.append(f"- Purpose: {understanding['purpose']}")

        lines.extend(["", "Best Files To Inspect:"])
        if files:
            for item in files[:8]:
                lines.append(
                    f"- {item.get('path')} (score {item.get('score')}, {item.get('lines')} lines): "
                    f"{item.get('summary', '')}"
                )
        else:
            lines.append("- No file matches found.")

        lines.extend(["", "Relevant Symbols:"])
        if symbols:
            for symbol in symbols[:10]:
                lines.append(
                    f"- {symbol.get('qualname')} [{symbol.get('kind')}] "
                    f"at {symbol.get('path')}:{symbol.get('line')}"
                )
        else:
            lines.append("- No symbol matches found.")

        lines.extend(["", "Evidence Snippets:"])
        if snippets:
            for snippet in snippets[:6]:
                lines.append(f"--- {snippet.get('path')}:{snippet.get('start_line')}")
                lines.append(snippet.get("text", ""))
        else:
            lines.append("- No direct snippets matched; use the files above as the starting point.")

        if suggestions:
            top = suggestions[0]
            lines.extend(
                [
                    "",
                    "Related Next Action:",
                    f"- [{top.get('priority')}] {top.get('title')}: {top.get('action')}",
                ]
            )

        lines.extend(
            [
                "",
                "Verification Hint:",
                answer.get("verification_hint", "Run `project-sentinel verify . --dry-run` after making changes."),
            ]
        )
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
                f"Tests {self._test_signal_label(risk_summary)}; "
                f"Security {self._security_label(risk_summary)}"
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

        risk_scores = result["audit"].get("risk_scores", [])
        if risk_scores:
            lines.append("Top Risks:")
            for item in risk_scores[:5]:
                coverage = item.get("coverage", {})
                coverage_text = coverage.get("status", "unknown")
                if coverage.get("test_file"):
                    coverage_text += f" via {coverage['test_file']}"
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
            f"Review Signals: {summary.get('open_issues', 0)}",
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

    def _test_signal_label(self, risk_summary: Dict[str, Any]) -> str:
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

    def _todo_category_label(self, category: str) -> str:
        return {
            "first_party_source": "first-party source",
            "tooling": "tooling",
            "tests_fixtures": "tests/fixtures",
            "docs": "documentation",
            "vendor_generated": "vendor/generated",
        }.get(category, category.replace("_", " "))

    def _health_breakdown_lines(self, health_data: Dict[str, Any]) -> list[str]:
        breakdown = health_data.get("breakdown", {}) if isinstance(health_data, dict) else {}
        if not breakdown:
            return []
        documentation = f"Documentation: {breakdown.get('documentation_percent', 'unknown')}%"
        if breakdown.get("documentation_reason"):
            documentation += f" - {breakdown['documentation_reason']}"
        # Use maintainability_risk if available (synced with maintainability_percent), else derive
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

    def _build_killer_insight(self, result: Dict[str, Any]) -> str:
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

        # Strong tests + high complexity
        if test_level in ("strong", "high") and runtime_level in ("high", "medium"):
            scenarios.append(
                "Strong test infrastructure but high runtime complexity — tests are your safety net, "
                "but complexity in core paths increases regression risk"
            )

        # Many TODOs + high risk
        if todos > 100 and high_risks:
            scenarios.append(
                f"{todos} TODO/FIXME markers and {len(high_risks)} high-risk files suggest "
                f"accumulated technical debt in critical areas"
            )

        # Large repo + limited docs
        doc_issues = sum(1 for i in issues if i.get("type") == "doc_code_drift")
        if files_scanned > 1000 and doc_issues > 10:
            scenarios.append(
                f"Large project with {doc_issues} documentation drift signals — "
                f"docs may lag behind the actual code in several places"
            )

        # Simple repo, strong test signal
        if test_level == "strong" and runtime_level in ("low", "unknown") and not high_risks:
            scenarios.append(
                "Well-structured project with good test coverage and low hotspot density — "
                "ideal for safe, incremental changes"
            )

        # No tests
        if test_level in ("missing", "none", "low") and files_scanned > 50:
            scenarios.append(
                "Limited test infrastructure for a project of this size — "
                "regression risk is higher than necessary"
            )

        if scenarios:
            return scenarios[0]

        # Fallback
        return (
            f"Project has {files_scanned} files across "
            f"{len(understanding.get('main_components', []))} main components. "
            f"Focus on the highest-risk files before broad changes."
        )

    def _security_label(self, risk_summary: Dict[str, Any]) -> str:
        level = str(risk_summary.get("security", {}).get("level", "not_assessed"))
        if level in ("not_assessed", "none"):
            return "not assessed (planned module)"
        return level.replace("_", " ")

    def _confirmed_issue_count(self, issues: Iterable[Dict[str, Any]]) -> int:
        return sum(1 for issue in issues if issue.get("severity") in {"critical", "high"} and issue.get("type") not in {"todo", "large_file", "large_file_size", "doc_code_drift"})

    def _scan_mode_label(self, result: Dict[str, Any]) -> str:
        metrics = result.get("audit", {}).get("metrics", {})
        perf = result.get("performance", {})
        files = int(metrics.get("total_files", 0) or result.get("files_scanned", 0) or 0)
        lines = int(metrics.get("total_lines", 0) or 0)
        if files >= 5_000 or lines >= 500_000:
            return "deep repo intelligence"
        if perf.get("fast_mode"):
            return "fast structure and risk scan"
        return "full project scan"
