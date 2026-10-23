"""Small shared helpers for Sentinel 2.0.

The package intentionally depends on the Python standard library only.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

TEXT_ENCODING = "utf-8"

GRADE_BANDS: tuple[tuple[int, str], ...] = (
    (97, "A+"),
    (93, "A"),
    (90, "A-"),
    (87, "B+"),
    (83, "B"),
    (80, "B-"),
    (77, "C+"),
    (73, "C"),
    (70, "C-"),
    (60, "D"),
    (0, "F"),
)

# The coarse band is the product claim: it is the resolution this analysis can
# actually defend. Five of six dimensions sit near A+ on maintained code, so a
# fine overall grade was manufacturing differences the evidence does not
# contain. A band deliberately allows ties - two repositories can both be
# "maintained" without the tool pretending to rank them.
#
# The labels describe maintenance evidence, not commit activity:
#   maintained   - tests, docs and dependency hygiene are in place
#   adequate     - usable, but carries real debt worth fixing
#   weak         - significant structural problems; risky to depend on
#   unmaintained - little or no evidence of maintenance practice
QUALITY_BANDS: tuple[tuple[int, str], ...] = (
    (80, "maintained"),
    (70, "adequate"),
    (60, "weak"),
    (0, "unmaintained"),
)
BAND_ORDER = ("unmaintained", "weak", "adequate", "maintained")

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
CONFIDENCE_ORDER = {"high": 0, "medium": 1, "low": 2}
DIMENSION_ORDER = {
    "architecture": 0,
    "maintainability": 1,
    "tests": 2,
    "docs": 3,
    "dependencies": 4,
    "security": 5,
}

_SECRET_PLACEHOLDERS = (
    "example",
    "sample",
    "placeholder",
    "changeme",
    "change-me",
    "your_",
    "your-",
    "xxxx",
    "****",
    "<",
    "${",
    "{{",
    "os.environ",
    "os.getenv",
    "getenv",
    "env.",
    "null",
    "none",
    "dummy",
    "fake",
    "test",
    "secret",
    "password",
    "token",
    "bearer",
    "authorization",
)

PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN (?:RSA |OPENSSH |EC |DSA |PGP )?PRIVATE KEY-----"
)
SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private key", PRIVATE_KEY_RE),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b")),
    ("GitHub fine-grained token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
    ("OpenAI-style key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("Stripe key", re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}\b")),
    (
        "assigned credential",
        re.compile(
            r"(?i)\b(?:api[_-]?key|apikey|secret|token|password|passwd|pwd)\b"
            r"\s*[:=]\s*[\"']([^\"'\n]{8,})[\"']"
        ),
    ),
)


def now_iso() -> str:
    """Return a local-time ISO timestamp with an offset (seconds precision)."""

    return datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8", errors="replace"))


def decode_bytes(data: bytes) -> tuple[str, bool]:
    """Decode UTF-8, replacing invalid bytes and reporting whether we did."""

    try:
        return data.decode("utf-8"), False
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace"), True


def count_lines(text: str) -> int:
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def format_bytes(value: int | float) -> str:
    number = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if number < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(number)} {unit}"
            return f"{number:.1f} {unit}"
        number /= 1024
    return f"{number:.1f} GB"


def format_ms(value: int | float) -> str:
    if value < 1000:
        return f"{int(value)}ms"
    return f"{value / 1000:.2f}s"


def relpath(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except (ValueError, OSError):
        return path.as_posix()


def is_within(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


@lru_cache(maxsize=200_000)
def module_name_for_path(rel_path: str) -> str:
    """Convert a project-relative source path into a dotted module name."""

    path = rel_path.replace("\\", "/")
    if path.startswith("src/"):
        path = path[4:]
    parts = [part for part in path.split("/") if part]
    if not parts:
        return ""
    parts[-1] = re.sub(r"\.(py|pyi|pyx)$", "", parts[-1])
    if parts[-1] in {"__init__", "index"}:
        parts.pop()
    return ".".join(part for part in parts if part and part not in {".", ".."})


def grade_for(score: float) -> str:
    for floor, label in GRADE_BANDS:
        if score >= floor:
            return label
    return "F"


def grade_score(label: str) -> int | None:
    wanted = label.strip().upper().rstrip("?")
    for floor, name in GRADE_BANDS:
        if name == wanted:
            return floor
    return None


def bands_from_rules(rules: Mapping[str, Any] | None) -> tuple[tuple[int, str], ...]:
    """Read the configured coarse bands, falling back to the defaults."""

    configured = (rules or {}).get("bands")
    if not isinstance(configured, list) or not configured:
        return QUALITY_BANDS
    bands: list[tuple[int, str]] = []
    for item in configured:
        if not isinstance(item, Mapping):
            continue
        try:
            floor = int(item["min_score"])
        except (KeyError, TypeError, ValueError):
            continue
        label = str(item.get("label", "")).strip()
        if label:
            bands.append((floor, label))
    return tuple(sorted(bands, key=lambda entry: entry[0], reverse=True)) or QUALITY_BANDS


def band_for(score: float, bands: tuple[tuple[int, str], ...] | None = None) -> str:
    """Map a score to its coarse quality band."""

    table = bands or QUALITY_BANDS
    for floor, label in table:
        if score >= floor:
            return label
    return table[-1][1]


def band_rank(label: str) -> int | None:
    """Ordinal position of a band, for monotonicity checks. None if unknown."""

    try:
        return BAND_ORDER.index(label.strip().lower())
    except ValueError:
        return None


def is_data_blob(lines: int, function_count: int, class_count: int, min_lines: int) -> bool:
    """True for large files that are literal data rather than maintainable code.

    Generated tables and benchmark payloads (for example a 40k-line Python file
    holding one big literal) have no functions and no classes. Treating them as
    ordinary source inflates file-size findings and forces sampling, which drags
    confidence down for a problem that is not really there.
    """

    return lines >= min_lines and function_count == 0 and class_count == 0


def parse_grade_threshold(value: str) -> int | None:
    """Resolve a band name, grade label or numeric score to a score floor."""

    text = value.strip().lower()
    for floor, label in QUALITY_BANDS:
        if label == text:
            return floor
    return grade_score(value)


def redact_secret(value: str) -> str:
    value = value.strip()
    if len(value) <= 6:
        return "[REDACTED]"
    return f"{value[:3]}...[REDACTED]"


def looks_like_placeholder(value: str) -> bool:
    lowered = value.strip().lower()
    return any(token in lowered for token in _SECRET_PLACEHOLDERS)


_CREDENTIAL_VALUE_RE = re.compile(r"^[A-Za-z0-9_\-+/=.]{8,}$")
_NON_CREDENTIAL_VALUES = {"true", "false", "none", "null", "undefined", "inherit"}


def looks_like_credential(value: str) -> bool:
    """Filter for ``assigned credential`` matches.

    The name-based pattern matches every ``token = "..."`` assignment, including
    docstring examples such as ``>>> token = 'variable|default:...'``. A real
    credential is one opaque token: no whitespace, no quotes or structural
    punctuation, and normally mixed letters with digits or symbols. Being wrong
    here costs a real repository its security grade, so the filter is strict.
    """

    value = value.strip()
    if not _CREDENTIAL_VALUE_RE.match(value):
        return False
    if value.lower() in _NON_CREDENTIAL_VALUES:
        return False
    if looks_like_placeholder(value):
        return False
    if not any(char.isalpha() for char in value):
        return False
    has_digit = any(char.isdigit() for char in value)
    has_symbol = any(not char.isalnum() for char in value)
    # Short plain words and simple identifiers are almost never credentials.
    if len(value) < 20 and not has_digit and not has_symbol:
        return False
    return has_digit or has_symbol or len(value) >= 24


def find_secrets(text: str, limit: int = 5) -> list[dict[str, Any]]:
    """Find likely secrets and return redacted evidence only."""

    found: list[dict[str, Any]] = []
    seen_lines: set[int] = set()
    lines = text.splitlines()
    for line_number, line in enumerate(lines, start=1):
        if line_number in seen_lines:
            continue
        if len(line) > 4000:
            line = line[:4000]
        for kind, pattern in SECRET_PATTERNS:
            match = pattern.search(line)
            if not match:
                continue
            value = match.group(1) if match.lastindex else match.group(0)
            if looks_like_placeholder(value):
                continue
            if kind == "assigned credential" and not looks_like_credential(value):
                continue
            found.append(
                {
                    "line": line_number,
                    "kind": kind,
                    "redacted": redact_secret(value),
                }
            )
            seen_lines.add(line_number)
            break
        if len(found) >= limit:
            break
    return found


def find_dangerous_patterns(text: str, limit: int = 10) -> list[dict[str, Any]]:
    patterns: tuple[tuple[str, re.Pattern[str]], ...] = (
        ("eval", re.compile(r"(?<![\w.])eval\s*\(")),
        ("exec", re.compile(r"(?<![\w.])exec\s*\(")),
        ("os.system", re.compile(r"\bos\.system\s*\(")),
        (
            "subprocess shell=True",
            re.compile(r"subprocess\.(?:run|call|Popen|check_output)\([^\n]*shell\s*=\s*True"),
        ),
        ("pickle.loads", re.compile(r"\bpickle\.loads\s*\(")),
        (
            "yaml.load without SafeLoader",
            re.compile(r"\byaml\.load\s*\((?![^\n]*SafeLoader)"),
        ),
    )
    found: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for kind, pattern in patterns:
            if pattern.search(line):
                found.append({"line": line_number, "kind": kind})
                break
        if len(found) >= limit:
            break
    return found


def find_debug_patterns(text: str, limit: int = 5) -> list[dict[str, Any]]:
    patterns: tuple[tuple[str, re.Pattern[str]], ...] = (
        ("debug mode enabled", re.compile(r"(?i)\bDEBUG\s*=\s*True\b")),
        ("flask debug enabled", re.compile(r"\.run\([^\n]*debug\s*=\s*True")),
        ("django debug enabled", re.compile(r"(?im)^\s*DEBUG\s*=\s*True\s*$")),
    )
    found: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for kind, pattern in patterns:
            if pattern.search(line):
                found.append({"line": line_number, "kind": kind})
                break
        if len(found) >= limit:
            break
    return found


def run_git(root: Path, args: Iterable[str], timeout: float = 15.0) -> subprocess.CompletedProcess[str] | None:
    """Run a git command inside *root*. Return None when git is unavailable."""

    command = ["git", "-C", str(root), *args]
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def git_available(root: Path) -> bool:
    if not (root / ".git").exists():
        result = run_git(root, ["rev-parse", "--is-inside-work-tree"])
        return bool(result and result.returncode == 0 and result.stdout.strip() == "true")
    result = run_git(root, ["rev-parse", "--is-inside-work-tree"])
    return bool(result and result.returncode == 0 and result.stdout.strip() == "true")


def git_metadata(root: Path) -> dict[str, Any]:
    """Return commit, branch and dirty flag when git is available."""

    result = run_git(root, ["rev-parse", "--short", "HEAD"])
    if not result or result.returncode != 0:
        return {"available": False, "commit": None, "branch": None, "dirty": False}
    commit = result.stdout.strip()
    branch_result = run_git(root, ["rev-parse", "--abbrev-ref", "HEAD"])
    branch = branch_result.stdout.strip() if branch_result and branch_result.returncode == 0 else None
    status = run_git(root, ["status", "--porcelain"])
    dirty = bool(status and status.stdout.strip())
    return {"available": True, "commit": commit, "branch": branch, "dirty": dirty}


def git_churn(root: Path, days: int = 90, max_commits: int = 500) -> dict[str, int] | None:
    """Count file touches over recent git history. Returns None without git."""

    if not git_available(root):
        return None
    result = run_git(
        root,
        [
            "log",
            f"--since={days} days ago",
            f"-n{max_commits}",
            "--name-only",
            "--pretty=format:",
        ],
        timeout=30.0,
    )
    if not result or result.returncode != 0:
        return None
    prefix_result = run_git(root, ["rev-parse", "--show-prefix"])
    prefix = prefix_result.stdout.strip().replace("\\", "/") if prefix_result and prefix_result.returncode == 0 else ""
    churn: dict[str, int] = {}
    for line in result.stdout.splitlines():
        path = line.strip().replace("\\", "/")
        if not path:
            continue
        if prefix:
            if not path.startswith(prefix):
                continue
            path = path[len(prefix) :]
        if path:
            churn[path] = churn.get(path, 0) + 1
    return churn


def atomic_write(path: Path, text: str, encoding: str = TEXT_ENCODING) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding=encoding, errors="replace", newline="") as handle:
            handle.write(text)
        os.replace(temp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temp_name)
        raise


def configure_console() -> None:
    """Make stdout/stderr safe for non-ASCII output on legacy Windows consoles."""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            with contextlib.suppress(OSError, ValueError):
                reconfigure(errors="replace")


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def report_fingerprint(report: dict[str, Any]) -> str:
    """Hash the meaningful parts of a report, ignoring timestamps/durations."""

    clone = json.loads(json.dumps(report))
    clone.pop("generated_at", None)
    scan = clone.get("scan", {})
    for key in ("duration_ms", "files_analyzed", "files_cached", "started_at", "finished_at"):
        scan.pop(key, None)
    clone["scan"] = scan
    for finding in clone.get("findings", []):
        finding.pop("id", None)
    return sha256_text(stable_json(clone))


def parse_project_name(text: str, fallback: str) -> str:
    """Extract a project name from pyproject.toml/package.json-like text."""

    json_match = re.search(r'"name"\s*:\s*"([^"]+)"', text)
    if json_match:
        return json_match.group(1)
    toml_match = re.search(r'(?m)^\s*name\s*=\s*"([^"]+)"', text)
    if toml_match:
        return toml_match.group(1)
    fallback_name = fallback.strip().lower().replace("_", "-")
    return fallback_name or "repository"
