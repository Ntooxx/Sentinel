"""Repository discovery: git-first with a deterministic filesystem fallback."""

from __future__ import annotations

import fnmatch
import os
import stat as stat_module
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .classify import Classification, classify_path
from .utils import git_available, git_metadata, run_git


@dataclass(frozen=True)
class Candidate:
    path: str
    abspath: Path
    size: int
    mtime_ns: int
    classification: Classification
    sampled_hint: bool = False


@dataclass
class DiscoveryResult:
    root: Path
    candidates: list[Candidate]
    skipped: list[dict[str, Any]] = field(default_factory=list)
    excluded_counts: dict[str, int] = field(default_factory=dict)
    git_info: dict[str, Any] = field(default_factory=lambda: {"available": False, "commit": None, "branch": None, "dirty": False})
    git_used: bool = False
    quick: bool = False


class _IgnoreMatcher:
    """Small .gitignore subset for non-git repositories."""

    def __init__(self, patterns: Iterable[str]):
        self.rules: list[tuple[str, bool, bool]] = []
        for raw in patterns:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            negate = line.startswith("!")
            if negate:
                line = line[1:]
            directory_only = line.endswith("/")
            line = line.rstrip("/")
            if not line:
                continue
            self.rules.append((line, negate, directory_only))

    def matches(self, rel_path: str) -> bool:
        ignored = False
        path = rel_path.replace("\\", "/")
        name = path.rsplit("/", 1)[-1]
        for pattern, negate, directory_only in self.rules:
            anchored = pattern.startswith("/")
            clean = pattern.lstrip("/")
            if directory_only and "/" not in clean:
                hit = clean in path.split("/")[:-1]
            elif anchored:
                hit = fnmatch.fnmatchcase(path, clean) or path.startswith(clean.rstrip("/") + "/")
            elif "/" in clean:
                hit = fnmatch.fnmatchcase(path, clean) or fnmatch.fnmatchcase(path, f"*/{clean}")
            else:
                hit = fnmatch.fnmatchcase(name, clean) or any(
                    fnmatch.fnmatchcase(part, clean) for part in path.split("/")
                )
            if hit:
                ignored = not negate
        return ignored


def _load_gitignore(root: Path) -> _IgnoreMatcher:
    path = root / ".gitignore"
    if not path.is_file():
        return _IgnoreMatcher([])
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return _IgnoreMatcher([])
    return _IgnoreMatcher(text.splitlines())


def _normalise_ignore(ignore: str, root: Path) -> str:
    try:
        candidate = Path(ignore)
        if candidate.is_absolute():
            candidate = candidate.resolve().relative_to(root.resolve())
        return candidate.as_posix().strip("/")
    except (OSError, ValueError):
        return ignore.replace("\\", "/").strip("/")


def _ignored_by_user(rel_path: str, ignores: Iterable[str]) -> bool:
    path = rel_path.replace("\\", "/")
    for ignore in ignores:
        if not ignore:
            continue
        if path == ignore or path.startswith(ignore + "/"):
            return True
        if any(char in ignore for char in "*?[]") and fnmatch.fnmatch(path, ignore):
            return True
    return False


def _inside_default_dir(rel_path: str, default_dirs: set[str]) -> bool:
    parts = rel_path.replace("\\", "/").split("/")
    return any(part in default_dirs for part in parts[:-1])


def _git_candidates(root: Path) -> list[str] | None:
    result = run_git(root, ["ls-files", "-co", "--exclude-standard", "-z"])
    if not result or result.returncode != 0:
        return None
    entries = [entry for entry in result.stdout.split("\0") if entry]
    return sorted(dict.fromkeys(entry.replace("\\", "/") for entry in entries))


def _walk_candidates(root: Path, default_dirs: set[str]) -> list[str]:
    ignore = _load_gitignore(root)
    results: list[str] = []

    def walk(directory: Path, relative: str) -> None:
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name.lower())
        except OSError:
            return
        for entry in entries:
            child_rel = f"{relative}/{entry.name}" if relative else entry.name
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if entry.name in default_dirs or ignore.matches(child_rel + "/"):
                        continue
                    walk(Path(entry.path), child_rel)
                elif entry.is_file(follow_symlinks=False):
                    if ignore.matches(child_rel):
                        continue
                    results.append(child_rel)
            except OSError:
                continue

    walk(root, "")
    return sorted(results)


def _probe_binary(path: Path, probe_bytes: int) -> str | None:
    try:
        with path.open("rb") as handle:
            chunk = handle.read(probe_bytes)
    except OSError:
        return "unreadable"
    if b"\0" in chunk:
        return "binary"
    return None


