from __future__ import annotations

import json
import os
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_KNOWLEDGE_BASE = {
    "files": {},
    "architecture": {},
    "dependencies": {},
    "understanding": {},
    "llm_readiness": {},
    "savings": {
        "events": [],
        "total_full_tokens": 0,
        "total_emitted_tokens": 0,
    },
    "task_memory": [],
    "patterns": [],
    "issues": [],
    "decisions": [],
    "suggestions": [],
    "scan_history": [],
    "last_scan": None,
    "last_checkpoint": None,
}


DEFAULT_CONFIG = {
    "scan_interval_seconds": 60,
    "important_extensions": [
        ".py",
        ".js",
        ".ts",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".md",
        ".txt",
        ".env",
        ".gitignore",
        "Dockerfile",
        "Makefile",
        ".sh",
        ".bash",
        ".cfg",
        ".ini",
        ".cpp",
        ".c",
        ".h",
        ".hpp",
        ".cc",
        ".cxx",
        ".hh",
        ".hxx",
        ".rs",
        ".cmake",
    ],
    "ignore_dirs": [
        "__pycache__",
        "node_modules",
        ".git",
        ".kilo",
        ".kilocode",
        ".sentinel",
        ".venv",
        "venv",
        "dist",
        "build",
        ".cache",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        ".idea",
        ".vscode",
    ],
    "max_file_size_bytes": 1_048_576,
    "checkpoint_on_significant_change": True,
    "auto_suggest": True,
    "knowledge_base_path": ".sentinel/knowledge_base.json",
    "checkpoints_path": ".sentinel/checkpoints.json",
    "reports_path": ".sentinel/reports",
    "log_file": ".sentinel/sentinel.log",
    "audit_rules_path": "config/audit_rules.json",
    "patterns_path": "config/patterns.json",
    "performance_budgets": {
        "scan_seconds": 5.0,
        "files": 500,
        "context_tokens": 4000,
    },
}


DEFAULT_AUDIT_RULES = {
    "large_file_line_threshold": 500,
    "large_file_size_threshold": 100_000,
    "health_penalties": {
        "critical": 20,
        "high": 15,
        "medium": 8,
        "low": 3,
    },
    "health_penalties_by_type": {
        "large_file": 1.5,
        "large_file_size": 1,
        "todo": 1,
        "doc_code_drift": 4,
        "no_readme": 8,
        "no_entry_point": 10,
        "no_tests": 25,
    },
    "health_score_floors": {
        "maintainability_only": 70,
    },
    "significant_change_thresholds": {
        "new_files": 1,
        "modified_files": 1,
        "deleted_files": 1,
    },
}


DEFAULT_PATTERNS = {
    "patterns": [
        {
            "name": "automated_tests",
            "description": "The project includes automated tests",
            "path_contains_any": ["tests/", "test_", "_test."],
            "extension_in": [".py", ".js", ".ts"],
        },
        {
            "name": "documentation",
            "description": "The project includes markdown documentation",
            "path_contains_any": ["readme.md", "docs/"],
            "extension_in": [".md"],
        },
        {
            "name": "command_line_interface",
            "description": "The project exposes a command-line entry point",
            "requires_main": True,
            "extension_in": [".py", ".js", ".ts", ".sh", ".bash", ".cpp", ".c", ".cc", ".cxx", ".rs"],
        },
        {
            "name": "containerization",
            "description": "The project includes container-related assets",
            "file_name_in": ["dockerfile", "docker-compose.yml", "docker-compose.yaml"],
        },
        {
            "name": "packaging",
            "description": "The project includes package or dependency manifests",
            "file_name_in": ["requirements.txt", "pyproject.toml", "setup.py", "package.json", "Cargo.toml", "CMakeLists.txt"],
        },
    ]
}


