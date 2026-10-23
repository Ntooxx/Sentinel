"""Self-contained HTML report renderer.

One file, inline CSS, no JavaScript, no network. The report is a rendering of
the same stable report object as every other format; nothing here recomputes
quality. Every repository-controlled string goes through ``html.escape``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from html import escape
from typing import Any

from ..utils import format_bytes, format_ms

CSS = """
:root {
  color-scheme: dark;
  --bg: #070b16;
  --bg-2: #0b1120;
  --surface: #0f1626;
  --surface-2: #141d33;
  --surface-3: #1b2542;
  --text: #e8eefb;
  --muted: #93a3c4;
  --faint: #5f6e8e;
  --border: #22304f;
  --border-2: #2f3e64;
  --accent: #38bdf8;
  --accent-2: #818cf8;
  --violet: #a78bfa;
  --good: #34d399;
  --warn: #fbbf24;
  --bad: #f87171;
  --info: #a5b4fc;
  --radius: 16px;
  --mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body {
  margin: 0;
  background:
    radial-gradient(900px 520px at 12% -8%, rgba(56, 189, 248, 0.16), transparent 60%),
    radial-gradient(760px 480px at 92% 4%, rgba(167, 139, 250, 0.14), transparent 62%),
    var(--bg);
  color: var(--text);
  font: 15px/1.6 ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", sans-serif;
  -webkit-font-smoothing: antialiased;
}
a { color: var(--accent); text-decoration: none; }
code, .mono { font-family: var(--mono); }

/* ---- top bar ---- */
.topbar {
  position: sticky; top: 0; z-index: 20;
  display: flex; align-items: center; gap: 18px; flex-wrap: wrap;
  padding: 12px max(20px, calc((100vw - 1180px) / 2));
  background: rgba(7, 11, 22, 0.82);
  backdrop-filter: blur(14px);
  border-bottom: 1px solid var(--border);
}
.brand { display: flex; align-items: center; gap: 10px; font-weight: 800; letter-spacing: 0.14em; font-size: 13px; }
.brand .dot {
  width: 11px; height: 11px; border-radius: 50%;
  background: radial-gradient(circle at 30% 30%, var(--accent), var(--accent-2));
  box-shadow: 0 0 14px rgba(56, 189, 248, 0.85);
}
.brand .repofile { color: var(--muted); font-weight: 600; letter-spacing: 0; text-transform: none; }
.navlinks { display: flex; gap: 4px; flex-wrap: wrap; margin-left: auto; }
.navlinks a {
  color: var(--muted); font-size: 12.5px; padding: 6px 10px; border-radius: 8px;
  border: 1px solid transparent;
}
.navlinks a:hover { color: var(--text); background: var(--surface-2); border-color: var(--border); }

/* ---- layout ---- */
main { max-width: 1180px; margin: 0 auto; padding: 26px 20px 70px; }
section { margin-top: 34px; scroll-margin-top: 74px; }
h2.sec {
  display: flex; align-items: center; gap: 10px;
  font-size: 12.5px; letter-spacing: 0.16em; text-transform: uppercase;
  color: var(--accent); margin: 0 0 14px; font-weight: 700;
}
h2.sec::after { content: ""; flex: 1; height: 1px; background: linear-gradient(90deg, var(--border-2), transparent); }
h2.sec .count {
  color: var(--faint); letter-spacing: 0.02em; font-size: 11.5px; font-weight: 600;
  border: 1px solid var(--border); border-radius: 999px; padding: 2px 8px;
}

/* ---- hero ---- */
.hero {
  position: relative; overflow: hidden;
  display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 26px; align-items: center;
  padding: 26px 28px; border: 1px solid var(--border); border-radius: 22px;
  background:
    linear-gradient(150deg, rgba(56, 189, 248, 0.10), transparent 46%),
    linear-gradient(180deg, var(--surface), var(--surface-2));
  box-shadow: 0 26px 60px rgba(0, 0, 0, 0.42);
}
.hero::before {
  content: ""; position: absolute; inset: 0 auto 0 0; width: 4px;
  background: linear-gradient(180deg, var(--accent), var(--accent-2), var(--violet));
}
.hero h1 { margin: 0 0 4px; font-size: 25px; letter-spacing: 0.02em; }
.hero .kicker { font-size: 11.5px; letter-spacing: 0.2em; text-transform: uppercase; color: var(--faint); font-weight: 700; }
.hero .repo { font-size: 20px; font-weight: 700; margin: 8px 0 2px; word-break: break-word; }
.hero .commit { color: var(--muted); font-size: 13px; margin-bottom: 14px; }
.hero .headline { color: var(--muted); font-size: 14px; max-width: 60ch; }
.chips { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 16px; }
.chip {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 5px 10px; border-radius: 999px; border: 1px solid var(--border);
  background: rgba(20, 29, 51, 0.7); color: var(--muted); font-size: 12px;
}
.chip b { color: var(--text); font-weight: 600; }
.chip .k { color: var(--faint); }

.scorewrap { display: flex; flex-direction: column; align-items: center; gap: 8px; min-width: 190px; }
.ring { position: relative; width: 148px; height: 148px; }
.ring svg { transform: rotate(-90deg); display: block; }
.ring .inner {
  position: absolute; inset: 0; display: flex; flex-direction: column;
  align-items: center; justify-content: center;
}
.ring .num { font-size: 40px; font-weight: 800; line-height: 1; letter-spacing: -0.02em; }
.ring .of { font-size: 11px; color: var(--faint); letter-spacing: 0.14em; }
.bandpill {
  font-size: 13px; font-weight: 800; letter-spacing: 0.16em; text-transform: uppercase;
  padding: 6px 14px; border-radius: 999px; border: 1px solid currentColor;
}
.bandpill.maintained { color: var(--good); background: rgba(52, 211, 153, 0.10); }
.bandpill.adequate { color: var(--accent); background: rgba(56, 189, 248, 0.10); }
.bandpill.weak { color: var(--warn); background: rgba(251, 191, 36, 0.10); }
.bandpill.unmaintained { color: var(--bad); background: rgba(248, 113, 113, 0.10); }
.bandpill.na { color: var(--muted); background: rgba(147, 163, 196, 0.10); }
.bandnote { font-size: 11.5px; color: var(--faint); }

/* ---- stat cards ---- */
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(148px, 1fr)); gap: 12px; }
.stat {
  border: 1px solid var(--border); border-radius: var(--radius); padding: 14px 16px;
  background: linear-gradient(180deg, var(--surface), var(--surface-2));
}
.stat .v { font-size: 23px; font-weight: 700; letter-spacing: -0.01em; }
.stat .v small { font-size: 13px; color: var(--muted); font-weight: 600; }
.stat .l { font-size: 11.5px; letter-spacing: 0.1em; text-transform: uppercase; color: var(--faint); margin-top: 3px; }