def discover(
    root: Path,
    rules: Mapping[str, Any],
    ignores: Iterable[str] = (),
    quick: bool = False,
    probe_binary: bool = True,
) -> DiscoveryResult:
    """Discover candidate files under *root*.

    ``quick`` skips content probes and is used by warm scans and watch-mode
    polling. Analysis still rejects binary files on the read path.
    """

    root = root.resolve()
    default_dirs = {str(item) for item in rules.get("exclude", {}).get("default_dirs", [])}
    max_file_size = int(rules.get("max_file_size", 5 * 1024 * 1024))
    probe_bytes = int(rules.get("binary_probe_bytes", 8192))
    user_ignores = [_normalise_ignore(item, root) for item in ignores]

    git_info = git_metadata(root)
    git_used = bool(git_info.get("available"))
    rel_paths = _git_candidates(root) if git_used else None
    if rel_paths is None:
        git_used = False
        rel_paths = _walk_candidates(root, default_dirs)

    candidates: list[Candidate] = []
    skipped: list[dict[str, Any]] = []
    excluded_counts: dict[str, int] = {}

    for rel_path in rel_paths:
        if _inside_default_dir(rel_path, default_dirs):
            continue
        if _ignored_by_user(rel_path, user_ignores):
            continue

        classification = classify_path(rel_path, rules)
        if classification.role in {"vendor", "generated", "doc_translation", "example"}:
            excluded_counts[classification.role] = excluded_counts.get(classification.role, 0) + 1
            continue

        absolute = root / rel_path
        try:
            file_stat = absolute.stat()
        except FileNotFoundError:
            # Tracked in git but absent from the working tree (deleted and not yet
            # committed). That is not a readability problem, so skip silently rather
            # than inflating the skipped-file count with a phantom "unreadable" row.
            continue
        except OSError:
            skipped.append({"path": rel_path, "reason": "unreadable", "role": classification.role, "size": 0})
            continue
        if not stat_module.S_ISREG(file_stat.st_mode):
            continue
        if file_stat.st_size > max_file_size:
            skipped.append(
                {
                    "path": rel_path,
                    "reason": "oversized",
                    "role": classification.role,
                    "size": file_stat.st_size,
                }
            )
            continue
        if not quick and probe_binary:
            binary_reason = _probe_binary(absolute, probe_bytes)
            if binary_reason:
                skipped.append(
                    {
                        "path": rel_path,
                        "reason": binary_reason,
                        "role": classification.role,
                        "size": file_stat.st_size,
                    }
                )
                continue

        candidates.append(
            Candidate(
                path=rel_path,
                abspath=absolute,
                size=file_stat.st_size,
                mtime_ns=file_stat.st_mtime_ns,
                classification=classification,
                sampled_hint=file_stat.st_size > int(rules.get("analyze_full_limit", 1024 * 1024)),
            )
        )

    candidates.sort(key=lambda item: item.path)
    skipped.sort(key=lambda item: item["path"])
    return DiscoveryResult(
        root=root,
        candidates=candidates,
        skipped=skipped,
        excluded_counts=excluded_counts,
        git_info=git_info,
        git_used=git_used,
        quick=quick,
    )


def snapshot_signature(result: DiscoveryResult) -> tuple[tuple[str, int, int], ...]:
    return tuple((candidate.path, candidate.size, candidate.mtime_ns) for candidate in result.candidates)


def changed_paths(previous: Iterable[Candidate], current: Iterable[Candidate]) -> set[str]:
    before = {candidate.path: (candidate.size, candidate.mtime_ns) for candidate in previous}
    after = {candidate.path: (candidate.size, candidate.mtime_ns) for candidate in current}
    return {path for path, signature in after.items() if before.get(path) != signature} | (set(before) - set(after))


def git_status_paths(root: Path) -> list[str] | None:
    """Return changed paths from git status, or None if git is unavailable."""

    if not git_available(root):
        return None
    result = run_git(root, ["status", "--porcelain", "-z"])
    if not result or result.returncode != 0:
        return None
    entries = result.stdout.split("\0")
    paths: list[str] = []
    index = 0
    while index < len(entries):
        entry = entries[index]
        if not entry:
            index += 1
            continue
        status = entry[:2]
        path = entry[3:] if len(entry) > 3 else ""
        if status.startswith("R") or status.startswith("C"):
            # In -z mode the new path is in this entry and the old path follows.
            if path:
                paths.append(path.replace("\\", "/"))
            index += 1
            if index < len(entries) and entries[index]:
                paths.append(entries[index].replace("\\", "/"))
            index += 1
            continue
        if path:
            paths.append(path.replace("\\", "/"))
        index += 1
    return sorted(dict.fromkeys(paths))


__all__ = [
    "Candidate",
    "DiscoveryResult",
    "changed_paths",
    "discover",
    "git_status_paths",
    "snapshot_signature",
]
