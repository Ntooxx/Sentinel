#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import tempfile
import xml.etree.ElementTree as ET
from copy import deepcopy
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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

        result = {
            "scan_number": self.scan_count,
            "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
            "files_scanned": len(current_files),
            "diff": diff,
            "audit": audit,
            "dependencies": dependencies,
            "suggestions": suggestions,
            "checkpoint": checkpoint,
            "project_summary": self.knowledge.get_project_summary(),
            "llm": llm_readiness,
            "performance": performance,
            "alerts": alerts,
        }
        self.knowledge.record_scan_event(
            health_score=audit["health_score"],
            files_scanned=len(current_files),
            diff=diff,
            suggestions=suggestions,
            performance=performance,
            persist=True,
            health_score_data=audit.get("health_score_data"),
        )
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
                    html = _dashboard_html(self.server.server_address, self.path).encode("utf-8")
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


def _run_dashboard_action(agent: SentinelAgent, request: Dict[str, Any]) -> Dict[str, Any]:
    action = str(request.get("action", "")).strip()
    if not action:
        raise ValueError("dashboard action is required")

    fast = _request_bool(request, "fast", True)
    goal = str(request.get("goal") or "next")
    budget = normalize_budget_name(str(request.get("budget") or "small"), default="small")
    limit = _request_int(request, "limit", 6)
    top = _request_int(request, "top", 1)
    text = ""
    data: Any = None
    artifacts: list[str] = []

    if action == "scan":
        data = agent.scan_once(print_report=False, fast_mode=fast, include_suggestions=True)
        text = agent.reporter.render_terminal(data)
    elif action == "brief":
        data = agent.scan_once(print_report=False, fast_mode=fast, include_suggestions=True, top_suggestions=top)
        text = agent.reporter.render_brief(data)
    elif action == "overview":
        data = agent.scan_once(print_report=False, fast_mode=fast, include_suggestions=True)
        text = agent.reporter.render_overview(data)
    elif action == "context":
        agent.scan_once(print_report=False, fast_mode=fast, include_suggestions=True)
        data = agent.build_context_pack(budget=budget)
        text = agent.reporter.render_context_pack(data)
    elif action == "prompt":
        scan = agent.scan_once(print_report=False, fast_mode=fast, include_suggestions=True)
        data = agent.build_prompt_pack(scan, goal=goal, budget=budget, suggestion_number=top)
        text = agent.reporter.render_prompt_pack(data)
    elif action == "retrieve":
        query = str(request.get("query") or request.get("question") or "").strip()
        if not query:
            raise ValueError("retrieve requires a query")
        data = agent.retrieve(query, goal=goal, limit=limit, fast_mode=fast)
        text = data["text"]
    elif action == "ask":
        question = str(request.get("question") or request.get("query") or "").strip()
        if not question:
            raise ValueError("ask requires a question")
        data = agent.ask(question, goal=goal, limit=limit, fast_mode=fast)
        text = data["text"]
    elif action == "graph":
        data = agent.build_graph_pack()
        text = _render_graph_pack(data)
    elif action == "verify":
        data = agent.verify(
            changed_files=_request_list(request, "changed_files") or None,
            command=str(request.get("verify_command") or "").strip() or None,
            dry_run=_request_bool(request, "dry_run", True),
            timeout=_request_int(request, "timeout", 120),
        )
        text = _render_verify_result(data)
    elif action == "memory_list":
        data = agent.get_memory(limit=_request_int(request, "memory_limit", 10))
        text = _render_memory(data)
    elif action == "memory_record":
        data = agent.record_task_memory(
            goal=str(request.get("memory_goal") or request.get("question") or "dashboard note"),
            changed_files=_request_list(request, "changed_files"),
            tests=_request_list(request, "tests"),
            risks=_request_list(request, "risks"),
            decisions=_request_list(request, "decisions"),
            verifier_summary=str(request.get("verifier_summary") or ""),
        )
        text = _render_memory([data])
    elif action == "savings":
        data = agent.get_savings()
        text = _render_savings(data)
    elif action == "doctor":
        data = agent.doctor()
        text = _render_doctor(data)
    elif action == "autofix":
        data = agent.autofix(dry_run=not _request_bool(request, "apply", False))
        text = _render_autofix(data)
    elif action == "pr":
        data = agent.pr_summary(verify=_request_bool(request, "verify", False), timeout=_request_int(request, "timeout", 120))
        text = _render_pr_summary(data)
    elif action == "timeline":
        data = agent.memory_timeline(limit=_request_int(request, "memory_limit", 20))
        text = _render_timeline(data)
    elif action == "mcp_health":
        data = agent.mcp_health()
        data["bridge"] = agent.inspect_bridge_context(agent.project_dir)
        text = _render_mcp_health(data)
    elif action == "coverage":
        data = agent.coverage_report()
        text = _render_coverage(data)
    elif action == "insights":
        data = agent.evidence_report(
            query=str(request.get("query") or request.get("question") or ""),
            fast_mode=fast,
            limit=limit,
        )
        text = _render_insights(data)
    elif action == "alerts":
        scan = agent.scan_once(print_report=False, fast_mode=fast, include_suggestions=True, create_checkpoint=False)
        data = {"project_dir": str(agent.project_dir), "alerts": scan.get("alerts", []), "scan": scan}
        text = _render_alerts(data)
    elif action == "ledger":
        data = agent.decision_ledger(limit=_request_int(request, "memory_limit", 20))
        text = _render_ledger(data)
    elif action == "speed_plan":
        data = agent.scan_speed_plan(fast_mode=fast)
        text = _render_speed_plan(data)
    elif action == "bundle":
        data = agent.save_static_bundle(
            output_dir=str(request.get("output_dir") or request.get("output_path") or "").strip() or None,
            fast_mode=fast,
        )
        artifacts = list(data.get("artifacts", {}).values())
        text = _render_static_bundle(data)
    elif action == "cleanup_reports":
        data = agent.cleanup_reports(
            keep=_request_int(request, "keep_reports", 5),
            dry_run=not _request_bool(request, "apply", False),
        )
        text = _render_cleanup_reports(data)
    elif action == "release_check":
        data = agent.release_check()
        text = _render_release_check(data)
    elif action == "adapters":
        data = build_adapter_docs(agent.project_dir, write=_request_bool(request, "write", False))
        text = _render_adapters(data)
    elif action == "report_markdown":
        destination = str(request.get("output_path") or "").strip() or None
        data = agent.save_full_report(destination=destination, fast_mode=fast)
        artifacts = [data["primary_path"], data["archive_path"]]
        text = f"Markdown report saved to {data['primary_path']}\nArchived copy saved to {data['archive_path']}\n"
    elif action == "report_html":
        destination = str(request.get("output_path") or "").strip() or None
        data = agent.save_html_report(destination=destination, fast_mode=fast)
        artifacts = [data["primary_path"], data["archive_path"]]
        text = f"HTML report saved to {data['primary_path']}\nArchived HTML copy saved to {data['archive_path']}\n"
    elif action == "report_both":
        destination = str(request.get("output_path") or "").strip() or None
        html_destination = str(Path(destination).resolve().with_suffix(".html")) if destination else None
        markdown = agent.save_full_report(destination=destination, fast_mode=fast)
        html_report = agent.save_html_report(destination=html_destination, fast_mode=fast)
        data = {"markdown": markdown, "html": html_report}
        artifacts = [
            markdown["primary_path"],
            markdown["archive_path"],
            html_report["primary_path"],
            html_report["archive_path"],
        ]
        text = (
            f"Markdown report saved to {markdown['primary_path']}\n"
            f"HTML report saved to {html_report['primary_path']}\n"
        )
    elif action == "analyze_url":
        repo_url = str(request.get("repo_url") or "").strip()
        if not repo_url:
            raise ValueError("analyze-url requires a repository URL or git source")
        data = analyze_repository_url(
            repo_url,
            output_dir=str(request.get("output_dir") or "").strip() or None,
            keep_clone=_request_bool(request, "keep_clone", False),
            fast_mode=fast,
            html_report=not _request_bool(request, "no_html", False),
            timeout=_request_int(request, "timeout", 300),
        )
        artifacts = [
            path
            for path in [
                data.get("markdown_report"),
                data.get("html_report"),
                data.get("context_pack"),
                data.get("prompt"),
                data.get("summary_path"),
            ]
            if path
        ]
        text = _render_analyze_url(data)
    elif action == "kilo_refresh":
        data = refresh_kilo_bridge(
            workspace_dir=str(request.get("workspace_dir") or agent.project_dir),
            scan_root=str(request.get("scan_root") or "."),
            budget=budget,
            goal=goal,
            fast_mode=fast,
            write_root_files=not _request_bool(request, "no_root_context", False),
        )
        artifacts = [path for path in data.get("paths", {}).values() if path]
        text = (
            f"Kilo bridge refreshed: {data.get('paths', {}).get('root_context')}\n"
            f"Focus files: {data.get('paths', {}).get('focus_files')}\n"
            f"Health: {data.get('health_score')}%\n"
        )
    elif action == "kilo_setup":
        data = setup_kilo_integration(
            workspace_dir=str(request.get("workspace_dir") or agent.project_dir),
            scan_root=str(request.get("scan_root") or "."),
            budget=budget,
            fast_mode=fast,
            portable=_request_bool(request, "portable", False),
            force=_request_bool(request, "force", False),
        )
        artifacts = [
            data.get("kilo_jsonc_path"),
            data.get("root_kilo_path"),
            data.get("legacy_mcp_path"),
            data.get("rule_path"),
            data.get("agent_path"),
        ]
        text = _render_json_lines("KILO SETUP", data)
    elif action == "features":
        data = {"features": True}
        text = _render_features()
    else:
        raise ValueError(f"unknown dashboard action: {action}")

    return {
        "ok": True,
        "action": action,
        "text": text,
        "data": data,
        "artifacts": artifacts,
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def _request_bool(request: Dict[str, Any], name: str, default: bool = False) -> bool:
    value = request.get(name, default)
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "apply", "run"}


def _request_int(request: Dict[str, Any], name: str, default: int) -> int:
    try:
        return int(request.get(name, default))
    except (TypeError, ValueError):
        return default


def _request_list(request: Dict[str, Any], name: str) -> list[str]:
    value = request.get(name, [])
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in re.split(r"[\n,]+", str(value)) if item.strip()]


def _render_json_lines(title: str, data: Dict[str, Any]) -> str:
    return title + "\n" + json.dumps(data, indent=2, sort_keys=True) + "\n"


def _extract_setup_version(path: Path) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="ignore")
    import re

    match = re.search(r"version\s*=\s*[\"']([^\"']+)[\"']", text)
    return match.group(1) if match else ""


def _dashboard_html(_: tuple[str, int], __: str) -> str:
    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sentinel</title>
<style>
:root{color-scheme:dark;--bg:#080c14;--surface:#101826;--surface2:#161f2f;--line:#1e2a3d;--ink:#e6edf5;--muted:#7b8fa8;--accent:#4b9eff;--accent-dim:#4b9eff22;--good:#3dd68c;--warn:#f0b429;--bad:#f56565;--radius:10px;--radius-sm:6px;--shadow:0 2px 8px #00000020}
*{box-sizing:border-box}
body{margin:0;font-family:Inter,system-ui,-apple-system,Segoe UI,Roboto,Noto Sans,sans-serif;background:var(--bg);color:var(--ink);line-height:1.5;-webkit-font-smoothing:antialiased}
main{max-width:1400px;margin:0 auto;padding:24px 22px}
h1{font-size:26px;font-weight:700;margin:0;letter-spacing:-.02em}
h2{font-size:15px;font-weight:600;margin:0 0 10px;letter-spacing:-.01em}
h3{font-size:13px;font-weight:600;margin:0 0 4px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted)}
p{margin:0 0 8px}
.muted{color:var(--muted);font-size:13px}
.good{color:var(--good)}.warn{color:var(--warn)}.bad{color:var(--bad)}

