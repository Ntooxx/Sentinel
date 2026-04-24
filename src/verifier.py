from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any


def verify_patch(
    project_dir: str | Path,
    changed_files: list[str] | None = None,
    *,
    command: str | None = None,
    dry_run: bool = False,
    timeout: int = 120,
) -> dict[str, Any]:
    root = Path(project_dir).resolve()
    changed = changed_files or detect_changed_files(root)
    commands = [command] if command else suggest_test_commands(root, changed)
    if not commands:
        commands = [f"{sys.executable} -m pytest"]

    results: list[dict[str, Any]] = []
    for test_command in commands:
        if dry_run:
            results.append(
                {
                    "command": test_command,
                    "returncode": None,
                    "passed": None,
                    "stdout_tail": "",
                    "stderr_tail": "",
                    "dry_run": True,
                }
            )
            continue
        completed = subprocess.run(
            test_command,
            cwd=root,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        results.append(
            {
                "command": test_command,
                "returncode": completed.returncode,
                "passed": completed.returncode == 0,
                "stdout_tail": _tail(completed.stdout),
                "stderr_tail": _tail(completed.stderr),
                "dry_run": False,
            }
        )

    passed = all(item["passed"] is True for item in results) if not dry_run else None
    return {
        "project_dir": str(root),
        "changed_files": changed,
        "commands": commands,
        "results": results,
        "passed": passed,
        "summary": _summary(changed, results, dry_run=dry_run),
    }


def detect_changed_files(root: Path) -> list[str]:
    try:
        diff = subprocess.run(
            ["git", "diff", "--name-only"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        status = subprocess.run(
            ["git", "status", "--short"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []

    files = set(line.strip() for line in diff.stdout.splitlines() if line.strip())
    for line in status.stdout.splitlines():
        if len(line) >= 4:
            files.add(line[3:].strip())
    return sorted(files)


def suggest_test_commands(root: Path, changed_files: list[str]) -> list[str]:
    tests = _find_related_tests(root, changed_files)
    if tests:
        joined = " ".join(_quote(path) for path in tests)
        return [f"{sys.executable} -m pytest {joined}"]
    if (root / "tests").is_dir():
        return [f"{sys.executable} -m pytest tests"]
    return []


def _find_related_tests(root: Path, changed_files: list[str]) -> list[str]:
    tests_dir = root / "tests"
    if not tests_dir.is_dir():
        return []
    test_files = sorted(path for path in tests_dir.rglob("test*.py") if path.is_file())
    selected: list[str] = []
    changed_stems = {Path(path).stem for path in changed_files if path.endswith(".py")}
    for changed in changed_files:
        path = Path(changed)
        if path.parts and path.parts[0] == "tests" and changed.endswith(".py"):
            selected.append(changed)
    for test_file in test_files:
        rel = test_file.relative_to(root).as_posix()
        stem = test_file.stem.removeprefix("test_").removesuffix("_test")
        try:
            content = test_file.read_text(encoding="utf-8", errors="ignore").lower()
        except OSError:
            content = ""
        if stem in changed_stems or any(stem and changed.lower() in content for changed in changed_stems):
            selected.append(rel)
    return sorted(set(selected))


def _quote(value: str) -> str:
    return f'"{value}"' if " " in value else value


def _tail(value: str, max_lines: int = 30) -> str:
    lines = value.splitlines()
    return "\n".join(lines[-max_lines:])


def _summary(changed: list[str], results: list[dict[str, Any]], *, dry_run: bool) -> str:
    if dry_run:
        return f"Planned {len(results)} narrow check(s) for {len(changed)} changed file(s)."
    failed = [item for item in results if item.get("passed") is False]
    if failed:
        return f"{len(failed)} of {len(results)} verification command(s) failed."
    return f"All {len(results)} verification command(s) passed."
