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
import threading
import xml.etree.ElementTree as ET
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from time import perf_counter, sleep
from typing import Any, Dict, Optional

# Ensure local src/ takes priority over PYTHONPATH or other installations
_self_dir = Path(__file__).resolve().parent
if str(_self_dir) not in sys.path:
    sys.path.insert(0, str(_self_dir))

from adapters import build_adapter_docs  # noqa: E402
from auditor import ProjectAuditor  # noqa: E402
from graph import build_python_graph  # noqa: E402
from knowledge import KnowledgeBase  # noqa: E402
from monitor import MonitorService  # noqa: E402
from reporter import ReportGenerator  # noqa: E402
from retriever import retrieve_context  # noqa: E402
from suggester import Suggester  # noqa: E402
from utils import (  # noqa: E402
    DEFAULT_CONFIG,
    ensure_dir,
    ensure_parent_dir,
    estimate_text_tokens,
    is_truthy_env,
    merge_dicts,
    normalize_budget_name,
    read_json,
    resolve_path,
    validate_config,
)
from verifier import verify_patch  # noqa: E402


def _safe_parse_xml(path: Path) -> ET.Element:
    raw = path.read_bytes()
    cleaned = re.sub(rb"<!DOCTYPE[^>]*>", b"", raw)
    return ET.fromstring(cleaned)  # nosec B314 — DTD stripped above, safe


def _extract_setup_version(path: Path) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="ignore")
    import re

    match = re.search(r"version\s*=\s*[\"']([^\"']+)[\"']", text)
    return match.group(1) if match else ""


