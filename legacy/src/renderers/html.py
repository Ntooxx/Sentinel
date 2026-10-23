from __future__ import annotations

from typing import Any, Dict, Iterable

from renderers._utils import (
    _build_killer_insight,
    _confirmed_issue_count,
    _health_breakdown_lines,
    _scan_mode_label,
    _security_label,
    _test_signal_label,
)

_LANG_COLORS = {
    "python": "#3572A5", "javascript": "#f7df1e", "typescript": "#3178c6",
    "go": "#00ADD8", "rust": "#dea584", "java": "#b07219", "kotlin": "#A97BFF",
    "ruby": "#701516", "c": "#555555", "cpp": "#f34b7d", "c++": "#f34b7d",
    "csharp": "#178600", "swift": "#F05138", "zig": "#ec915c", "scala": "#c22d40",
    "elixir": "#4e2a59", "ocaml": "#3be133", "f#": "#b845fc",
    "shell": "#89e051", "makefile": "#427819", "dockerfile": "#384d54",
    "yaml": "#cb171e", "toml": "#9c4221", "json": "#292929", "markdown": "#083fa1",
    "html": "#e34c26", "css": "#563d7c", "sql": "#e38c00",
    "cmake": "#DAF1E8", "protocol buffers": "#6c2b9c",
}

_SHIELD_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="48" height="48">'
    '<defs><linearGradient id="sg" x1="0" y1="0" x2="1" y2="1">'
    '<stop offset="0%" stop-color="#0f6b9e"/><stop offset="100%" stop-color="#1a9edb"/>'
    '</linearGradient></defs>'
    '<rect x="12" y="16" width="40" height="32" rx="8" fill="url(#sg)"/>'
    '<polygon points="32,20 24,30 28,30 24,44 32,36 40,44 36,30 40,30" fill="#fff"/>'
    '</svg>'
)


def _esc(value: Any) -> str:
    s = str(value)
    s = s.replace("&", "&amp;")
    s = s.replace("<", "&lt;")
    s = s.replace(">", "&gt;")
    s = s.replace('"', "&quot;")
    return s


def _dedupe_list(items: list) -> list:
    seen = set()
    result = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _lang_style(lang: str) -> str:
    color = _LANG_COLORS.get(lang.lower(), "#666")
    return f"background:{color};border-color:{color}"

_SORT_ASC = chr(0x25B2)
_SORT_DESC = chr(0x25BC)
_SORT_JS = """<script>
(function(){function s(t,c){var n='asc',b=t.getAttribute('data-order'),d=!0;if(b==='asc'){n='desc';d=!1}var e=t.closest('table').querySelector('tbody');if(!e)return;var r=Array.from(e.querySelectorAll('tr'));var i=r.sort(function(a,b){var p=a.children[c].textContent.trim(),q=b.children[c].textContent.trim();if(t.hasAttribute('data-num')){p=parseFloat(p)||0;q=parseFloat(q)||0}else{p=p.toLowerCase();q=q.toLowerCase()}return p<q?-1:p>q?1:0});if(!d)i.reverse();i.forEach(function(o){e.appendChild(o)});t.closest('table').querySelectorAll('.sortable').forEach(function(h){h.removeAttribute('data-order')});t.setAttribute('data-order',n);t.querySelector('.sort-arrow').textContent=n==='asc'?'_SORT_ASC_':'_SORT_DESC_'}document.querySelectorAll('.sortable').forEach(function(h,i){h.addEventListener('click',function(){s(this,parseInt(this.getAttribute('data-col')))});if(i===0)s(h,0)})})();
</script>"""
_SORT_JS = _SORT_JS.replace("_SORT_ASC_", _SORT_ASC).replace("_SORT_DESC_", _SORT_DESC)


