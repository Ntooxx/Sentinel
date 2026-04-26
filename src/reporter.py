from __future__ import annotations

import html
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
            lines.append(f"  Duration: {perf.get('duration_seconds', 0):.3f}s | Mode: {self._scan_mode_label(result)}")
        lines.append("=" * self.width)
        lines.append("")

        health = result["audit"]["health_score"]
        health_data = result["audit"].get("health_score_data", {})
        health_text = f"Health: {self._health_bar(health)} {health}%"
        if health_data.get("security_assessed") is False:
            health_text += " (excluding security review)"
        lines.append(health_text)
        risk_summary = result["audit"].get("risk_summary", {})
        if risk_summary:
            lines.append(
                "Risk: "
                f"Maintainability risk: {risk_summary.get('maintainability', {}).get('level', 'unknown')} | "
                f"Runtime complexity: {risk_summary.get('runtime', {}).get('level', 'unknown')} | "
                f"Test signal: {self._test_signal_label(risk_summary)} | "
                f"Security review: {risk_summary.get('security', {}).get('level', 'not_assessed')}"
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
        lines.append(f"- Health Score: {health_text}")
        risk_summary = result["audit"].get("risk_summary", {})
        if risk_summary:
            lines.append(
                "- Risk Summary: "
                f"Maintainability risk: {risk_summary.get('maintainability', {}).get('level', 'unknown')}; "
                f"Runtime complexity: {risk_summary.get('runtime', {}).get('level', 'unknown')}; "
                f"Test signal: {self._test_signal_label(risk_summary)}; "
                f"Security review: {risk_summary.get('security', {}).get('level', 'not_assessed')}"
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
                rows.append(
                    "<tr>"
                    f"<td><span class=\"badge\">{esc(item.get('level', 'unknown')).upper()}</span></td>"
                    f"<td><code>{esc(item.get('file', ''))}</code></td>"
                    f"<td>{esc(item.get('score', 0))}</td>"
                    f"<td>{esc(', '.join(item.get('factors', [])[:4]))}</td>"
                    "</tr>"
                )
            return "".join(rows)

        def grouped_risk_sections() -> str:
            label_map = {
                "runtime": "Top runtime risks",
                "build_tooling": "Top build/tooling risks",
                "test": "Top test risks",
                "documentation": "Top documentation risks",
                "other": "Other risks",
            }
            sections = []
            for key, label in label_map.items():
                items = list(risk_groups.get(key, []))
                if not items:
                    continue
                rows = []
                for item in items[:8]:
                    rows.append(
                        "<tr>"
                        f"<td><span class=\"badge\">{esc(item.get('level', 'unknown')).upper()}</span></td>"
                        f"<td><code>{esc(item.get('file', ''))}</code></td>"
                        f"<td>{esc(item.get('score', 0))}</td>"
                        f"<td>{esc(', '.join(item.get('factors', [])[:4]))}</td>"
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
<title>Sentinel Report - {esc(project_name)}</title>
<style>
:root{{color-scheme:light;--ink:#18212b;--muted:#607080;--line:#d9e1e8;--soft:#f4f7f9;--panel:#ffffff;--accent:#146c94;--accent2:#8a5a12;--good:#157347;--warn:#9a6700;--bad:#b42318}}
*{{box-sizing:border-box}}
body{{margin:0;font-family:Inter,ui-sans-serif,system-ui,-apple-system,Segoe UI,sans-serif;background:#eef3f6;color:var(--ink);line-height:1.45}}
main{{max-width:1180px;margin:0 auto;padding:28px}}
header{{padding:28px 0 18px;border-bottom:1px solid var(--line)}}
h1{{font-size:34px;line-height:1.1;margin:0 0 10px;letter-spacing:0}}
h2{{font-size:21px;margin:0 0 14px}}
h3{{font-size:16px;margin:4px 0 8px}}
p{{margin:0 0 10px}}
.muted{{color:var(--muted)}}
.hero{{display:grid;grid-template-columns:minmax(0,1.4fr) minmax(280px,.8fr);gap:22px;align-items:end}}
.summary{{font-size:16px;max-width:820px;color:#354654}}
.stats{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:22px 0}}
.stat,.section,.item{{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:16px}}
.stat .label,.item-kicker{{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);font-weight:700}}
.stat .value{{font-size:27px;font-weight:800;margin-top:4px}}
.good{{color:var(--good)}}.warn{{color:var(--warn)}}.bad{{color:var(--bad)}}
.grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;margin:14px 0}}
.suggestions{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}}
.meta{{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0;color:var(--muted);font-size:13px}}
.meta span,.pill,.badge{{border:1px solid var(--line);border-radius:999px;padding:3px 8px;background:var(--soft)}}
.badge{{font-size:12px;font-weight:800;border-radius:6px}}
.file-list{{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}}
table{{width:100%;border-collapse:collapse;font-size:14px}}
th,td{{text-align:left;border-bottom:1px solid var(--line);padding:9px;vertical-align:top}}
th{{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}}
code,pre{{font-family:ui-monospace,SFMono-Regular,Consolas,monospace}}
pre{{white-space:pre-wrap;overflow:auto;background:#101820;color:#eef6ff;border-radius:8px;padding:14px;max-height:420px}}
.progress{{height:10px;background:#dbe4ea;border-radius:999px;overflow:hidden;margin-top:10px}}
.progress span{{display:block;height:100%;background:var(--accent);width:{health}%}}
@media(max-width:820px){{main{{padding:18px}}.hero,.grid,.suggestions{{grid-template-columns:1fr}}.stats{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}
</style>
</head>
<body>
<main>
<header class="hero">
  <div>
    <p class="muted">Sentinel report</p>
    <h1>{esc(project_name)}</h1>
    <p class="summary">{esc(understanding.get('summary') or understanding.get('purpose') or understanding.get('project_type') or 'Project scan and engineering guidance.')}</p>
    <div class="file-list">{pills(understanding.get('frameworks', [])[:8])}</div>
  </div>
  <div class="section">
    <div class="muted">Health score</div>
    <div class="stat-value {health_class}" style="font-size:42px;font-weight:900">{health}%</div>
    <div class="progress"><span></span></div>
    {f'<p class="muted" style="margin-top:10px">(excluding security review)</p>' if result['audit'].get('health_score_data', {}).get('security_assessed') is False else ''}
    {f'<p style="margin-top:10px">{esc(health_data.get("explanation", ""))}</p>' if health_data.get("explanation") else ''}
    {f'<p class="muted" style="margin-top:10px">{health_breakdown}</p>' if health_breakdown else ''}
    <p class="muted" style="margin-top:10px">Scan mode: {esc(scan_mode)}<br>Duration: {esc(perf.get('duration_seconds', 0))}s</p>
    <p class="muted" style="margin-top:10px">Scan #{esc(result.get('scan_number'))} at {esc(result.get('timestamp'))}</p>
  </div>
</header>

  <section class="stats">
    <div class="stat"><div class="label">Files</div><div class="value">{esc(metrics.get('total_files', 0))}</div></div>
    <div class="stat"><div class="label">Lines</div><div class="value">{esc(metrics.get('total_lines', 0))}</div></div>
    <div class="stat"><div class="label">Confirmed Issues</div><div class="value">{esc(confirmed_issues)}</div></div>
    <div class="stat"><div class="label">Review Signals</div><div class="value">{esc(len(issues))}</div></div>
    <div class="stat"><div class="label">TODOs</div><div class="value">{esc(metrics.get('open_todos', 0))}</div></div>
  </section>

<section class="grid">
  <div class="section">
    <h2>Project Identity</h2>
    <p><strong>Type:</strong> {esc(understanding.get('project_type', 'unknown'))}</p>
    <p><strong>Purpose:</strong> {esc(understanding.get('purpose', 'unknown'))}</p>
    <p><strong>Workflow:</strong> {esc(', '.join(understanding.get('workflow_hints', [])[:6]) or 'not detected')}</p>
    <p><strong>Recent changes:</strong> {esc(timeline_hint)}</p>
  </div>
  <div class="section">
    <h2>Risk Summary</h2>
    <p><strong>Maintainability risk:</strong> {esc(risk_summary.get('maintainability', {}).get('level', 'unknown'))}</p>
    <p><strong>Runtime complexity:</strong> {esc(risk_summary.get('runtime', {}).get('level', 'unknown'))}</p>
    <p><strong>Test signal:</strong> {esc(self._test_signal_label(risk_summary))}</p>
    <p><strong>Security review:</strong> {esc(risk_summary.get('security', {}).get('level', 'not assessed'))}</p>
  </div>
</section>

{f'''<section class="section">
  <h2>Scan Coverage</h2>
  <p><strong>Warning:</strong> {esc(coverage.get("warning", ""))}</p>
  <p><strong>Source lines:</strong> {esc(coverage.get("category_lines", {}).get("source", 0))}</p>
  <p><strong>Test lines:</strong> {esc(coverage.get("category_lines", {}).get("tests", 0))}</p>
  <div class="file-list">{pills(coverage.get("underrepresented_directories", [])[:8])}</div>
</section>''' if coverage.get("warning") else ''}

<section class="section">
  <h2>Recommended Next Actions</h2>
  <div class="suggestions">{suggestion_cards()}</div>
</section>

<section class="grid">
  <div class="section">
    <h2>Focus Files</h2>
    <div class="file-list">{pills(focus_files[:10])}</div>
  </div>
  <div class="section">
    <h2>Primary Hotspots</h2>
    <div class="file-list">{pills([item.get('path', '') for item in hotspots[:10]])}</div>
  </div>
</section>

<section class="grid">
  <div class="section">
    <h2>Entry Points</h2>
    {grouped_pills(
        entry_points_by_category,
        label_map={
            "runtime": "Runtime entry points",
            "build": "Build entry points",
            "generator": "Generator entry points",
            "test": "Test runners",
            "environment": "Environment setup",
        },
    )}
  </div>
  <div class="section">
    <h2>Other Hotspots</h2>
    {grouped_pills(
        hotspot_groups,
        label_map={
            "build_tooling": "Build/tooling hotspots",
            "vendor": "Vendor/third-party hotspots — track only, do not refactor by default",
            "test_data": "Test/data hotspots",
            "documentation": "Documentation hotspots",
        },
    )}
  </div>
</section>

<section class="section">
  <h2>Main Components</h2>
  <table><thead><tr><th>Path</th><th>Role</th><th>Files</th><th>Lines</th></tr></thead><tbody>{component_rows()}</tbody></table>
</section>

<section class="section">
  <h2>Top File Risks By Surface</h2>
  {grouped_risk_sections()}
</section>

<section class="section">
  <h2>Review Signals</h2>
  <p><strong>Confirmed issues:</strong> {esc(confirmed_issues)}</p>
  <p><strong>Review signals:</strong> {esc(len(issues))}</p>
  <table><thead><tr><th>Severity</th><th>Message</th><th>File</th></tr></thead><tbody>{issue_rows()}</tbody></table>
</section>

<section class="section">
  <h2>Agent Prompt</h2>
  <pre>{esc(markdown_prompt or 'No prompt generated.')}</pre>
</section>

<section class="section">
  <h2>Knowledge Context</h2>
  <pre>{esc(knowledge_context.strip() or 'No knowledge context exported.')}</pre>
</section>

<footer class="muted" style="padding:24px 0">Generated by Sentinel. Scan mode: {esc(scan_mode)}. Duration: {esc(perf.get('duration_seconds', 0))}s.</footer>
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
                f"Security {risk_summary.get('security', {}).get('level', 'not_assessed')}"
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
            ("build_tooling", "Build/Tooling Hotspots"),
            ("vendor", "Vendor/Third-Party Hotspots"),
            ("test_data", "Test/Data Hotspots"),
            ("documentation", "Documentation Hotspots"),
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
                ("runtime", "Runtime"),
                ("build", "Build"),
                ("generator", "Generator"),
                ("test", "Test"),
                ("environment", "Environment"),
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
                lines.append(
                    f"- [{item.get('level')}] {item.get('file')} score={item.get('score')} "
                    f"({', '.join(item.get('factors', [])[:3])}; coverage={coverage_text})"
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
            "medium": "present",
            "low": "limited",
            "missing": "missing",
            "strong": "strong",
            "present": "present — coverage unknown",
            "unknown": "unknown",
        }
        mapped = label_map.get(value, value)
        if value == "present" and reason:
            mapped = f"present — {reason.lower()}"
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
            f"Security: {str(breakdown.get('security', 'unknown')).replace('_', ' ')}",
        ]

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
