"""Kilo integration functions for Sentinel.

Contains helpers and public entry points for bridging Sentinel scan results
into Kilo-readable workspace files (CONTEXT.md, AGENTS.md, focus-files.txt,
prompt.md, overview.md, status.json, kilo.jsonc / kilo.json / mcp.json).
"""

import json
import logging
import os
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from orchestrator import SentinelAgent
from utils import ensure_dir, ensure_parent_dir, read_json

def _portable_arg(path: Path, root: Path) -> str:
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError:
        return str(path.resolve())
    value = relative.as_posix()
    return value or "."

def _merge_unique_strings(existing: Any, additions: list[str]) -> list[str]:
    values = [item for item in existing if isinstance(item, str)] if isinstance(existing, list) else []
    for item in additions:
        if item not in values:
            values.append(item)
    return values

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

def _validate_workspace_focus_paths(root: Path, focus_files: list[str]) -> Dict[str, Any]:
    invalid = [path for path in focus_files if path and not (root / path).exists()]
    return {
        "context_fresh": not invalid,
        "invalid_focus_files": invalid,
        "message": "all focus files exist" if not invalid else "focus files are stale or invalid",
    }

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


