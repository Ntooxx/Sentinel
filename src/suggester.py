from __future__ import annotations

from typing import Any, Dict, List

from classify import classifyRiskSurface
from utils import estimate_text_tokens, normalize_budget_name


class Suggester:
    """Rule-based next-step suggestion engine."""

    def __init__(self):
        self.priority_rules = [
            self._check_hotspot_trace,
            self._check_missing_tests,
            self._check_todos,
            self._check_large_files,
            self._check_doc_drift,
            self._check_risk_hotspots,
            self._check_missing_docs,
            self._check_no_entry_point,
            self._check_recent_changes,
            self._check_dependencies,
            self._check_structure,
            self._check_security_basics,
        ]

    def generate_suggestions(
        self,
        audit: Dict[str, Any],
        diff: Dict[str, Any],
        knowledge: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        suggestions: List[Dict[str, Any]] = []

        for rule in self.priority_rules:
            result = rule(audit, diff, knowledge)
            if not result:
                continue
            if isinstance(result, list):
                suggestions.extend(result)
            else:
                suggestions.append(result)

        deduped = []
        seen = set()
        for suggestion in suggestions:
            signature = (suggestion.get("title"), suggestion.get("action"))
            if signature in seen:
                continue
            seen.add(signature)
            suggestion["suggested_prompt"] = self._make_prompt(suggestion)
            self._enrich_suggestion(suggestion, audit, diff, knowledge)
            deduped.append(suggestion)

        priority_map = {"critical": 4, "high": 3, "medium": 2, "low": 1}
        impact_map = {"high": 3, "medium": 2, "low": 1}
        effort_map = {"low": 3, "medium": 2, "high": 1}
        deduped.sort(
            key=lambda item: (
                priority_map.get(item.get("priority", "low"), 0),
                impact_map.get(item.get("impact", "medium"), 0),
                effort_map.get(item.get("effort", "medium"), 0),
            ),
            reverse=True,
        )
        return deduped[:10]

    def _enrich_suggestion(
        self,
        suggestion: Dict[str, Any],
        audit: Dict[str, Any],
        diff: Dict[str, Any],
        knowledge: Dict[str, Any],
    ) -> None:
        focus_files = [path for path in suggestion.get("focus_files", []) if path]
        known_files = knowledge.get("files", {})
        matching_issues = [
            issue
            for issue in audit.get("issues", [])
            if not focus_files or issue.get("file") in focus_files
        ]
        evidence = []
        if suggestion.get("reason"):
            evidence.append(suggestion["reason"])
        if focus_files:
            existing = [path for path in focus_files if path in known_files]
            evidence.append(f"{len(existing)}/{len(focus_files)} focus file(s) known to Sentinel")
        if matching_issues:
            evidence.append(f"{len(matching_issues)} related audit issue(s)")
        if diff.get("summary"):
            evidence.append(diff["summary"])

        priority = suggestion.get("priority", "low")
        category = suggestion.get("category", "general")
        impact = "high" if priority in {"critical", "high"} or category in {"security", "testing"} else "medium"
        if priority == "low":
            impact = "low"
        effort = "low" if len(focus_files) <= 2 and category not in {"refactoring"} else "medium"
        if category == "refactoring" or len(focus_files) > 5:
            effort = "high"
        if category == "cleanup":
            effort = "low to triage, high to remediate"

        if priority in {"critical", "high"}:
            label = "risky"
        elif impact == "high" and effort == "low":
            label = "quick win"
        elif category in {"architecture", "refactoring", "release"}:
            label = "strategic"
        elif not focus_files:
            label = "needs user decision"
        else:
            label = "quick win" if effort == "low" else "strategic"

        uncertainty = "low" if focus_files or matching_issues else "medium"
        if not evidence:
            uncertainty = "high"

        # Surface confidence downgrades overall confidence
        surface_confidence = suggestion.get("surface_confidence", "high")
        if surface_confidence == "low" and uncertainty == "low":
            uncertainty = "medium"

        suggestion["impact"] = impact
        suggestion["effort"] = effort
        suggestion["ranking_label"] = label
        suggestion["confidence"] = {
            "level": "high" if uncertainty == "low" else "medium" if uncertainty == "medium" else "low",
            "evidence": evidence[:4],
            "files_inspected": len(known_files),
            "uncertainty": uncertainty,
            "surface_confidence": surface_confidence,
        }
        if "verification" not in suggestion:
            suggestion["verification"] = {
                "commands": self._test_commands_for_focus(focus_files, audit),
            }

    def _test_commands_for_focus(self, focus_files: List[str], audit: Dict[str, Any]) -> List[str]:
        tests = audit.get("structure", {}).get("test_files", [])
        if not tests:
            return []
        selected = []
        focus_stems = {path.split("/")[-1].removesuffix(".py") for path in focus_files if path.endswith(".py")}
        for test in tests:
            stem = test.split("/")[-1].removeprefix("test_").removesuffix(".py")
            if not focus_stems or stem in focus_stems:
                selected.append(test)
        python_selected = [test for test in selected if test.endswith(".py")]
        if python_selected:
            return [f"python -m pytest {' '.join(python_selected[:4])}"]
        if selected:
            return ["Run the project's native test runner for the affected test files."]
        if any(test.endswith(".py") for test in tests):
            python_tests = [test for test in tests if test.endswith(".py")]
            return [f"python -m pytest {' '.join(python_tests[:4])}"]
        return ["Run the project's native test runner."]

    @staticmethod
    def _is_weak_entry_point(path: str) -> bool:
        lower = path.replace("\\", "/").lower()
        weak_dirs = {"/assets/", "/asset/", "/static/", "/docs/", "/docs_src/", "/examples/", "/example/", "/samples/", "/sample/"}
        return any(d in lower for d in weak_dirs)

    def _check_hotspot_trace(
        self,
        audit: Dict[str, Any],
        _: Dict[str, Any],
        __: Dict[str, Any],
    ) -> Dict[str, Any] | None:
        understanding = audit.get("understanding", {})
        archetype = understanding.get("archetype", "")
        hotspot_groups = understanding.get("hotspot_groups", {})
        all_hotspots = understanding.get("hotspots", []) or hotspot_groups.get("runtime", []) or hotspot_groups.get("build_tooling", [])
        runtime_hotspots = [h for h in (hotspot_groups.get("runtime", []) or []) if classifyRiskSurface(h.get("path", "")) == "runtime"]
        if not runtime_hotspots:
            runtime_hotspots = [h for h in all_hotspots if classifyRiskSurface(h.get("path", "")) == "runtime"]
        entry_points_by_category = audit.get("structure", {}).get("entry_points_by_category", {})
        runtime_entries = entry_points_by_category.get("runtime", [])
        build_entries = entry_points_by_category.get("build", [])
        generator_entries = entry_points_by_category.get("generator", [])

        # Prefer strong runtime entries (not in assets/docs/examples)
        strong_entries = [e for e in (runtime_entries or []) if not self._is_weak_entry_point(e)]
        entry_points = strong_entries or runtime_entries or build_entries or generator_entries

        hotspot_paths = [item.get("path") for item in runtime_hotspots[:3] if item.get("path")]

        # Detect surface mismatch: if the primary entry point is weak/asset but hotspots are real code
        entry_is_weak = bool(entry_points and self._is_weak_entry_point(entry_points[0]))
        has_real_hotspots = bool(hotspot_paths) and not all(self._is_weak_entry_point(h) for h in hotspot_paths)
        surface_confidence = "low" if (entry_is_weak or not entry_points) and has_real_hotspots else "high"

        if archetype == "framework_library":
            return {
                "category": "debugging",
                "priority": "medium",
                "title": "Map the relevant API/runtime surface before editing",
                "reason": f"Framework/library repo detected. Relevant surfaces: {', '.join(hotspot_paths[:2])}",
                "action": "Map the relevant API/runtime surface before editing",
                "focus_files": hotspot_paths,
                "suggested_prompt": (
                    "Map the relevant API/runtime surface before editing. "
                    "Explain which modules are touched first, identify the hotspots involved, "
                    "and recommend the safest first change before making edits."
                ),
                "surface_confidence": surface_confidence,
            }

        if archetype == "monorepo":
            return {
                "category": "debugging",
                "priority": "medium",
                "title": "Select the affected package/app/service first, then trace locally",
                "reason": f"Monorepo detected. Local hotspots: {', '.join(hotspot_paths[:2])}",
                "action": "Select the affected package/app/service first, then trace locally",
                "focus_files": hotspot_paths,
                "suggested_prompt": (
                    "Select the affected package/app/service first, then trace locally. "
                    "Explain which modules are touched first, identify the hotspots involved, "
                    "and recommend the safest first change before making edits."
                ),
                "surface_confidence": surface_confidence,
            }

        if surface_confidence == "low" and runtime_hotspots:
            # Entry point is in a weak directory (assets/docs/examples) but we have real hotspots
            return {
                "category": "debugging",
                "priority": "medium",
                "title": "Trace the main execution path before editing",
                "reason": f"Real hotspots detected ({', '.join(hotspot_paths[:2])}) but primary runtime entry was uncertain. Use hotspots as the tracing anchor.",
                "action": "Map the runtime path through current hotspots",
                "focus_files": hotspot_paths,
                "suggested_prompt": (
                    f"Trace the execution flow through the main hotspots: {', '.join(hotspot_paths[:2])}. "
                    "Explain which modules are touched first, identify the entry points involved, "
                    "and recommend the safest first change before making edits."
                ),
                "surface_confidence": surface_confidence,
            }

        if not entry_points:
            if not runtime_hotspots:
                return None
            return {
                "category": "debugging",
                "priority": "medium",
                "title": "Map runtime hotspots before editing",
                "reason": f"No entry point detected. Focus on the hottest files: {', '.join(hotspot_paths[:2])}",
                "action": "Analyze hotspots before making changes",
                "focus_files": hotspot_paths,
                "suggested_prompt": (
                    "No clear entry point was detected. Start by examining the hottest files: "
                    f"{', '.join(hotspot_paths[:2])}. Explain which modules are touched first "
                    "and recommend the safest first change."
                ),
            }

        runtime_focus = [entry_points[0], *hotspot_paths] if entry_points else hotspot_paths
        reason = f"Start from {runtime_focus[0]} and trace into {', '.join(hotspot_paths[:2])}" if len(hotspot_paths) >= 2 else f"Start from {runtime_focus[0]} and trace into hotspots"
        return {
            "category": "debugging",
            "priority": "medium",
            "title": "Trace the main execution path before editing",
            "reason": reason,
            "action": "Map the runtime path through entry points and current hotspots",
            "focus_files": runtime_focus,
            "suggested_prompt": (
                f"Trace the execution flow starting at {runtime_focus[0]}. "
                "Explain which modules are touched first, identify the hotspots involved, "
                "and recommend the safest first change before making edits."
            ),
            "surface_confidence": surface_confidence,
        }

    def _check_missing_tests(self, audit: Dict[str, Any], diff: Dict[str, Any], _: Dict[str, Any]) -> List[Dict[str, Any]]:
        suggestions = []
        structure = audit.get("structure", {})
        if not structure.get("has_tests"):
            suggestions.append(
                {
                    "category": "testing",
                    "priority": "high",
                    "title": "Add tests for your project",
                    "reason": "No test files detected. Tests prevent regressions.",
                    "action": "Create test files for core functionality",
                    "focus_files": structure.get("entry_points", [])[:2],
                    "suggested_prompt": (
                        "Generate comprehensive tests for this project. "
                        "Start with unit tests for the main modules, "
                        "covering the critical paths and edge cases."
                    ),
                }
            )

        for modified in diff.get("modified_files", []):
            if "test" in modified.lower():
                continue
            if not self._is_testable_source_file(modified):
                continue
            test_candidate = self._test_candidate_for_file(modified, structure)
            focus_files = [modified]
            if test_candidate in structure.get("test_files", []):
                focus_files.append(test_candidate)
            suggestions.append(
                {
                    "category": "testing",
                    "priority": "medium",
                    "title": f"Update tests for {modified}",
                    "reason": f"{modified} changed and may need refreshed test coverage",
                    "action": f"Write or update tests for {modified}",
                    "focus_files": focus_files,
                    "suggested_prompt": (
                        f"Write tests for the recent changes in {modified}. "
                        f"Create or update {test_candidate} if appropriate, "
                        "and cover the changed behavior with focused unit tests."
                    ),
                }
            )
        return suggestions

    def _is_testable_source_file(self, path: str) -> bool:
        lower = path.lower()
        return lower.endswith((".py", ".js", ".ts", ".cpp", ".c", ".h", ".hpp"))

    def _test_candidate_for_file(self, path: str, structure: Dict[str, Any]) -> str:
        stem = path.split("/")[-1].removesuffix(".py")
        expected = f"tests/test_{stem}.py"
        for test_path in structure.get("test_files", []):
            name = test_path.split("/")[-1]
            if name in {f"test_{stem}.py", f"{stem}_test.py"}:
                return test_path
        return expected

    def _check_todos(self, audit: Dict[str, Any], _: Dict[str, Any], __: Dict[str, Any]) -> Dict[str, Any] | None:
        metrics = audit.get("metrics", {})
        todos = metrics.get("open_todos", 0)
        todo_categories = metrics.get("todo_categories", {})
        if todos > 5:
            source_todos = todo_categories.get("first_party_source", 0)
            tooling_todos = todo_categories.get("tooling", 0)
            test_todos = todo_categories.get("tests_fixtures", 0)
            docs_todos = todo_categories.get("docs", 0)
            vendor_todos = todo_categories.get("vendor_generated", 0)
            reason = (
                f"Total TODOs: {todos}. "
                f"First-party source: {source_todos}; tooling: {tooling_todos}; "
                f"test/fixture: {test_todos}; documentation: {docs_todos}; "
                f"vendor/generated: {vendor_todos}."
            )
            return {
                "category": "cleanup",
                "priority": "medium",
                "title": f"Prioritise {source_todos} first-party source TODO/FIXME markers",
                "reason": reason,
                "action": "Review first-party TODOs first, then triage tooling, tests, and documentation separately",
                "focus_files": [],
                "suggested_prompt": (
                    f"There are {todos} TODO/FIXME markers in the codebase: "
                    f"{source_todos} first-party source, {tooling_todos} tooling, "
                    f"{test_todos} tests/fixtures, {docs_todos} documentation, and {vendor_todos} vendor/generated. "
                    "prioritize the most important first-party source items, and implement the top three fixes."
                ),
            }
        return None

    def _check_large_files(self, audit: Dict[str, Any], _: Dict[str, Any], __: Dict[str, Any]) -> Dict[str, Any] | None:
        large = [issue for issue in audit.get("issues", []) if issue.get("type") == "large_file"]
        if large:
            code_like = [
                issue
                for issue in large
                if not str(issue.get("file", "")).lower().endswith((".json", ".yaml", ".yml", ".toml", ".ini", ".cfg"))
            ]
            focus_source = code_like or large
            focus_files = [issue["file"] for issue in focus_source[:3] if issue.get("file")]
            files = ", ".join(focus_files)
            return {
                "category": "refactoring",
                "priority": "medium",
                "title": f"Review {len(large)} oversized file(s)",
                "reason": f"Large files are maintainability signals, not automatic failures: {files}",
                "action": "Map tests and module boundaries before refactoring",
                "focus_files": focus_files,
                "suggested_prompt": (
                    f"These files are large and may need refactoring: {files}. "
                    "For each one, classify whether it is code, data, or config; identify existing test coverage; "
                    "then recommend the smallest safe action. Do not split data/config files unless there is a "
                    "real loading, validation, or maintenance problem."
                ),
            }
        return None

    def _check_doc_drift(self, audit: Dict[str, Any], _: Dict[str, Any], __: Dict[str, Any]) -> Dict[str, Any] | None:
        drift = [issue for issue in audit.get("issues", []) if issue.get("type") == "doc_code_drift"]
        if not drift:
            return None
        focus_files = [issue["file"] for issue in drift[:4] if issue.get("file")]
        files = ", ".join(focus_files)
        return {
            "category": "documentation",
            "priority": "medium",
            "title": "Reconcile stale or scaffold-like documentation",
            "reason": f"Documentation drift signals were found in {files}",
            "action": "Update docs so they match the current code and remove placeholder claims",
            "focus_files": focus_files,
            "suggested_prompt": (
                f"Review these documentation files for stale or placeholder content: {files}. "
                "Compare their claims against the current code and update them to be release-ready."
            ),
        }

    def _check_risk_hotspots(self, audit: Dict[str, Any], _: Dict[str, Any], __: Dict[str, Any]) -> Dict[str, Any] | None:
        risky = [item for item in audit.get("risk_scores", []) if item.get("level") in {"high", "medium"}]
        if not risky:
            return None
        focus_files = [item["file"] for item in risky[:4] if item.get("file")]
        factors = "; ".join(
            f"{item['file']} ({', '.join(item.get('factors', [])[:3])})"
            for item in risky[:3]
        )
        return {
            "category": "review",
            "priority": "medium",
            "title": "Review highest-risk files before broad changes",
            "reason": f"Risk scoring flagged {factors}",
            "action": "Inspect high-risk files, map tests, and reduce the riskiest blind spot",
            "focus_files": focus_files,
            "suggested_prompt": (
                "Review Sentinel's highest-risk files first. For each file, explain why it is risky, "
                "which tests cover it, and the smallest change that would reduce risk."
            ),
        }

    def _check_missing_docs(self, audit: Dict[str, Any], _: Dict[str, Any], __: Dict[str, Any]) -> Dict[str, Any] | None:
        if any(issue.get("type") == "no_readme" for issue in audit.get("issues", [])):
            return {
                "category": "documentation",
                "priority": "medium",
                "title": "Create a README",
                "reason": "No README found. Documentation helps onboarding and adoption.",
                "action": "Write a comprehensive README.md",
                "focus_files": audit.get("structure", {}).get("entry_points", [])[:3],
                "suggested_prompt": (
                    "Analyze this project and create a comprehensive README.md "
                    "covering the project purpose, installation, usage, "
                    "project structure, and configuration."
                ),
            }
        return None

    def _check_no_entry_point(self, audit: Dict[str, Any], _: Dict[str, Any], __: Dict[str, Any]) -> Dict[str, Any] | None:
        entry_points = audit.get("structure", {}).get("entry_points", [])
        if not entry_points and audit.get("metrics", {}).get("total_files", 0) > 3:
            return {
                "category": "structure",
                "priority": "low",
                "title": "Define a clear entry point",
                "reason": "No main entry point was detected",
                "action": "Create or identify the main entry point",
                "focus_files": [],
                "suggested_prompt": (
                    "There is no clear entry point in this project. "
                    "Analyze the codebase and create a proper main entry "
                    "point that ties the core modules together."
                ),
            }
        return None

    def _check_recent_changes(self, _: Dict[str, Any], diff: Dict[str, Any], __: Dict[str, Any]) -> Dict[str, Any] | None:
        if diff.get("modified_count", 0) > 10:
            return {
                "category": "review",
                "priority": "medium",
                "title": "Review recent batch of changes",
                "reason": f"{diff['modified_count']} files changed since the last checkpoint",
                "action": "Review changes for consistency and correctness",
                "focus_files": diff.get("modified_files", [])[:6],
                "suggested_prompt": (
                    f"Review the {diff['modified_count']} recently modified files. "
                    "Check for consistency, potential regressions, and whether "
                    "the changes integrate cleanly."
                ),
            }
        if diff.get("new_count", 0) > 0:
            new_files = ", ".join(diff.get("new_files", [])[:5])
            return {
                "category": "integration",
                "priority": "low",
                "title": "Integrate new files",
                "reason": f"New files were added: {new_files}",
                "action": "Ensure new files are wired into the project correctly",
                "focus_files": diff.get("new_files", [])[:5],
                "suggested_prompt": (
                    f"Review these new files: {new_files}. "
                    "Check whether imports, registration points, or documentation "
                    "need to be updated so they are fully integrated."
                ),
            }
        return None

    def _check_dependencies(self, _: Dict[str, Any], __: Dict[str, Any], knowledge: Dict[str, Any]) -> Dict[str, Any] | None:
        if not knowledge.get("dependencies"):
            return {
                "category": "setup",
                "priority": "medium",
                "title": "Document or set up dependencies",
                "reason": "No dependency manifest was detected",
                "action": "Create requirements.txt, pyproject.toml, or equivalent",
                "focus_files": [],
                "suggested_prompt": (
                    "Analyze the project imports and create the appropriate "
                    "dependency manifest with the required runtime dependencies."
                ),
            }
        return None

    def _check_structure(self, audit: Dict[str, Any], _: Dict[str, Any], __: Dict[str, Any]) -> Dict[str, Any] | None:
        metrics = audit.get("metrics", {})
        directories = audit.get("structure", {}).get("directories", [])
        if metrics.get("total_files", 0) > 15 and len(directories) < 3:
            return {
                "category": "structure",
                "priority": "low",
                "title": "Improve project structure",
                "reason": "The project has many files but only a shallow directory structure",
                "action": "Organize files into clearer logical directories",
                "focus_files": [],
                "suggested_prompt": (
                    "This project has many files in a relatively flat structure. "
                    "Suggest and implement a better directory organization, then "
                    "update imports and references accordingly."
                ),
            }
        return None

    def _check_security_basics(self, _: Dict[str, Any], __: Dict[str, Any], knowledge: Dict[str, Any]) -> Dict[str, Any] | None:
        files = knowledge.get("files", {})
        has_env = any(".env" in path for path in files)
        has_gitignore = any(".gitignore" in path for path in files)
        if has_env and not has_gitignore:
            return {
                "category": "security",
                "priority": "critical",
                "title": "Add .gitignore immediately",
                "reason": ".env file found without .gitignore; secrets may be at risk",
                "action": "Create a .gitignore that excludes environment and build artifacts",
                "focus_files": [path for path in files if ".env" in path][:1],
                "suggested_prompt": (
                    "URGENT: There is a .env file but no .gitignore. "
                    "Create a comprehensive .gitignore and verify that no secrets "
                    "have already been committed or shared."
                ),
            }
        return None

    def _make_prompt(self, suggestion: Dict[str, Any]) -> str:
        return suggestion.get("suggested_prompt", suggestion.get("action", ""))

    def build_prompt_pack(
        self,
        goal: str,
        audit: Dict[str, Any],
        diff: Dict[str, Any],
        knowledge: Dict[str, Any],
        suggestions: List[Dict[str, Any]],
        compact_context: str,
        budget: str = "small",
        suggestion_index: int = 0,
    ) -> Dict[str, Any]:
        budget_name = normalize_budget_name(budget, default="small")
        understanding = audit.get("understanding", {})
        selected = suggestions[min(max(suggestion_index, 0), max(len(suggestions) - 1, 0))] if suggestions else None

        focus_limit = {"tiny": 4, "small": 6, "medium": 8, "large": 10}[budget_name]
        focus_files = self._collect_focus_files(audit, diff, selected, limit=focus_limit)
        objective = self._goal_objective(goal, selected, understanding)
        deliverables = self._goal_deliverables(goal, selected)
        top_issues = [
            issue.get("message", "")
            for issue in audit.get("issues", [])[:3]
            if issue.get("message")
        ]
        risk_file_messages: list[str] = []
        for risk in audit.get("risk_scores", [])[:5]:
            surface = risk.get("surface", "runtime")
            if surface not in ("runtime",):
                continue
            path = risk.get("file", "")
            score = risk.get("score", 0)
            factors = risk.get("factors", [])
            deduped_factors: list[str] = []
            seen_set: set[str] = set()
            for f in factors:
                key = f.lower().strip()
                if key not in seen_set:
                    seen_set.add(key)
                    deduped_factors.append(f)
            extra = []
            if deduped_factors:
                extra.append(", ".join(deduped_factors[:2]))
            if score >= 40:
                extra.append(f"risk score {score}")
            parts = [path]
            if extra:
                parts.append(": ".join(extra))
            if len(risk_file_messages) < 3:
                risk_file_messages.append("; ".join(parts))
        summary = understanding.get("summary") or knowledge.get("understanding", {}).get("summary", "")
        project_name = understanding.get("project_name") or "the project"

        prompt_lines = [
            f"You are helping on the project \"{project_name}\".",
            f"Goal: {objective}",
        ]
        if summary:
            prompt_lines.append(f"Project summary: {summary}")
        if understanding.get("frameworks"):
            prompt_lines.append(f"Framework signals: {', '.join(understanding['frameworks'][:6])}")
        if selected:
            prompt_lines.append(f"Recommended next step: [{selected.get('priority', 'low')}] {selected.get('title', '')}")
            prompt_lines.append(f"Why now: {selected.get('reason', '')}")
            prompt_lines.append(f"Action to take: {selected.get('action', '')}")

        if focus_files:
            prompt_lines.append("Focus files first:")
            prompt_lines.extend(f"- {path}" for path in focus_files)

        if risk_file_messages:
            prompt_lines.append("Current risks:")
            prompt_lines.extend(f"- {msg}" for msg in risk_file_messages)
        elif top_issues:
            prompt_lines.append("Current risks:")
            prompt_lines.extend(f"- {issue}" for issue in top_issues)

        if diff.get("modified_count", 0) or diff.get("new_count", 0) or diff.get("deleted_count", 0):
            prompt_lines.append(f"Recent change summary: {diff.get('summary', '')}")

        prompt_lines.append("Constraints:")
        prompt_lines.append("- Minimize token use: start from the compact context below instead of rereading the whole repo.")
        prompt_lines.append("- Only open additional files if the focus files are insufficient.")
        prompt_lines.append("- Preserve the existing architecture unless you have a clear reason to improve it.")
        prompt_lines.append("- If you change code, update or add tests when the behavior is affected.")
        prompt_lines.append("Expected output:")
        prompt_lines.extend(f"- {item}" for item in deliverables)
        prompt_lines.append("")
        prompt_lines.append("Compact project context:")
        prompt_lines.append(compact_context.strip())

        prompt_text = "\n".join(prompt_lines).strip() + "\n"
        return {
            "goal": goal,
            "budget": budget_name,
            "project_name": project_name,
            "objective": objective,
            "deliverables": deliverables,
            "focus_files": focus_files,
            "selected_suggestion": selected,
            "prompt_text": prompt_text,
            "estimated_prompt_tokens": estimate_text_tokens(prompt_text),
        }

    def _collect_focus_files(
        self,
        audit: Dict[str, Any],
        diff: Dict[str, Any],
        selected: Dict[str, Any] | None,
        limit: int,
    ) -> List[str]:
        focus = []
        seen = set()
        understanding = audit.get("understanding", {})
        archetype = understanding.get("archetype", "")

        def add_many(items: List[str]) -> None:
            for item in items:
                if item and item not in seen:
                    seen.add(item)
                    focus.append(item)
                    if len(focus) >= limit:
                        return

        if selected:
            add_many(selected.get("focus_files", []))
        important = audit.get("understanding", {}).get("important_files", [])
        if archetype in ("framework_library",):
            api_important = [item.get("path") for item in important if item.get("path") and ("api/" in item["path"] or "core/" in item["path"])]
            add_many(api_important)
        runtime_important = [item.get("path") for item in important if item.get("path") and classifyRiskSurface(item["path"]) == "runtime"]
        add_many(runtime_important)
        add_many([item.get("path") for item in important if item.get("path")])
        add_many(diff.get("modified_files", []))
        add_many(diff.get("new_files", []))
        return focus[:limit]

    def _goal_objective(
        self,
        goal: str,
        selected: Dict[str, Any] | None,
        understanding: Dict[str, Any],
    ) -> str:
        normalized = goal.strip().lower()
        archetype = understanding.get("archetype", "")
        objectives = {
            "next": "execute the highest-value next engineering step with minimal extra discovery",
            "debug": "find the most likely root cause and produce the safest fix path",
            "review": "review the architecture, risks, and likely regressions before editing",
            "plan": "produce a concrete implementation plan grounded in the current codebase",
            "document": "write or improve documentation that matches the real project structure",
            "test": "improve or repair test coverage around the most important behavior",
        }
        objective = objectives.get(normalized, objectives["next"])
        if archetype == "framework_library" and normalized == "next":
            objective = "map the relevant API/runtime surface, then execute the highest-value next engineering step with minimal extra discovery"
        if archetype == "monorepo" and normalized == "next":
            objective = "select the affected package/app/service first, then execute the highest-value next engineering step with minimal extra discovery"
        if selected and normalized == "next":
            objective = f"{objective}: {selected.get('action', '').rstrip('.')}"
        if normalized == "plan" and understanding.get("hotspots"):
            objective = f"{objective}; start from {understanding['hotspots'][0].get('path', 'the main hotspot')}"
        return objective

    def _goal_deliverables(self, goal: str, selected: Dict[str, Any] | None) -> List[str]:
        normalized = goal.strip().lower()
        common = [
            "Explain the reasoning briefly and reference the most relevant files.",
            "Keep the scope tight and start with the smallest high-leverage change.",
        ]
        if normalized == "debug":
            return [
                "Identify the most likely root cause.",
                "Describe the fix plan before or alongside code changes.",
                "Verify the fix with focused tests or checks.",
                *common,
            ]
        if normalized == "review":
            return [
                "List the highest-risk findings first.",
                "Call out missing tests, unclear ownership, or brittle areas.",
                "Recommend the single best next action.",
                *common,
            ]
        if normalized == "plan":
            return [
                "Produce a short step-by-step plan with file-level focus.",
                "Highlight blockers, assumptions, and dependencies.",
                "Recommend the first implementation slice.",
                *common,
            ]
        if normalized == "document":
            return [
                "Summarize the project purpose, architecture, and commands accurately.",
                "Use the actual entry points and manifests found in the repo.",
                "Avoid generic documentation filler.",
                *common,
            ]
        if normalized == "test":
            return [
                "Target the most important behavior first.",
                "Add or update focused tests for the changed or risky files.",
                "Explain what remains untested.",
                *common,
            ]
        action = selected.get("action", "the recommended next step") if selected else "the recommended next step"
        return [
            f"Execute {action}.",
            "State what you changed and why it is the highest-value move now.",
            "Verify the result with the narrowest useful checks.",
            *common,
        ]

    def format_suggestions(self, suggestions: List[Dict[str, Any]]) -> str:
        if not suggestions:
            return "No urgent suggestions. Project looks healthy."

        lines = ["# Suggested Next Steps", ""]
        for index, suggestion in enumerate(suggestions, start=1):
            lines.append(f"## {index}. [{suggestion['priority'].upper()}] {suggestion['title']}")
            lines.append(f"Category: {suggestion['category']}")
            lines.append(f"Reason: {suggestion['reason']}")
            lines.append(f"Action: {suggestion['action']}")
            lines.append("")
            lines.append("Suggested Prompt:")
            lines.append("```text")
            lines.append(suggestion["suggested_prompt"])
            lines.append("```")
            lines.append("")

        lines.append("Recommended next action: start with suggestion #1.")
        return "\n".join(lines)