class HTMLRenderer:
    def __init__(self, width: int = 60):
        self.width = width

    def render(self, result: Dict[str, Any], knowledge_context: str = "") -> str:
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
        languages = understanding.get("languages", [])
        frameworks = understanding.get("frameworks", [])
        primary_language = understanding.get("primary_language", "")
        project_name = understanding.get("project_name") or "Sentinel Project"
        health = int(result["audit"].get("health_score", 0) or 0)
        health_class = "good" if health >= 80 else "warn" if health >= 55 else "bad"
        file_types = metrics.get("file_types", {})
        file_list = result.get("file_list", [])

        risk_map = {r["file"]: r for r in risk_scores}
        issue_map: dict[str, list] = {}
        for issue in issues:
            f = issue.get("file", "")
            if f:
                issue_map.setdefault(f, []).append(issue)

        def file_table_rows() -> str:
            if not file_list:
                return "<tr><td colspan=\"5\" class=\"muted\">No file data available.</td></tr>"
            rows = []
            for entry in file_list:
                path = entry["path"]
                lines = entry.get("lines", 0) or 0
                risk = risk_map.get(path)
                file_issues = issue_map.get(path, [])
                risk_score = risk["score"] if risk else 0
                risk_level = risk["level"] if risk else "none"
                risk_badge_class = risk_level if risk_level != "none" else "low"
                risk_display = risk_level.upper() if risk_level != "none" else "-"
                issue_count = len(file_issues)
                issue_label = str(issue_count) if issue_count else ""
                cls = "risk-high" if risk_level == "high" else "risk-mid" if risk_level == "medium" else ""
                rows.append(
                    "<tr>"
                    f"<td class=\"file-cell\"><code>{_esc(path)}</code></td>"
                    f"<td class=\"num\">{_esc(lines)}</td>"
                    f"<td><span class=\"badge {risk_badge_class}\">{_esc(risk_display)}</span></td>"
                    f"<td class=\"num\">{_esc(risk_score)}</td>"
                    f"<td class=\"num\">{_esc(issue_label)}</td>"
                    f"<td style=\"font-size:12px;color:var(--muted)\">{_esc(', '.join(risk.get('factors', [])[:2])) if risk else ''}</td>"
                    "</tr>"
                )
            return "".join(rows)

        def pills(values: Iterable[Any]) -> str:
            items = [f"<span class=\"pill\">{_esc(value)}</span>" for value in values if str(value)]
            return "".join(items) or "<span class=\"muted\">None detected</span>"

        def maybe_pills(values: Iterable[Any], fallback: str = "") -> str:
            items = [f"<span class=\"pill\">{_esc(value)}</span>" for value in values if str(value)]
            if items:
                return "".join(items)
            return f"<span class=\"muted\">{_esc(fallback)}</span>" if fallback else ""

        def grouped_pills(groups: Dict[str, Iterable[Any]], *, label_map: Dict[str, str]) -> str:
            chunks = []
            for key, label in label_map.items():
                values = list(groups.get(key, []))
                if not values:
                    continue
                display_values = [item.get("path", item) if isinstance(item, dict) else item for item in values]
                chunks.append(
                    f"<div class=\"group-section\"><h3>{_esc(label)}</h3><div class=\"file-list\">{pills(display_values[:6])}</div></div>"
                )
            return "".join(chunks) or "<p class=\"muted\">None detected.</p>"

        def issue_rows() -> str:
            if not issues:
                return "<tr><td colspan=\"3\" class=\"muted\">No review signals detected.</td></tr>"
            rows = []
            for issue in issues[:40]:
                rows.append(
                    "<tr>"
                    f"<td><span class=\"badge\">{_esc(issue.get('severity', 'unknown')).upper()}</span></td>"
                    f"<td>{_esc(issue.get('message', ''))}</td>"
                    f"<td><code>{_esc(issue.get('file', ''))}</code></td>"
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
                    f"<div class=\"item-kicker\">#{index} {_esc(suggestion.get('priority', 'medium')).upper()}</div>"
                    f"<h3>{_esc(suggestion.get('title', 'Untitled suggestion'))}</h3>"
                    f"<p>{_esc(suggestion.get('reason', ''))}</p>"
                    f"<p><strong>Action:</strong> {_esc(suggestion.get('action', ''))}</p>"
                    "<div class=\"meta\">"
                    f"<span>impact {_esc(suggestion.get('impact', 'medium'))}</span>"
                    f"<span>effort {_esc(suggestion.get('effort', 'medium'))}</span>"
                    f"<span>confidence {_esc(confidence.get('level', 'unknown'))}</span>"
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
                    f"<td><code>{_esc(component.get('path', ''))}</code></td>"
                    f"<td>{_esc(component.get('role', ''))}</td>"
                    f"<td>{_esc(component.get('file_count', 0))}</td>"
                    f"<td>{_esc(component.get('line_count', 0))}</td>"
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
                    f"<td><span class=\"badge\">{_esc(item.get('level', 'unknown')).upper()}</span></td>"
                    f"<td><code>{_esc(item.get('file', ''))}</code></td>"
                    f"<td>{_esc(item.get('score', 0))}</td>"
                    f"<td>{_esc(', '.join(deduped[:4]))}</td>"
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
                "vendor": "Vendor/third-party hotspots",
                "generated_sdk": "Generated SDK/client code",
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
                        f"<td><span class=\"badge\">{_esc(item.get('level', 'unknown')).upper()}</span></td>"
                        f"<td><code>{_esc(item.get('file', ''))}</code></td>"
                        f"<td>{_esc(item.get('score', 0))}</td>"
                        f"<td>{_esc(', '.join(deduped[:4]))}</td>"
                        "</tr>"
                    )
                sections.append(
                    f"<div class=\"risk-table-wrap\"><h3>{_esc(label)}</h3>"
                    "<table><thead><tr><th>Level</th><th>File</th><th>Score</th><th>Factors</th></tr></thead>"
                    f"<tbody>{''.join(rows)}</tbody></table></div>"
                )
            return "".join(sections) or "<p class=\"muted\">No grouped risk scores available.</p>"

        def language_bar() -> str:
            if not file_types:
                return ""
            total = sum(file_types.values())
            if total == 0:
                return ""
            sorted_types = sorted(file_types.items(), key=lambda x: -x[1])
            segments = []
            for ext, count in sorted_types[:10]:
                pct = round(count / total * 100, 1)
                clean_name = ext.lstrip(".").lower()
                if clean_name == "dockerfile":
                    clean_name = "dockerfile"
                color = _LANG_COLORS.get(clean_name, "#666")
                segments.append(
                    f"<div style=\"flex:{pct * 100:.0f};height:8px;background:{color};"
                    f"border-radius:4px 0 0 4px\" title=\"{_esc(ext)}: {pct:.1f}%\"></div>"
                )
            bar = f"<div class=\"lang-bar\">{''.join(segments)}</div>"

            labels = []
            for ext, count in sorted_types[:10]:
                pct = round(count / total * 100, 1)
                clean_name = ext.lstrip(".").lower()
                color = _LANG_COLORS.get(clean_name, "#666")
                labels.append(
                    f"<span class=\"lang-label\"><span class=\"lang-dot\" style=\"background:{color}\"></span>"
                    f"{_esc(ext.lstrip('.'))} {pct:.1f}%</span>"
                )
            return bar + "<div class=\"lang-labels\">" + "".join(labels) + "</div>"

        def scan_timeline() -> str:
            history = result.get("scan_history") or result.get("knowledge", {}).get("scan_history", [])
            if not isinstance(history, list) or len(history) < 1:
                return ""
            points = history[-15:]
            n = len(points)
            W, H = 600, 160
            PL, PR, PT, PB = 50, 585, 12, 140
            PW, PH = PR - PL, PB - PT
            if n == 0:
                return ""
            svg_parts = []

            def _x(i: int) -> float:
                return PL + (i / (max(1, n - 1))) * PW

            def _y(h: float) -> float:
                return PB - (max(0, min(100, h)) / 100) * PH

            def _color(h: float) -> str:
                return "#1a7f4c" if h >= 80 else "#a86800" if h >= 55 else "#c62828"

            gridlines = [0, 25, 50, 75, 100]
            for g in gridlines:
                gy = _y(g)
                svg_parts.append(
                    f"<line x1='{PL}' y1='{gy:.1f}' x2='{PR}' y2='{gy:.1f}' "
                    f"stroke='#e8edf2' stroke-width='1'/>"
                )
                svg_parts.append(
                    f"<text x='{PL - 6}' y='{gy + 3.5:.1f}' text-anchor='end' "
                    f"fill='#9aa9b9' font-size='10'>{g}</text>"
                )

            line_coords = " ".join(
                f"{_x(i):.1f},{_y(points[i].get('health_score', 0) or 0):.1f}"
                for i in range(n)
            )
            if n >= 2:
                svg_parts.append(
                    f"<polyline points='{line_coords}' "
                    f"fill='none' stroke='#8899aa' stroke-width='1.5' stroke-dasharray='4,3'/>"
                )

            for i, entry in enumerate(points):
                h = entry.get("health_score", 0) or 0
                cx, cy = _x(i), _y(h)
                color = _color(h)
                label = entry.get("scan_number", i + 1)
                svg_parts.append(
                    f"<circle cx='{cx:.1f}' cy='{cy:.1f}' r='3.5' "
                    f"fill='{color}' stroke='#fff' stroke-width='1.5'>"
                    f"<title>Scan #{label}: {h:.0f}/100</title></circle>"
                )

            svg_parts.append(
                f"<text x='{PL}' y='{PB + 16}' fill='#9aa9b9' font-size='9'>"
                f"Scans ({n}) &mdash; "
                f"{_esc(points[0].get('timestamp', '')[:10] if points[0].get('timestamp') else '')}"
                f" to "
                f"{_esc(points[-1].get('timestamp', '')[:10] if points[-1].get('timestamp') else '')}"
                f"</text>"
            )

            return (
                "<div class='timeline-card'>"
                f"<h3>Health Trend</h3>"
                f"<svg viewBox='0 0 {W} {H}' preserveAspectRatio='xMidYMid meet' "
                f"style='width:100%;max-height:160px'>"
                f"{''.join(svg_parts)}"
                f"</svg></div>"
            )

        health_breakdown = "<br>".join(_esc(line) for line in _health_breakdown_lines(health_data))
        scan_mode = _scan_mode_label(result)
        confirmed_issues = _confirmed_issue_count(issues)

        markdown_prompt = suggestions[0].get("suggested_prompt", "") if suggestions else ""
        if diff.get("is_first_scan"):
            timeline_hint = "Baseline scan: all files new"
        else:
            timeline_hint = (
                f"{diff.get('new_count', 0)} new, "
                f"{diff.get('modified_count', 0)} modified, "
                f"{diff.get('deleted_count', 0)} deleted"
            )

        desc = understanding.get("description") or ""
        summary_text = understanding.get("summary") or understanding.get("purpose") or ""

        primary_lang_badge = (
            f"<span class=\"lang-pill\" style=\"{_lang_style(primary_language)}\">{_esc(primary_language)}</span>"
            if primary_language else ""
        )
        archetype = understanding.get("archetype", "")
        secondary = understanding.get("archetype_secondary", [])

        total_lines = metrics.get("total_lines", 0) or 0
        total_files = metrics.get("total_files", 0) or 0

        return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sentinel Report - {_esc(project_name)}</title>
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

