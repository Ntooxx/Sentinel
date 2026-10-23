from __future__ import annotations

from typing import Any, Dict


class CompactRenderer:
    """Renders compact one-line summaries and context/prompt packs."""

    def __init__(self, width: int = 60):
        self.width = width

    def render(self, result: Dict[str, Any]) -> str:
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
