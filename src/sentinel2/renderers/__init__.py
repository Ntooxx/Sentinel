"""Report renderers."""

from __future__ import annotations

from typing import Any

from .html import render_html
from .json import render_json
from .markdown import render_markdown
from .text import render_text


def render(
    report: dict[str, Any],
    fmt: str,
    *,
    color: bool = True,
    all_findings: bool = False,
    refresh_seconds: int | None = None,
) -> str:
    if fmt == "text":
        return render_text(report, color=color, all_findings=all_findings)
    if fmt == "md":
        return render_markdown(report, all_findings=all_findings)
    if fmt == "html":
        return render_html(report, all_findings=all_findings, refresh_seconds=refresh_seconds)
    if fmt == "json":
        return render_json(report)
    raise ValueError(f"unknown format: {fmt}")


__all__ = ["render", "render_html", "render_json", "render_markdown", "render_text"]