/* ---- dimensions ---- */
.dimgrid { display: grid; grid-template-columns: 320px minmax(0, 1fr); gap: 20px; align-items: start; }
.panel {
  border: 1px solid var(--border); border-radius: var(--radius);
  background: linear-gradient(180deg, var(--surface), var(--surface-2)); padding: 18px;
}
.radarwrap { display: flex; flex-direction: column; align-items: center; }
.radarwrap .cap { font-size: 11.5px; color: var(--faint); letter-spacing: 0.1em; text-transform: uppercase; margin-top: 6px; }
.dims { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 12px; }
.dim { border: 1px solid var(--border); border-radius: var(--radius); padding: 15px 16px; background: var(--surface); }
.dim .top { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.dim .name { display: flex; align-items: center; gap: 8px; font-weight: 650; font-size: 14.5px; }
.dim .icon { font-size: 16px; }
.grade {
  font-weight: 800; font-size: 13px; padding: 3px 10px; border-radius: 8px;
  border: 1px solid currentColor; letter-spacing: 0.04em;
}
.grade.good { color: var(--good); background: rgba(52, 211, 153, 0.08); }
.grade.accent { color: var(--accent); background: rgba(56, 189, 248, 0.08); }
.grade.warn { color: var(--warn); background: rgba(251, 191, 36, 0.08); }
.grade.bad { color: var(--bad); background: rgba(248, 113, 113, 0.08); }
.grade.muted { color: var(--muted); background: rgba(147, 163, 196, 0.08); }
.bar { height: 6px; border-radius: 999px; background: var(--surface-3); overflow: hidden; margin: 12px 0 10px; }
.bar > i { display: block; height: 100%; border-radius: 999px; }
.bar > i.good { background: linear-gradient(90deg, #059669, var(--good)); }
.bar > i.accent { background: linear-gradient(90deg, #0284c7, var(--accent)); }
.bar > i.warn { background: linear-gradient(90deg, #d97706, var(--warn)); }
.bar > i.bad { background: linear-gradient(90deg, #dc2626, var(--bad)); }
.bar > i.muted { background: var(--surface-3); }
.dim p { margin: 0; color: var(--muted); font-size: 13.5px; }
.dim .conf { color: var(--faint); font-size: 11.5px; margin-top: 8px; }
.evidence { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
.evidence code {
  font-size: 11.5px; color: var(--muted); background: var(--bg-2);
  border: 1px solid var(--border); border-radius: 7px; padding: 3px 7px;
}

/* ---- findings ---- */
.findings { display: grid; gap: 12px; }
.finding {
  position: relative; border: 1px solid var(--border); border-radius: var(--radius);
  padding: 16px 18px 16px 20px; background: var(--surface); overflow: hidden;
}
.finding::before { content: ""; position: absolute; inset: 0 auto 0 0; width: 4px; background: var(--faint); }
.finding.sev-high::before, .finding.sev-critical::before { background: linear-gradient(180deg, #ef4444, var(--bad)); }
.finding.sev-medium::before { background: linear-gradient(180deg, #d97706, var(--warn)); }
.finding.sev-low::before { background: linear-gradient(180deg, #0284c7, var(--accent)); }
.finding.sev-info::before { background: linear-gradient(180deg, #6366f1, var(--info)); }
.finding .fhead { display: flex; align-items: flex-start; gap: 10px; justify-content: space-between; flex-wrap: wrap; }
.finding h3 { margin: 0; font-size: 15.5px; font-weight: 650; }
.tags { display: flex; gap: 6px; flex-wrap: wrap; }
.tag {
  font-size: 10.5px; letter-spacing: 0.08em; text-transform: uppercase; font-weight: 700;
  padding: 3px 8px; border-radius: 6px; border: 1px solid var(--border-2); color: var(--muted);
}
.tag.sev-high, .tag.sev-critical { color: var(--bad); border-color: rgba(248, 113, 113, 0.4); }
.tag.sev-medium { color: var(--warn); border-color: rgba(251, 191, 36, 0.4); }
.tag.sev-low { color: var(--accent); border-color: rgba(56, 189, 248, 0.4); }
.tag.sev-info { color: var(--info); border-color: rgba(165, 180, 252, 0.4); }
.where { font-family: var(--mono); font-size: 12.5px; color: var(--accent); margin-top: 8px; word-break: break-all; }
.where .rule { color: var(--faint); }
.finding ul.ev { margin: 10px 0 0; padding-left: 18px; color: var(--muted); font-size: 13px; }
.finding ul.ev li { margin: 3px 0; }
.finding .why { margin-top: 10px; color: var(--muted); font-size: 13.5px; }
.finding .next {
  margin-top: 12px; padding: 10px 12px; border-radius: 10px;
  background: rgba(56, 189, 248, 0.07); border: 1px solid rgba(56, 189, 248, 0.22);
  font-size: 13.5px;
}
.finding .next b { color: var(--accent); letter-spacing: 0.06em; font-size: 11px; text-transform: uppercase; }
.empty {
  border: 1px dashed var(--border-2); border-radius: var(--radius);
  padding: 22px; color: var(--muted); text-align: center;
}

/* ---- actions ---- */
.steps { display: grid; gap: 10px; counter-reset: step; }
.step {
  display: grid; grid-template-columns: 40px minmax(0, 1fr) auto; gap: 14px; align-items: center;
  border: 1px solid var(--border); border-radius: var(--radius); padding: 13px 16px;
  background: linear-gradient(180deg, var(--surface), var(--surface-2));
}
.step .n {
  counter-increment: step; width: 34px; height: 34px; border-radius: 10px;
  display: flex; align-items: center; justify-content: center; font-weight: 800; font-size: 15px;
  color: var(--bg); background: linear-gradient(135deg, var(--accent), var(--accent-2));
}
.step .n::before { content: counter(step); }
.step .t { font-weight: 650; font-size: 14.5px; }
.step .w { color: var(--muted); font-size: 12.5px; margin-top: 2px; }
.meta2 { display: flex; gap: 6px; }
.pill { font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.08em; font-weight: 700; padding: 4px 9px; border-radius: 999px; border: 1px solid var(--border-2); color: var(--muted); }
.pill.high { color: var(--bad); border-color: rgba(248, 113, 113, 0.4); }
.pill.medium { color: var(--warn); border-color: rgba(251, 191, 36, 0.4); }
.pill.low { color: var(--good); border-color: rgba(52, 211, 153, 0.4); }

/* ---- architecture / coverage ---- */
.cols { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 12px; }
.kv { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 10px; }
.kv code { font-size: 12px; color: var(--muted); background: var(--bg-2); border: 1px solid var(--border); border-radius: 8px; padding: 4px 8px; }
.blk { border: 1px solid var(--border); border-radius: var(--radius); padding: 15px 16px; background: var(--surface); }
.blk .h { font-size: 11.5px; letter-spacing: 0.12em; text-transform: uppercase; color: var(--faint); font-weight: 700; margin-bottom: 6px; }
.blk .big { font-size: 17px; font-weight: 700; }
.blk p { margin: 6px 0 0; color: var(--muted); font-size: 13px; }

/* ---- changes ---- */
.delta { display: flex; gap: 12px; flex-wrap: wrap; }
.delta .d { border: 1px solid var(--border); border-radius: 12px; padding: 11px 15px; background: var(--surface); font-size: 13px; }
.delta .d b { font-size: 18px; font-weight: 700; display: block; }
.delta .up b { color: var(--good); } .delta .down b { color: var(--bad); }

/* ---- notes ---- */
details.notes { border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); padding: 12px 16px; }
details.notes summary { cursor: pointer; color: var(--muted); font-size: 13px; }
details.notes ul { margin: 12px 0 0; padding-left: 20px; color: var(--muted); font-size: 13px; }
details.notes li { margin: 5px 0; }
footer { margin-top: 40px; padding-top: 18px; border-top: 1px solid var(--border); color: var(--faint); font-size: 12px; display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap; }

@media (max-width: 860px) {
  .dimgrid { grid-template-columns: 1fr; }
  .hero { grid-template-columns: 1fr; }
  .scorewrap { flex-direction: row; justify-content: flex-start; }
  .step { grid-template-columns: 34px minmax(0, 1fr); }
  .step .meta2 { grid-column: 2; }
}
@media print {
  :root { color-scheme: light; }
  body { background: #fff; color: #111; }
  .topbar { position: static; background: #fff; }
  .finding, .dim, .stat, .panel, .step, .blk { break-inside: avoid; }
}
"""

_BAND_LABEL = {
    "maintained": "maintained",
    "adequate": "adequate",
    "weak": "weak",
    "unmaintained": "unmaintained",
}

_DIM_ICON = {
    "architecture": "\U0001F3D7",
    "maintainability": "\U0001F9F9",
    "tests": "\U0001F9EA",
    "docs": "\U0001F4DA",
    "dependencies": "\U0001F4E6",
    "security": "\U0001F510",
}


def _band_class(band: str) -> str:
    label = (band or "").strip().lower()
    return label if label in _BAND_LABEL else "na"


def _severity_class(severity: str) -> str:
    return {"critical": "sev-critical", "high": "sev-high", "medium": "sev-medium", "low": "sev-low"}.get(
        str(severity), "sev-info"
    )


def _dimension_grade_class(grade: str) -> str:
    first = (grade or "")[:1].upper()
    if first == "A":
        return "good"
    if first == "B":
        return "accent"
    if first in {"C", "D"}:
        return "warn"
    if first == "F":
        return "bad"
    return "muted"


def _confidence_pill(confidence: str) -> str:
    label = escape(str(confidence or "unknown"))
    tone = {"high": "low", "medium": "medium", "low": "high"}.get(str(confidence).lower(), "medium")
    return f'<span class="pill {tone}">{label} confidence</span>'


def _num(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _score(value: Any) -> int:
    try:
        return max(0, min(100, int(round(float(value)))))
    except (TypeError, ValueError):
        return 0


def _ring(score: int, band_class: str, *, size: int = 148) -> str:
    color = {
        "maintained": "var(--good)",
        "adequate": "var(--accent)",
        "weak": "var(--warn)",
        "unmaintained": "var(--bad)",
    }.get(band_class, "var(--muted)")
    stroke = 10
    radius = (size - stroke) / 2
    circumference = 2 * math.pi * radius
    filled = circumference * (score / 100.0)
    centre = size / 2
    return (
        f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}" role="img" '
        f'aria-label="overall score {score} out of 100">'
        f'<circle cx="{centre}" cy="{centre}" r="{radius}" fill="none" '
        f'stroke="var(--surface-3)" stroke-width="{stroke}"/>'
        f'<circle cx="{centre}" cy="{centre}" r="{radius}" fill="none" '
        f'stroke="{color}" stroke-width="{stroke}" stroke-linecap="round" '
        f'stroke-dasharray="{filled:.2f} {circumference:.2f}"/></svg>'
    )


_DIM_SHORT = {
    "architecture": "Arch",
    "maintainability": "Maint",
    "tests": "Tests",
    "docs": "Docs",
    "dependencies": "Deps",
    "security": "Sec",
}


def _radar(dimensions: Sequence[Mapping[str, Any]]) -> str:
    assessed = [d for d in dimensions if d.get("assessed", True)]
    if len(assessed) < 3:
        return ""
    width, height = 280, 266
    cx, cy, radius = 140, 130, 78
    count = len(assessed)
    angles = [(-math.pi / 2) + (2 * math.pi * index / count) for index in range(count)]

    rings: list[str] = []
    for fraction in (0.25, 0.5, 0.75, 1.0):
        points = " ".join(
            f"{cx + radius * fraction * math.cos(angle):.1f},{cy + radius * fraction * math.sin(angle):.1f}"
            for angle in angles
        )
        rings.append(f'<polygon points="{points}" fill="none" stroke="var(--border)" stroke-width="1"/>')
    spokes = "".join(
        f'<line x1="{cx}" y1="{cy}" x2="{cx + radius * math.cos(a):.1f}" y2="{cy + radius * math.sin(a):.1f}" '
        f'stroke="var(--border)" stroke-width="1"/>'
        for a in angles
    )

    value_points = []
    dots = []
    labels = []
    for dim, angle in zip(assessed, angles, strict=True):
        score = _score(dim.get("score", 0))
        px = cx + radius * (score / 100.0) * math.cos(angle)
        py = cy + radius * (score / 100.0) * math.sin(angle)
        value_points.append(f"{px:.1f},{py:.1f}")
        dots.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="2.6" fill="var(--accent)"/>')
        lx = cx + (radius + 22) * math.cos(angle)
        ly = cy + (radius + 22) * math.sin(angle)
        anchor = "middle"
        if math.cos(angle) > 0.35:
            anchor = "start"
        elif math.cos(angle) < -0.35:
            anchor = "end"
        dim_id = str(dim.get("id", ""))
        name = _DIM_SHORT.get(dim_id, dim_id.replace("_", " ").title()[:6])
        labels.append(
            f'<text x="{lx:.1f}" y="{ly:.1f}" fill="var(--muted)" font-size="11" font-weight="600" '
            f'text-anchor="{anchor}" dominant-baseline="middle">{escape(name)}</text>'
        )
    return (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="dimension score radar">'
        f'{"".join(rings)}{spokes}'
        f'<polygon points="{" ".join(value_points)}" fill="rgba(56, 189, 248, 0.22)" '
        f'stroke="var(--accent)" stroke-width="2"/>'
        f'{"".join(dots)}{"".join(labels)}</svg>'
    )


def render_html(
    report: Mapping[str, Any],
    *,
    all_findings: bool = False,
    refresh_seconds: int | None = None,
) -> str:
    project = report.get("project", {}) or {}
    scan = report.get("scan", {}) or {}
    summary = report.get("summary", {}) or {}
    metrics = report.get("metrics", {}) or {}
    git = project.get("git", {}) or {}
    name = escape(str(project.get("name") or "repository"))

    commit = git.get("commit")
    branch = git.get("branch")
    dirty = bool(git.get("dirty"))
    commit_text = ""
    if commit:
        commit_text = f"<code>{escape(str(commit))}</code>"
        if branch:
            commit_text += f" on <code>{escape(str(branch))}</code>"
        if dirty:
            commit_text += ' <span class="chip"><b>dirty</b></span>'

    band_raw = str(summary.get("band") or "n/a")
    band_class = _band_class(band_raw)
    provisional = " (provisional)" if summary.get("provisional") else ""
    headline = escape(str(summary.get("headline") or ""))
    overall = _score(summary.get("overall_score", 0))
    refresh = f'<meta http-equiv="refresh" content="{int(refresh_seconds)}">' if refresh_seconds else ""

    files_total = _num(scan.get("files_total"))
    source_lines = _num(metrics.get("source_lines"))
    test_lines = _num(metrics.get("test_lines"))
    max_complexity = _num(metrics.get("max_complexity"))

    hero_chips = [
        f'<span class="chip"><span class="k">band</span> <b>{escape(band_raw)}{provisional}</b></span>',
        f'<span class="chip"><span class="k">archetype</span> <b>{escape(str(metrics.get("archetype") or "n/a"))}</b></span>',
        f'<span class="chip"><span class="k">files</span> <b>{files_total:,}</b></span>',
        f'<span class="chip"><span class="k">lines</span> <b>{source_lines + test_lines:,}</b></span>',
        f'<span class="chip"><span class="k">scan</span> <b>{escape(str(scan.get("mode") or "cold"))} {format_ms(_num(scan.get("duration_ms")))}</b></span>',
        f'<span class="chip"><span class="k">index</span> <b>{format_bytes(_num(scan.get("index_size_bytes")))}</b></span>',
    ]

    ratio = metrics.get("test_to_source_file_ratio")
    ratio_value = f"{ratio}<small>:1</small>" if ratio not in (None, "") else "<small>n/a</small>"
    stat_cards = [
        ("Source files", f"{_num(metrics.get('source_files')):,}"),
        ("Test files", f"{_num(metrics.get('test_files')):,}"),
        ("Test ratio", ratio_value),
        ("Max complexity", f"{max_complexity}"),
        ("Oversized files", f"{_num(metrics.get('oversized_source_files'))}"),
        ("TODOs", f"{_num(metrics.get('source_todos'))}"),
        ("Parse errors", f"{_num(metrics.get('parse_errors'))}"),
    ]
    stats_html = "".join(
        f'<div class="stat"><div class="v">{value}</div><div class="l">{label}</div></div>'
        for label, value in stat_cards
    )

    dimensions = list(report.get("dimensions", []) or [])
    dim_cards: list[str] = []
    for dimension in dimensions:
        dim_id = str(dimension.get("id", ""))
        label = escape(dim_id.replace("_", " ").title())
        icon = _DIM_ICON.get(dim_id, "\u2022")
        grade = escape(str(dimension.get("grade") or "n/a"))
        grade_class = _dimension_grade_class(grade)
        score = _score(dimension.get("score", 0))
        assessed = bool(dimension.get("assessed", True))
        dim_summary = escape(str(dimension.get("summary") or ""))
        body = dim_summary if assessed else "Not assessed for this repository."
        bar = (
            f'<div class="bar"><i class="{grade_class}" style="width:{score}%"></i></div>'
            if assessed
            else '<div class="bar"><i class="muted" style="width:100%"></i></div>'
        )
        evidence = "".join(
            f"<code>{escape(str(item))}</code>" for item in (dimension.get("evidence", []) or [])[:8]
        )
        confidence = escape(str(dimension.get("confidence") or "unknown"))
        dim_cards.append(
            f'<div class="dim"><div class="top">'
            f'<div class="name"><span class="icon">{icon}</span>{label}</div>'
            f'<span class="grade {grade_class}">{grade}</span></div>'
            f"{bar}<p>{body}</p>"
            + (f'<div class="evidence">{evidence}</div>' if evidence else "")
            + f'<div class="conf">score {score}/100 | {confidence} confidence</div>'
            + "</div>"
        )
    radar = _radar(dimensions)

    findings = list(report.get("findings", []) or [])
    shown = findings if all_findings else findings[:5]
    finding_blocks: list[str] = []
    for finding in shown:
        severity = str(finding.get("severity") or "medium")
        location = escape(str(finding.get("file") or "repository"))
        line = _num(finding.get("line"))
        if line > 1:
            location += f":{line}"
        dimension = escape(str(finding.get("dimension") or ""))
        rule_id = escape(str(finding.get("rule_id") or "rule"))
        confidence = escape(str(finding.get("confidence") or ""))
        evidence = "".join(f"<li>{escape(str(item))}</li>" for item in finding.get("evidence", []) or [])
        why = escape(str(finding.get("why") or ""))
        recommendation = escape(str(finding.get("recommendation") or ""))
        finding_blocks.append(
            f'<article class="finding {_severity_class(severity)}">'
            f'<div class="fhead"><h3>{escape(str(finding.get("title") or ""))}</h3>'
            f'<div class="tags"><span class="tag {_severity_class(severity)}">{escape(severity)}</span>'
            + (f'<span class="tag">{dimension}</span>' if dimension else "")
            + (f'<span class="tag">{confidence}</span>' if confidence else "")
            + "</div></div>"
            f'<div class="where"><span class="rule">{rule_id}</span> - {location}</div>'
            + (f'<ul class="ev">{evidence}</ul>' if evidence else "")
            + (f'<div class="why">{why}</div>' if why else "")
            + (f'<div class="next"><b>Next</b><br>{recommendation}</div>' if recommendation else "")
            + "</article>"
        )
    if not shown:
        finding_blocks.append(
            '<div class="empty">No findings. This is either a clean repository or a small surface.</div>'
        )

    steps = "".join(
        f'<div class="step"><div class="n"></div><div><div class="t">{escape(str(action.get("title") or ""))}</div>'
        + (f'<div class="w">{escape(str(action.get("why") or ""))}</div>' if action.get("why") else "")
        + "</div>"
        f'<div class="meta2"><span class="pill {escape(str(action.get("impact") or "medium"))}">'
        f'{escape(str(action.get("impact") or "medium"))} impact</span>'
        f'<span class="pill {escape(str(action.get("effort") or "medium"))}">'
        f'{escape(str(action.get("effort") or "medium"))} effort</span></div></div>'
        for action in report.get("actions", []) or []
    )
    if not steps:
        steps = '<div class="empty">No action items.</div>'

    architecture = report.get("architecture", {}) or {}
    detected = '<span class="muted">none detected</span>'
    components = "".join(f"<code>{escape(str(c))}</code>" for c in (architecture.get("components", []) or [])[:24])
    entry_points = "".join(f"<code>{escape(str(p))}</code>" for p in (architecture.get("entry_points", []) or [])[:12])
    hotspots = "".join(f"<code>{escape(str(p))}</code>" for p in (architecture.get("hotspots", []) or [])[:8])
    archetype = escape(str(architecture.get("archetype") or "unknown"))
    arch_cards = (
        f'<div class="blk"><div class="h">Archetype</div><div class="big">{archetype}</div></div>'
        f'<div class="blk"><div class="h">Components</div><div class="kv">{components or detected}</div></div>'
        f'<div class="blk"><div class="h">Entry points</div><div class="kv">{entry_points or detected}</div></div>'
        f'<div class="blk"><div class="h">Hotspots</div><div class="kv">{hotspots or detected}</div></div>'
    )

    coverage = scan.get("coverage", {}) or {}
    coverage_cards = "".join(
        f'<div class="blk"><div class="h">{escape(label)}</div><div class="big">{escape(str(value))}</div></div>'
        for label, value in (
            ("Source", coverage.get("source", "n/a")),
            ("Tests", coverage.get("tests", "n/a")),
            ("Docs", coverage.get("docs", "n/a")),
            ("Vendor", coverage.get("vendor", "excluded")),
            ("Generated", coverage.get("generated", "excluded")),
            ("Examples", coverage.get("examples", "excluded")),
        )
    )

    exclusions = report.get("exclusions", {}) or {}
    exclusion_note = escape(
        f"vendor {_num(exclusions.get('vendor'))} | generated {_num(exclusions.get('generated'))} | "
        f"docs translations {_num(exclusions.get('docs_translations'))} | examples {_num(exclusions.get('examples'))}. "
        + str(exclusions.get("reason") or "")
    )

    changes = report.get("changes", {}) or {}
    if changes.get("since_scan_id") is None:
        changes_html = (
            '<div class="blk"><div class="h">Baseline</div>'
            '<p>Initial scan - no previous snapshot to compare. This run established the baseline for future deltas.</p></div>'
        )
    else:
        delta = _num(changes.get("quality_delta"))
        tone = "up" if delta > 0 else "down" if delta < 0 else ""
        delta_label = f"{delta:+d}" if delta else "0"
        changes_html = (
            '<div class="delta">'
            f'<div class="d"><b>{_num(changes.get("added"))}</b>files added</div>'
            f'<div class="d"><b>{_num(changes.get("modified"))}</b>files modified</div>'
            f'<div class="d"><b>{_num(changes.get("deleted"))}</b>files deleted</div>'
            f'<div class="d {tone}"><b>{escape(delta_label)}</b>quality delta</div>'
            "</div>"
        )

    limitations = "".join(f"<li>{escape(str(item))}</li>" for item in report.get("limitations", []) or [])
    generated_at = escape(str(report.get("generated_at") or ""))
    rules_version = escape(str(report.get("rules_version") or ""))

    nav_items = [
        ("glance", "At a glance"),
        ("dimensions", "Dimensions"),
        ("findings", "Findings"),
        ("actions", "Action plan"),
        ("architecture", "Architecture"),
        ("coverage", "Coverage"),
        ("changes", "Changes"),
        ("notes", "Notes"),
    ]
    nav = "".join(f'<a href="#{anchor}">{escape(label)}</a>' for anchor, label in nav_items)

    dimensions_section = (
        f'<section id="dimensions"><h2 class="sec">Dimensions <span class="count">{len(dimensions)}</span></h2>'
        f'<div class="dimgrid">'
        + (f'<div class="panel radarwrap">{radar}<div class="cap">Score radar</div></div>' if radar else "")
        + f'<div class="dims">{"".join(dim_cards)}</div></div></section>'
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{refresh}
<title>Sentinel Quality Report - {name}</title>
<style>{CSS}</style>
</head>
<body>
<div class="topbar">
  <div class="brand"><span class="dot"></span>SENTINEL <span class="repofile">{name}</span></div>
  <nav class="navlinks">{nav}</nav>
</div>
<main>
  <header class="hero">
    <div>
      <div class="kicker">Sentinel Quality Report</div>
      <h1>SENTINEL QUALITY REPORT</h1>
      <div class="repo">{name}</div>
      {f'<div class="commit">{commit_text}</div>' if commit_text else ''}
      <div class="headline">{headline}</div>
      <div class="chips">{''.join(hero_chips)}</div>
    </div>
    <div class="scorewrap">
      <div class="ring">{_ring(overall, band_class)}
        <div class="inner"><div class="num">{overall}</div><div class="of">/ 100</div></div>
      </div>
      <div class="bandpill {band_class}">{escape(band_raw) if band_raw != 'n/a' else 'n/a'}</div>
      <div class="bandnote">quality band{provisional} - {_confidence_pill(str(summary.get('confidence') or 'unknown'))}</div>
    </div>
  </header>

  <section id="glance">
    <h2 class="sec">At a glance</h2>
    <div class="stats">{stats_html}</div>
  </section>

  {dimensions_section}

  <section id="findings">
    <h2 class="sec">{'Findings' if all_findings else 'What to fix first'} <span class="count">{len(shown)} of {len(findings)}</span></h2>
    <div class="findings">{''.join(finding_blocks)}</div>
  </section>

  <section id="actions">
    <h2 class="sec">Action plan <span class="count">{len(report.get('actions', []) or [])}</span></h2>
    <div class="steps">{steps}</div>
  </section>

  <section id="architecture">
    <h2 class="sec">Architecture</h2>
    <div class="cols">{arch_cards}</div>
  </section>

  <section id="coverage">
    <h2 class="sec">Coverage</h2>
    <div class="cols">{coverage_cards}</div>
    <p class="bandnote" style="margin-top:12px">{exclusion_note}</p>
  </section>

  <section id="changes">
    <h2 class="sec">Changed since last scan</h2>
    {changes_html}
  </section>

  <section id="notes">
    <h2 class="sec">Notes and limitations</h2>
    <details class="notes" open>
      <summary>{len(report.get('limitations', []) or [])} limitation(s) - what this report does not claim</summary>
      <ul>{limitations}</ul>
    </details>
  </section>

  <footer>
    <span>Generated by Sentinel 2.0. Paths are project-relative. Local-only: no network, no telemetry.</span>
    <span>rules {rules_version} | {generated_at}</span>
  </footer>
</main>
</body>
</html>
"""