class SentinelAgent:
    """Main orchestrator for the Project Sentinel agent."""

    def __init__(self, project_dir: str, config_path: Optional[str] = None):
        self.repo_root = Path(__file__).resolve().parent.parent
        self.project_dir = Path(project_dir).resolve()
        if not self.project_dir.exists():
            raise ValueError(f"Project directory does not exist: {self.project_dir}")
        if not self.project_dir.is_dir():
            raise ValueError(
                f"Project path is not a directory: {self.project_dir}. "
                "If this is a file, provide the parent directory instead."
            )

        self.config, self.config_base_dir = self._load_config(config_path)
        state_home = os.getenv("SENTINEL_HOME")
        self.state_root = Path(state_home).expanduser().resolve() if state_home else self.project_dir
        self.knowledge_path = resolve_path(self.state_root, self.config["knowledge_base_path"])
        self.checkpoint_path = resolve_path(self.state_root, self.config["checkpoints_path"])
        self.reports_path = resolve_path(self.state_root, self.config["reports_path"])
        self.log_path = resolve_path(self.state_root, self.config["log_file"])
        self.audit_rules_path = resolve_path(self.config_base_dir, self.config["audit_rules_path"])
        self.patterns_path = resolve_path(self.config_base_dir, self.config["patterns_path"])
        self.project_report_path = self.project_dir / "SENTINEL_REPORT.md"

        self._ensure_runtime_paths()
        self.log = self._configure_logging()

        self.knowledge = KnowledgeBase(str(self.knowledge_path))
        self.auditor = ProjectAuditor(
            str(self.project_dir),
            str(self.checkpoint_path),
            str(self.audit_rules_path),
            str(self.patterns_path),
        )
        self.suggester = Suggester()
        self.reporter = ReportGenerator()
        self.monitor = MonitorService(self.config["scan_interval_seconds"], logger=self.log)
        self.scan_count = 0

    def _load_config(self, config_path: Optional[str]) -> tuple[Dict[str, Any], Path]:
        env_config = os.getenv("SENTINEL_CONFIG_PATH")
        candidates = []
        if config_path:
            candidates.append(Path(config_path).expanduser())
        if env_config:
            candidates.append(Path(env_config).expanduser())
        candidates.append(self.repo_root / "config" / "config.json")

        loaded: Dict[str, Any] = {}
        loaded_from: Optional[Path] = None
        for candidate in candidates:
            resolved = candidate if candidate.is_absolute() else (Path.cwd() / candidate).resolve()
            if resolved.exists():
                possible = read_json(resolved, {})
                if isinstance(possible, dict):
                    loaded = possible
                    loaded_from = resolved
                break

        config = merge_dicts(DEFAULT_CONFIG, loaded)
        errors = validate_config(config)
        if errors:
            source = str(loaded_from) if loaded_from else "built-in defaults"
            detail = "; ".join(errors)
            raise ValueError(f"Invalid Sentinel config from {source}: {detail}")
        config["scan_interval_seconds"] = max(1, int(config.get("scan_interval_seconds", 60)))
        default_config_path = (self.repo_root / "config" / "config.json").resolve()
        if loaded_from and loaded_from.resolve() != default_config_path:
            base_dir = loaded_from.parent
        else:
            base_dir = self.repo_root
        return config, base_dir

    def _ensure_runtime_paths(self) -> None:
        ensure_parent_dir(self.knowledge_path)
        ensure_parent_dir(self.checkpoint_path)
        ensure_parent_dir(self.log_path)
        ensure_dir(self.reports_path)

    def _configure_logging(self) -> logging.Logger:
        level_name = os.getenv("SENTINEL_LOG_LEVEL")
        if not level_name:
            level_name = "DEBUG" if is_truthy_env("SENTINEL_DEBUG_MODE") else "INFO"
        level = getattr(logging, level_name.upper(), logging.INFO)

        logging.basicConfig(
            level=level,
            format="%(asctime)s [%(levelname)s] %(message)s",
            handlers=[
                logging.StreamHandler(sys.stderr),
                logging.FileHandler(self.log_path, encoding="utf-8"),
            ],
            force=True,
        )
        return logging.getLogger("sentinel")

    def scan_once(
        self,
        print_report: bool = True,
        output_format: str = "text",
        compact: bool = False,
        fast_mode: bool = False,
        include_suggestions: Optional[bool] = None,
        create_checkpoint: bool = True,
        top_suggestions: Optional[int] = None,
        extra_ignore_paths: Optional[list[str]] = None,
        use_git_discovery: bool = False,
    ) -> Dict[str, Any]:
        started_at = perf_counter()
        self.scan_count += 1
        self.log.info("Starting scan #%s for %s", self.scan_count, self.project_dir)

        t0 = perf_counter()
        current_files = self.auditor.scan_directory(
            ignore_dirs=self.config["ignore_dirs"],
            extensions=self.config["important_extensions"],
            max_size=int(self.config["max_file_size_bytes"]),
            ignore_paths=self._runtime_ignore_paths(extra_ignore_paths),
            fast_mode=fast_mode,
            use_git_discovery=use_git_discovery,
        )
        t1 = perf_counter()
        diff = self.auditor.diff_from_last_checkpoint(current_files)
        audit = self.auditor.audit_project(current_files)
        t2 = perf_counter()
        dependencies = self._detect_dependencies(current_files)

        self._sync_knowledge(current_files, diff, audit, dependencies)

        checkpoint = None
        should_checkpoint = create_checkpoint and self.config.get("checkpoint_on_significant_change", True)
        if should_checkpoint and self.auditor.is_significant_change(diff):
            checkpoint = self.auditor.create_checkpoint(current_files, audit)
            self.knowledge.set_last_checkpoint(checkpoint["timestamp"], persist=False)
            self.knowledge.save()

        suggestions = []
        if include_suggestions is None:
            include_suggestions = bool(self.config.get("auto_suggest", True))
        if include_suggestions:
            suggestions = self.suggester.generate_suggestions(audit, diff, self.knowledge.data)
            if top_suggestions is not None:
                suggestions = suggestions[: max(0, top_suggestions)]

        self.knowledge.update_understanding(audit.get("understanding", {}), persist=False)
        self.knowledge.update_suggestions(suggestions, persist=False)
        llm_readiness = self._build_llm_readiness(audit, suggestions)
        self.knowledge.update_llm_readiness(llm_readiness, persist=False)
        self.knowledge.save()
        t3 = perf_counter()
        duration = round(t3 - started_at, 4)
        t_discovery = round(t1 - t0, 4)
        t_audit = round(t2 - t1, 4)
        t_suggest = round(t3 - t2, 4)
        performance = {
            "duration_seconds": duration,
            "fast_mode": fast_mode,
            "output_format": output_format,
            "compact": compact,
            "timing": {
                "discovery": t_discovery,
                "audit": t_audit,
                "suggestions": t_suggest,
            },
            "cache": getattr(self.auditor, "last_cache_stats", {}),
            "discovery_mode": getattr(self.auditor, "last_discovery_mode", "walk"),
            "budgets": self.config.get("performance_budgets", {}),
            "budget_alerts": self._evaluate_performance_budgets(
                duration_seconds=duration,
                files_scanned=len(current_files),
                context_tokens=int(llm_readiness.get("estimated_compact_context_tokens", 0) or 0),
            ),
        }
        self.log.debug(
            "Scan timing: discovery=%.4fs audit=%.4fs suggest=%.4fs total=%.4fs files=%d",
            t_discovery, t_audit, t_suggest, duration, len(current_files),
        )
        alerts = self.build_watch_alerts(audit, diff, performance)

        file_list = sorted(
            (
                {
                    "path": path,
                    "lines": info.get("lines", 0),
                    "size": info.get("size", 0),
                }
                for path, info in current_files.items()
            ),
            key=lambda item: item["path"],
        )
        self.knowledge.record_scan_event(
            health_score=audit["health_score"],
            files_scanned=len(current_files),
            diff=diff,
            suggestions=suggestions,
            performance=performance,
            persist=True,
            health_score_data=audit.get("health_score_data"),
        )
        result = {
            "scan_number": self.scan_count,
            "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
            "files_scanned": len(current_files),
            "file_list": file_list,
            "diff": diff,
            "audit": audit,
            "dependencies": dependencies,
            "suggestions": suggestions,
            "checkpoint": checkpoint,
            "project_summary": self.knowledge.get_project_summary(),
            "llm": llm_readiness,
            "performance": performance,
            "alerts": alerts,
            "scan_history": self.knowledge.get_scan_history(limit=20),
        }
        result["project_summary"] = self.knowledge.get_project_summary()

        if print_report:
            print(self._render_result(result, output_format=output_format, compact=compact))

        self.log.info("Completed scan #%s", self.scan_count)
        return result

    def _render_result(self, result: Dict[str, Any], output_format: str = "text", compact: bool = False) -> str:
        if output_format == "json":
            return self.reporter.render_json(result)
        if output_format == "markdown":
            return self.reporter.render_markdown(result)
        if output_format == "brief":
            return self.reporter.render_brief(result)
        if compact:
            return self.reporter.render_compact(result)
        return self.reporter.render_terminal(result)

    def _build_llm_readiness(self, audit: Dict[str, Any], suggestions: list[Dict[str, Any]]) -> Dict[str, Any]:
        recommended_budget = self._recommended_budget(audit.get("metrics", {}))
        compact_context = self.knowledge.export_context(budget=recommended_budget)
        full_tokens = self._estimate_full_context_tokens()
        compact_tokens = estimate_text_tokens(compact_context)
        savings = 0 if full_tokens <= 0 else max(0, round((1 - (compact_tokens / full_tokens)) * 100))
        focus_files = [
            item.get("path")
            for item in audit.get("understanding", {}).get("important_files", [])[:6]
            if item.get("path")
        ]
        if suggestions:
            for path in suggestions[0].get("focus_files", []):
                if path and path not in focus_files:
                    focus_files.append(path)

        return {
            "recommended_budget": recommended_budget,
            "estimated_full_context_tokens": full_tokens,
            "estimated_compact_context_tokens": compact_tokens,
            "estimated_token_savings_percent": savings,
            "focus_files": focus_files[:8],
        }

    def _evaluate_performance_budgets(
        self,
        *,
        duration_seconds: float,
        files_scanned: int,
        context_tokens: int,
    ) -> list[Dict[str, Any]]:
        budgets = self.config.get("performance_budgets", {}) or {}
        checks = [
            ("scan_seconds", duration_seconds, budgets.get("scan_seconds")),
            ("files", files_scanned, budgets.get("files")),
            ("context_tokens", context_tokens, budgets.get("context_tokens")),
        ]
        alerts: list[Dict[str, Any]] = []
        for name, actual, budget in checks:
            if budget is None:
                continue
            try:
                budget_value = float(budget)
            except (TypeError, ValueError):
                continue
            if float(actual) > budget_value:
                alerts.append(
                    {
                        "name": name,
                        "actual": actual,
                        "budget": budget,
                        "message": f"{name} {actual} exceeds budget {budget}",
                    }
                )
        return alerts

    def _recommended_budget(self, metrics: Dict[str, Any]) -> str:
        total_files = int(metrics.get("total_files", 0))
        total_lines = int(metrics.get("total_lines", 0))
        if total_files <= 12 and total_lines <= 1_500:
            return "tiny"
        if total_files <= 60 and total_lines <= 10_000:
            return "small"
        if total_files <= 150 and total_lines <= 30_000:
            return "medium"
        return "large"

    def _estimate_full_context_tokens(self) -> int:
        total_size = sum(info.get("size", 0) for info in self.knowledge.get_all_files().values())
        return max(0, (total_size + 3) // 4)

    def build_context_pack(self, budget: str = "small") -> Dict[str, Any]:
        budget_name = normalize_budget_name(budget, default="small")
        context = self.knowledge.export_context(budget=budget_name)
        full_tokens = self._estimate_full_context_tokens()
        compact_tokens = estimate_text_tokens(context)
        savings = 0 if full_tokens <= 0 else max(0, round((1 - (compact_tokens / full_tokens)) * 100))
        self.knowledge.record_savings("context", full_tokens, compact_tokens, persist=False)
        self.knowledge.save()
        return {
            "budget": budget_name,
            "context": context,
            "estimated_full_context_tokens": full_tokens,
            "estimated_context_tokens": compact_tokens,
            "estimated_token_savings_percent": savings,
        }

    def build_prompt_pack(
        self,
        result: Dict[str, Any],
        goal: str = "next",
        budget: str = "small",
        suggestion_number: int = 1,
    ) -> Dict[str, Any]:
        context_pack = self.build_context_pack(budget=budget)
        prompt_pack = self.suggester.build_prompt_pack(
            goal=goal,
            audit=result["audit"],
            diff=result["diff"],
            knowledge=self.knowledge.data,
            suggestions=result.get("suggestions", []),
            compact_context=context_pack["context"],
            budget=budget,
            suggestion_index=max(0, suggestion_number - 1),
        )
        prompt_pack["estimated_full_context_tokens"] = context_pack["estimated_full_context_tokens"]
        prompt_pack["estimated_context_tokens"] = context_pack["estimated_context_tokens"]
        prompt_pack["estimated_token_savings_percent"] = context_pack["estimated_token_savings_percent"]
        self.knowledge.record_savings(
            "prompt",
            prompt_pack["estimated_full_context_tokens"],
            prompt_pack["estimated_context_tokens"],
            persist=False,
        )
        self.knowledge.save()
        return prompt_pack

    def retrieve(
        self,
        query: str,
        *,
        goal: str = "next",
        limit: int = 6,
        fast_mode: bool = False,
        extra_ignore_paths: Optional[list[str]] = None,
    ) -> Dict[str, Any]:
        files = self.auditor.scan_directory(
            ignore_dirs=self.config["ignore_dirs"],
            extensions=self.config["important_extensions"],
            max_size=int(self.config["max_file_size_bytes"]),
            ignore_paths=self._runtime_ignore_paths(extra_ignore_paths),
            fast_mode=fast_mode,
        )
        result = retrieve_context(
            self.project_dir,
            files,
            query,
            goal=goal,
            limit=limit,
        )
        self.knowledge.record_savings(
            "retrieve",
            result["estimated_full_context_tokens"],
            result["estimated_retrieved_tokens"],
        )
        return result

    def ask(
        self,
        question: str,
        *,
        goal: str = "next",
        limit: int = 6,
        fast_mode: bool = True,
        extra_ignore_paths: Optional[list[str]] = None,
    ) -> Dict[str, Any]:
        scan = self.scan_once(
            print_report=False,
            fast_mode=fast_mode,
            include_suggestions=True,
            create_checkpoint=False,
            extra_ignore_paths=extra_ignore_paths,
        )
        retrieval = self.retrieve(
            question,
            goal=goal,
            limit=limit,
            fast_mode=fast_mode,
            extra_ignore_paths=extra_ignore_paths,
        )
        short_answer = self._build_local_answer(question, retrieval, scan)
        answer = {
            "question": question,
            "goal": goal,
            "short_answer": short_answer,
            "verification_hint": "Run `project-sentinel verify . --dry-run` to preview focused checks after edits.",
            "retrieval": retrieval,
            "scan": scan,
        }
        answer["text"] = self.reporter.render_ask_answer(answer)
        return answer

    def _build_local_answer(self, question: str, retrieval: Dict[str, Any], scan: Dict[str, Any]) -> str:
        files = retrieval.get("files", [])
        symbols = retrieval.get("symbols", [])
        snippets = retrieval.get("snippets", [])
        understanding = scan.get("audit", {}).get("understanding", {})
        if files:
            lead = files[0]
            symbol_text = ""
            if symbols:
                first_symbol = symbols[0]
                symbol_text = (
                    f" The closest symbol match is `{first_symbol.get('qualname')}` "
                    f"in `{first_symbol.get('path')}`."
                )
            snippet_text = " Direct matching snippets were found." if snippets else " No direct line snippet matched, so inspect the ranked files first."
            return (
                f"Start with `{lead.get('path')}` because it is the strongest local match for this question. "
                f"It has score {lead.get('score')} and {lead.get('lines')} lines.{symbol_text}{snippet_text}"
            )
        if understanding.get("summary"):
            return f"No exact file match was found. Project summary: {understanding['summary']}"
        return "No exact local match was found. Try a more specific question with a feature name, file name, symbol, or error text."

    def build_graph_pack(self) -> Dict[str, Any]:
        return build_python_graph(self.project_dir)

    def verify(
        self,
        changed_files: Optional[list[str]] = None,
        *,
        command: Optional[str] = None,
        dry_run: bool = False,
        timeout: int = 120,
    ) -> Dict[str, Any]:
        result = verify_patch(
            self.project_dir,
            changed_files=changed_files,
            command=command,
            dry_run=dry_run,
            timeout=timeout,
        )
        self.knowledge.record_task_memory(
            goal="patch verification",
            changed_files=result.get("changed_files", []),
            tests=result.get("commands", []),
            risks=[] if result.get("passed") is not False else ["verification failed"],
            decisions=[],
            verifier_summary=result.get("summary", ""),
        )
        return result

    def record_task_memory(
        self,
        *,
        goal: str,
        changed_files: list[str],
        tests: list[str],
        risks: list[str],
        decisions: list[str],
        verifier_summary: str = "",
    ) -> Dict[str, Any]:
        return self.knowledge.record_task_memory(
            goal=goal,
            changed_files=changed_files,
            tests=tests,
            risks=risks,
            decisions=decisions,
            verifier_summary=verifier_summary,
        )

    def get_memory(self, limit: int = 10) -> list[Dict[str, Any]]:
        return self.knowledge.get_task_memory(limit=limit)

    def get_savings(self) -> Dict[str, Any]:
        return self.knowledge.get_savings_summary()

    def inspect_bridge_context(self, workspace_dir: str | Path | None = None) -> Dict[str, Any]:
        root = Path(workspace_dir or self.project_dir).resolve()
        status_path = root / ".sentinel" / "kilo" / "status.json"
        if not status_path.exists():
            return {
                "exists": False,
                "fresh": False,
                "message": "no Kilo file bridge status found",
                "invalid_focus_files": [],
            }
        status = read_json(status_path, {})
        if not isinstance(status, dict):
            return {
                "exists": True,
                "fresh": False,
                "message": f"unreadable bridge status: {status_path}",
                "invalid_focus_files": [],
            }
        focus_files = [path for path in status.get("focus_files", []) if isinstance(path, str)]
        invalid = [path for path in focus_files if not (root / path).exists()]
        fresh = bool(status.get("context_fresh", not invalid)) and not invalid
        message = "bridge context is fresh" if fresh else "bridge context is stale; run project-sentinel kilo-refresh"
        return {
            "exists": True,
            "fresh": fresh,
            "message": message,
            "generated_at": status.get("generated_at"),
            "invalid_focus_files": invalid,
            "status_path": str(status_path),
        }

    def mcp_health(self) -> Dict[str, Any]:
        try:
            from sentinel_mcp import SentinelMCPServer

            server = SentinelMCPServer(project_dir=str(self.project_dir), config_path=None)
            tools = server.list_tools()
            names = {tool.get("name") for tool in tools}
            required = {"sentinel_context", "sentinel_overview", "sentinel_prompt"}
            missing = sorted(required - names)
            return {
                "ok": not missing,
                "message": "MCP tools available" if not missing else f"missing MCP tools: {', '.join(missing)}",
                "tools": sorted(name for name in names if isinstance(name, str)),
                "missing": missing,
            }
        except Exception as exc:
            return {
                "ok": False,
                "message": f"MCP health check failed: {exc}",
                "tools": [],
                "missing": ["sentinel_context", "sentinel_overview", "sentinel_prompt"],
            }

    def doctor(self) -> Dict[str, Any]:
        checks = []
        config_errors = validate_config(self.config)
        checks.append(
            {
                "name": "config",
                "ok": not config_errors,
                "message": "valid" if not config_errors else "; ".join(config_errors),
            }
        )
        for label, path in [
            ("project_dir", self.project_dir),
            ("knowledge_path_parent", self.knowledge_path.parent),
            ("checkpoint_path_parent", self.checkpoint_path.parent),
            ("reports_path", self.reports_path),
            ("audit_rules_path", self.audit_rules_path),
            ("patterns_path", self.patterns_path),
        ]:
            checks.append(
                {
                    "name": label,
                    "ok": Path(path).exists(),
                    "message": str(path),
                }
            )
        bridge = self.inspect_bridge_context(self.project_dir)
        checks.append(
            {
                "name": "kilo_file_bridge",
                "ok": not bridge.get("exists") or bridge.get("fresh", False),
                "message": bridge.get("message", ""),
            }
        )
        mcp = self.mcp_health()
        checks.append(
            {
                "name": "mcp_surface",
                "ok": mcp.get("ok", False),
                "message": mcp.get("message", ""),
            }
        )
        return {
            "project_dir": str(self.project_dir),
            "config_base_dir": str(self.config_base_dir),
            "checks": checks,
            "ok": all(check["ok"] for check in checks),
        }

    def _runtime_ignore_paths(self, extra_ignore_paths: Optional[list[str]] = None) -> list[str]:
        base_paths = [
            str(self.knowledge_path),
            str(self.checkpoint_path),
            str(self.reports_path),
            str(self.log_path),
            str(self.project_report_path),
        ]
        for raw_path in extra_ignore_paths or []:
            base_paths.append(str(resolve_path(self.project_dir, raw_path)))
        return base_paths

    def _sync_knowledge(
        self,
        current_files: Dict[str, Dict[str, Any]],
        diff: Dict[str, Any],
        audit: Dict[str, Any],
        dependencies: Dict[str, Any],
    ) -> None:
        for filepath, info in current_files.items():
            self.knowledge.update_file_info(filepath, info, persist=False)

        for deleted in diff.get("deleted_files", []):
            self.knowledge.remove_file(deleted, persist=False)

        self.knowledge.replace_patterns(audit.get("patterns", []), persist=False)
        self.knowledge.replace_issues(audit.get("issues", []), persist=False)
        self.knowledge.update_architecture(audit.get("architecture", {}), persist=False)
        self.knowledge.update_understanding(audit.get("understanding", {}), persist=False)
        self.knowledge.update_dependencies(dependencies, persist=False)
        self.knowledge.set_last_scan(persist=False)
        self.knowledge.save()

    def _detect_dependencies(self, files: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        dependencies: Dict[str, Any] = {}
        filenames = {Path(path).name: path for path in files}

        python_manifests = [
            filenames[name]
            for name in ["requirements.txt", "pyproject.toml", "setup.py"]
            if name in filenames
        ]
        if python_manifests:
            dependencies["python"] = {"manifests": python_manifests}

        if "package.json" in filenames:
            dependencies["node"] = {"manifests": [filenames["package.json"]]}

        container_files = [
            path
            for path in files
            if Path(path).name.lower() in {"dockerfile", "docker-compose.yml", "docker-compose.yaml"}
        ]
        if container_files:
            dependencies["containers"] = {"manifests": container_files}

        return dependencies

    def cleanup_reports(self, keep: int = 5, dry_run: bool = True) -> Dict[str, Any]:
        reports_dir = self.reports_path
        reports = sorted(
            reports_dir.glob("SENTINEL_REPORT_*.md"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        ) if reports_dir.exists() else []
        keep = max(0, keep)
        kept = reports[:keep]
        old = reports[keep:]
        archived_dir = reports_dir / "historical"
        moved = []
        if not dry_run and old:
            archived_dir.mkdir(parents=True, exist_ok=True)
            for path in old:
                target = archived_dir / path.name
                path.replace(target)
                moved.append(str(target))
        return {
            "reports_path": str(reports_dir),
            "dry_run": dry_run,
            "kept": [str(path) for path in kept],
            "historical": [str(path) for path in old],
            "moved": moved,
            "message": (
                f"{len(old)} old report(s) would be marked historical"
                if dry_run
                else f"{len(moved)} old report(s) moved to historical/"
            ),
        }

    def autofix(self, dry_run: bool = True) -> Dict[str, Any]:
        actions: list[dict[str, Any]] = []
        gitignore = self.project_dir / ".gitignore"
        if (self.project_dir / ".env").exists() and not gitignore.exists():
            content = ".env\n__pycache__/\n.pytest_cache/\n.ruff_cache/\n.sentinel/\n"
            actions.append({"type": "create_gitignore", "path": str(gitignore), "applied": False})
            if not dry_run:
                gitignore.write_text(content, encoding="utf-8")
                actions[-1]["applied"] = True

        bridge = self.inspect_bridge_context(self.project_dir)
        if bridge.get("exists") and not bridge.get("fresh"):
            actions.append(
                {
                    "type": "refresh_bridge_recommended",
                    "path": bridge.get("status_path"),
                    "invalid_focus_files": bridge.get("invalid_focus_files", []),
                    "applied": False,
                }
            )

        readme = self.project_dir / "README.md"
        if readme.exists():
            text = readme.read_text(encoding="utf-8", errors="ignore")
            if "TODO" in text or "TBD" in text:
                actions.append({"type": "doc_placeholder_found", "path": str(readme), "applied": False})

        return {
            "project_dir": str(self.project_dir),
            "dry_run": dry_run,
            "actions": actions,
            "message": f"{len(actions)} safe autofix action(s) {'planned' if dry_run else 'processed'}",
        }

    def pr_summary(self, verify: bool = False, timeout: int = 120) -> Dict[str, Any]:
        from verifier import detect_changed_files, suggest_test_commands

        changed = detect_changed_files(self.project_dir)
        commands = suggest_test_commands(self.project_dir, changed)
        verification = None
        if verify:
            verification = self.verify(changed_files=changed, timeout=timeout)
        risks = []
        latest = self.scan_once(print_report=False, fast_mode=True, include_suggestions=True, create_checkpoint=False)
        risk_by_file = {item["file"]: item for item in latest["audit"].get("risk_scores", [])}
        for path in changed:
            risk = risk_by_file.get(path)
            if risk:
                risks.append(risk)
        return {
            "project_dir": str(self.project_dir),
            "changed_files": changed,
            "suggested_tests": commands,
            "risks": risks,
            "top_suggestion": latest.get("suggestions", [None])[0],
            "verification": verification,
        }

    def memory_timeline(self, limit: int = 20) -> Dict[str, Any]:
        return {
            "project_dir": str(self.project_dir),
            "scans": self.knowledge.get_scan_history(limit=limit),
            "tasks": self.knowledge.get_task_memory(limit=limit),
            "savings": self.knowledge.get_savings_summary(),
        }

    def coverage_report(self) -> Dict[str, Any]:
        coverage_xml = self.project_dir / "coverage.xml"
        if not coverage_xml.exists():
            return {
                "project_dir": str(self.project_dir),
                "exists": False,
                "message": "coverage.xml not found; run coverage xml or pytest --cov first",
                "files": [],
                "untested_hotspots": [],
            }
        root = _safe_parse_xml(coverage_xml)
        files: list[dict[str, Any]] = []
        for cls in root.findall(".//class"):
            filename = cls.attrib.get("filename", "")
            rate = float(cls.attrib.get("line-rate", 0) or 0)
            files.append({"file": filename, "line_rate": rate, "percent": round(rate * 100, 1)})
        latest = self.scan_once(print_report=False, fast_mode=True, include_suggestions=False, create_checkpoint=False)
        risky = latest["audit"].get("risk_scores", [])
        coverage_map = {Path(item["file"]).as_posix(): item for item in files}
        untested = []
        for risk in risky:
            cov = coverage_map.get(risk["file"]) or coverage_map.get(Path(risk["file"]).as_posix())
            if cov is None or cov.get("line_rate", 0) < 0.7:
                untested.append({"risk": risk, "coverage": cov})
        return {
            "project_dir": str(self.project_dir),
            "exists": True,
            "files": files,
            "untested_hotspots": untested[:10],
            "message": f"Loaded coverage for {len(files)} file(s)",
        }

    def benchmark_run(self, fast_mode: bool = False) -> Dict[str, Any]:
        fixtures_dir = self.repo_root / "benchmarks" / "fixtures"
        if not fixtures_dir.exists():
            return {"ok": False, "message": "benchmarks/fixtures/ not found", "results": []}
        results = []
        for entry in sorted(fixtures_dir.iterdir()):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            agent = SentinelAgent(str(entry), config_path=str(self.config_base_dir / "config.json"))
            start = perf_counter()
            scan = agent.scan_once(
                print_report=False,
                fast_mode=fast_mode,
                include_suggestions=False,
                create_checkpoint=False,
            )
            elapsed = perf_counter() - start
            health = scan["audit"]["health_score"]
            metrics = scan["audit"]["metrics"]
            results.append({
                "fixture": entry.name,
                "files": metrics["total_files"],
                "lines": metrics["total_lines"],
                "time_seconds": round(elapsed, 3),
                "health": health,
            })
        return {
            "ok": True,
            "message": f"Benchmarked {len(results)} fixture(s)",
            "results": results,
        }

    def build_watch_alerts(
        self,
        audit: Dict[str, Any],
        diff: Dict[str, Any],
        performance: Dict[str, Any],
    ) -> list[Dict[str, Any]]:
        alerts: list[Dict[str, Any]] = []
        health = int(audit.get("health_score", 0) or 0)
        if health < 55:
            alerts.append({"severity": "high", "type": "health", "message": f"Health score is low at {health}%"})
        elif health < 75:
            alerts.append({"severity": "medium", "type": "health", "message": f"Health score needs attention at {health}%"})

        changed = int(diff.get("new_count", 0)) + int(diff.get("modified_count", 0)) + int(diff.get("deleted_count", 0))
        if changed >= 25:
            alerts.append({"severity": "medium", "type": "change_volume", "message": f"{changed} files changed since the last checkpoint"})

        for budget_alert in performance.get("budget_alerts", []):
            alerts.append(
                {
                    "severity": "medium",
                    "type": "performance_budget",
                    "message": budget_alert.get("message", "A performance budget was exceeded"),
                }
            )

        high_risks = [item for item in audit.get("risk_scores", []) if item.get("level") == "high"]
        if high_risks:
            alerts.append(
                {
                    "severity": "medium",
                    "type": "risk_hotspots",
                    "message": f"{len(high_risks)} high-risk file(s) are currently ranked for review",
                    "files": [item.get("file") for item in high_risks[:5] if item.get("file")],
                }
            )

        coverage = audit.get("scan_coverage", {})
        if coverage.get("warning"):
            alerts.append({"severity": "medium", "type": "scan_coverage", "message": coverage["warning"]})
        return alerts

    def evidence_report(self, query: str = "", fast_mode: bool = True, limit: int = 6) -> Dict[str, Any]:
        scan = self.scan_once(print_report=False, fast_mode=fast_mode, include_suggestions=True, create_checkpoint=False)
        audit = scan.get("audit", {})
        issues_by_file: dict[str, list[Dict[str, Any]]] = {}
        for issue in audit.get("issues", []):
            path = issue.get("file") or ""
            if path:
                issues_by_file.setdefault(path, []).append(issue)

        drilldowns = []
        for risk in audit.get("risk_scores", [])[:15]:
            path = risk.get("file", "")
            info = self.knowledge.get_file_info(path) or {}
            drilldowns.append(
                {
                    "file": path,
                    "risk": risk,
                    "issues": issues_by_file.get(path, [])[:5],
                    "symbols": info.get("symbols", [])[:8],
                    "imports": info.get("imports", [])[:8],
                    "line_count": info.get("line_count", 0),
                    "todo_count": info.get("todo_count", 0),
                    "why": ", ".join(risk.get("factors", [])) or "ranked by Sentinel risk scoring",
                }
            )

        suggestion_evidence = []
        for suggestion in scan.get("suggestions", [])[:8]:
            confidence = suggestion.get("confidence", {})
            suggestion_evidence.append(
                {
                    "title": suggestion.get("title", ""),
                    "priority": suggestion.get("priority", "medium"),
                    "reason": suggestion.get("reason", ""),
                    "focus_files": suggestion.get("focus_files", []),
                    "confidence": confidence,
                    "evidence": confidence.get("evidence", []),
                    "uncertainty": confidence.get("uncertainty", []),
                    "verification": suggestion.get("verification", {}),
                }
            )

        retrieval = None
        if query.strip():
            retrieval = self.retrieve(query, limit=limit, fast_mode=fast_mode)

        return {
            "project_dir": str(self.project_dir),
            "query": query,
            "scan": scan,
            "alerts": scan.get("alerts", []),
            "diff_impact": self._build_diff_impact(scan),
            "suggestions": suggestion_evidence,
            "drilldowns": drilldowns,
            "retrieval": retrieval,
        }

    def decision_ledger(self, limit: int = 20) -> Dict[str, Any]:
        return {
            "project_dir": str(self.project_dir),
            "decisions": self.knowledge.data.get("decisions", [])[-max(1, limit):],
            "tasks": self.knowledge.get_task_memory(limit=limit),
            "scans": self.knowledge.get_scan_history(limit=limit),
            "savings": self.knowledge.get_savings_summary(),
        }

    def save_static_bundle(self, output_dir: Optional[str] = None, fast_mode: bool = True) -> Dict[str, Any]:
        destination = Path(output_dir).resolve() if output_dir else self.reports_path / f"static_bundle_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        ensure_dir(destination)
        scan = self.scan_once(print_report=False, fast_mode=fast_mode, include_suggestions=True, create_checkpoint=False)
        context_pack = self.build_context_pack(budget="medium")
        prompt_pack = self.build_prompt_pack(scan, goal="next", budget="small")
        markdown = self.reporter.render_markdown(scan, knowledge_context=context_pack["context"])
        html = self.reporter.render_html(scan, knowledge_context=context_pack["context"])
        insights = self.evidence_report(fast_mode=fast_mode)

        index_path = destination / "index.html"
        artifacts = {
            "index": str(index_path),
            "markdown": str(destination / "SENTINEL_REPORT.md"),
            "html": str(destination / "SENTINEL_REPORT.html"),
            "context": str(destination / "CONTEXT.md"),
            "prompt": str(destination / "NEXT_PROMPT.md"),
            "analysis": str(destination / "analysis.json"),
        }
        ensure_parent_dir(index_path).write_text(html, encoding="utf-8")
        (destination / "SENTINEL_REPORT.md").write_text(markdown, encoding="utf-8")
        (destination / "SENTINEL_REPORT.html").write_text(html, encoding="utf-8")
        (destination / "CONTEXT.md").write_text(context_pack["context"], encoding="utf-8")
        (destination / "NEXT_PROMPT.md").write_text(prompt_pack.get("prompt_text", ""), encoding="utf-8")
        (destination / "analysis.json").write_text(
            json.dumps({"scan": scan, "context": context_pack, "prompt": prompt_pack, "insights": insights}, indent=2, default=str),
            encoding="utf-8",
        )
        return {
            "project_dir": str(self.project_dir),
            "output_dir": str(destination),
            "artifacts": artifacts,
            "health_score": scan.get("audit", {}).get("health_score"),
            "files_scanned": scan.get("files_scanned"),
        }

    def scan_speed_plan(self, fast_mode: bool = True) -> Dict[str, Any]:
        scan = self.scan_once(print_report=False, fast_mode=fast_mode, include_suggestions=False, create_checkpoint=False)
        perf = scan.get("performance", {})
        cache = perf.get("cache", {})
        metrics = scan.get("audit", {}).get("metrics", {})
        return {
            "project_dir": str(self.project_dir),
            "current": {
                "duration_seconds": perf.get("duration_seconds", 0),
                "files_scanned": scan.get("files_scanned", 0),
                "lines": metrics.get("total_lines", 0),
                "cache": cache,
                "quality_position": "Preserve exact per-file analysis for changed files; reuse cached analysis only when size and mtime match.",
            },
            "implemented": [
                "Persistent scan metadata cache beside checkpoints",
                "Cache reuse for unchanged files with full-scan entries eligible for fast and full scans",
                "Opt-in git-aware candidate file discovery with os.walk fallback",
                "Bounded parallel analysis for cache misses",
                "Dashboard and JSON performance cache counters",
            ],
            "plan": [
                {
                    "phase": "1. Incremental risk recomputation",
                    "impact": "medium",
                    "work": "Recompute global metrics from cached per-file facts, but recompute expensive per-file extraction only for misses.",
                    "quality_guard": "Keep aggregate health and suggestions derived from the complete file map every scan.",
                },
                {
                    "phase": "2. Optional native index",
                    "impact": "very large",
                    "work": "Store cache in SQLite with indexes on path, extension, component, symbols, imports, risk score, and TODO count.",
                    "quality_guard": "SQLite is an acceleration layer only; JSON report output remains canonical and reproducible.",
                },
                {
                    "phase": "3. Configurable source-only profile",
                    "impact": "large",
                    "work": "Expose git discovery as a documented source-only scan mode for cloned repositories and CI, while local scans keep workspace-inclusive behavior.",
                    "quality_guard": "The default local scan remains os.walk based so ignored local artifacts do not silently disappear from existing reports.",
                },
            ],
            "non_goals": [
                "Do not skip changed files.",
                "Do not sample full scans.",
                "Do not drop TODO, import, symbol, or risk extraction to hit a timer.",
            ],
        }

    def _build_diff_impact(self, scan: Dict[str, Any]) -> Dict[str, Any]:
        diff = scan.get("diff", {})
        changed = [*diff.get("new_files", []), *diff.get("modified_files", []), *diff.get("deleted_files", [])]
        risk_map = {item.get("file"): item for item in scan.get("audit", {}).get("risk_scores", [])}
        return {
            "summary": diff.get("summary", ""),
            "changed_files": changed[:50],
            "changed_count": len(changed),
            "risky_changed_files": [risk_map[path] for path in changed if path in risk_map],
        }

    def release_check(self) -> Dict[str, Any]:
        files = {path.name.lower(): path for path in self.project_dir.iterdir() if path.is_file()}
        setup_path = self.project_dir / "setup.py"
        readme_path = self.project_dir / "README.md"
        version = _extract_setup_version(setup_path)
        checks = [
            {"name": "README", "ok": readme_path.exists(), "message": str(readme_path)},
            {"name": "LICENSE", "ok": "license" in files or "license.md" in files, "message": "license file present"},
            {"name": "package metadata", "ok": setup_path.exists(), "message": str(setup_path)},
            {"name": "version", "ok": bool(version), "message": version or "version not found"},
            {"name": "tests", "ok": (self.project_dir / "tests").is_dir(), "message": "tests directory present"},
        ]
        help_check = subprocess.run(
            [sys.executable, str(self.repo_root / "sentinel.py"), "--help"],
            cwd=self.project_dir,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        checks.append(
            {
                "name": "CLI help",
                "ok": help_check.returncode == 0 and "Project Sentinel" in help_check.stdout,
                "message": "project-sentinel help renders" if help_check.returncode == 0 else help_check.stderr.strip(),
            }
        )
        doctor = self.doctor()
        checks.append({"name": "doctor", "ok": doctor.get("ok", False), "message": "doctor ready" if doctor.get("ok") else "doctor found issues"})
        return {
            "project_dir": str(self.project_dir),
            "version": version,
            "checks": checks,
            "ready": all(check["ok"] for check in checks),
        }

    def run_dashboard(self, host: str = "127.0.0.1", port: int = 8765, interval: int = 10, fast_mode: bool = True) -> None:
        from dashboard import _DASHBOARD_HTML, _run_dashboard_action

        state: Dict[str, Any] = {"latest": None, "history": self.knowledge.get_scan_history(limit=50)}
        stop_event = threading.Event()
        dashboard_agent = self

        def scan_loop() -> None:
            while not stop_event.is_set():
                try:
                    state["latest"] = self.scan_once(
                        print_report=False,
                        fast_mode=fast_mode,
                        include_suggestions=True,
                        create_checkpoint=True,
                    )
                    state["history"] = self.knowledge.get_scan_history(limit=50)
                except Exception as exc:  # pragma: no cover - dashboard resilience
                    state["error"] = str(exc)
                stop_event.wait(max(1, interval))

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return

            def do_GET(self) -> None:  # noqa: N802
                try:
                    if self.path == "/api/status":
                        payload = json.dumps(state, indent=2, default=str).encode("utf-8")
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(payload)))
                        self.end_headers()
                        self.wfile.write(payload)
                        return
                    if self.path.startswith("/logos/"):
                        logo_path = Path(__file__).resolve().parent.parent / "logos" / self.path[len("/logos/"):]
                        if logo_path.exists():
                            data = logo_path.read_bytes()
                            ctype = "image/svg+xml" if logo_path.suffix == ".svg" else "image/png"
                            self.send_response(200)
                            self.send_header("Content-Type", ctype)
                            self.send_header("Content-Length", str(len(data)))
                            self.end_headers()
                            self.wfile.write(data)
                            return
                        self.send_error(404, "Logo not found")
                        return
                    html = _DASHBOARD_HTML.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(html)))
                    self.end_headers()
                    self.wfile.write(html)
                except Exception:
                    try:
                        self.send_response(500)
                        self.send_header("Content-Type", "text/plain")
                        self.end_headers()
                        self.wfile.write(b"Internal server error")
                    except Exception:
                        pass

            def do_POST(self) -> None:  # noqa: N802
                if self.path != "/api/run":
                    self.send_error(404, "Not found")
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0") or "0")
                    raw = self.rfile.read(length).decode("utf-8") if length else "{}"
                    request = json.loads(raw) if raw.strip() else {}
                    result = _run_dashboard_action(dashboard_agent, request)
                    state["last_action"] = result
                    payload = json.dumps(result, indent=2, default=str).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                except Exception as exc:  # pragma: no cover - dashboard resilience
                    try:
                        payload = json.dumps({"ok": False, "error": str(exc)}, indent=2).encode("utf-8")
                        self.send_response(500)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(payload)))
                        self.end_headers()
                        self.wfile.write(payload)
                    except Exception:
                        pass

        worker = threading.Thread(target=scan_loop, daemon=True)
        worker.start()
        server = ThreadingHTTPServer((host, port), Handler)
        print(f"Sentinel dashboard running at http://{host}:{port}")
        try:
            server.serve_forever()
        finally:
            stop_event.set()
            server.server_close()

    def get_full_report(self, fast_mode: bool = False) -> str:
        result = self.scan_once(print_report=False, fast_mode=fast_mode)
        knowledge_context = self.knowledge.export_context(budget="medium")
        return self.reporter.render_markdown(result, knowledge_context=knowledge_context)

    def get_html_report(self, fast_mode: bool = False) -> str:
        result = self.scan_once(print_report=False, fast_mode=fast_mode)
        knowledge_context = self.knowledge.export_context(budget="medium")
        return self.reporter.render_html(result, knowledge_context=knowledge_context)

    def save_full_report(self, destination: Optional[str] = None, fast_mode: bool = False) -> Dict[str, Any]:
        report_text = self.get_full_report(fast_mode=fast_mode)
        primary = Path(destination).resolve() if destination else self.project_report_path
        archive = self.reports_path / f"SENTINEL_REPORT_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"

        self.reporter.save_markdown(report_text, primary)
        self.reporter.save_markdown(report_text, archive)

        return {
            "report": report_text,
            "primary_path": str(primary),
            "archive_path": str(archive),
        }

    def save_html_report(self, destination: Optional[str] = None, fast_mode: bool = False) -> Dict[str, Any]:
        report_text = self.get_html_report(fast_mode=fast_mode)
        primary = Path(destination).resolve() if destination else self.project_dir / "SENTINEL_REPORT.html"
        archive = self.reports_path / f"SENTINEL_REPORT_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"

        self.reporter.save_markdown(report_text, primary)
        self.reporter.save_markdown(report_text, archive)

        return {
            "report": report_text,
            "primary_path": str(primary),
            "archive_path": str(archive),
        }

    def run_continuous(self, fast_mode: bool = False, compact: bool = False) -> None:
        self.monitor.interval_seconds = int(self.config["scan_interval_seconds"])
        print(f"Sentinel started for {self.project_dir}")
        print(f"Scan interval: {self.monitor.interval_seconds} second(s)")
        print("Press Ctrl+C to stop.")
        self.monitor.run(
            lambda: self.scan_once(
                print_report=True,
                output_format="text",
                compact=compact,
                fast_mode=fast_mode,
            )
        )
        self.close()
        print("Sentinel stopped.")

    def get_status(self) -> Dict[str, Any]:
        return {
            "project_dir": str(self.project_dir),
            "state_root": str(self.state_root),
            "summary": self.knowledge.get_project_summary(),
            "understanding": self.knowledge.data.get("understanding", {}),
            "top_suggestion": self.knowledge.get_top_suggestion(),
            "storage": {
                "knowledge_base_path": str(self.knowledge_path),
                "checkpoints_path": str(self.checkpoint_path),
                "reports_path": str(self.reports_path),
                "log_path": str(self.log_path),
            },
        }

    def close(self) -> None:
        logging.shutdown()
