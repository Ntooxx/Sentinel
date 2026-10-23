from __future__ import annotations

from typing import Any, Dict


class AskRenderer:
    """Renders Sentinel Ask/Answer responses for terminal display."""

    def __init__(self, width: int = 60):
        self.width = width

    def render(self, answer: Dict[str, Any]) -> str:
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
