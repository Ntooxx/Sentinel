from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from classify import (
    ARCHETYPE_FRAMEWORK_LIBRARY,
    ARCHETYPE_MONOREPO,
    classifyFile,
    classifyLargeFilePolicy,
    detectRepoArchetype,
)
from utils import DEFAULT_AUDIT_RULES, merge_dicts, now_iso, read_json


class IssueDetector:
    """Detects issues in scanned files."""

    def __init__(self, audit_rules_path: Optional[str] = None):
        if not audit_rules_path:
            self.rules = dict(DEFAULT_AUDIT_RULES)
        else:
            loaded = read_json(audit_rules_path, DEFAULT_AUDIT_RULES)
            if isinstance(loaded, dict):
                self.rules = merge_dicts(DEFAULT_AUDIT_RULES, loaded)
            else:
                self.rules = dict(DEFAULT_AUDIT_RULES)

    def _find_issues(
        self,
        files: Dict[str, Dict[str, Any]],
        metrics: Dict[str, Any],
        structure: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        issues = []
        line_threshold = int(self.rules.get("large_file_line_threshold", 500))
        size_threshold = int(self.rules.get("large_file_size_threshold", 100_000))

        for filepath, info in files.items():
            fc = classifyFile(filepath, info)

            if info["todo_count"] > 0:
                issues.append(
                    {
                        "type": "todo",
                        "severity": "low",
                        "category": "maintainability",
                        "file": filepath,
                        "message": f"Contains {info['todo_count']} TODO/FIXME markers",
                        "timestamp": now_iso(),
                    }
                )

            if info["line_count"] > line_threshold:
                if fc.isVendor or fc.isDependencyLock:
                    continue
                policy = classifyLargeFilePolicy(filepath, info)
                is_config_or_doc = fc.isConfig or fc.isDocumentation or fc.isLocalization or fc.isSpecification
                if "{lines}" in policy:
                    message = policy.format(lines=info['line_count'])
                else:
                    message = policy
                issues.append(
                    {
                        "type": "large_file",
                        "severity": "medium",
                        "category": "maintainability" if is_config_or_doc else "structural",
                        "file": filepath,
                        "message": message,
                        "timestamp": now_iso(),
                    }
                )

            if info["size"] > size_threshold:
                if fc.isVendor or fc.isDependencyLock:
                    continue
                is_config_or_doc = fc.isConfig or fc.isDocumentation or fc.isLocalization or fc.isSpecification
                category = "maintainability" if is_config_or_doc else "structural"
                issues.append(
                    {
                        "type": "large_file_size",
                        "severity": "medium",
                        "category": category,
                        "file": filepath,
                        "message": f"File is {info['size'] // 1024}KB",
                        "timestamp": now_iso(),
                    }
                )

            metadata = info.get("metadata", {})
            drift_flags = metadata.get("doc_drift_flags") or []
            empty_headings = int(metadata.get("empty_heading_count", 0) or 0)
            doc_matches = metadata.get("doc_drift_matches") or []
            if drift_flags or empty_headings >= 3:
                details = []
                confidence = "medium"
                if drift_flags:
                    if doc_matches:
                        evidence = "; ".join(f'"{label}" at line {ln}' for label, _, ln in doc_matches[:2])
                        details.append(f"possible drift indicator: {evidence}")
                        confidence = "low"
                    else:
                        details.append("placeholder-like patterns")
                if empty_headings:
                    details.append(f"{empty_headings} empty-looking headings")
                fc = classifyFile(filepath, info)
                if fc.isGeneratedSdk or fc.isLocalization:
                    category = "structural"
                    detail = "Large generated sdk/localization file; review for readability and drift: " + ", ".join(details)
                else:
                    category = "maintainability"
                    if any("possible drift indicator" in d for d in details):
                        detail = "Documentation may contain drift: " + "; ".join(details)
                    else:
                        detail = "Documentation may be stale or scaffold-like: " + ", ".join(details)
                issues.append(
                    {
                        "type": "doc_code_drift",
                        "severity": "low" if confidence == "low" else "medium",
                        "category": category,
                        "file": filepath,
                        "message": detail,
                        "timestamp": now_iso(),
                    }
                )

        if not structure.get("has_tests"):
            issues.append(
                {
                    "type": "no_tests",
                    "severity": "high",
                    "category": "test",
                    "file": None,
                    "message": "No test files detected in project",
                    "timestamp": now_iso(),
                }
            )

        has_readme = any(Path(path).name.lower() in {"readme.md", "readme.txt", "readme"} for path in files)
        if not has_readme:
            issues.append(
                {
                    "type": "no_readme",
                    "severity": "medium",
                    "category": "structural",
                    "file": None,
                    "message": "No README file found",
                    "timestamp": now_iso(),
                }
            )

        archetype_data = detectRepoArchetype(files, [], "", [])
        if archetype_data["primaryArchetype"] in (ARCHETYPE_FRAMEWORK_LIBRARY, ARCHETYPE_MONOREPO):
            pass
        elif metrics.get("total_files", 0) > 3 and not structure.get("entry_points"):
            issues.append(
                {
                    "type": "no_entry_point",
                    "severity": "low",
                    "category": "runtime",
                    "file": None,
                    "message": "No clear entry point detected",
                    "timestamp": now_iso(),
                }
            )

        return issues