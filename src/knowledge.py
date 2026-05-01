from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from utils import CONTEXT_BUDGETS, DEFAULT_KNOWLEDGE_BASE, merge_dicts, normalize_budget_name, now_iso, read_json, write_json


class KnowledgeBase:
    """Persistent project knowledge captured across scans."""

    def __init__(self, storage_path: str):
        self.storage_path = Path(storage_path)
        self.data: Dict[str, Any] = deepcopy(DEFAULT_KNOWLEDGE_BASE)
        self.load()

    def load(self) -> Dict[str, Any]:
        loaded = read_json(self.storage_path, DEFAULT_KNOWLEDGE_BASE)
        if isinstance(loaded, dict):
            self.data = merge_dicts(DEFAULT_KNOWLEDGE_BASE, loaded)
        else:
            self.data = deepcopy(DEFAULT_KNOWLEDGE_BASE)
        return self.data

    def save(self) -> Path:
        return write_json(self.storage_path, self.data)

    def reset(self) -> None:
        self.data = deepcopy(DEFAULT_KNOWLEDGE_BASE)
        self.save()

    def update_file_info(self, filepath: str, info: Dict[str, Any], persist: bool = True) -> None:
        entry = dict(info)
        entry["last_seen"] = now_iso()
        self.data["files"][filepath] = entry
        if persist:
            self.save()

    def remove_file(self, filepath: str, persist: bool = True) -> None:
        if filepath in self.data["files"]:
            del self.data["files"][filepath]
            if persist:
                self.save()

    def get_all_files(self) -> Dict[str, Dict[str, Any]]:
        return dict(self.data["files"])

    def get_file_info(self, filepath: str) -> Dict[str, Any] | None:
        return self.data["files"].get(filepath)

    def replace_patterns(self, patterns: Iterable[Dict[str, Any]], persist: bool = True) -> None:
        deduped = []
        seen = set()
        for pattern in patterns:
            signature = json.dumps(pattern, sort_keys=True)
            if signature not in seen:
                seen.add(signature)
                deduped.append(dict(pattern))
        self.data["patterns"] = deduped
        if persist:
            self.save()

    def add_pattern(self, entry: Dict[str, Any] | str, persist: bool = True) -> None:
        current = list(self.data["patterns"])
        signature = json.dumps(entry, sort_keys=True) if isinstance(entry, dict) else str(entry)
        if all(
            signature
            != (json.dumps(item, sort_keys=True) if isinstance(item, dict) else str(item))
            for item in current
        ):
            current.append(entry)
            self.data["patterns"] = current
            if persist:
                self.save()

    def replace_issues(self, issues: Iterable[Dict[str, Any]], persist: bool = True) -> None:
        normalized = []
        for issue in issues:
            normalized.append(
                {
                    "type": issue.get("type", "general"),
                    "severity": issue.get("severity", "low"),
                    "file": issue.get("file"),
                    "message": issue.get("message", ""),
                    "timestamp": issue.get("timestamp", now_iso()),
                }
            )
        self.data["issues"] = normalized
        if persist:
            self.save()

    def add_issue(
        self,
        issue: str,
        severity: str,
        filepath: str | None = None,
        issue_type: str = "general",
        persist: bool = True,
    ) -> None:
        self.data["issues"].append(
            {
                "type": issue_type,
                "issue": issue,
                "message": issue,
                "severity": severity,
                "file": filepath,
                "timestamp": now_iso(),
            }
        )
        if persist:
            self.save()

    def add_decision(self, decision: str, reason: str, persist: bool = True) -> None:
        self.data["decisions"].append(
            {
                "decision": decision,
                "reason": reason,
                "timestamp": now_iso(),
            }
        )
        if persist:
            self.save()

    def update_architecture(self, arch: Dict[str, Any], persist: bool = True) -> None:
        self.data["architecture"] = dict(arch)
        if persist:
            self.save()

    def update_dependencies(self, deps: Dict[str, Any], persist: bool = True) -> None:
        self.data["dependencies"] = dict(deps)
        if persist:
            self.save()

    def update_understanding(self, understanding: Dict[str, Any], persist: bool = True) -> None:
        self.data["understanding"] = dict(understanding)
        if persist:
            self.save()

    def update_llm_readiness(self, readiness: Dict[str, Any], persist: bool = True) -> None:
        self.data["llm_readiness"] = dict(readiness)
        if persist:
            self.save()

    def record_savings(
        self,
        command: str,
        full_tokens: int,
        emitted_tokens: int,
        persist: bool = True,
    ) -> None:
        savings = self.data.setdefault(
            "savings",
            {"events": [], "total_full_tokens": 0, "total_emitted_tokens": 0},
        )
        savings["total_full_tokens"] = int(savings.get("total_full_tokens", 0)) + max(0, full_tokens)
        savings["total_emitted_tokens"] = int(savings.get("total_emitted_tokens", 0)) + max(0, emitted_tokens)
        savings.setdefault("events", []).append(
            {
                "timestamp": now_iso(),
                "command": command,
                "full_tokens": max(0, full_tokens),
                "emitted_tokens": max(0, emitted_tokens),
                "saved_percent": _saved_percent(full_tokens, emitted_tokens),
            }
        )
        savings["events"] = savings["events"][-50:]
        if persist:
            self.save()

    def get_savings_summary(self) -> Dict[str, Any]:
        savings = self.data.get("savings", {})
        full = int(savings.get("total_full_tokens", 0))
        emitted = int(savings.get("total_emitted_tokens", 0))
        return {
            "total_full_tokens": full,
            "total_emitted_tokens": emitted,
            "estimated_tokens_saved": max(0, full - emitted),
            "estimated_token_savings_percent": _saved_percent(full, emitted),
            "events": savings.get("events", [])[-10:],
        }

    def record_task_memory(
        self,
        *,
        goal: str,
        changed_files: Iterable[str],
        tests: Iterable[str],
        risks: Iterable[str],
        decisions: Iterable[str],
        verifier_summary: str = "",
        persist: bool = True,
    ) -> Dict[str, Any]:
        entry = {
            "timestamp": now_iso(),
            "goal": goal,
            "changed_files": list(changed_files),
            "tests": list(tests),
            "risks": list(risks),
            "decisions": list(decisions),
            "verifier_summary": verifier_summary,
        }
        memory = self.data.setdefault("task_memory", [])
        memory.append(entry)
        self.data["task_memory"] = memory[-100:]
        if persist:
            self.save()
        return entry

    def get_task_memory(self, limit: int = 10) -> list[Dict[str, Any]]:
        return list(self.data.get("task_memory", []))[-max(1, limit):]

    def record_scan_event(
        self,
        *,
        health_score: int,
        files_scanned: int,
        diff: Dict[str, Any],
        suggestions: Iterable[Dict[str, Any]],
        performance: Dict[str, Any],
        persist: bool = True,
        health_score_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        suggestion_list = list(suggestions)
        top = suggestion_list[0] if suggestion_list else None
        entry = {
            "timestamp": now_iso(),
            "health_score": health_score,
            "files_scanned": files_scanned,
            "change_summary": diff.get("summary", ""),
            "new_count": diff.get("new_count", 0),
            "modified_count": diff.get("modified_count", 0),
            "deleted_count": diff.get("deleted_count", 0),
            "top_suggestion": top.get("title") if isinstance(top, dict) else None,
            "duration_seconds": performance.get("duration_seconds", 0),
            "budget_alerts": performance.get("budget_alerts", []),
        }
        if health_score_data:
            entry["health_score_data"] = health_score_data
        history = self.data.setdefault("scan_history", [])
        history.append(entry)
        self.data["scan_history"] = history[-100:]
        if persist:
            self.save()
        return entry

    def get_scan_history(self, limit: int = 20) -> list[Dict[str, Any]]:
        return list(self.data.get("scan_history", []))[-max(1, limit):]

    def update_suggestions(self, suggestions: Iterable[Dict[str, Any]], persist: bool = True) -> None:
        normalized = []
        for suggestion in suggestions:
            normalized.append(
                {
                    "title": suggestion.get("title", ""),
                    "priority": suggestion.get("priority", "low"),
                    "category": suggestion.get("category", "general"),
                    "reason": suggestion.get("reason", ""),
                    "action": suggestion.get("action", ""),
                    "suggested_prompt": suggestion.get("suggested_prompt", ""),
                    "impact": suggestion.get("impact", "medium"),
                    "effort": suggestion.get("effort", "medium"),
                    "ranking_label": suggestion.get("ranking_label", ""),
                    "confidence": suggestion.get("confidence", {}),
                    "verification": suggestion.get("verification", {}),
                }
            )
        self.data["suggestions"] = normalized
        if persist:
            self.save()

    def get_top_suggestion(self) -> Dict[str, Any] | None:
        suggestions = self.data.get("suggestions", [])
        return suggestions[0] if suggestions else None

    def set_last_scan(self, persist: bool = True) -> None:
        self.data["last_scan"] = now_iso()
        if persist:
            self.save()

    def set_last_checkpoint(self, timestamp: str | None = None, persist: bool = True) -> None:
        self.data["last_checkpoint"] = timestamp or now_iso()
        if persist:
            self.save()

    def get_project_summary(self) -> Dict[str, Any]:
        files = self.data["files"]
        total_lines = sum(info.get("line_count", 0) for info in files.values())
        extensions: Dict[str, int] = {}
        for info in files.values():
            ext = info.get("extension") or "unknown"
            extensions[ext] = extensions.get(ext, 0) + 1

        understanding = self.data.get("understanding", {})
        top_suggestion = self.get_top_suggestion()
        llm_readiness = self.data.get("llm_readiness", {})
        savings = self.get_savings_summary()

        scan_history = self.data.get("scan_history", [])
        latest_scan = scan_history[-1] if scan_history else None
        health_score_data = latest_scan.get("health_score_data") if latest_scan else None

        return {
            "total_files": len(files),
            "total_lines": total_lines,
            "file_types": dict(sorted(extensions.items())),
            "open_issues": len(
                [issue for issue in self.data["issues"] if issue.get("severity") != "resolved"]
            ),
            "patterns_found": len(self.data["patterns"]),
            "decisions_made": len(self.data["decisions"]),
            "scan_history_count": len(scan_history),
            "project_name": understanding.get("project_name"),
            "project_type": understanding.get("project_type"),
            "primary_language": understanding.get("primary_language"),
            "top_suggestion": top_suggestion.get("title") if top_suggestion else None,
            "estimated_token_savings_percent": llm_readiness.get("estimated_token_savings_percent"),
            "tracked_token_savings_percent": savings.get("estimated_token_savings_percent"),
            "last_scan": self.data["last_scan"],
            "last_checkpoint": self.data["last_checkpoint"],
            "health_score_data": health_score_data,
        }

    def export_context(self, max_items: int = 50, budget: str = "medium") -> str:
        """Export the knowledge base as a compact context string."""

        budget_name = normalize_budget_name(budget, default="medium")
        limits = CONTEXT_BUDGETS[budget_name]
        lines = ["# Project Knowledge Base", ""]
        summary = self.get_project_summary()
        lines.append("## Summary")
        if summary.get("project_name"):
            lines.append(f"- Project: {summary['project_name']}")
        if summary.get("project_type"):
            lines.append(f"- Type: {summary['project_type']}")
        if summary.get("primary_language"):
            lines.append(f"- Primary Language: {summary['primary_language']}")
        lines.append(f"- Files: {summary['total_files']}")
        lines.append(f"- Total Lines: {summary['total_lines']}")
        lines.append(f"- Review Signals: {summary['open_issues']}")
        lines.append(f"- Patterns Found: {summary['patterns_found']}")
        if summary.get("top_suggestion"):
            lines.append(f"- Top Suggestion: {summary['top_suggestion']}")
        if summary.get("estimated_token_savings_percent") is not None:
            lines.append(f"- Estimated Token Savings: {summary['estimated_token_savings_percent']}%")
        lines.append(f"- Last Scan: {summary['last_scan']}")
        lines.append(f"- Last Checkpoint: {summary['last_checkpoint']}")
        lines.append("")

        understanding = self.data.get("understanding", {})
        if understanding:
            lines.append("## Project Understanding")
            if understanding.get("summary"):
                lines.append(f"- Summary: {understanding['summary']}")
            if understanding.get("purpose"):
                lines.append(f"- Purpose: {understanding['purpose']}")
            frameworks = understanding.get("frameworks", [])
            if frameworks:
                lines.append(f"- Frameworks: {', '.join(frameworks[: limits['frameworks']])}")
            workflow = understanding.get("workflow_hints", [])
            if workflow:
                lines.append(f"- Workflow: {', '.join(workflow[: limits['frameworks']])}")
            coverage = understanding.get("scan_coverage", {})
            if coverage.get("warning"):
                lines.append(f"- Scan Coverage Warning: {coverage['warning']}")
            lines.append("")

            components = understanding.get("main_components", [])
            if components:
                lines.append("## Main Components")
                for component in components[: min(max_items, limits["components"])]:
                    lines.append(
                        f"- {component.get('path', 'unknown')}: "
                        f"{component.get('role', 'general logic')} "
                        f"({component.get('file_count', 0)} files / {component.get('line_count', 0)} lines)"
                    )
                lines.append("")

            important_files = understanding.get("important_files", [])
            if important_files:
                lines.append("## Important Files")
                for item in important_files[: min(max_items, limits["files"])]:
                    reason = item.get("reason")
                    suffix = f": {reason}" if reason else ""
                    lines.append(f"- {item.get('path', 'unknown')}{suffix}")
                lines.append("")

        if self.data["architecture"]:
            lines.append("## Architecture")
            architecture = self.data["architecture"]
            if architecture.get("entry_points"):
                lines.append(f"- Entry Points: {', '.join(architecture['entry_points'][: limits['files']])}")
            entry_points_by_category = architecture.get("entry_points_by_category", {})
            if entry_points_by_category:
                for label, key in [
                    ("Runtime Entry Points", "runtime"),
                    ("Build Entry Points", "build"),
                    ("Generator Entry Points", "generator"),
                    ("Environment Setup", "environment"),
                ]:
                    values = entry_points_by_category.get(key, [])
                    if values:
                        lines.append(f"- {label}: {', '.join(values[: limits['files']])}")
            if architecture.get("patterns"):
                lines.append(f"- Patterns: {', '.join(architecture['patterns'][: limits['patterns']])}")
            if architecture.get("directories"):
                lines.append(f"- Directories: {', '.join(architecture['directories'][: limits['directories']])}")
            lines.append("")

        if self.data["dependencies"]:
            lines.append("## Dependencies")
            for name, details in self.data["dependencies"].items():
                manifests = details.get("manifests", [])
                if manifests:
                    lines.append(f"- {name}: {', '.join(manifests[: limits['files']])}")
                else:
                    lines.append(f"- {name}")
            lines.append("")

        if self.data["patterns"]:
            lines.append("## Patterns")
            for pattern in self.data["patterns"][: min(max_items, limits["patterns"])]:
                if isinstance(pattern, dict):
                    lines.append(f"- {pattern.get('name', 'unknown')}: {pattern.get('description', '')}")
                else:
                    lines.append(f"- {pattern}")
            lines.append("")

        if self.data["issues"]:
            lines.append("## Recent Review Signals")
            for issue in self.data["issues"][: min(max_items, limits["issues"])]:
                message = issue.get("message") or issue.get("issue", "")
                severity = issue.get("severity", "low")
                location = f" ({issue['file']})" if issue.get("file") else ""
                lines.append(f"- [{severity}] {message}{location}")
            lines.append("")

        suggestions = self.data.get("suggestions", [])
        if suggestions:
            lines.append("## Suggested Next Move")
            for suggestion in suggestions[: min(max_items, limits["suggestions"])]:
                lines.append(
                    f"- [{suggestion.get('priority', 'low')}] {suggestion.get('title', 'Untitled')}: "
                    f"{suggestion.get('action', '')}"
                )
                if budget_name != "tiny" and suggestion.get("reason"):
                    lines.append(f"  Reason: {suggestion['reason']}")
                if budget_name in {"medium", "large"} and suggestion.get("suggested_prompt"):
                    lines.append(f"  Prompt: {suggestion['suggested_prompt']}")
            lines.append("")

        llm_readiness = self.data.get("llm_readiness", {})
        if llm_readiness:
            lines.append("## LLM Strategy")
            if llm_readiness.get("recommended_budget"):
                lines.append(f"- Recommended Budget: {llm_readiness['recommended_budget']}")
            if llm_readiness.get("estimated_full_context_tokens") is not None:
                lines.append(f"- Full Context Tokens: {llm_readiness['estimated_full_context_tokens']}")
            if llm_readiness.get("estimated_compact_context_tokens") is not None:
                lines.append(f"- Compact Context Tokens: {llm_readiness['estimated_compact_context_tokens']}")
            if llm_readiness.get("estimated_token_savings_percent") is not None:
                lines.append(f"- Estimated Savings: {llm_readiness['estimated_token_savings_percent']}%")
            lines.append("")

        if self.data["decisions"]:
            lines.append("## Decisions")
            for decision in self.data["decisions"][: min(max_items, limits["decisions"])]:
                lines.append(f"- {decision['decision']}: {decision['reason']}")
            lines.append("")

        task_memory = self.data.get("task_memory", [])
        if task_memory:
            lines.append("## Task Memory")
            for entry in task_memory[-min(max_items, limits["decisions"]) :]:
                changed = ", ".join(entry.get("changed_files", [])[:4]) or "none recorded"
                tests = ", ".join(entry.get("tests", [])[:3]) or "none recorded"
                risks = ", ".join(entry.get("risks", [])[:3]) or "none recorded"
                lines.append(f"- {entry.get('goal', 'task')}: changed {changed}; tests {tests}; risks {risks}")
            lines.append("")

        scan_history = self.data.get("scan_history", [])
        if scan_history and budget_name in {"medium", "large"}:
            lines.append("## Recent Scan Timeline")
            for entry in scan_history[-min(max_items, limits["decisions"]) :]:
                alerts = entry.get("budget_alerts", [])
                alert_text = f"; alerts {len(alerts)}" if alerts else ""
                lines.append(
                    f"- {entry.get('timestamp')}: health {entry.get('health_score')}%, "
                    f"{entry.get('change_summary', 'no change')}{alert_text}"
                )
            lines.append("")

        return "\n".join(lines).rstrip() + "\n"


def _saved_percent(full_tokens: int, emitted_tokens: int) -> int:
    if full_tokens <= 0:
        return 0
    return max(0, round((1 - (emitted_tokens / full_tokens)) * 100))
