"""CLI infrastructure for Project Sentinel.

Contains the argument parser, argv normalizer, main entry point, and
helper functions that drive the command-line interface.
"""

import argparse
import json
import re
import sys
from pathlib import Path
from time import sleep

from orchestrator import SentinelAgent
from cli_renderers import (
    _render_analyze_url,
    _render_graph_pack,
    _render_verify_result,
    _render_memory,
    _render_savings,
    _render_cleanup_reports,
    _render_autofix,
    _render_pr_summary,
    _render_timeline,
    _render_mcp_health,
    _render_coverage,
    _render_benchmark,
    _render_insights,
    _render_alerts,
    _render_ledger,
    _render_static_bundle,
    _render_speed_plan,
    _render_release_check,
    _render_doctor,
    _render_adapters,
    _render_features,
    _render_json_lines,
    _as_text_list,
)
from dashboard import run_dashboard
from kilo import setup_kilo_integration, refresh_kilo_bridge, _print_kilo_bridge_summary
from url_reports import analyze_repository_url
from utils import normalize_budget_name


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
        "benchmark",
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

    benchmark_parser = subparsers.add_parser("benchmark", parents=[common], help="Run reproducible scan benchmarks against bundled fixture repos")
    benchmark_parser.add_argument(
        "--format",
        choices=["text", "json", "markdown"],
        default="text",
        help="Output format for benchmark results",
    )
    benchmark_parser.add_argument("--fast", action="store_true", help="Use faster scanning for benchmarks")

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


def _request_bool(request: dict, name: str, default: bool = False) -> bool:
    value = request.get(name, default)
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "apply", "run"}


def _request_int(request: dict, name: str, default: int) -> int:
    try:
        return int(request.get(name, default))
    except (TypeError, ValueError):
        return default


def _request_list(request: dict, name: str) -> list[str]:
    value = request.get(name, [])
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in re.split(r"[\n,]+", str(value)) if item.strip()]


def main(argv=None):
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
            import logging
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
                payload = entry
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
            run_dashboard(agent, host=args.host, port=args.port, interval=args.interval, fast_mode=args.fast)
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

        if args.command == "benchmark":
            result = agent.benchmark_run(fast_mode=args.fast)
            if args.format == "json":
                print(agent.reporter.render_json(result))
            else:
                print(_render_benchmark(result))
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
            from adapters import build_adapter_docs
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
