from __future__ import annotations

from typing import Any, Dict


class StatusRenderer:
    """Renders Sentinel status information for terminal display."""

    def __init__(self, width: int = 60):
        self.width = width

    def render(self, status: Dict[str, Any]) -> str:
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