/* hero */
.hero{{display:grid;grid-template-columns:auto 1fr 280px;gap:20px;align-items:start;padding:28px 0 24px;border-bottom:1px solid var(--line);margin-bottom:24px}}
.hero-logo{{width:56px;height:56px;flex-shrink:0;margin-top:4px}}
.hero-info h1{{font-size:28px;margin-bottom:2px;display:flex;align-items:center;gap:10px}}
.hero-description{{font-size:14px;color:var(--muted);line-height:1.5;margin:4px 0 10px;max-width:640px}}
.hero-badges{{display:flex;flex-wrap:wrap;gap:6px;align-items:center}}
.lang-pill{{display:inline-flex;align-items:center;gap:4px;font-size:11px;font-weight:700;color:#fff;padding:3px 9px;border-radius:999px;text-transform:uppercase;letter-spacing:.04em}}
.hero-badge{{display:inline-flex;align-items:center;gap:4px;font-size:11px;font-weight:600;color:var(--muted);padding:3px 9px;border:1px solid var(--line);border-radius:999px;background:var(--surface)}}

/* health card */
.health-card{{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:20px;box-shadow:var(--shadow)}}
.health-score{{display:flex;align-items:center;gap:14px;margin-bottom:8px}}
.health-ring{{position:relative;width:60px;height:60px;flex-shrink:0}}
.health-ring svg{{transform:rotate(-90deg);width:60px;height:60px}}
.health-ring .bg{{fill:none;stroke:#e8edf2;stroke-width:5}}
.health-ring .fg{{fill:none;stroke:currentColor;stroke-width:5;stroke-linecap:round;stroke-dasharray:{max(0.5, health * 1.88)} 188.5}}
.health-ring .label{{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-size:15px;font-weight:800}}
.health-meta{{font-size:12px;color:var(--muted);line-height:1.6}}

/* stats row */
.stats{{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:22px}}
.stat{{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:14px 16px;box-shadow:var(--shadow)}}
.stat .label{{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:700}}
.stat .value{{font-size:22px;font-weight:800;margin-top:2px;letter-spacing:-.02em}}

/* language bar */
.lang-bar{{display:flex;gap:2px;margin:12px 0 8px;overflow:hidden;border-radius:4px}}
.lang-labels{{display:flex;flex-wrap:wrap;gap:8px 14px;margin:4px 0 0}}
.lang-label{{font-size:12px;color:var(--muted);display:flex;align-items:center;gap:5px}}
.lang-dot{{width:10px;height:10px;border-radius:50%;flex-shrink:0}}

/* timeline */
.timeline-card{{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:14px 16px;box-shadow:var(--shadow);margin-bottom:18px}}
.timeline-card h3{{font-size:12px;margin:0 0 8px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}}
.timeline{{display:flex;gap:6px;align-items:center;flex-wrap:wrap}}
.dot{{width:14px;height:14px;border-radius:50%;display:inline-block;opacity:.7}}
.dot.good{{background:var(--good)}}.dot.warn{{background:var(--warn)}}.dot.bad{{background:var(--bad)}}

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
.file-list{{display:flex;flex-wrap:wrap;gap:5px;margin-top:8px}}

/* table */
table{{width:100%;border-collapse:collapse;font-size:13.5px}}
th,td{{text-align:left;padding:9px 10px;vertical-align:top;border-bottom:1px solid var(--line)}}
th{{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:700;background:var(--bg)}}
tr:last-child td{{border-bottom:none}}

/* sortable table */
.sortable{{cursor:pointer;user-select:none}}
.sortable:hover{{background:#e3e9ef}}
.sort-arrow{{font-size:10px;margin-left:3px;color:var(--muted)}}
.risk-high{{background:#fef2f2}}
.risk-mid{{background:#fffbeb}}
.num{{text-align:right;font-variant-numeric:tabular-nums}}
.file-cell{{max-width:380px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
#file-table{{table-layout:auto;width:100%}}
#file-table th{{position:sticky;top:0;z-index:1}}

/* code */
code{{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:12.5px;background:var(--bg);padding:1px 5px;border-radius:4px;word-break:break-all}}
pre{{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:13px;line-height:1.5;white-space:pre-wrap;overflow:auto;background:#0e1a2b;color:#e2ecf9;border-radius:var(--radius);padding:18px;max-height:440px;box-shadow:var(--shadow-lg);margin:4px 0 0}}
pre::before{{content:"$   ";color:#5a7a9a;margin-right:8px;user-select:none}}

/* insight */
.insight{{font-size:17px;font-weight:600;margin:4px 0;line-height:1.45;padding:4px 0}}

/* sections */
.section{{margin-bottom:22px}}
.group-section{{margin-bottom:12px}}
.risk-table-wrap{{margin-bottom:16px}}

/* prompt */
.prompt-wrap{{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:18px;box-shadow:var(--shadow)}}

/* divider */
.divider{{border:none;border-top:1px solid var(--line);margin:18px 0}}

/* callout */
.callout{{background:#fffbeb;border:1px solid #fde68a;border-radius:var(--radius);padding:14px 16px;margin-bottom:18px}}

/* identity grid */
.identity-grid{{display:grid;grid-template-columns:2fr 1fr;gap:14px;margin-bottom:18px}}
.identity-dl{{display:grid;grid-template-columns:auto 1fr;gap:2px 16px;margin:4px 0 0}}
.identity-dl dt{{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:700;padding:3px 0}}
.identity-dl dd{{margin:0;font-size:13.5px;padding:3px 0;line-height:1.4}}

/* badges row */
.badge-row{{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}}

/* description block */
.desc-block{{font-size:14px;line-height:1.6;color:var(--ink);opacity:.85;margin:6px 0 0;padding:10px 14px;background:var(--bg);border-radius:var(--radius-sm);border:1px solid var(--line)}}

@media(max-width:820px){{
main{{padding:20px 16px}}
.hero{{grid-template-columns:1fr;gap:14px}}
.stats{{grid-template-columns:repeat(3,1fr)}}
.grid-2,.grid-3,.suggestions,.identity-grid{{grid-template-columns:1fr}}
}}
@media(max-width:500px){{
.stats{{grid-template-columns:repeat(2,1fr)}}
}}
</style>
</head>
<body>
<main>

<!-- hero -->
<header class="hero">
  <div class="hero-logo">{_SHIELD_SVG}</div>
  <div class="hero-info">
    <p class="cap" style="margin-bottom:2px">Sentinel project report</p>
    <h1>{_esc(project_name)}</h1>
    <p class="hero-description">{_esc(summary_text or 'No summary available.')}</p>
    <div class="hero-badges">
      {primary_lang_badge}
      <span class="hero-badge">{_esc(archetype) if archetype else 'project'}</span>
      <span class="hero-badge">{_esc(f'{len(components)} components') if components else 'no components'}</span>
      <span class="hero-badge">{_esc(scan_mode)}</span>
      <span class="hero-badge">{_esc(perf.get('duration_seconds', 0))}s</span>
    </div>
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
        <p class="muted" style="margin:2px 0 0">Scan #{_esc(result.get('scan_number'))}</p>
      </div>
    </div>
    <div class="health-meta">
      {f'<p>confidence: {_esc(health_data.get("confidence_label", "normal"))}</p>' if health_data.get("confidence_label") in ("low_confidence", "moderate_confidence") else ''}
      {health_breakdown}
    </div>
    <div class="health-meta" style="margin-top:8px;padding-top:8px;border-top:1px solid var(--line)">
      <p>{_esc(result.get('timestamp', ''))}</p>
    </div>
  </div>
</header>

<!-- stats -->
<section class="stats">
  <div class="stat"><div class="label">Files</div><div class="value">{_esc(total_files)}</div></div>
  <div class="stat"><div class="label">Lines</div><div class="value">{_esc(f'{total_lines:,}')}</div></div>
  <div class="stat"><div class="label">Languages</div><div class="value">{_esc(len(languages)) if languages else '?'}</div></div>
  <div class="stat"><div class="label">Issues</div><div class="value">{_esc(confirmed_issues)}</div></div>
  <div class="stat"><div class="label">TODOs</div><div class="value">{_esc(metrics.get('open_todos', 0))}</div></div>
</section>

<!-- scan timeline -->
{scan_timeline()}

<!-- identity + description -->
<section class="identity-grid">
  <div class="card">
    <h2>Project Identity</h2>
    <div class="identity-dl">
      <dt>Type</dt><dd>{_esc(understanding.get('project_type', 'unknown'))}</dd>
      <dt>Archetype</dt><dd>{_esc(archetype)}{f' (+{_esc(", ".join(secondary[:4]))})' if secondary else ''}</dd>
      <dt>Primary language</dt><dd>{_esc(primary_language) if primary_language else 'unknown'}</dd>
      <dt>Purpose</dt><dd>{_esc(understanding.get('purpose', 'unknown'))}</dd>
      <dt>Workflow</dt><dd>{_esc(', '.join(understanding.get('workflow_hints', [])[:6]) or 'not detected')}</dd>
      <dt>Recent changes</dt><dd>{_esc(timeline_hint)}</dd>
    </div>
  </div>
  <div class="card">
    <h2>Risk Summary</h2>
    <div class="identity-dl">
      <dt>Maintainability</dt><dd>{_esc(risk_summary.get('maintainability', {}).get('level', 'unknown'))}</dd>
      <dt>Runtime complexity</dt><dd>{_esc(risk_summary.get('runtime', {}).get('level', 'unknown'))}</dd>
      <dt>Test signal</dt><dd>{_esc(_test_signal_label(risk_summary))}</dd>
      <dt>Security review</dt><dd>{_esc(_security_label(risk_summary))}</dd>
    </div>
  </div>
</section>

{f'<section class="card card-accent"><h2>About This Project</h2><div class="desc-block">{_esc(desc.strip() or summary_text)}</div></section>' if (desc or summary_text) else ''}

<!-- language breakdown -->
<section class="card" style="margin-bottom:18px">
  <h2>Language Breakdown</h2>
  {language_bar()}
</section>

<!-- top risk insight -->
<section class="card card-accent" style="margin-bottom:18px">
  <h2>Top Risk Insight</h2>
  <p class="insight">{_esc(_build_killer_insight(result))}</p>
  <p class="muted" style="margin-top:2px">Generated by Sentinel from scan signals.</p>
</section>

<!-- scan coverage warning -->
{f'''<section class="callout">
  <p><strong>Scan Coverage Warning:</strong> {_esc(coverage.get("warning", ""))}</p>
  <p><strong>Source lines:</strong> {_esc(coverage.get("category_lines", {}).get("source", 0))} &middot; <strong>Test lines:</strong> {_esc(coverage.get("category_lines", {}).get("tests", 0))}</p>
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

<!-- frameworks -->
{f'''<section class="section">
  <h2>Frameworks &amp; Tools</h2>
  <div class="badge-row">{pills(frameworks)}</div>
</section>''' if frameworks else ''}

<!-- per-file breakdown -->
<section class="section">
  <h2>All Files</h2>
  <p class="muted" style="margin-bottom:8px">{_esc(len(file_list))} files sorted by risk score. Click column headers to sort.</p>
  <div class="card" style="padding:0;overflow-x:auto">
  <table id="file-table">
  <thead><tr>
    <th class="sortable" data-col="0">File <span class="sort-arrow"></span></th>
    <th class="sortable num" data-col="1" data-num="1">Lines <span class="sort-arrow"></span></th>
    <th class="sortable" data-col="2">Risk <span class="sort-arrow"></span></th>
    <th class="sortable num" data-col="3" data-num="1">Score <span class="sort-arrow"></span></th>
    <th class="sortable num" data-col="4" data-num="1">Issues <span class="sort-arrow"></span></th>
    <th>Factors</th>
  </tr></thead>
  <tbody>{file_table_rows()}</tbody>
  </table>
  </div>
</section>

{_SORT_JS}

<!-- file risks -->
<section class="section">
  <h2>Top File Risks By Surface</h2>
  {grouped_risk_sections()}
</section>

<!-- review signals -->
<section class="section">
  <h2>Review Signals</h2>
  <p class="muted" style="margin-bottom:10px"><strong>Confirmed issues:</strong> {_esc(confirmed_issues)} &middot; <strong>Total signals:</strong> {_esc(len(issues))}</p>
  <div class="card" style="padding:0;overflow:hidden">
  <table><thead><tr><th>Severity</th><th>Message</th><th>File</th></tr></thead><tbody>{issue_rows()}</tbody></table>
  </div>
</section>

<!-- agent prompt -->
<section class="section">
  <div class="prompt-wrap">
    <h2 style="margin-bottom:4px">Agent Prompt</h2>
    <p class="muted" style="margin-bottom:8px">Recommended prompt for an AI agent working on this codebase.</p>
    <pre>{_esc(markdown_prompt or 'No prompt generated - run a full scan to generate one.')}</pre>
  </div>
</section>

<!-- footer -->
<hr class="divider">
<footer class="muted" style="font-size:12px;padding-bottom:12px;text-align:center">
  Generated by Sentinel v1.1.0 &middot; {_esc(scan_mode)} &middot; {_esc(perf.get('duration_seconds', 0))}s &middot; {_esc(result.get('timestamp', ''))}
</footer>

</main>
</body>
</html>
"""