CONTEXT_BUDGETS = {
    "tiny": {
        "components": 3,
        "files": 4,
        "issues": 3,
        "patterns": 3,
        "suggestions": 1,
        "directories": 4,
        "decisions": 2,
        "frameworks": 4,
    },
    "small": {
        "components": 5,
        "files": 6,
        "issues": 4,
        "patterns": 5,
        "suggestions": 2,
        "directories": 6,
        "decisions": 3,
        "frameworks": 6,
    },
    "medium": {
        "components": 7,
        "files": 8,
        "issues": 6,
        "patterns": 8,
        "suggestions": 3,
        "directories": 10,
        "decisions": 5,
        "frameworks": 8,
    },
    "large": {
        "components": 10,
        "files": 12,
        "issues": 10,
        "patterns": 12,
        "suggestions": 5,
        "directories": 16,
        "decisions": 8,
        "frameworks": 12,
    },
}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def estimate_text_tokens(text: str) -> int:
    stripped = text.strip()
    if not stripped:
        return 0
    return max(1, (len(stripped) + 3) // 4)


def normalize_budget_name(name: str | None, default: str = "small") -> str:
    if not name:
        return default
    lowered = name.strip().lower()
    if lowered in CONTEXT_BUDGETS:
        return lowered
    return default


def is_truthy_env(name: str) -> bool:
    value = os.getenv(name, "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def ensure_parent_dir(path: os.PathLike[str] | str) -> Path:
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OSError(
            f"Cannot create state directory at {target.parent}: {exc}. "
            "This may be caused by an intermediate path component that is not a directory, "
            "a Windows path length limit, or a restricted temp directory. "
            "Try setting SENTINEL_HOME to a writable directory with a short path."
        ) from exc
    return target


def ensure_dir(path: os.PathLike[str] | str) -> Path:
    target = Path(path)
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OSError(
            f"Cannot create directory at {target}: {exc}. "
            "This may be caused by an intermediate path component that is not a directory, "
            "a Windows path length limit, or a restricted temp directory. "
            "Try setting SENTINEL_HOME to a writable directory with a short path."
        ) from exc
    return target


def read_json(path: os.PathLike[str] | str, default: Any) -> Any:
    target = Path(path)
    if not target.exists():
        return deepcopy(default)

    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return deepcopy(default)


def write_json(path: os.PathLike[str] | str, payload: Any) -> Path:
    target = ensure_parent_dir(path)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return target


def resolve_path(base_dir: os.PathLike[str] | str, raw_path: os.PathLike[str] | str) -> Path:
    target = Path(raw_path).expanduser()
    if target.is_absolute():
        return target
    return (Path(base_dir) / target).resolve()


def merge_dicts(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    merged = deepcopy(dict(base))
    for key, value in override.items():
        if isinstance(merged.get(key), Mapping) and isinstance(value, Mapping):
            merged[key] = merge_dicts(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def validate_config(config: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    required = {
        "scan_interval_seconds": int,
        "important_extensions": list,
        "ignore_dirs": list,
        "max_file_size_bytes": int,
        "checkpoint_on_significant_change": bool,
        "auto_suggest": bool,
        "knowledge_base_path": str,
        "checkpoints_path": str,
        "reports_path": str,
        "log_file": str,
        "audit_rules_path": str,
        "patterns_path": str,
    }
    for key, expected in required.items():
        if key not in config:
            errors.append(f"Missing config key: {key}")
            continue
        value = config[key]
        if expected is bool:
            if not isinstance(value, bool):
                errors.append(f"Config key {key} must be true or false")
        elif expected is int:
            if not isinstance(value, int) or isinstance(value, bool):
                errors.append(f"Config key {key} must be an integer")
        elif not isinstance(value, expected):
            errors.append(f"Config key {key} must be {expected.__name__}")

    if isinstance(config.get("scan_interval_seconds"), int) and config["scan_interval_seconds"] < 1:
        errors.append("Config key scan_interval_seconds must be >= 1")
    if isinstance(config.get("max_file_size_bytes"), int) and config["max_file_size_bytes"] < 1:
        errors.append("Config key max_file_size_bytes must be >= 1")
    if isinstance(config.get("important_extensions"), list):
        bad = [item for item in config["important_extensions"] if not isinstance(item, str) or not item]
        if bad:
            errors.append("Config key important_extensions must contain only non-empty strings")
    if isinstance(config.get("ignore_dirs"), list):
        bad = [item for item in config["ignore_dirs"] if not isinstance(item, str) or not item]
        if bad:
            errors.append("Config key ignore_dirs must contain only non-empty strings")
    budgets = config.get("performance_budgets")
    if budgets is not None:
        if not isinstance(budgets, Mapping):
            errors.append("Config key performance_budgets must be an object")
        else:
            for key in ["scan_seconds", "files", "context_tokens"]:
                value = budgets.get(key)
                if value is not None and not isinstance(value, (int, float)):
                    errors.append(f"Performance budget {key} must be numeric")
    return errors