/* header */
.top{display:flex;justify-content:space-between;align-items:center;margin-bottom:18px;padding:0 0 16px;border-bottom:1px solid var(--line)}
.top-left{display:flex;align-items:center;gap:12px}
.top-left .logo{width:32px;height:32px;background:var(--accent);border-radius:8px;display:flex;align-items:center;justify-content:center;font-weight:900;font-size:16px;color:#080c14;flex-shrink:0}
.top-right{display:flex;align-items:center;gap:10px}
.badge-pulse{display:inline-flex;align-items:center;gap:6px;padding:4px 10px;border-radius:999px;background:var(--surface2);border:1px solid var(--line);font-size:12px;color:var(--muted)}
.badge-pulse::before{content:"";width:6px;height:6px;border-radius:50%;background:var(--good);animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}

/* stats row */
.stats{display:grid;grid-template-columns:repeat(6,1fr);gap:10px;margin-bottom:16px}
.stat{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:12px 14px}
.stat .lbl{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:700}
.stat .val{font-size:20px;font-weight:800;margin-top:1px;letter-spacing:-.02em}
.stat .val small{font-size:13px;font-weight:600;color:var(--muted)}

/* card */
.card{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:16px}
.card-accent{border-left:3px solid var(--accent)}

/* dl grid */
.dl{display:grid;grid-template-columns:auto 1fr;gap:2px 16px;font-size:13.5px}
.dt{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.06em;font-weight:700;margin-top:8px}
.dt:first-of-type{margin-top:0}
.dd{margin:0;word-break:break-word}

/* shared inputs */
.inputs{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:16px;margin-bottom:14px}
.input-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
.input-grid.lg{grid-template-columns:repeat(2,1fr)}
.input-grid label{display:flex;flex-direction:column;gap:3px;font-size:12px;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.04em}
.input-grid label.double{grid-column:span 2}
input,select,textarea{width:100%;background:var(--surface2);border:1px solid var(--line);border-radius:var(--radius-sm);color:var(--ink);padding:7px 10px;font:inherit;font-size:13px;outline:none;transition:border .15s}
input:focus,select:focus,textarea:focus{border-color:var(--accent)}
textarea{min-height:68px;resize:vertical}
.toggles{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}
.toggles label{display:flex;gap:6px;align-items:center;color:var(--muted);font-size:12.5px;cursor:pointer;padding:3px 8px;border:1px solid var(--line);border-radius:999px;background:var(--surface2);transition:all .15s}
.toggles label:has(input:checked){border-color:var(--accent);color:var(--accent);background:var(--accent-dim)}
.toggles input{width:auto;accent-color:var(--accent)}

/* tool cards */
.tools{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:14px}
.tool{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:14px;display:flex;flex-direction:column;gap:8px}
.tool p{margin:0;font-size:13px;color:var(--muted);line-height:1.4}
.tool .btns{display:flex;flex-wrap:wrap;gap:5px}
.tool.wide{grid-column:span 2}

/* buttons */
button{background:var(--accent-dim);color:var(--accent);border:1px solid transparent;border-radius:var(--radius-sm);font-weight:600;font-size:12px;padding:6px 11px;cursor:pointer;transition:all .12s;white-space:nowrap}
button:hover{background:var(--accent);color:#080c14}
button.primary{background:var(--accent);color:#080c14}
button.primary:hover{opacity:.85}
button.danger{color:var(--bad);background:#f5656515}
button.danger:hover{background:var(--bad);color:#080c14}
button:disabled{opacity:.5;cursor:wait}

/* layout cols */
.cols{display:grid;grid-template-columns:1.15fr .85fr;gap:14px;margin-bottom:14px}
.cols-3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin-bottom:14px}

/* output */
.output{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:16px;display:flex;flex-direction:column}
.term{flex:1;background:var(--surface2);border:1px solid var(--line);border-radius:var(--radius-sm);padding:12px 14px;font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:13px;line-height:1.5;white-space:pre-wrap;overflow:auto;min-height:260px;max-height:460px;margin:6px 0 0}
.artifact{display:block;color:var(--good);font-size:13px;margin:4px 0;word-break:break-all}

/* suggestions list */
.suggest-list{list-style:none;padding:0;margin:0}
.suggest-list li{padding:7px 0;border-bottom:1px solid var(--line);font-size:13px;line-height:1.4}
.suggest-list li:last-child{border-bottom:none}
.suggest-list li .tag{display:inline-block;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;padding:1px 6px;border-radius:4px;margin-right:5px}

/* pills */
.pills{display:flex;flex-wrap:wrap;gap:5px}
.pill,.badge{display:inline-flex;border:1px solid var(--line);background:var(--surface2);border-radius:999px;padding:2px 8px;font-size:12px}
.badge{border-radius:4px;font-weight:700;text-transform:uppercase;letter-spacing:.03em}
.badge.high{background:#f5656515;border-color:#f5656533;color:var(--bad)}
.badge.medium{background:#f0b42915;border-color:#f0b42933;color:var(--warn)}
.badge.low{background:#4b9eff15;border-color:#4b9eff33;color:var(--accent)}

/* table */
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:7px 8px;vertical-align:top;border-bottom:1px solid var(--line)}
th{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:700}
tr:last-child td{border-bottom:none}
.table-wrap{overflow-x:auto;margin-top:6px}

/* code */
code{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:12px;word-break:break-all}
pre{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:13px;line-height:1.5;white-space:pre-wrap;overflow:auto;background:var(--surface2);border:1px solid var(--line);border-radius:var(--radius-sm);padding:12px 14px;max-height:380px;margin:6px 0 0}

/* divider */
hr{border:none;border-top:1px solid var(--line);margin:14px 0}

@media(max-width:1200px){.tools{grid-template-columns:repeat(3,1fr)}.stats{grid-template-columns:repeat(3,1fr)}}
@media(max-width:860px){.tools{grid-template-columns:repeat(2,1fr)}.cols,.cols-3{grid-template-columns:1fr}.input-grid,.input-grid.lg{grid-template-columns:1fr 1fr}.input-grid label.double{grid-column:span 1}}
@media(max-width:550px){main{padding:16px 14px}.stats{grid-template-columns:repeat(2,1fr)}.tools{grid-template-columns:1fr}.input-grid,.input-grid.lg{grid-template-columns:1fr}.top{flex-direction:column;align-items:flex-start;gap:10px}}
</style>
</head>
<body>
<main>

<!-- top bar -->
<div class="top">
  <div class="top-left">
    <div class="logo">S</div>
    <h1>Sentinel</h1>
    <span class="muted" style="font-size:13px">Command Center</span>
  </div>
  <div class="top-right">
    <span class="muted" id="updated">Loading...</span>
    <button class="primary" onclick="load()">Refresh</button>
  </div>
</div>

<!-- stats -->
<div class="stats" id="stats"></div>

<!-- identity + risk -->
<div class="cols">
  <div class="card"><h2>Project Identity</h2><div class="dl" id="identity"></div></div>
  <div class="card card-accent"><h2>Risk Summary</h2><div class="dl" id="risk"></div></div>
</div>

<!-- shared inputs -->
<div class="inputs">
  <div class="input-grid">
    <label>Query<input id="question" placeholder="where is auth handled?"></label>
    <label>Repo URL<input id="repoUrl" placeholder="https://github.com/user/repo"></label>
    <label>Output path<input id="outputPath" placeholder="report path / output dir"></label>
    <label>Scan root<input id="scanRoot" placeholder="."></label>
    <label>Workspace<input id="workspaceDir" placeholder="optional workspace dir"></label>
  </div>
  <div class="input-grid" style="margin-top:8px">
    <label>Goal<select id="goal"><option>next</option><option>debug</option><option>review</option><option>plan</option><option>document</option><option>test</option></select></label>
    <label>Budget<select id="budget"><option>small</option><option>tiny</option><option>medium</option><option>large</option></select></label>
    <label>Limit<input id="limit" type="number" value="6" min="1"></label>
    <label>Timeout<input id="timeout" type="number" value="120" min="1"></label>
  </div>
  <div class="input-grid lg" style="margin-top:8px">
    <label class="double">Changed files / tests<textarea id="changedFiles" placeholder="app.py&#10;tests/test_app.py"></textarea></label>
    <label class="double">Memory note<textarea id="memoryGoal" placeholder="what changed, decision, or task note"></textarea></label>
  </div>
  <div class="toggles">
    <label><input id="fast" type="checkbox" checked> fast</label>
    <label><input id="dryRun" type="checkbox" checked> dry-run</label>
    <label><input id="apply" type="checkbox"> apply</label>
    <label><input id="verify" type="checkbox"> verify</label>
    <label><input id="write" type="checkbox"> adapters</label>
    <label><input id="keepClone" type="checkbox"> keep clone</label>
    <label><input id="force" type="checkbox"> force</label>
  </div>
</div>

<!-- tool cards -->
<div class="tools">
  <div class="tool">
    <h3>Understand</h3>
    <p>Scan, summarize, retrieve, and generate prompts.</p>
    <div class="btns"><button onclick="run('scan')">Scan</button><button onclick="run('overview')">Overview</button><button onclick="run('brief')">Brief</button><button onclick="run('context')">Context</button><button onclick="run('prompt')">Prompt</button><button onclick="run('graph')">Graph</button><button onclick="run('insights')">Insights</button><button onclick="run('alerts')">Alerts</button></div>
  </div>
  <div class="tool">
    <h3>Ask</h3>
    <p>Local retrieval, symbols, snippets, and project memory.</p>
    <div class="btns"><button onclick="run('ask')">Ask</button><button onclick="run('retrieve')">Retrieve</button><button onclick="run('features')">Features</button></div>
  </div>
  <div class="tool">
    <h3>Reports</h3>
    <p>Shareable markdown and HTML reports.</p>
    <div class="btns"><button onclick="run('report_html')">HTML</button><button onclick="run('report_markdown')">Markdown</button><button onclick="run('report_both')">Both</button><button onclick="run('bundle')">Bundle</button></div>
  </div>
  <div class="tool">
    <h3>Quality</h3>
    <p>Verification, PR, release readiness, coverage, health.</p>
    <div class="btns"><button onclick="run('verify')">Verify</button><button onclick="run('pr')">PR</button><button onclick="run('coverage')">Coverage</button><button onclick="run('release_check')">Release</button><button onclick="run('doctor')">Doctor</button><button onclick="run('mcp_health')">MCP</button></div>
  </div>
  <div class="tool">
    <h3>Memory</h3>
    <p>History, token savings, lightweight task memory.</p>
    <div class="btns"><button onclick="run('timeline')">Timeline</button><button onclick="run('savings')">Savings</button><button onclick="run('memory_list')">List</button><button onclick="run('memory_record')">Record</button><button onclick="run('ledger')">Ledger</button><button onclick="run('speed_plan')">Speed</button></div>
  </div>
  <div class="tool">
    <h3>Maintenance</h3>
    <p>Fix plans, clean reports, adapters, Kilo refresh.</p>
    <div class="btns"><button onclick="run('autofix')">Autofix</button><button onclick="run('cleanup_reports')">Cleanup</button><button onclick="run('adapters')">Adapters</button><button onclick="run('kilo_refresh')">Kilo Refresh</button><button onclick="run('kilo_setup')">Kilo Setup</button></div>
  </div>
  <div class="tool wide">
    <h3>Analyze Repository URL</h3>
    <p>Paste a git URL or local git source. Sentinel clones, scans, and bundles a report.</p>
    <div class="btns"><button class="primary" onclick="run('analyze_url')">Analyze URL</button></div>
  </div>
</div>

<!-- output + suggestions -->
<div class="cols">
  <div class="output">
    <div style="display:flex;justify-content:space-between;align-items:center"><h2>Output</h2><span class="muted" style="font-size:11px">terminal</span></div>
    <pre class="term" id="runOutput">Choose an action above.</pre>
    <div id="artifacts"></div>
  </div>
  <div class="card" style="display:flex;flex-direction:column">
    <h2>Suggestions</h2>
    <ul class="suggest-list" id="suggestions"></ul>
    <hr>
    <h2>Agent Prompt</h2>
    <pre id="prompt" style="flex:1;min-height:80px">Loading...</pre>
  </div>
</div>

<!-- focus + hotspots + frameworks -->
<div class="cols-3">
  <div class="card"><h2>Focus Files</h2><div class="pills" id="focus"></div></div>
  <div class="card"><h2>Hotspots</h2><div class="pills" id="hotspots"></div></div>
  <div class="card"><h2>Frameworks</h2><div class="pills" id="frameworks"></div></div>
</div>

<!-- risks + signals -->
<div class="cols">
  <div class="card">
    <h2>File Risks</h2>
    <div class="table-wrap"><table><thead><tr><th>Level</th><th>File</th><th>Score</th></tr></thead><tbody id="fileRisks"></tbody></table></div>
  </div>
  <div class="card">
    <h2>Review Signals</h2>
    <div class="table-wrap"><table><thead><tr><th>Severity</th><th>Message</th><th>File</th></tr></thead><tbody id="issues"></tbody></table></div>
  </div>
</div>

<!-- timeline -->
<div class="card" style="margin-top:4px"><h2>Health Timeline</h2><pre id="timeline">Loading...</pre></div>
</main>
<script>
const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const pill = value => `<span class="pill">${esc(value)}</span>`;
const levelClass = value => {
  const n = Number(value || 0);
  if (n >= 80) return 'good';
  if (n >= 55) return 'warn';
  return 'bad';
};
const testSignal = risk => {
  const value = String((risk.test || {}).level || 'unknown');
  return ({high:'strong', good:'strong', medium:'present', low:'limited'})[value] || value;
};
const confirmedIssueCount = issues => (issues || []).filter(issue => {
  const severity = String(issue.severity || '').toLowerCase();
  const type = String(issue.type || '').toLowerCase();
  return ['critical', 'high'].includes(severity) && !['todo', 'large_file', 'large_file_size', 'doc_code_drift'].includes(type);
}).length;
function payload(action){
  const output = document.getElementById('outputPath').value.trim();
  return {
    action,
    question: document.getElementById('question').value.trim(),
    query: document.getElementById('question').value.trim(),
    repo_url: document.getElementById('repoUrl').value.trim(),
    output_path: output,
    output_dir: output,
    workspace_dir: document.getElementById('workspaceDir').value.trim(),
    scan_root: document.getElementById('scanRoot').value.trim() || '.',
    goal: document.getElementById('goal').value,
    budget: document.getElementById('budget').value,
    limit: Number(document.getElementById('limit').value || 6),
    timeout: Number(document.getElementById('timeout').value || 120),
    changed_files: document.getElementById('changedFiles').value,
    tests: document.getElementById('changedFiles').value,
    risks: '',
    decisions: '',
    memory_goal: document.getElementById('memoryGoal').value.trim(),
    fast: document.getElementById('fast').checked,
    dry_run: document.getElementById('dryRun').checked,
    apply: document.getElementById('apply').checked,
    verify: document.getElementById('verify').checked,
    write: document.getElementById('write').checked,
    keep_clone: document.getElementById('keepClone').checked,
    force: document.getElementById('force').checked
  };
}
async function run(action){
  const buttons = [...document.querySelectorAll('button')];
  buttons.forEach(button => button.disabled = true);
  const out = document.getElementById('runOutput');
  const artifacts = document.getElementById('artifacts');
  out.textContent = `Running ${action}...`;
  artifacts.innerHTML = '';
  try{
    const response = await fetch('/api/run', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify(payload(action))
    });
    const result = await response.json();
    if(!response.ok || !result.ok){
      out.textContent = result.error || 'Action failed';
      return;
    }
    out.textContent = result.text || JSON.stringify(result.data, null, 2);
    artifacts.innerHTML = (result.artifacts || []).map(path =>
      `<span class="artifact">${esc(path)}</span>`
    ).join('');
    load();
  }catch(error){
    out.textContent = String(error);
  }finally{
    buttons.forEach(button => button.disabled = false);
  }
}
async function load(){
  let data;
  try{
    data = await fetch('/api/status').then(r=>r.json());
  }catch(error){
    document.getElementById('updated').textContent = 'Connection error: ' + String(error);
    return;
  }
  const latest = data.latest || {};
  const audit = latest.audit || {};
  const perf = latest.performance || {};
  const cache = perf.cache || {};
  const metrics = audit.metrics || {};
  const understanding = audit.understanding || {};
  const risk = audit.risk_summary || {};
  const llm = latest.llm || {};
  const suggestions = latest.suggestions || [];
  const issues = audit.issues || [];
  const fileRisks = audit.risk_scores || [];
  const confirmedIssues = confirmedIssueCount(issues);
  document.getElementById('updated').textContent = latest.timestamp ? `Updated ${latest.timestamp}` : 'Waiting for scan';
  document.getElementById('stats').innerHTML = [
    ['Health', (audit.health_score ?? '-') + '%', levelClass(audit.health_score)],
    ['Files', metrics.total_files ?? '-'],
    ['Lines', metrics.total_lines ?? '-'],
    ['Confirmed Issues', confirmedIssues],
    ['Review Signals', issues.length],
    ['Duration', (perf.duration_seconds ?? 0) + 's'],
    ['Cache Hits', cache.hits ?? 0],
    ['Alerts', (latest.alerts || []).length],
    ['Token Savings', ((latest.llm || {}).estimated_token_savings_percent ?? '-') + '%']
  ].map(([k,v,c])=>`<div class="card"><div class="label">${esc(k)}</div><div class="value ${c || ''}">${esc(v)}</div></div>`).join('');
  document.getElementById('identity').innerHTML = `
    <p><b>${esc(understanding.project_name || 'unknown')}</b></p>
    <p class="muted">${esc(understanding.project_type || 'unknown project type')}</p>
    <p>${esc(understanding.purpose || understanding.summary || 'No purpose inferred yet.')}</p>
  `;
  document.getElementById('risk').innerHTML = ['maintainability','runtime','test','security'].map(name => {
    const item = risk[name] || {};
    const label = name === 'test' ? testSignal(risk) : (item.level || 'unknown');
    return `<p><b>${esc(name)}</b>: ${esc(label)} <span class="muted">${esc(item.reason || '')}</span></p>`;
  }).join('');
  document.getElementById('suggestions').innerHTML = suggestions.slice(0,8).map(s =>
    `<li><b>[${esc(s.priority)}] ${esc(s.title)}</b><br><span class="muted">${esc(s.action || '')}</span><br><span class="muted">${esc(s.ranking_label || '')} confidence=${esc((s.confidence||{}).level || 'unknown')}</span></li>`
  ).join('') || '<li>No suggestions yet.</li>';
  document.getElementById('prompt').textContent = suggestions[0]?.suggested_prompt || 'No prompt generated yet.';
  document.getElementById('focus').innerHTML = (llm.focus_files || []).slice(0,10).map(pill).join('') || '<span class="muted">No focus files yet.</span>';
  document.getElementById('hotspots').innerHTML = (understanding.hotspots || []).slice(0,10).map(h => pill(h.path)).join('') || '<span class="muted">No hotspots yet.</span>';
  document.getElementById('frameworks').innerHTML = (understanding.frameworks || []).slice(0,10).map(pill).join('') || '<span class="muted">No frameworks detected.</span>';
  document.getElementById('fileRisks').innerHTML = fileRisks.slice(0,10).map(r =>
    `<tr><td><span class="badge">${esc(r.level)}</span></td><td><code>${esc(r.file)}</code></td><td>${esc(r.score)}</td></tr>`
  ).join('') || '<tr><td colspan="3" class="muted">No file risks yet.</td></tr>';
  document.getElementById('issues').innerHTML = issues.slice(0,10).map(i =>
    `<tr><td><span class="badge">${esc(i.severity)}</span></td><td>${esc(i.message)}</td><td><code>${esc(i.file || '')}</code></td></tr>`
  ).join('') || '<tr><td colspan="3" class="muted">No review signals detected.</td></tr>';
  document.getElementById('timeline').textContent = (data.history || []).map(h =>
    `${h.timestamp} health=${h.health_score}% changes=${h.change_summary}`
  ).join('\\n') || 'No scan history yet.';
}
load(); setInterval(load, 3000);
</script>
</body>
</html>"""


def _build_kilo_rule(scan_root: str, budget: str) -> str:
    lines = [
        "# Sentinel-first workflow",
        "",
        "Use Sentinel MCP tools before broad repo reads, large grep searches, or multi-file exploration.",
        "",
        "## How to call Sentinel",
        "- Use Kilo's MCP tool dispatcher, not a written XML/function block.",
        "- Call `use_mcp_tool` with `server_name: sentinel`.",
        "- Use `tool_name: sentinel_prompt`, `tool_name: sentinel_overview`, or `tool_name: sentinel_context`.",
        "- Do not pass the permission key `sentinel_sentinel_prompt` as the MCP `tool_name`.",
        "",
        "## Required flow",
        "- For implementation, debugging, review, or planning tasks, call Sentinel tool `sentinel_prompt` first.",
        "- For architecture or onboarding questions, call Sentinel tool `sentinel_overview` first.",
        "- For compact repo context, call Sentinel tool `sentinel_context` before opening more files.",
        "- For task-specific context, run `project-sentinel retrieve . --query \"<task>\" --goal debug --fast`.",
        "- Start with the returned focus files and do not read additional files unless they are needed.",
        "- Re-run a Sentinel tool after major edits or when the task focus changes.",
        "- After edits, run `project-sentinel verify .` or `project-sentinel verify . --dry-run` to choose narrow tests.",
        "",
        "## Token strategy",
        f"- Prefer the Sentinel compact context pack with budget `{budget}` over raw repo dumping.",
        "- Use Sentinel context to decide which files deserve full reads.",
        "- Avoid broad codebase searches until Sentinel focus files are exhausted.",
        "",
        "## Tool arguments",
        "- The default MCP scan root is already configured; normally call Sentinel tools with no `project_dir` argument.",
        f"- If you pass `project_dir`, use `{scan_root}` for the default target.",
        "- Use `goal: next` for implementation, `goal: debug` for bug work, and `goal: review` for review work.",
        "",
        "## Permission names",
        "- Kilo may show permission names like `sentinel_sentinel_prompt`; these are approval keys only.",
        "- The actual MCP tool names remain `sentinel_prompt`, `sentinel_overview`, and `sentinel_context`.",
        "",
        "## Failure modes",
        "- If MCP calls are ignored or unavailable, read `CONTEXT.md` and `.sentinel/kilo/prompt.md` instead.",
        "- If context feels stale, run `project-sentinel kilo-refresh . --scan-root "
        f"{scan_root} --budget {budget} --fast`.",
        "- If retrieval returns too many files, retry with a narrower query including the function, error, or command name.",
        "- If verification cannot infer tests, pass `project-sentinel verify . --command \"<test command>\"`.",
    ]
    if scan_root != ".":
        lines.extend(
            [
                "",
                "## Default target",
                f"- Sentinel is configured to scan `{scan_root}` by default in this workspace.",
                "- If you need a different area, pass `project_dir` explicitly to the Sentinel MCP tool.",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _build_kilo_agent(scan_root: str, budget: str) -> str:
    return (
        "---\n"
        "description: Sentinel-first coding agent for low-token work on this project\n"
        "mode: primary\n"
        "color: \"#0EA5A4\"\n"
        "permission:\n"
        "  read: allow\n"
        "  grep: allow\n"
        "  glob: allow\n"
        "  list: allow\n"
        "  edit: allow\n"
        "  bash: ask\n"
        "  sentinel_sentinel_context: allow\n"
        "  sentinel_sentinel_overview: allow\n"
        "  sentinel_sentinel_prompt: allow\n"
        "---\n"
        "\n"
        "# Sentinel Code Agent\n"
        "\n"
        "You are the Sentinel-first coding agent for this workspace.\n"
        "\n"
        "Before broad repo exploration, use Sentinel MCP tools:\n"
        "\n"
        "- Call `use_mcp_tool` with `server_name: sentinel` and `tool_name: sentinel_prompt` for implementation, debugging, review, and planning.\n"
        "- Call `use_mcp_tool` with `server_name: sentinel` and `tool_name: sentinel_overview` for architecture or onboarding questions.\n"
        "- Call `use_mcp_tool` with `server_name: sentinel` and `tool_name: sentinel_context` when you need compact project context.\n"
        "- Do not write a fake `<function=tool>` block in chat; dispatch the MCP tool through Kilo.\n"
        "- Do not use `sentinel_sentinel_prompt` as the MCP `tool_name`; that is only Kilo's permission key.\n"
        f"- The default target is `{scan_root}`, with compact context budget `{budget}`.\n"
        "\n"
        "Work from Sentinel's focus files first. Read more files only when the focus files do not answer the task.\n"
        "For exact task context, run `project-sentinel retrieve . --query \"<task>\" --goal debug --fast`.\n"
        "After meaningful edits, run `project-sentinel verify .` and record risks with `project-sentinel memory record`.\n"
    )


def _build_kilo_file_bridge_rule(scan_root: str, budget: str) -> str:
    return (
        "# Sentinel file bridge\n"
        "\n"
        "Sentinel writes compact project context into normal workspace files so Kilo can use it without MCP.\n"
        "\n"
        "## Required flow\n"
        "- Read `CONTEXT.md` before broad exploration.\n"
        "- If `CONTEXT.md` is missing or stale, run `project-sentinel kilo-refresh . --scan-root "
        f"{scan_root} --budget {budget} --fast`.\n"
        "- Use `.sentinel/kilo/prompt.md` as the task brief when implementation, debugging, planning, or review starts.\n"
        "- Use `.sentinel/kilo/focus-files.txt` as the first file list to inspect.\n"
        "- Only search or open extra files after the focus files are insufficient.\n"
        "- Refresh with `project-sentinel kilo-refresh . --scan-root "
        f"{scan_root} --budget {budget} --fast` after meaningful edits.\n"
    )


def _build_agents_file(scan_root: str, budget: str) -> str:
    return (
        "# Project Agent Instructions\n"
        "\n"
        "Use Sentinel before broad project exploration.\n"
        "\n"
        "Primary path, no MCP required:\n"
        f"- Read `CONTEXT.md`, which Sentinel generates for `{scan_root}` with budget `{budget}`.\n"
        "- Use `.sentinel/kilo/prompt.md` as the task brief.\n"
        "- Start with `.sentinel/kilo/focus-files.txt` and only read more files when needed.\n"
        "- Use `project-sentinel retrieve . --query \"...\"` for task-specific context.\n"
        "- Use `project-sentinel verify .` after edits to run narrow checks.\n"
        f"- If context is stale or missing, run `project-sentinel kilo-refresh . --scan-root {scan_root} --budget {budget} --fast`.\n"
        "- After meaningful edits, refresh Sentinel before continuing broad analysis.\n"
        "\n"
        "Optional MCP path, if MCP is healthy:\n"
        "- Use Kilo's MCP dispatcher with `server_name: sentinel` and `tool_name: sentinel_prompt`.\n"
        "- Do not write a fake `<function=tool>` block in chat.\n"
        "- Do not pass `sentinel_sentinel_prompt` as the MCP `tool_name`; that is only Kilo's permission key.\n"
        "\n"
        f"Sentinel is configured to scan `{scan_root}` by default in this workspace.\n"
    )


def _build_root_context(
    *,
    scan_root: str,
    budget: str,
    generated_at: str,
    context_text: str,
    prompt_pack: Dict[str, Any],
    overview_path: str,
    context_path: str,
    prompt_path: str,
    focus_path: str,
    context_fresh: bool = True,
    invalid_focus_files: Optional[list[str]] = None,
) -> str:
    focus_files = prompt_pack.get("focus_files", [])
    lines = [
        "# Sentinel Context",
        "",
        f"Generated: {generated_at}",
        f"Target: `{scan_root}`",
        f"Budget: `{budget}`",
        f"Prompt Tokens: {prompt_pack.get('estimated_prompt_tokens', 0)}",
        "",
        "Kilo should start from this generated Sentinel context before broad file reads.",
        "",
        "## Freshness",
        f"- Context Fresh: {'yes' if context_fresh else 'no'}",
        "- Path Validation: "
        + ("all focus files exist" if context_fresh else "some focus files are missing"),
        "",
        "## Files",
        f"- Overview: `{overview_path}`",
        f"- Context Pack: `{context_path}`",
        f"- Prompt Pack: `{prompt_path}`",
        f"- Focus Files: `{focus_path}`",
        "",
        "## Focus Files",
        "Paths are relative to the workspace root.",
    ]
    if focus_files:
        lines.extend(f"- `{path}`" for path in focus_files)
    else:
        lines.append("- None yet")

    if not context_fresh:
        lines.extend(
            [
                "",
                "## Stale Context",
                "- Some focus files do not exist from the workspace root.",
                f"- Missing: {', '.join(invalid_focus_files or [])}",
                "- Recommended refresh: `project-sentinel kilo-refresh . --scan-root "
                f"{scan_root} --budget {budget} --fast`",
            ]
        )

    selected = prompt_pack.get("selected_suggestion") or {}
    if selected:
        lines.extend(
            [
                "",
                "## Recommended Next Step",
                f"- Title: {selected.get('title', 'Unknown')}",
                f"- Priority: {selected.get('priority', 'unknown')}",
                f"- Action: {selected.get('action', '')}",
                f"- Reason: {selected.get('reason', '')}",
            ]
        )

    lines.extend(
        [
            "",
            "## Working Rule",
            "- Read focus files first.",
            "- Only open extra files if the focus files are insufficient.",
            "- Refresh Sentinel after meaningful edits with `project-sentinel kilo-refresh . --scan-root "
            f"{scan_root} --budget {budget} --fast`.",
            "",
            "## Compact Context",
            "",
            context_text.strip(),
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _write_kilo_ignore(root: Path) -> Path:
    ignore_path = root / ".kilocodeignore"
    patterns = [
        ".git/",
        ".kilo/node_modules/",
        ".kilocode/node_modules/",
        ".pytest_cache/",
        ".ruff_cache/",
        ".sentinel/reports/",
        "tools/sentinel/src/__pycache__/",
        "**/__pycache__/",
        "sentinel_cli_*.md",
        "sentinel_help_*.md",
    ]
    ensure_parent_dir(ignore_path).write_text("\n".join(patterns).rstrip() + "\n", encoding="utf-8")
    return ignore_path


def _merge_unique_strings(existing: Any, additions: list[str]) -> list[str]:
    values = [item for item in existing if isinstance(item, str)] if isinstance(existing, list) else []
    for item in additions:
        if item not in values:
            values.append(item)
    return values


def _write_root_kilo_compat_config(root: Path, scan_root: str, budget: str, fast_mode: bool) -> Path:
    kilo_path = root / "kilo.json"
    payload = read_json(kilo_path, {}) if kilo_path.exists() else {}
    if not isinstance(payload, dict):
        payload = {}
    payload.setdefault("$schema", "https://app.kilo.ai/config.json")
    payload["instructions"] = _merge_unique_strings(
        payload.get("instructions"),
        [
            "AGENTS.md",
            "CONTEXT.md",
            ".kilo/rules/sentinel-first.md",
            ".kilo/rules/sentinel-file-bridge.md",
        ],
    )

    fast_arg = " --fast" if fast_mode else ""
    command_payload = payload.get("command")
    if not isinstance(command_payload, dict):
        command_payload = {}
    command_payload.update(
        {
            "sentinel-refresh": {
                "template": "Refresh Sentinel's no-MCP file bridge for Kilo.",
                "description": "Refresh Sentinel Kilo bridge",
                "shell": (
                    "python tools/sentinel/sentinel.py kilo-refresh . "
                    f"--scan-root {scan_root} --budget {budget} --goal next{fast_arg}"
                ),
                "async": False,
            },
            "sentinel-watch": {
                "template": "Keep Sentinel's no-MCP file bridge fresh while Kilo works.",
                "description": "Watch Sentinel Kilo bridge",
                "shell": (
                    "python tools/sentinel/sentinel.py kilo-watch . "
                    f"--scan-root {scan_root} --budget {budget} --interval 30{fast_arg}"
                ),
                "async": True,
            },
        }
    )
    payload["command"] = command_payload
    ensure_parent_dir(kilo_path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return kilo_path


def _workspace_relative_focus(path: str, relative_scan_root: str) -> str:
    clean = path.replace("\\", "/").strip()
    if not clean or Path(clean).is_absolute():
        return clean
    root = relative_scan_root.replace("\\", "/").strip()
    if root in {"", "."}:
        return clean
    if clean == root or clean.startswith(f"{root}/"):
        return clean
    return f"{root}/{clean}"


def _scope_prompt_pack_for_workspace(prompt_pack: Dict[str, Any], relative_scan_root: str) -> Dict[str, Any]:
    scoped = deepcopy(prompt_pack)
    original_focus = [path for path in prompt_pack.get("focus_files", []) if isinstance(path, str)]
    scoped_focus = [_workspace_relative_focus(path, relative_scan_root) for path in original_focus]
    scoped["focus_files"] = scoped_focus

    selected = scoped.get("selected_suggestion")
    if isinstance(selected, dict):
        selected["focus_files"] = [
            _workspace_relative_focus(path, relative_scan_root)
            for path in selected.get("focus_files", [])
            if isinstance(path, str)
        ]

    prompt_text = scoped.get("prompt_text", "")
    for old, new in zip(original_focus, scoped_focus):
        prompt_text = prompt_text.replace(f"- {old}", f"- {new}")
        prompt_text = prompt_text.replace(f"`{old}`", f"`{new}`")
    scoped["prompt_text"] = prompt_text
    return scoped


def _validate_workspace_focus_paths(root: Path, focus_files: list[str]) -> Dict[str, Any]:
    invalid = [path for path in focus_files if path and not (root / path).exists()]
    return {
        "context_fresh": not invalid,
        "invalid_focus_files": invalid,
        "message": "all focus files exist" if not invalid else "focus files are stale or invalid",
    }


def refresh_kilo_bridge(
    workspace_dir: str,
    scan_root: str = ".",
    budget: str = "small",
    goal: str = "next",
    fast_mode: bool = True,
    write_root_files: bool = True,
) -> Dict[str, Any]:
    root = Path(workspace_dir).resolve()
    target = (root / scan_root).resolve() if not Path(scan_root).is_absolute() else Path(scan_root).resolve()
    if not root.is_dir():
        raise ValueError(f"Workspace directory does not exist: {root}")
    if not target.is_dir():
        raise ValueError(f"Scan root does not exist: {target}")

    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    bridge_dir = ensure_dir(root / ".sentinel" / "kilo")
    relative_scan_root = _portable_arg(target, root)

    agent = SentinelAgent(str(target))
    try:
        logging.getLogger().setLevel(logging.ERROR)
        for handler in logging.getLogger().handlers:
            handler.setLevel(logging.ERROR)
        agent.log.setLevel(logging.ERROR)
        result = agent.scan_once(
            print_report=False,
            fast_mode=fast_mode,
            include_suggestions=True,
            create_checkpoint=True,
        )
        context_pack = agent.build_context_pack(budget=budget)
        prompt_pack = agent.build_prompt_pack(result=result, goal=goal, budget=budget)
        prompt_pack = _scope_prompt_pack_for_workspace(prompt_pack, relative_scan_root)
        validation = _validate_workspace_focus_paths(root, prompt_pack.get("focus_files", []))
        overview_text = agent.reporter.render_overview(result)
        context_text = agent.reporter.render_context_pack(context_pack)
        prompt_text = agent.reporter.render_prompt_pack(prompt_pack)
    finally:
        agent.close()

    overview_path = bridge_dir / "overview.md"
    context_path = bridge_dir / "context.md"
    prompt_path = bridge_dir / "prompt.md"
    focus_path = bridge_dir / "focus-files.txt"
    status_path = bridge_dir / "status.json"
    rule_path = ensure_dir(root / ".kilo" / "rules") / "sentinel-file-bridge.md"

    ensure_parent_dir(overview_path).write_text(overview_text, encoding="utf-8")
    ensure_parent_dir(context_path).write_text(context_text, encoding="utf-8")
    ensure_parent_dir(prompt_path).write_text(prompt_text, encoding="utf-8")
    ensure_parent_dir(focus_path).write_text(
        "\n".join(prompt_pack.get("focus_files", [])).rstrip() + "\n",
        encoding="utf-8",
    )
    ensure_parent_dir(rule_path).write_text(
        _build_kilo_file_bridge_rule(relative_scan_root, budget),
        encoding="utf-8",
    )

    root_context_path = root / "CONTEXT.md"
    agents_path = root / "AGENTS.md"
    ignore_path = _write_kilo_ignore(root)
    if write_root_files:
        root_context = _build_root_context(
            scan_root=relative_scan_root,
            budget=budget,
            generated_at=generated_at,
            context_text=context_pack["context"],
            prompt_pack=prompt_pack,
            overview_path=_portable_arg(overview_path, root),
            context_path=_portable_arg(context_path, root),
            prompt_path=_portable_arg(prompt_path, root),
            focus_path=_portable_arg(focus_path, root),
            context_fresh=validation["context_fresh"],
            invalid_focus_files=validation["invalid_focus_files"],
        )
        ensure_parent_dir(root_context_path).write_text(root_context, encoding="utf-8")
        ensure_parent_dir(agents_path).write_text(_build_agents_file(relative_scan_root, budget), encoding="utf-8")

    status = {
        "generated_at": generated_at,
        "workspace_root": str(root),
        "scan_root": str(target),
        "budget": budget,
        "goal": goal,
        "health_score": result["audit"]["health_score"],
        "focus_files": prompt_pack.get("focus_files", []),
        "context_fresh": validation["context_fresh"],
        "invalid_focus_files": validation["invalid_focus_files"],
        "stale_recommendation": (
            ""
            if validation["context_fresh"]
            else f"Run project-sentinel kilo-refresh . --scan-root {relative_scan_root} --budget {budget} --fast"
        ),
        "estimated_full_context_tokens": context_pack.get("estimated_full_context_tokens", 0),
        "estimated_context_tokens": context_pack.get("estimated_context_tokens", 0),
        "estimated_token_savings_percent": context_pack.get("estimated_token_savings_percent", 0),
        "paths": {
            "overview": str(overview_path),
            "context": str(context_path),
            "prompt": str(prompt_path),
            "focus_files": str(focus_path),
            "root_context": str(root_context_path),
            "agents": str(agents_path),
            "rule": str(rule_path),
            "ignore": str(ignore_path),
        },
    }
    status["paths"]["status"] = str(status_path)
    ensure_parent_dir(status_path).write_text(json.dumps(status, indent=2), encoding="utf-8")
    return status


def _print_kilo_bridge_summary(status: Dict[str, Any], *, watched: bool = False) -> None:
    action = "refreshed"
    if watched:
        action = "watch refresh complete"
    print(f"Sentinel Kilo bridge {action}: {status['paths']['root_context']}")
    print(f"Focus files: {status['paths']['focus_files']}")
    print(
        "LLM readiness: "
        f"full {status['estimated_full_context_tokens']} tok, "
        f"compact {status['estimated_context_tokens']} tok, "
        f"saved {status['estimated_token_savings_percent']}%"
    )
    print(f"Health score: {status['health_score']}/100")
    if not status.get("context_fresh", True):
        print("Context stale: missing focus files: " + ", ".join(status.get("invalid_focus_files", [])))
        if status.get("stale_recommendation"):
            print(f"Recommended refresh: {status['stale_recommendation']}")


def _render_graph_pack(graph: Dict[str, Any]) -> str:
    summary = graph.get("summary", {})
    lines = [
        "SENTINEL PYTHON GRAPH",
        f"Modules: {summary.get('modules', 0)}",
        f"Symbols: {summary.get('symbols', 0)}",
        f"Import edges: {summary.get('imports', 0)}",
        f"Call sites: {summary.get('call_sites', 0)}",
        f"Parse errors: {summary.get('parse_errors', 0)}",
        "",
        "Top Symbols:",
    ]
    for symbol in graph.get("symbols", [])[:20]:
        lines.append(f"- {symbol['qualname']} [{symbol['kind']}] {symbol['path']}:{symbol['line']}")
    if not graph.get("symbols"):
        lines.append("- None")
    lines.append("")
    lines.append("Import Graph:")
    for module, imports in list(graph.get("import_graph", {}).items())[:20]:
        lines.append(f"- {module}: {', '.join(imports[:8]) if imports else '(no imports)'}")
    if graph.get("dependency_degree"):
        lines.append("")
        lines.append("Dependency Hotspots:")
        for item in graph["dependency_degree"][:10]:
            lines.append(f"- {item['module']}: inbound={item['inbound']} outbound={item['outbound']}")
    if graph.get("runtime_paths"):
        lines.append("")
        lines.append("Runtime Paths:")
        for item in graph["runtime_paths"][:10]:
            lines.append(f"- {' -> '.join(item.get('path', []))}")
    return "\n".join(lines).rstrip() + "\n"


def _render_verify_result(result: Dict[str, Any]) -> str:
    lines = [
        "SENTINEL VERIFY",
        f"Project: {result.get('project_dir')}",
        f"Changed files: {len(result.get('changed_files', []))}",
    ]
    for path in result.get("changed_files", []):
        lines.append(f"- {path}")
    lines.append("")
    lines.append("Commands:")
    for command in result.get("commands", []):
        lines.append(f"- {command}")
    lines.append("")
    lines.append(f"Summary: {result.get('summary', '')}")
    for item in result.get("results", []):
        status = "planned" if item.get("dry_run") else ("passed" if item.get("passed") else "failed")
        lines.append(f"- [{status}] {item.get('command')}")
        if item.get("stderr_tail"):
            lines.append(item["stderr_tail"])
        elif item.get("stdout_tail"):
            lines.append(item["stdout_tail"])
    return "\n".join(lines).rstrip() + "\n"


def _render_memory(entries: list[Dict[str, Any]]) -> str:
    lines = ["SENTINEL TASK MEMORY"]
    if not entries:
        lines.append("No task memory recorded yet.")
        return "\n".join(lines) + "\n"
    for entry in entries:
        lines.append("")
        lines.append(f"- {entry.get('timestamp')} | {entry.get('goal')}")
        if entry.get("changed_files"):
            lines.append(f"  changed: {', '.join(entry['changed_files'])}")
        if entry.get("tests"):
            lines.append(f"  tests: {', '.join(entry['tests'])}")
        if entry.get("risks"):
            lines.append(f"  risks: {', '.join(entry['risks'])}")
        if entry.get("decisions"):
            lines.append(f"  decisions: {', '.join(entry['decisions'])}")
        if entry.get("verifier_summary"):
            lines.append(f"  verify: {entry['verifier_summary']}")
    return "\n".join(lines).rstrip() + "\n"


def _render_savings(summary: Dict[str, Any]) -> str:
    lines = [
        "SENTINEL SAVINGS",
        f"Full-context tokens tracked: {summary.get('total_full_tokens', 0)}",
        f"Emitted tokens tracked: {summary.get('total_emitted_tokens', 0)}",
        f"Estimated tokens saved: {summary.get('estimated_tokens_saved', 0)}",
        f"Estimated savings: {summary.get('estimated_token_savings_percent', 0)}%",
        "",
        "Recent Events:",
    ]
    events = summary.get("events", [])
    if not events:
        lines.append("- None")
    for event in events:
        lines.append(
            f"- {event.get('timestamp')} {event.get('command')}: "
            f"{event.get('emitted_tokens', 0)}/{event.get('full_tokens', 0)} tok "
            f"({event.get('saved_percent', 0)}% saved)"
        )
    return "\n".join(lines).rstrip() + "\n"


def _render_cleanup_reports(result: Dict[str, Any]) -> str:
    lines = ["SENTINEL REPORT CLEANUP", result.get("message", "")]
    if result.get("historical"):
        lines.append("Historical candidates:")
        lines.extend(f"- {path}" for path in result["historical"][:20])
    return "\n".join(lines).rstrip() + "\n"


def _render_autofix(result: Dict[str, Any]) -> str:
    lines = ["SENTINEL AUTOFIX", result.get("message", "")]
    for action in result.get("actions", []):
        status = "applied" if action.get("applied") else "planned"
        lines.append(f"- [{status}] {action.get('type')}: {action.get('path', '')}")
    if not result.get("actions"):
        lines.append("- No safe autofixes found.")
    return "\n".join(lines).rstrip() + "\n"


def _render_pr_summary(result: Dict[str, Any]) -> str:
    lines = ["SENTINEL PR SUMMARY", f"Project: {result.get('project_dir')}", ""]
    lines.append("Changed Files:")
    lines.extend(f"- {path}" for path in result.get("changed_files", [])) if result.get("changed_files") else lines.append("- None detected")
    lines.append("")
    lines.append("Suggested Tests:")
    lines.extend(f"- {cmd}" for cmd in result.get("suggested_tests", [])) if result.get("suggested_tests") else lines.append("- None")
    if result.get("risks"):
        lines.append("")
        lines.append("Risk Focus:")
        for risk in result["risks"]:
            lines.append(f"- [{risk.get('level')}] {risk.get('file')} score={risk.get('score')}")
    if result.get("top_suggestion"):
        lines.append("")
        lines.append(f"Top Sentinel Suggestion: {result['top_suggestion'].get('title')}")
    if result.get("verification"):
        lines.append("")
        lines.append(f"Verification: {result['verification'].get('summary')}")
    return "\n".join(lines).rstrip() + "\n"


def _render_timeline(result: Dict[str, Any]) -> str:
    lines = ["SENTINEL MEMORY TIMELINE", ""]
    lines.append("Scans:")
    scans = result.get("scans", [])
    if scans:
        for scan in scans:
            lines.append(f"- {scan.get('timestamp')} health={scan.get('health_score')}% {scan.get('change_summary')}")
    else:
        lines.append("- None")
    lines.append("")
    lines.append("Tasks:")
    tasks = result.get("tasks", [])
    if tasks:
        for task in tasks:
            lines.append(f"- {task.get('timestamp')} {task.get('goal')}")
    else:
        lines.append("- None")
    lines.append("")
    lines.append(f"Tracked token savings: {result.get('savings', {}).get('estimated_token_savings_percent', 0)}%")
    return "\n".join(lines).rstrip() + "\n"


def _render_mcp_health(result: Dict[str, Any]) -> str:
    lines = ["SENTINEL MCP HEALTH", f"Status: {'ready' if result.get('ok') else 'needs attention'}", result.get("message", "")]
    if result.get("tools"):
        lines.append("Tools:")
        lines.extend(f"- {tool}" for tool in result["tools"])
    if result.get("missing"):
        lines.append("Missing:")
        lines.extend(f"- {tool}" for tool in result["missing"])
    return "\n".join(lines).rstrip() + "\n"


def _render_coverage(result: Dict[str, Any]) -> str:
    lines = ["SENTINEL COVERAGE", result.get("message", "")]
    if result.get("untested_hotspots"):
        lines.append("Untested Hotspots:")
        for item in result["untested_hotspots"]:
            risk = item.get("risk", {})
            coverage = item.get("coverage") or {}
            percent = coverage.get("percent", "missing")
            lines.append(f"- {risk.get('file')} risk={risk.get('score')} coverage={percent}")
    return "\n".join(lines).rstrip() + "\n"


def _render_insights(result: Dict[str, Any]) -> str:
    lines = ["SENTINEL INSIGHTS", f"Project: {result.get('project_dir')}", ""]
    lines.append("Alerts:")
    alerts = result.get("alerts", [])
    if alerts:
        for alert in alerts[:10]:
            lines.append(f"- [{alert.get('severity')}] {alert.get('type')}: {alert.get('message')}")
    else:
        lines.append("- None")

    diff = result.get("diff_impact", {})
    lines.extend(["", "Diff Impact:", f"- {diff.get('summary', 'No diff summary')}"])
    if diff.get("risky_changed_files"):
        for item in diff["risky_changed_files"][:8]:
            lines.append(f"- risky change: {item.get('file')} score={item.get('score')}")

    lines.extend(["", "Suggestion Evidence:"])
    for suggestion in result.get("suggestions", [])[:6]:
        lines.append(f"- [{suggestion.get('priority')}] {suggestion.get('title')}")
        evidence = suggestion.get("evidence", [])
        if evidence:
            lines.append(f"  evidence: {'; '.join(str(item) for item in evidence[:3])}")
        uncertainty = _as_text_list(suggestion.get("uncertainty", []))
        if uncertainty:
            lines.append(f"  uncertainty: {'; '.join(str(item) for item in uncertainty[:2])}")

    lines.extend(["", "File Drilldowns:"])
    for item in result.get("drilldowns", [])[:8]:
        risk = item.get("risk", {})
        lines.append(
            f"- [{risk.get('level')}] {item.get('file')} score={risk.get('score')} "
            f"todos={item.get('todo_count')} lines={item.get('line_count')} why={item.get('why')}"
        )
        if item.get("symbols"):
            lines.append(f"  symbols: {', '.join(item['symbols'][:5])}")
    if result.get("retrieval"):
        lines.extend(["", "Query Retrieval:", result["retrieval"].get("text", "").strip()])
    return "\n".join(lines).rstrip() + "\n"


def _as_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    return [str(value)]


def _render_alerts(result: Dict[str, Any]) -> str:
    lines = ["SENTINEL WATCH ALERTS", f"Project: {result.get('project_dir')}", ""]
    alerts = result.get("alerts", [])
    if not alerts:
        lines.append("- No alerts for the latest scan.")
    for alert in alerts:
        lines.append(f"- [{alert.get('severity')}] {alert.get('type')}: {alert.get('message')}")
        if alert.get("files"):
            lines.append(f"  files: {', '.join(alert['files'])}")
    return "\n".join(lines).rstrip() + "\n"


def _render_ledger(result: Dict[str, Any]) -> str:
    lines = ["SENTINEL DECISION LEDGER", f"Project: {result.get('project_dir')}", ""]
    lines.append("Decisions:")
    decisions = result.get("decisions", [])
    if decisions:
        for item in decisions:
            lines.append(f"- {item.get('timestamp')} {item.get('decision')}: {item.get('reason')}")
    else:
        lines.append("- None recorded")
    lines.append("")
    lines.append("Tasks:")
    tasks = result.get("tasks", [])
    if tasks:
        for task in tasks:
            changed = ", ".join(task.get("changed_files", [])[:4]) or "none"
            tests = ", ".join(task.get("tests", [])[:3]) or "none"
            lines.append(f"- {task.get('timestamp')} {task.get('goal')} | changed={changed} | tests={tests}")
    else:
        lines.append("- None recorded")
    lines.append("")
    lines.append("Scan History:")
    scans = result.get("scans", [])
    if scans:
        for scan in scans:
            lines.append(f"- {scan.get('timestamp')} health={scan.get('health_score')}% {scan.get('change_summary')}")
    else:
        lines.append("- None")
    lines.append("")
    lines.append(f"Tracked token savings: {result.get('savings', {}).get('estimated_token_savings_percent', 0)}%")
    return "\n".join(lines).rstrip() + "\n"


def _render_static_bundle(result: Dict[str, Any]) -> str:
    lines = [
        "SENTINEL STATIC BUNDLE",
        f"Output: {result.get('output_dir')}",
        f"Health: {result.get('health_score')}%",
        f"Files scanned: {result.get('files_scanned')}",
        "",
        "Artifacts:",
    ]
    for name, path in result.get("artifacts", {}).items():
        lines.append(f"- {name}: {path}")
    return "\n".join(lines).rstrip() + "\n"


def _render_speed_plan(result: Dict[str, Any]) -> str:
    current = result.get("current", {})
    lines = [
        "SENTINEL SCAN SPEED PLAN",
        f"Project: {result.get('project_dir')}",
        "",
        "Current Measurement:",
        f"- Duration: {current.get('duration_seconds')}s",
        f"- Files: {current.get('files_scanned')}",
        f"- Lines: {current.get('lines')}",
        f"- Cache: {current.get('cache')}",
        f"- Quality: {current.get('quality_position')}",
        "",
        "Already Implemented:",
    ]
    lines.extend(f"- {item}" for item in result.get("implemented", []))
    lines.extend(["", "Plan:"])
    for item in result.get("plan", []):
        lines.append(f"- {item.get('phase')} [{item.get('impact')}]")
        lines.append(f"  work: {item.get('work')}")
        lines.append(f"  guard: {item.get('quality_guard')}")
    lines.extend(["", "Non-goals:"])
    lines.extend(f"- {item}" for item in result.get("non_goals", []))
    return "\n".join(lines).rstrip() + "\n"


def _render_release_check(result: Dict[str, Any]) -> str:
    lines = [
        "SENTINEL RELEASE CHECK",
        f"Project: {result.get('project_dir')}",
        f"Version: {result.get('version') or 'unknown'}",
        f"Status: {'ready' if result.get('ready') else 'needs attention'}",
        "",
    ]
    for check in result.get("checks", []):
        status = "ok" if check.get("ok") else "fail"
        lines.append(f"- [{status}] {check.get('name')}: {check.get('message')}")
    return "\n".join(lines).rstrip() + "\n"


def _render_doctor(result: Dict[str, Any]) -> str:
    lines = [
        "SENTINEL DOCTOR",
        f"Project: {result.get('project_dir')}",
        f"Config base: {result.get('config_base_dir')}",
        "",
    ]
    for check in result.get("checks", []):
        status = "ok" if check.get("ok") else "fail"
        lines.append(f"- [{status}] {check.get('name')}: {check.get('message')}")
    lines.append("")
    lines.append("Status: ready" if result.get("ok") else "Status: needs attention")
    return "\n".join(lines).rstrip() + "\n"


def _render_adapters(result: Dict[str, Any]) -> str:
    lines = ["SENTINEL ADAPTERS", ""]
    for name in result.get("adapters", []):
        lines.append(f"## {name}")
        lines.append(result["docs"][name].strip())
        lines.append("")
    if result.get("written"):
        lines.append("Written files:")
        lines.extend(f"- {name}: {path}" for name, path in result["written"].items())
    return "\n".join(lines).rstrip() + "\n"


def _render_features() -> str:
    return (
        "SENTINEL COMMAND CENTER\n"
        "\n"
        "Core:\n"
        "- scan: audit files, detect changes, health, suggestions\n"
        "- brief: tiny AI-friendly summary\n"
        "- overview: architecture, hotspots, workflow hints\n"
        "- context: compact low-token project context\n"
        "- prompt: focused LLM task prompt\n"
        "- retrieve: query-specific files, symbols, snippets, call/import hints\n"
        "\n"
        "Intelligence:\n"
        "- graph: Python AST symbol index plus import and call graph\n"
        "- verify: choose and run narrow tests for changed files\n"
        "- memory: record/list task changes, tests, decisions, risks\n"
        "- savings: token/cost-savings ledger\n"
        "- doctor: config and runtime path validation\n"
        "- dashboard: live local health, suggestions, timeline, and budget panel\n"
        "- autofix: safe small fixes for config, bridge, and doc hygiene\n"
        "- pr: changed-file summary with risks and suggested checks\n"
        "- timeline: scan and task memory over time\n"
        "- mcp-health: MCP/file-bridge readiness panel\n"
        "- coverage: ingest coverage.xml and flag untested hotspots\n"
        "- insights: evidence explorer with drilldowns, diff impact, alerts, and optional retrieval\n"
        "- alerts: watch-style regression alerts for health, risk, coverage, and budgets\n"
        "- ledger: decision, task, scan, and savings ledger\n"
        "- bundle: portable static report bundle for CI artifacts or hosting\n"
        "- speed-plan: current scan measurement plus a no-quality-loss acceleration plan\n"
        "- cleanup-reports: mark stale archived reports as historical\n"
        "- release-check: open-source release readiness checklist\n"
        "\n"
        "Integrations:\n"
        "- mcp: stdio MCP server\n"
        "- kilo-setup, kilo-bridge, kilo-refresh, kilo-watch\n"
        "- adapters: prompts for Cline, Claude Code, Codex, Roo, Continue\n"
        "\n"
        "Examples:\n"
        "  project-sentinel retrieve . --query \"scheduler timeout bug\" --goal debug --fast\n"
        "  project-sentinel graph . --format text\n"
        "  project-sentinel verify . --dry-run\n"
        "  project-sentinel pr .\n"
        "  project-sentinel release-check .\n"
        "  project-sentinel insights . --query \"auth routing\" --fast\n"
        "  project-sentinel bundle . --output-dir .sentinel/static-report --fast\n"
        "  project-sentinel speed-plan . --fast\n"
        "  project-sentinel dashboard . --port 8765\n"
        "  project-sentinel memory record . --goal \"fixed CLI\" --changed-file src/sentinel.py --test \"python -m pytest tests\"\n"
    )


def _animate_command_center() -> None:
    frames = [
        "[=     ] scanning commands",
        "[==    ] indexing symbols",
        "[===   ] mapping calls",
        "[ ==== ] choosing context",
        "[  === ] planning checks",
        "[   == ] saving memory",
        "[    =] ready",
    ]
    for frame in frames:
        print(f"\r{frame}", end="", flush=True)
        sleep(0.08)
    print("\r[ready] Sentinel command center online")


def _portable_arg(path: Path, root: Path) -> str:
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError:
        return str(path.resolve())
    value = relative.as_posix()
    return value or "."


def setup_kilo_integration(
    workspace_dir: str,
    scan_root: str = ".",
    budget: str = "small",
    fast_mode: bool = True,
    portable: bool = False,
    force: bool = False,
) -> Dict[str, Any]:
    root = Path(workspace_dir).resolve()
    if not root.is_dir():
        raise ValueError(f"Workspace directory does not exist: {root}")

    target = (root / scan_root).resolve() if not Path(scan_root).is_absolute() else Path(scan_root).resolve()
    if not target.is_dir():
        raise ValueError(f"Scan root does not exist: {target}")

    sentinel_wrapper = root / "tools" / "sentinel" / "sentinel.py"
    if not sentinel_wrapper.exists():
        raise ValueError(f"Sentinel wrapper not found at: {sentinel_wrapper}")

    modern_dir = ensure_dir(root / ".kilo")
    modern_rules_dir = ensure_dir(modern_dir / "rules")
    modern_agents_dir = ensure_dir(modern_dir / "agents")
    legacy_dir = ensure_dir(root / ".kilocode")
    legacy_rules_dir = ensure_dir(legacy_dir / "rules")
    kilo_jsonc_path = modern_dir / "kilo.jsonc"
    root_kilo_path = root / "kilo.json"
    legacy_mcp_path = legacy_dir / "mcp.json"
    modern_rule_path = modern_rules_dir / "sentinel-first.md"
    legacy_rule_path = legacy_rules_dir / "sentinel-first.md"
    agent_path = modern_agents_dir / "sentinel-code.md"

    existing_outputs = [path for path in [kilo_jsonc_path, legacy_mcp_path] if path.exists()]
    if existing_outputs and not force:
        existing = ", ".join(str(path) for path in existing_outputs)
        raise ValueError(f"Kilo config already exists: {existing}. Re-run with --force to replace it.")

    if portable:
        command = "python"
        args = [
            _portable_arg(sentinel_wrapper, root),
            "mcp",
            _portable_arg(target, root),
            "--workspace-root",
            ".",
            "--budget",
            budget,
        ]
    else:
        command = sys.executable
        args = [
            str(sentinel_wrapper.resolve()),
            "mcp",
            str(target.resolve()),
            "--workspace-root",
            str(root.resolve()),
            "--budget",
            budget,
        ]

    if fast_mode:
        args.append("--fast")

    command_array = [command, *args]
    environment = {
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    }

    refresh_command = [
        "python",
        _portable_arg(sentinel_wrapper, root),
        "kilo-refresh",
        ".",
        "--scan-root",
        _portable_arg(target, root),
        "--budget",
        budget,
    ]
    watch_command = [
        "python",
        _portable_arg(sentinel_wrapper, root),
        "kilo-watch",
        ".",
        "--scan-root",
        _portable_arg(target, root),
        "--budget",
        budget,
        "--interval",
        "30",
    ]
    if fast_mode:
        refresh_command.append("--fast")
        watch_command.append("--fast")

    modern_payload = {
        "$schema": "https://app.kilo.ai/config.json",
        "instructions": [
            "AGENTS.md",
            "CONTEXT.md",
            ".kilo/rules/sentinel-first.md",
            ".kilo/rules/sentinel-file-bridge.md",
        ],
        "command": {
            "sentinel-refresh": {
                "description": "Refresh Sentinel's file bridge for Kilo without MCP",
                "command": refresh_command,
            },
            "sentinel-watch": {
                "description": "Continuously refresh Sentinel's file bridge for Kilo without MCP",
                "command": watch_command,
            },
        },
        "mcp": {
            "sentinel": {
                "type": "local",
                "command": command_array,
                "environment": environment,
                "enabled": True,
                "timeout": 30000,
            }
        },
        "permission": {
            "sentinel_sentinel_context": "allow",
            "sentinel_sentinel_overview": "allow",
            "sentinel_sentinel_prompt": "allow",
        },
    }

    legacy_payload = {
        "mcpServers": {
            "sentinel": {
                "command": command,
                "args": args,
                "env": environment,
                "alwaysAllow": [
                    "sentinel_context",
                    "sentinel_overview",
                    "sentinel_prompt",
                ],
                "disabled": False,
            }
        }
    }

    rule_text = _build_kilo_rule(_portable_arg(target, root), budget)
    agent_text = _build_kilo_agent(_portable_arg(target, root), budget)
    ensure_parent_dir(kilo_jsonc_path).write_text(json.dumps(modern_payload, indent=2), encoding="utf-8")
    _write_root_kilo_compat_config(root, _portable_arg(target, root), budget, fast_mode)
    ensure_parent_dir(legacy_mcp_path).write_text(json.dumps(legacy_payload, indent=2), encoding="utf-8")
    ensure_parent_dir(modern_rule_path).write_text(rule_text, encoding="utf-8")
    ensure_parent_dir(legacy_rule_path).write_text(rule_text, encoding="utf-8")
    ensure_parent_dir(agent_path).write_text(agent_text, encoding="utf-8")

    return {
        "workspace_root": str(root),
        "scan_root": str(target),
        "kilo_jsonc_path": str(kilo_jsonc_path),
        "root_kilo_path": str(root_kilo_path),
        "legacy_mcp_path": str(legacy_mcp_path),
        "rule_path": str(modern_rule_path),
        "legacy_rule_path": str(legacy_rule_path),
        "agent_path": str(agent_path),
        "portable": portable,
        "command": command,
        "args": args,
        "command_array": command_array,
    }


def _normalize_argv(argv: list[str]) -> list[str]:
    if not argv:
        return ["watch"]

    commands = {
        "scan",
        "watch",
        "report",
        "status",
        "brief",
        "overview",
        "context",
        "prompt",
        "retrieve",
        "ask",
        "analyze-url",
        "graph",
        "verify",
        "memory",
        "savings",
        "doctor",
        "dashboard",
        "autofix",
        "pr",
        "timeline",
        "mcp-health",
        "coverage",
        "insights",
        "alerts",
        "ledger",
        "bundle",
        "speed-plan",
        "cleanup-reports",
        "release-check",
        "features",
        "adapters",
        "mcp",
        "kilo-setup",
        "kilo-bridge",
        "kilo-refresh",
        "kilo-watch",
    }
    if argv[0] in commands or argv[0] in {"-h", "--help"}:
        return argv

    normalized = list(argv)
    if "--full-report" in normalized:
        normalized.remove("--full-report")
        return ["report", *normalized]
    if "--once" in normalized:
        normalized.remove("--once")
        return ["scan", *normalized]
    return ["watch", *normalized]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Project Sentinel - project-aware audit, planning, and low-token LLM handoff CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Typical workflow:\n"
            "  project-sentinel overview .\\axiom --fast\n"
            "  project-sentinel context .\\axiom --budget small --fast\n"
            "  project-sentinel prompt .\\axiom --goal next --budget small --fast\n"
            "  project-sentinel retrieve .\\axiom --query \"scheduler timeout\" --goal debug --fast\n"
            "  project-sentinel verify .\\axiom --dry-run\n"
            "  project-sentinel report .\\axiom\n"
            "  project-sentinel kilo-setup . --scan-root axiom --force\n"
            "  project-sentinel kilo-bridge . --scan-root axiom --budget small --fast --force\n"
            "  project-sentinel kilo-watch . --scan-root axiom --budget small --fast --interval 30\n"
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "directory",
        nargs="?",
        default=".",
        help="Project directory to monitor (default: current directory)",
    )
    common.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to a config.json file",
    )
    common.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress informational logs and print only the command result",
    )
    common.add_argument(
        "--ignore-path",
        action="append",
        default=[],
        help="Project-relative path to exclude from scanning; can be passed multiple times",
    )

    scan_parser = subparsers.add_parser("scan", parents=[common], help="Run a single scan")
    scan_parser.add_argument(
        "--format",
        choices=["text", "json", "markdown"],
        default="text",
        help="Output format for the scan result",
    )
    scan_parser.add_argument(
        "--compact",
        action="store_true",
        help="Print a very small summary instead of the full text report",
    )
    scan_parser.add_argument(
        "--fast",
        action="store_true",
        help="Use a faster scan mode with shallower content analysis",
    )
    scan_parser.add_argument(
        "--no-suggest",
        action="store_true",
        help="Skip next-step suggestion generation",
    )
    scan_parser.add_argument(
        "--no-checkpoint",
        action="store_true",
        help="Do not create a checkpoint for this scan",
    )
    scan_parser.add_argument(
        "--top",
        type=int,
        default=None,
        help="Maximum number of suggestions to include in the result",
    )

    brief_parser = subparsers.add_parser("brief", parents=[common], help="Run a tiny AI-friendly summary")
    brief_parser.add_argument(
        "--fast",
        action="store_true",
        help="Use a faster scan mode",
    )
    brief_parser.add_argument(
        "--format",
        choices=["brief", "json"],
        default="brief",
        help="Output format for the brief result",
    )
    brief_parser.add_argument(
        "--top",
        type=int,
        default=1,
        help="Maximum number of suggestions to include",
    )
    brief_parser.add_argument(
        "--no-checkpoint",
        action="store_true",
        help="Do not create a checkpoint for this scan",
    )

    overview_parser = subparsers.add_parser("overview", parents=[common], help="Summarize how the project works")
    overview_parser.add_argument(
        "--fast",
        action="store_true",
        help="Use a faster scan mode",
    )
    overview_parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format for the project overview",
    )
    overview_parser.add_argument(
        "--no-checkpoint",
        action="store_true",
        help="Do not create a checkpoint for this scan",
    )

    context_parser = subparsers.add_parser(
        "context",
        parents=[common],
        help="Emit a compact project context pack for another LLM or agent",
    )
    context_parser.add_argument(
        "--fast",
        action="store_true",
        help="Use a faster scan mode while refreshing project knowledge",
    )
    context_parser.add_argument(
        "--budget",
        choices=["tiny", "small", "medium", "large"],
        default="small",
        help="How much context to include",
    )
    context_parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format for the context pack",
    )
    context_parser.add_argument(
        "--no-checkpoint",
        action="store_true",
        help="Do not create a checkpoint for this scan",
    )

    prompt_parser = subparsers.add_parser(
        "prompt",
        parents=[common],
        help="Generate a focused prompt for the next LLM step",
    )
    prompt_parser.add_argument(
        "--fast",
        action="store_true",
        help="Use a faster scan mode while refreshing project knowledge",
    )
    prompt_parser.add_argument(
        "--goal",
        choices=["next", "debug", "review", "plan", "document", "test"],
        default="next",
        help="What kind of prompt to generate",
    )
    prompt_parser.add_argument(
        "--budget",
        choices=["tiny", "small", "medium", "large"],
        default="small",
        help="How much context to include",
    )
    prompt_parser.add_argument(
        "--top",
        type=int,
        default=1,
        help="Which suggestion number to use as the prompt anchor (1-based)",
    )
    prompt_parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format for the prompt pack",
    )
    prompt_parser.add_argument(
        "--no-checkpoint",
        action="store_true",
        help="Do not create a checkpoint for this scan",
    )

    retrieve_parser = subparsers.add_parser(
        "retrieve",
        parents=[common],
        help="Return query-specific context: files, symbols, snippets, import and call hints",
    )
    retrieve_parser.add_argument("--query", required=True, help="Task or bug query to retrieve context for")
    retrieve_parser.add_argument(
        "--goal",
        choices=["next", "debug", "review", "plan", "document", "test"],
        default="next",
        help="Task mode used to bias retrieval",
    )
    retrieve_parser.add_argument("--fast", action="store_true", help="Use faster file metadata scanning")
    retrieve_parser.add_argument("--limit", type=int, default=6, help="Maximum relevant files to return")
    retrieve_parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format for retrieved context",
    )

    ask_parser = subparsers.add_parser(
        "ask",
        parents=[common],
        help="Answer a project question using Sentinel's local retrieval context",
    )
    ask_parser.add_argument("--question", "-q", required=True, help="Question to ask about the project")
    ask_parser.add_argument(
        "--goal",
        choices=["next", "debug", "review", "plan", "document", "test"],
        default="next",
        help="Task mode used to bias retrieval",
    )
    ask_parser.add_argument("--fast", action="store_true", help="Use faster scanning")
    ask_parser.add_argument("--limit", type=int, default=6, help="Maximum relevant files to include")
    ask_parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format for the answer",
    )

    analyze_url_parser = subparsers.add_parser(
        "analyze-url",
        help="Clone a git repository URL or source, scan it, and write a report bundle",
    )
    analyze_url_parser.add_argument("repo_url", help="Git repository URL, file:// URL, or local git source path")
    analyze_url_parser.add_argument("--output-dir", default=None, help="Directory where reports should be written")
    analyze_url_parser.add_argument("--keep-clone", action="store_true", help="Keep the cloned repository inside the output directory")
    analyze_url_parser.add_argument("--fast", action="store_true", help="Use faster scanning after cloning")
    analyze_url_parser.add_argument("--no-html", action="store_true", help="Skip HTML report generation")
    analyze_url_parser.add_argument("--timeout", type=int, default=300, help="Git clone timeout in seconds")
    analyze_url_parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format for the URL analysis summary",
    )

    graph_parser = subparsers.add_parser(
        "graph",
        parents=[common],
        help="Build a Python AST symbol index with import graph and call graph",
    )
    graph_parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format for the graph",
    )

    verify_parser = subparsers.add_parser(
        "verify",
        parents=[common],
        help="Run the narrowest useful tests for changed files",
    )
    verify_parser.add_argument(
        "--changed-file",
        action="append",
        default=[],
        help="Changed project-relative file; can be passed multiple times",
    )
    verify_parser.add_argument("--command", dest="verify_command", default=None, help="Explicit verification command to run")
    verify_parser.add_argument("--dry-run", action="store_true", help="Show chosen commands without running them")
    verify_parser.add_argument("--timeout", type=int, default=120, help="Verification timeout in seconds")
    verify_parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format for verification",
    )

    memory_parser = subparsers.add_parser("memory", help="Record or list task memory")
    memory_parser.add_argument(
        "memory_command",
        nargs="?",
        choices=["list", "record"],
        default="list",
        help="Memory action to run",
    )
    memory_parser.add_argument(
        "directory",
        nargs="?",
        default=".",
        help="Project directory (default: current directory)",
    )
    memory_parser.add_argument("--config", type=str, default=None, help="Path to a config.json file")
    memory_parser.add_argument("--quiet", action="store_true", help="Suppress informational logs")
    memory_parser.add_argument("--ignore-path", action="append", default=[], help="Project-relative path to exclude")
    memory_parser.add_argument("--limit", type=int, default=10, help="Number of entries to show")
    memory_parser.add_argument("--goal", default="", help="Task goal or short summary")
    memory_parser.add_argument("--changed-file", action="append", default=[], help="Changed file")
    memory_parser.add_argument("--test", action="append", default=[], help="Test or check that ran")
    memory_parser.add_argument("--risk", action="append", default=[], help="Remaining risk")
    memory_parser.add_argument("--decision", action="append", default=[], help="Decision made")
    memory_parser.add_argument("--verifier-summary", default="", help="Verification summary")
    memory_parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format for task memory",
    )

    savings_parser = subparsers.add_parser("savings", parents=[common], help="Show tracked token savings")
    savings_parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format for savings",
    )

    doctor_parser = subparsers.add_parser("doctor", parents=[common], help="Validate config and Sentinel runtime paths")
    doctor_parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format for doctor checks",
    )

    dashboard_parser = subparsers.add_parser("dashboard", parents=[common], help="Run a live local Sentinel dashboard")
    dashboard_parser.add_argument("--host", default="127.0.0.1", help="Dashboard host")
    dashboard_parser.add_argument("--port", type=int, default=8765, help="Dashboard port")
    dashboard_parser.add_argument("--interval", type=int, default=10, help="Seconds between scans")
    dashboard_parser.add_argument("--fast", action="store_true", help="Use faster scans")

    autofix_parser = subparsers.add_parser("autofix", parents=[common], help="Plan or apply small safe Sentinel fixes")
    autofix_parser.add_argument("--apply", action="store_true", help="Apply safe fixes instead of dry-run")
    autofix_parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format for autofix",
    )

    pr_parser = subparsers.add_parser("pr", parents=[common], help="Summarize changed files, risks, and PR checks")
    pr_parser.add_argument("--verify", action="store_true", help="Run suggested verification")
    pr_parser.add_argument("--timeout", type=int, default=120, help="Verification timeout")
    pr_parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format for PR summary",
    )

    timeline_parser = subparsers.add_parser("timeline", parents=[common], help="Show scan and task memory over time")
    timeline_parser.add_argument("--limit", type=int, default=20, help="Number of events to show")
    timeline_parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format for timeline",
    )

    mcp_health_parser = subparsers.add_parser("mcp-health", parents=[common], help="Validate MCP and file-bridge readiness")
    mcp_health_parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format for MCP health",
    )

    coverage_parser = subparsers.add_parser("coverage", parents=[common], help="Read coverage.xml and flag untested hotspots")
    coverage_parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format for coverage",
    )

    insights_parser = subparsers.add_parser("insights", parents=[common], help="Show dashboard-style evidence, drilldowns, alerts, and diff impact")
    insights_parser.add_argument("--query", default="", help="Optional query to include retrieval evidence")
    insights_parser.add_argument("--fast", action="store_true", help="Use faster scanning")
    insights_parser.add_argument("--limit", type=int, default=6, help="Maximum retrieved files for query evidence")
    insights_parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format for insights",
    )

    alerts_parser = subparsers.add_parser("alerts", parents=[common], help="Show watch-style alerts for the latest scan")
    alerts_parser.add_argument("--fast", action="store_true", help="Use faster scanning")
    alerts_parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format for alerts",
    )

    ledger_parser = subparsers.add_parser("ledger", parents=[common], help="Show decisions, task memory, scan history, and savings")
    ledger_parser.add_argument("--limit", type=int, default=20, help="Number of ledger entries to show")
    ledger_parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format for ledger",
    )

    bundle_parser = subparsers.add_parser("bundle", parents=[common], help="Write a portable static Sentinel report bundle")
    bundle_parser.add_argument("--output-dir", default=None, help="Directory where the bundle should be written")
    bundle_parser.add_argument("--fast", action="store_true", help="Use faster scanning")
    bundle_parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format for bundle summary",
    )

    speed_parser = subparsers.add_parser("speed-plan", parents=[common], help="Measure current scan behavior and print a no-quality-loss speed plan")
    speed_parser.add_argument("--fast", action="store_true", help="Measure using fast scan mode")
    speed_parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format for speed plan",
    )

    cleanup_parser = subparsers.add_parser("cleanup-reports", parents=[common], help="Mark old Sentinel reports as historical")
    cleanup_parser.add_argument("--keep", type=int, default=5, help="Recent reports to keep in place")
    cleanup_parser.add_argument("--apply", action="store_true", help="Move old reports into historical/")
    cleanup_parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format for cleanup",
    )

    release_parser = subparsers.add_parser("release-check", parents=[common], help="Run an open-source release readiness checklist")
    release_parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format for release check",
    )

    adapters_parser = subparsers.add_parser(
        "adapters",
        parents=[common],
        help="Print or write adapter prompts for Cline, Claude Code, Codex, Roo, and Continue",
    )
    adapters_parser.add_argument("--write", action="store_true", help="Write docs under .sentinel/adapters")
    adapters_parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format for adapters",
    )

    features_parser = subparsers.add_parser("features", help="Show every Sentinel feature and example command")
    features_parser.add_argument("--animate", action="store_true", help="Play a short terminal startup animation")

    mcp_parser = subparsers.add_parser(
        "mcp",
        help="Run Sentinel as a stdio MCP server for Kilo Code and similar agents",
    )
    mcp_parser.add_argument(
        "directory",
        nargs="?",
        default=".",
        help="Default project directory for Sentinel MCP tools",
    )
    mcp_parser.add_argument(
        "--workspace-root",
        type=str,
        default=None,
        help="Workspace root used to resolve project_dir overrides from MCP tool calls",
    )
    mcp_parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to a config.json file",
    )
    mcp_parser.add_argument(
        "--budget",
        choices=["tiny", "small", "medium", "large"],
        default="small",
        help="Default compact context budget exposed by MCP tools",
    )
    mcp_parser.add_argument(
        "--fast",
        action="store_true",
        help="Use faster scans inside MCP tools",
    )

    kilo_parser = subparsers.add_parser(
        "kilo-setup",
        help="Write project-local Kilo Code MCP config and Sentinel-first rules",
    )
    kilo_parser.add_argument(
        "directory",
        nargs="?",
        default=".",
        help="Workspace root where .kilo/kilo.jsonc and compatibility files should be written",
    )
    kilo_parser.add_argument(
        "--scan-root",
        type=str,
        default=".",
        help="Project subdirectory Sentinel should scan by default inside Kilo",
    )
    kilo_parser.add_argument(
        "--budget",
        choices=["tiny", "small", "medium", "large"],
        default="small",
        help="Default compact context budget for the generated Kilo integration",
    )
    kilo_parser.add_argument(
        "--portable",
        action="store_true",
        help="Write a repo-relative Kilo config instead of absolute local paths",
    )
    kilo_parser.add_argument(
        "--force",
        action="store_true",
        help="Replace any existing Sentinel Kilo config outputs",
    )
    kilo_parser.add_argument(
        "--no-fast",
        action="store_true",
        help="Disable fast-mode in the generated Sentinel MCP command",
    )

    bridge_parent = argparse.ArgumentParser(add_help=False)
    bridge_parent.add_argument(
        "directory",
        nargs="?",
        default=".",
        help="Workspace root shared by Kilo and Sentinel",
    )
    bridge_parent.add_argument(
        "--scan-root",
        type=str,
        default=".",
        help="Project subdirectory Sentinel should scan for Kilo context",
    )
    bridge_parent.add_argument(
        "--budget",
        choices=["tiny", "small", "medium", "large"],
        default="small",
        help="Compact context budget to write into the file bridge",
    )
    bridge_parent.add_argument(
        "--goal",
        choices=["next", "debug", "review", "plan", "document", "test"],
        default="next",
        help="Prompt goal to write into .sentinel/kilo/prompt.md",
    )
    bridge_parent.add_argument(
        "--fast",
        action="store_true",
        help="Use faster scans while refreshing the file bridge",
    )
    bridge_parent.add_argument(
        "--no-root-context",
        action="store_true",
        help="Do not write root CONTEXT.md and AGENTS.md",
    )

    bridge_parser = subparsers.add_parser(
        "kilo-bridge",
        parents=[bridge_parent],
        help="Set up Kilo files and refresh Sentinel's no-MCP file bridge",
    )
    bridge_parser.add_argument(
        "--portable",
        action="store_true",
        help="Write repo-relative MCP compatibility config during setup",
    )
    bridge_parser.add_argument(
        "--force",
        action="store_true",
        help="Replace existing Sentinel Kilo config outputs during setup",
    )

    subparsers.add_parser(
        "kilo-refresh",
        parents=[bridge_parent],
        help="Refresh Sentinel's Kilo file bridge without using MCP",
    )

    kilo_watch_parser = subparsers.add_parser(
        "kilo-watch",
        parents=[bridge_parent],
        help="Continuously refresh Sentinel's Kilo file bridge without using MCP",
    )
    kilo_watch_parser.add_argument(
        "--interval",
        type=int,
        default=30,
        help="Seconds between bridge refreshes",
    )

    report_parser = subparsers.add_parser("report", parents=[common], help="Generate and save a markdown or HTML report")
    report_parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Optional destination for the primary markdown report",
    )
    report_parser.add_argument(
        "--fast",
        action="store_true",
        help="Use a faster scan mode while generating the report",
    )
    report_parser.add_argument(
        "--format",
        choices=["markdown", "html", "both"],
        default="markdown",
        help="Report format to save",
    )
    report_parser.add_argument("--html", action="store_true", help="Shortcut for --format html")

    watch_parser = subparsers.add_parser("watch", parents=[common], help="Continuously monitor a project")
    watch_parser.add_argument(
        "--interval",
        type=int,
        default=None,
        help="Override scan interval in seconds",
    )
    watch_parser.add_argument(
        "--fast",
        action="store_true",
        help="Use a faster scan mode while watching",
    )
    watch_parser.add_argument(
        "--compact",
        action="store_true",
        help="Print compact summaries during watch mode",
    )

    status_parser = subparsers.add_parser("status", parents=[common], help="Show the latest saved project status")
    status_parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format for the saved status",
    )

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    args = _build_parser().parse_args(_normalize_argv(raw_argv))

    agent = None
    try:
        if args.command == "features":
            if args.animate:
                _animate_command_center()
            print(_render_features())
            return 0

        if args.command == "kilo-setup":
            saved = setup_kilo_integration(
                workspace_dir=args.directory,
                scan_root=args.scan_root,
                budget=args.budget,
                fast_mode=not args.no_fast,
                portable=args.portable,
                force=args.force,
            )
            print(f"Kilo config written to {saved['kilo_jsonc_path']}")
            print(f"Root Kilo compatibility config updated at {saved['root_kilo_path']}")
            print(f"Kilo Sentinel rule written to {saved['rule_path']}")
            print(f"Kilo Sentinel agent written to {saved['agent_path']}")
            print(f"Legacy MCP compatibility config written to {saved['legacy_mcp_path']}")
            print(f"Default Sentinel scan root: {saved['scan_root']}")
            print("Reload Kilo Code or refresh MCP servers to pick up the new Sentinel tools.")
            return 0

        if args.command == "kilo-bridge":
            saved = setup_kilo_integration(
                workspace_dir=args.directory,
                scan_root=args.scan_root,
                budget=args.budget,
                fast_mode=args.fast,
                portable=args.portable,
                force=args.force,
            )
            status = refresh_kilo_bridge(
                workspace_dir=args.directory,
                scan_root=args.scan_root,
                budget=args.budget,
                goal=args.goal,
                fast_mode=args.fast,
                write_root_files=not args.no_root_context,
            )
            print(f"Kilo config written to {saved['kilo_jsonc_path']}")
            print(f"Root Kilo compatibility config updated at {saved['root_kilo_path']}")
            print(f"Kilo Sentinel rule written to {saved['rule_path']}")
            print(f"Kilo Sentinel file bridge rule written to {status['paths']['rule']}")
            print(f"Kilo Sentinel agent written to {saved['agent_path']}")
            print(f"Legacy MCP compatibility config written to {saved['legacy_mcp_path']}")
            _print_kilo_bridge_summary(status)
            print("Kilo can now work from CONTEXT.md and .sentinel/kilo/prompt.md even if MCP is ignored.")
            return 0

        if args.command == "kilo-refresh":
            status = refresh_kilo_bridge(
                workspace_dir=args.directory,
                scan_root=args.scan_root,
                budget=args.budget,
                goal=args.goal,
                fast_mode=args.fast,
                write_root_files=not args.no_root_context,
            )
            _print_kilo_bridge_summary(status)
            return 0

        if args.command == "kilo-watch":
            interval = max(1, args.interval)
            print(
                "Watching Sentinel Kilo bridge. "
                f"Refreshing every {interval}s for scan root {args.scan_root}. Press Ctrl+C to stop."
            )
            try:
                while True:
                    status = refresh_kilo_bridge(
                        workspace_dir=args.directory,
                        scan_root=args.scan_root,
                        budget=args.budget,
                        goal=args.goal,
                        fast_mode=args.fast,
                        write_root_files=not args.no_root_context,
                    )
                    _print_kilo_bridge_summary(status, watched=True)
                    sleep(interval)
            except KeyboardInterrupt:
                print("Sentinel Kilo bridge watcher stopped.")
            return 0

        if args.command == "mcp":
            from sentinel_mcp import SentinelMCPServer

            server = SentinelMCPServer(
                project_dir=args.directory,
                workspace_root=args.workspace_root,
                config_path=args.config,
                budget=args.budget,
                fast_mode=args.fast,
            )
            server.serve()
            return 0

        if args.command == "analyze-url":
            result = analyze_repository_url(
                args.repo_url,
                output_dir=args.output_dir,
                keep_clone=args.keep_clone,
                fast_mode=args.fast,
                html_report=not args.no_html,
                timeout=args.timeout,
            )
            if args.format == "json":
                print(json.dumps(result, indent=2, sort_keys=True))
            else:
                print(_render_analyze_url(result))
            return 0

        agent = SentinelAgent(args.directory, args.config)
        if args.quiet:
            logging.getLogger().setLevel(logging.ERROR)
            for handler in logging.getLogger().handlers:
                handler.setLevel(logging.ERROR)
            agent.log.setLevel(logging.ERROR)

        if args.command == "watch" and args.interval is not None:
            agent.config["scan_interval_seconds"] = max(1, args.interval)
            agent.monitor.interval_seconds = max(1, args.interval)

        if args.command == "report":
            report_format = "html" if args.html else args.format
            if report_format == "markdown":
                saved = agent.save_full_report(destination=args.output, fast_mode=args.fast)
                print(f"Full report saved to {saved['primary_path']}")
                print(f"Archived copy saved to {saved['archive_path']}")
            elif report_format == "html":
                saved = agent.save_html_report(destination=args.output, fast_mode=args.fast)
                print(f"HTML report saved to {saved['primary_path']}")
                print(f"Archived HTML copy saved to {saved['archive_path']}")
            else:
                markdown_output = args.output
                html_output = None
                if markdown_output:
                    output_path = Path(markdown_output).resolve()
                    html_output = str(output_path.with_suffix(".html"))
                md_saved = agent.save_full_report(destination=markdown_output, fast_mode=args.fast)
                html_saved = agent.save_html_report(destination=html_output, fast_mode=args.fast)
                print(f"Full report saved to {md_saved['primary_path']}")
                print(f"HTML report saved to {html_saved['primary_path']}")
                print(f"Archived markdown copy saved to {md_saved['archive_path']}")
                print(f"Archived HTML copy saved to {html_saved['archive_path']}")
            return 0

        if args.command == "retrieve":
            result = agent.retrieve(
                args.query,
                goal=args.goal,
                limit=args.limit,
                fast_mode=args.fast,
                extra_ignore_paths=args.ignore_path,
            )
            if args.format == "json":
                print(agent.reporter.render_json(result))
            else:
                print(result["text"])
            return 0

        if args.command == "ask":
            result = agent.ask(
                args.question,
                goal=args.goal,
                limit=args.limit,
                fast_mode=args.fast,
                extra_ignore_paths=args.ignore_path,
            )
            if args.format == "json":
                print(agent.reporter.render_json(result))
            else:
                print(result["text"])
            return 0

        if args.command == "graph":
            result = agent.build_graph_pack()
            if args.format == "json":
                print(agent.reporter.render_json(result))
            else:
                print(_render_graph_pack(result))
            return 0

        if args.command == "verify":
            result = agent.verify(
                changed_files=args.changed_file or None,
                command=args.verify_command,
                dry_run=args.dry_run,
                timeout=args.timeout,
            )
            if args.format == "json":
                print(agent.reporter.render_json(result))
            else:
                print(_render_verify_result(result))
            return 0

        if args.command == "memory":
            if args.memory_command == "record":
                if not args.goal:
                    raise ValueError("memory record requires --goal")
                entry = agent.record_task_memory(
                    goal=args.goal,
                    changed_files=args.changed_file,
                    tests=args.test,
                    risks=args.risk,
                    decisions=args.decision,
                    verifier_summary=args.verifier_summary,
                )
                payload: Any = entry
            else:
                payload = agent.get_memory(limit=getattr(args, "limit", 10))
            if args.format == "json":
                print(agent.reporter.render_json(payload))
            else:
                print(_render_memory(payload if isinstance(payload, list) else [payload]))
            return 0

        if args.command == "savings":
            result = agent.get_savings()
            if args.format == "json":
                print(agent.reporter.render_json(result))
            else:
                print(_render_savings(result))
            return 0

        if args.command == "doctor":
            result = agent.doctor()
            if args.format == "json":
                print(agent.reporter.render_json(result))
            else:
                print(_render_doctor(result))
            return 0

        if args.command == "dashboard":
            agent.run_dashboard(host=args.host, port=args.port, interval=args.interval, fast_mode=args.fast)
            return 0

        if args.command == "autofix":
            result = agent.autofix(dry_run=not args.apply)
            if args.format == "json":
                print(agent.reporter.render_json(result))
            else:
                print(_render_autofix(result))
            return 0

        if args.command == "pr":
            result = agent.pr_summary(verify=args.verify, timeout=args.timeout)
            if args.format == "json":
                print(agent.reporter.render_json(result))
            else:
                print(_render_pr_summary(result))
            return 0

        if args.command == "timeline":
            result = agent.memory_timeline(limit=args.limit)
            if args.format == "json":
                print(agent.reporter.render_json(result))
            else:
                print(_render_timeline(result))
            return 0

        if args.command == "mcp-health":
            result = agent.mcp_health()
            bridge = agent.inspect_bridge_context(agent.project_dir)
            result["bridge"] = bridge
            if args.format == "json":
                print(agent.reporter.render_json(result))
            else:
                print(_render_mcp_health(result))
            return 0

        if args.command == "coverage":
            result = agent.coverage_report()
            if args.format == "json":
                print(agent.reporter.render_json(result))
            else:
                print(_render_coverage(result))
            return 0

        if args.command == "insights":
            result = agent.evidence_report(
                query=args.query,
                fast_mode=args.fast,
                limit=args.limit,
            )
            if args.format == "json":
                print(agent.reporter.render_json(result))
            else:
                print(_render_insights(result))
            return 0

        if args.command == "alerts":
            scan = agent.scan_once(
                print_report=False,
                fast_mode=args.fast,
                include_suggestions=True,
                create_checkpoint=False,
                extra_ignore_paths=args.ignore_path,
            )
            result = {"project_dir": str(agent.project_dir), "alerts": scan.get("alerts", []), "scan": scan}
            if args.format == "json":
                print(agent.reporter.render_json(result))
            else:
                print(_render_alerts(result))
            return 0

        if args.command == "ledger":
            result = agent.decision_ledger(limit=args.limit)
            if args.format == "json":
                print(agent.reporter.render_json(result))
            else:
                print(_render_ledger(result))
            return 0

        if args.command == "bundle":
            result = agent.save_static_bundle(output_dir=args.output_dir, fast_mode=args.fast)
            if args.format == "json":
                print(agent.reporter.render_json(result))
            else:
                print(_render_static_bundle(result))
            return 0

        if args.command == "speed-plan":
            result = agent.scan_speed_plan(fast_mode=args.fast)
            if args.format == "json":
                print(agent.reporter.render_json(result))
            else:
                print(_render_speed_plan(result))
            return 0

        if args.command == "cleanup-reports":
            result = agent.cleanup_reports(keep=args.keep, dry_run=not args.apply)
            if args.format == "json":
                print(agent.reporter.render_json(result))
            else:
                print(_render_cleanup_reports(result))
            return 0

        if args.command == "release-check":
            result = agent.release_check()
            if args.format == "json":
                print(agent.reporter.render_json(result))
            else:
                print(_render_release_check(result))
            return 0

        if args.command == "adapters":
            result = build_adapter_docs(agent.project_dir, write=args.write)
            if args.format == "json":
                print(agent.reporter.render_json(result))
            else:
                print(_render_adapters(result))
            return 0

        if args.command == "scan":
            agent.scan_once(
                print_report=True,
                output_format=args.format,
                compact=args.compact,
                fast_mode=args.fast,
                include_suggestions=not args.no_suggest,
                create_checkpoint=not args.no_checkpoint,
                top_suggestions=args.top,
                extra_ignore_paths=args.ignore_path,
            )
            return 0

        if args.command == "brief":
            result = agent.scan_once(
                print_report=True,
                output_format=args.format,
                compact=False,
                fast_mode=args.fast,
                include_suggestions=True,
                create_checkpoint=not args.no_checkpoint,
                top_suggestions=args.top,
                extra_ignore_paths=args.ignore_path,
            )
            return 0

        if args.command == "overview":
            result = agent.scan_once(
                print_report=False,
                fast_mode=args.fast,
                include_suggestions=True,
                create_checkpoint=not args.no_checkpoint,
                extra_ignore_paths=args.ignore_path,
            )
            if args.format == "json":
                print(agent.reporter.render_json(result))
            else:
                print(agent.reporter.render_overview(result))
            return 0

        if args.command == "context":
            agent.scan_once(
                print_report=False,
                fast_mode=args.fast,
                include_suggestions=True,
                create_checkpoint=not args.no_checkpoint,
                extra_ignore_paths=args.ignore_path,
            )
            context_pack = agent.build_context_pack(budget=args.budget)
            if args.format == "json":
                print(agent.reporter.render_json(context_pack))
            else:
                print(agent.reporter.render_context_pack(context_pack))
            return 0

        if args.command == "prompt":
            result = agent.scan_once(
                print_report=False,
                fast_mode=args.fast,
                include_suggestions=True,
                create_checkpoint=not args.no_checkpoint,
                extra_ignore_paths=args.ignore_path,
            )
            prompt_pack = agent.build_prompt_pack(
                result=result,
                goal=args.goal,
                budget=args.budget,
                suggestion_number=args.top,
            )
            if args.format == "json":
                print(agent.reporter.render_json(prompt_pack))
            else:
                print(agent.reporter.render_prompt_pack(prompt_pack))
            return 0

        if args.command == "status":
            status = agent.get_status()
            if args.format == "json":
                print(agent.reporter.render_json(status))
            else:
                print(agent.reporter.render_status(status))
            return 0

        agent.run_continuous(fast_mode=args.fast, compact=args.compact)
        return 0
    except Exception as exc:  # pragma: no cover - defensive CLI behavior
        print(f"Sentinel failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if agent is not None:
            agent.close()


if __name__ == "__main__":
    raise SystemExit(main())
