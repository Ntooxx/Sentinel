#!/usr/bin/env python3
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Dict, Optional

# Ensure local src/ takes priority over PYTHONPATH or other installations
_self_dir = Path(__file__).resolve().parent
if str(_self_dir) not in sys.path:
    sys.path.insert(0, str(_self_dir))

from orchestrator import SentinelAgent  # noqa: E402
from utils import ensure_dir  # noqa: E402


def analyze_repository_url(
    repo_source: str,
    *,
    output_dir: str | Path | None = None,
    keep_clone: bool = False,
    fast_mode: bool = True,
    html_report: bool = True,
    timeout: int = 300,
) -> Dict[str, Any]:
    """Clone a git repository source, scan it, and save a portable report bundle."""

    safe_name = _safe_repo_name(repo_source)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = Path(output_dir).expanduser().resolve() if output_dir else (Path.cwd() / "sentinel-url-reports" / f"{safe_name}_{timestamp}").resolve()
    ensure_dir(report_dir)

    temp_handle: tempfile.TemporaryDirectory[str] | None = None
    if keep_clone:
        clone_parent = report_dir / "repository"
        ensure_dir(clone_parent)
    else:
        temp_handle = tempfile.TemporaryDirectory(prefix="sentinel_repo_")
        clone_parent = Path(temp_handle.name)

    clone_dir = clone_parent / safe_name
    source = _normalize_git_source(repo_source)
    command = ["git", "-c", "core.longpaths=true", "clone", "--depth", "1", source, str(clone_dir)]
    started = perf_counter()
    clone_result: subprocess.CompletedProcess[str] | None = None
    agent: SentinelAgent | None = None
    try:
        if shutil.which("git") is None:
            raise RuntimeError("git is required for analyze-url but was not found on PATH")
        clone_result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if clone_result.returncode != 0:
            detail = clone_result.stderr.strip() or clone_result.stdout.strip() or "git clone failed"
            if "Filename too long" in detail or "unable to checkout working tree" in detail:
                detail += (
                    "\nSentinel already retried with git -c core.longpaths=true. "
                    "On Windows, enable long paths with `git config --global core.longpaths true` "
                    "and the Windows LongPathsEnabled policy, or choose a shorter output directory."
                )
            raise RuntimeError(detail)

        agent = SentinelAgent(str(clone_dir))
        scan = agent.scan_once(
            print_report=False,
            fast_mode=fast_mode,
            include_suggestions=True,
            use_git_discovery=True,
        )
        knowledge_context = agent.knowledge.export_context(budget="medium")
        markdown = agent.reporter.render_markdown(scan, knowledge_context=knowledge_context)
        markdown_path = report_dir / "SENTINEL_REPORT.md"
        agent.reporter.save_markdown(markdown, markdown_path)

        html_path: Path | None = None
        if html_report:
            html_text = agent.reporter.render_html(scan, knowledge_context=knowledge_context)
            html_path = report_dir / "SENTINEL_REPORT.html"
            agent.reporter.save_markdown(html_text, html_path)

        context_pack = agent.build_context_pack(budget="small")
        context_path = report_dir / "CONTEXT.md"
        agent.reporter.save_markdown(agent.reporter.render_context_pack(context_pack), context_path)

        prompt_pack = agent.build_prompt_pack(scan, goal="next", budget="small")
        prompt_path = report_dir / "NEXT_PROMPT.md"
        agent.reporter.save_markdown(agent.reporter.render_prompt_pack(prompt_pack), prompt_path)

        summary = {
            "repo_source": repo_source,
            "clone_source": source,
            "clone_dir": str(clone_dir) if keep_clone else None,
            "report_dir": str(report_dir),
            "markdown_report": str(markdown_path),
            "html_report": str(html_path) if html_path else None,
            "context_pack": str(context_path),
            "prompt": str(prompt_path),
            "duration_seconds": round(perf_counter() - started, 3),
            "health_score": scan.get("audit", {}).get("health_score"),
            "files_scanned": scan.get("files_scanned"),
            "top_suggestion": (scan.get("suggestions") or [{}])[0].get("title"),
            "kept_clone": keep_clone,
        }
        summary_path = report_dir / "analysis.json"
        summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
        summary["summary_path"] = str(summary_path)
        return summary
    finally:
        if agent is not None:
            agent.close()
        if temp_handle is not None:
            temp_handle.cleanup()


def _safe_repo_name(repo_source: str) -> str:
    source = repo_source.rstrip("/").replace("\\", "/")
    tail = source.split("/")[-1] or "repository"
    if tail.endswith(".git"):
        tail = tail[:-4]
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", tail).strip("-._")
    return safe or "repository"


def _normalize_git_source(repo_source: str) -> str:
    possible_path = Path(repo_source).expanduser()
    if possible_path.exists():
        return str(possible_path.resolve())
    if repo_source.startswith("file://"):
        return repo_source
    if repo_source.startswith(("http://", "https://", "ssh://", "git@")):
        return repo_source
    return repo_source


def _render_analyze_url(result: Dict[str, Any]) -> str:
    lines = [
        "SENTINEL URL ANALYSIS",
        f"Source: {result.get('repo_source')}",
        f"Report Directory: {result.get('report_dir')}",
        f"Markdown Report: {result.get('markdown_report')}",
    ]
    if result.get("html_report"):
        lines.append(f"HTML Report: {result['html_report']}")
    lines.extend(
        [
            f"Context Pack: {result.get('context_pack')}",
            f"Next Prompt: {result.get('prompt')}",
            f"Health: {result.get('health_score')}%",
            f"Files Scanned: {result.get('files_scanned')}",
            f"Top Suggestion: {result.get('top_suggestion')}",
            f"Duration: {result.get('duration_seconds')}s",
        ]
    )
    if result.get("clone_dir"):
        lines.append(f"Clone Kept At: {result['clone_dir']}")
    return "\n".join(lines).rstrip() + "\n"
