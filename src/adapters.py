from __future__ import annotations

from pathlib import Path
from typing import Any

from utils import ensure_dir

ADAPTERS = {
    "cline": {
        "file": "cline.md",
        "title": "Cline",
        "prompt": "Before broad exploration, run `project-sentinel retrieve . --query \"<task>\" --fast` and inspect only the returned focus files first.",
    },
    "claude-code": {
        "file": "claude-code.md",
        "title": "Claude Code",
        "prompt": "Start with `project-sentinel prompt . --goal next --budget small --fast`, then use `project-sentinel verify .` after edits.",
    },
    "codex": {
        "file": "codex.md",
        "title": "Codex",
        "prompt": "Read `CONTEXT.md` when present, then run `project-sentinel retrieve . --query \"<task>\" --goal debug --fast` for task-specific context.",
    },
    "roo": {
        "file": "roo.md",
        "title": "Roo",
        "prompt": "Use Sentinel's `kilo-refresh` style file bridge: consume `.sentinel/kilo/prompt.md`, `.sentinel/kilo/focus-files.txt`, and refresh after meaningful edits.",
    },
    "continue": {
        "file": "continue.md",
        "title": "Continue",
        "prompt": "Pin Sentinel output into chat context with `project-sentinel context . --budget small --fast` and use `retrieve --query` for each focused step.",
    },
}


def build_adapter_docs(project_dir: str | Path, *, write: bool = False) -> dict[str, Any]:
    root = Path(project_dir).resolve()
    docs: dict[str, str] = {}
    for key, adapter in ADAPTERS.items():
        docs[key] = _adapter_text(adapter["title"], adapter["prompt"])

    written: dict[str, str] = {}
    if write:
        target_dir = ensure_dir(root / ".sentinel" / "adapters")
        for key, text in docs.items():
            path = target_dir / ADAPTERS[key]["file"]
            path.write_text(text, encoding="utf-8")
            written[key] = str(path)

    return {
        "adapters": list(ADAPTERS),
        "docs": docs,
        "written": written,
    }


def _adapter_text(title: str, prompt: str) -> str:
    return (
        f"# Sentinel Adapter: {title}\n\n"
        "## Startup Prompt\n"
        f"{prompt}\n\n"
        "## Failure Modes\n"
        "- If Sentinel state is stale, run `project-sentinel scan . --fast` first.\n"
        "- If the retrieval looks too broad, repeat it with a more specific query.\n"
        "- If tests are unknown, run `project-sentinel verify . --dry-run` to see the narrow command Sentinel would choose.\n"
        "- If MCP is unavailable, use the file bridge outputs under `.sentinel/`.\n"
    )
