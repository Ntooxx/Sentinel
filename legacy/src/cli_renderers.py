"""CLI render helper functions for Project Sentinel.

Contains presentation helpers that format structured results into
human-readable terminal output used by the dashboard and CLI.
"""

import json
from typing import Any, Dict


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


def _render_benchmark(result: Dict[str, Any]) -> str:
    lines = ["SENTINEL BENCHMARK", result.get("message", "")]
    for r in result.get("results", []):
        lines.append(
            f"  {r['fixture']:20s}  files={r['files']:>5d}  "
            f"lines={r['lines']:>6d}  time={r['time_seconds']:>7.3f}s  health={r['health']}%"
        )
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


def _render_json_lines(title: str, data: Dict[str, Any]) -> str:
    return title + "\n" + json.dumps(data, indent=2, sort_keys=True) + "\n"


def _as_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    return [str(value)]
