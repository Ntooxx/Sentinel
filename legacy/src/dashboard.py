"""Dashboard module: serves the Sentinel command center HTML and handles dashboard actions."""

import json
from datetime import datetime
from json import loads as json_loads
import threading
from pathlib import Path
from typing import Any, Dict

from orchestrator import SentinelAgent
from utils import ensure_dir, normalize_budget_name

_DASHBOARD_HTML = Path(__file__).with_name("dashboard.html").read_text(encoding="utf-8")


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
    import re as _re

    match = _re.search(r"version\s*=\s*[\"']([^\"']+)[\"']", text)
    return match.group(1) if match else ""


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


def run_dashboard(agent: SentinelAgent, host: str = "127.0.0.1", port: int = 8765, interval: int = 10, fast_mode: bool = True) -> None:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from datetime import datetime

    state: Dict[str, Any] = {"latest": None, "history": agent.knowledge.get_scan_history(limit=50)}
    stop_event = threading.Event()
    dashboard_agent = agent

    def scan_loop() -> None:
        while not stop_event.is_set():
            try:
                state["latest"] = agent.scan_once(
                    print_report=False,
                    fast_mode=fast_mode,
                    include_suggestions=True,
                    create_checkpoint=True,
                )
                state["history"] = agent.knowledge.get_scan_history(limit=50)
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
