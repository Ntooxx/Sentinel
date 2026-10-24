#!/usr/bin/env python3
"""Benchmark Sentinel against ruff, bandit and semgrep on a frozen corpus.

This is a developer tool, not a public command. It exists to answer four
questions with numbers instead of adjectives:

1. Precision@5  - of the five findings Sentinel shows first on a real
                   repository, how many survive manual review?
2. Recall       - on a fixture of deliberately seeded defects, how many
                   defects inside each tool's *documented* scope does it find?
3. Missed work  - which high-severity files do the competitors flag that
                   Sentinel's top five never mention?
4. Cost         - wall time, peak memory and index size for identical input.

Subcommands::

    python tools/competitive_bench.py freeze      # record the frozen state
    python tools/competitive_bench.py corpus      # run every tool on the corpus
    python tools/competitive_bench.py defects     # run every tool on the fixture
    python tools/competitive_bench.py evaluate    # score Precision@5 + coverage
    python tools/competitive_bench.py status      # show freeze / run state

Freeze discipline: ``freeze`` hashes the analysis package. Every other
subcommand refuses to run while the hash differs, so the corpus cannot be used
to tune the rubric it is measuring. Start a new round explicitly with
``freeze`` after changing analysis code.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
BENCHMARKS = ROOT / "benchmarks"
CORPUS_FILE = BENCHMARKS / "corpus.json"
FREEZE_FILE = BENCHMARKS / "freeze.json"
LABELS_FILE = BENCHMARKS / "labels.json"
DEFECT_DIR = BENCHMARKS / "fixtures" / "defect_corpus"
DEFECT_FILE = DEFECT_DIR / "defects.json"
RESULTS_DIR = BENCHMARKS / "results"

ANALYSIS_PACKAGE = SRC / "sentinel2"
TOOLS = ("sentinel", "ruff", "bandit", "semgrep")
SEVERITY_ORDER = ("info", "low", "medium", "high")
FREEZE_MISMATCH = (
    "the analysis package changed after the benchmark was frozen "
    "({reason}). Scoring on a moving target is not a measurement: either "
    "restore the frozen package, or start a new round with "
    "`python tools/competitive_bench.py freeze` and record why in "
    "benchmarks/REPORT.md."
)


# --------------------------------------------------------------------------
# freeze
# --------------------------------------------------------------------------


def _package_files() -> list[Path]:
    files = [path for path in sorted(ANALYSIS_PACKAGE.rglob("*")) if path.is_file() and "__pycache__" not in path.parts]
    return files


def _package_digest() -> tuple[str, dict[str, str]]:
    digest = hashlib.sha256()
    per_file: dict[str, str] = {}
    for path in _package_files():
        rel = path.relative_to(ROOT).as_posix()
        content = path.read_bytes()
        file_hash = hashlib.sha256(content).hexdigest()
        per_file[rel] = file_hash
        digest.update(rel.encode("utf-8"))
        digest.update(content)
    return digest.hexdigest(), per_file


def _git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None if result.returncode == 0 else None


def _analysis_version() -> int | None:
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    try:
        from sentinel2 import config as sentinel_config
    except Exception:  # pragma: no cover - freeze must still work on a broken tree
        return None
    version = getattr(sentinel_config, "ANALYSIS_VERSION", None)
    if version is None:
        rules = sentinel_config.load_rules()
        version = rules.get("analysis_version")
    return int(version) if version is not None else None


def write_freeze() -> dict[str, Any]:
    digest, per_file = _package_digest()
    payload = {
        "frozen_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_commit": _git_commit(),
        "analysis_version": _analysis_version(),
        "package_sha256": digest,
        "package_files": len(per_file),
        "tool_versions": _tool_versions(),
        "rule": "The analysis package is not modified while a benchmark round is running. "
        "Any change to src/sentinel2 requires a new freeze and a new result directory.",
    }
    FREEZE_FILE.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def check_freeze() -> dict[str, Any]:
    if not FREEZE_FILE.is_file():
        raise SystemExit(
            "no freeze recorded. Start the round with `python tools/competitive_bench.py freeze`."
        )
    frozen = json.loads(FREEZE_FILE.read_text(encoding="utf-8"))
    digest, _ = _package_digest()
    if frozen.get("package_sha256") != digest:
        raise SystemExit(
            FREEZE_MISMATCH.format(
                reason=f"package hash {str(digest)[:12]} != frozen {str(frozen.get('package_sha256'))[:12]}"
            )
        )
    return frozen


# --------------------------------------------------------------------------
# measurement
# --------------------------------------------------------------------------


def _child_env() -> dict[str, str]:
    env = dict(os.environ)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(SRC) + (os.pathsep + existing if existing else "")
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


if sys.platform == "win32":  # pragma: no cover - exercised on Windows only
    import ctypes
    import ctypes.wintypes as win

    class _MemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", win.DWORD),
            ("PageFaultCount", win.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    class _ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", win.DWORD),
            ("cntUsage", win.DWORD),
            ("th32ProcessID", win.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", win.DWORD),
            ("cntThreads", win.DWORD),
            ("th32ParentProcessID", win.DWORD),
            ("th32PriClassBase", ctypes.c_long),
            ("dwFlags", win.DWORD),
            ("szExeFile", ctypes.c_char * 260),
        ]

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    PROCESS_QUERY_INFORMATION = 0x0400
    PROCESS_VM_READ = 0x0010
    TH32CS_SNAPPROCESS = 0x00000002
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    def _open(pid: int) -> Any:
        kernel32 = ctypes.windll.kernel32
        for access in (
            PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ,
            PROCESS_QUERY_INFORMATION | PROCESS_VM_READ,
        ):
            handle = kernel32.OpenProcess(access, False, pid)
            if handle:
                return handle
        return None

    def _working_set(handle: Any) -> int:
        counters = _MemoryCounters()
        counters.cb = ctypes.sizeof(_MemoryCounters)
        if ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return int(counters.WorkingSetSize)
        return 0

    def _descendants(root_pid: int) -> list[int]:
        kernel32 = ctypes.windll.kernel32
        snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snapshot in (None, INVALID_HANDLE_VALUE):
            return []
        parents: dict[int, int] = {}
        entry = _ProcessEntry()
        entry.dwSize = ctypes.sizeof(_ProcessEntry)
        try:
            if kernel32.Process32First(snapshot, ctypes.byref(entry)):
                while True:
                    parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
                    if not kernel32.Process32Next(snapshot, ctypes.byref(entry)):
                        break
        finally:
            kernel32.CloseHandle(snapshot)
        members = {root_pid}
        changed = True
        while changed:
            changed = False
            for pid, parent in parents.items():
                if parent in members and pid not in members:
                    members.add(pid)
                    changed = True
        return sorted(members - {root_pid})

    class TreeSampler:
        """Track the peak summed working set of a process tree."""

        def __init__(self, pid: int) -> None:
            self.peak = 0
            self.handles: dict[int, Any] = {}
            self.unreadable: set[int] = set()
            self._add(pid)
            self._refresh_children(pid)
            self._ticks = 0

        def _add(self, pid: int) -> None:
            if pid in self.handles or pid in self.unreadable:
                return
            handle = _open(pid)
            if handle:
                self.handles[pid] = handle
            else:
                self.unreadable.add(pid)

        def _refresh_children(self, root_pid: int) -> None:
            for pid in _descendants(root_pid):
                self._add(pid)

        def sample(self, root_pid: int) -> None:
            self._ticks += 1
            if self._ticks % 20 == 1 and self._ticks > 1:
                self._refresh_children(root_pid)
            total = 0
            for handle in list(self.handles.values()):
                total += _working_set(handle)
            if total > self.peak:
                self.peak = total

        def close(self) -> None:
            for handle in self.handles.values():
                ctypes.windll.kernel32.CloseHandle(handle)
            self.handles.clear()

else:  # pragma: no cover - POSIX branch

    class TreeSampler:  # type: ignore[no-redef]
        def __init__(self, pid: int) -> None:
            import resource

            self.peak = 0
            self._ru = resource
            self._pid = pid

        def sample(self, _root_pid: int) -> None:
            # ru_maxrss is the high-water mark of *any* reaped child, in KB on
            # Linux and bytes on macOS. It is a per-process ceiling, not a tree
            # sum; the report states which method produced a number.
            value = self._ru.getrusage(self._ru.RUSAGE_CHILDREN).ru_maxrss
            peak = value * (1 if sys.platform == "darwin" else 1024)
            if peak > self.peak:
                self.peak = peak

        def close(self) -> None:
            return None


def run_measured(command: list[str], *, cwd: Path, env: dict[str, str], timeout: int = 3600) -> dict[str, Any]:
    """Run a command, capturing wall time, peak tree RSS and its stdout."""

    # mkstemp hands back an open descriptor; close it or Windows refuses the
    # unlink below for the rest of the run.
    stdout_fd, stdout_name = tempfile.mkstemp(prefix="bench-out-", suffix=".txt")
    stderr_fd, stderr_name = tempfile.mkstemp(prefix="bench-err-", suffix=".txt")
    os.close(stdout_fd)
    os.close(stderr_fd)
    stdout_path = Path(stdout_name)
    stderr_path = Path(stderr_name)
    started = time.perf_counter()
    peak_rss: int | None = None
    returncode: int | None = None
    timed_out = False
    spawn_error: str | None = None
    with stdout_path.open("wb") as out, stderr_path.open("wb") as err:
        process: subprocess.Popen[bytes] | None = None
        try:
            process = subprocess.Popen(command, cwd=str(cwd), env=env, stdout=out, stderr=err)
        except OSError as exc:
            spawn_error = str(exc)
        if process is not None:
            sampler = TreeSampler(process.pid)
            try:
                while process.poll() is None:
                    sampler.sample(process.pid)
                    if time.perf_counter() - started > timeout:
                        timed_out = True
                        process.kill()
                        process.wait()
                        break
                    time.sleep(0.05)
            finally:
                sampler.sample(process.pid)
                returncode = process.returncode
                peak_rss = sampler.peak or None
                sampler.close()
    wall_ms = (time.perf_counter() - started) * 1000.0
    stdout = stdout_path.read_text(encoding="utf-8", errors="replace")
    stderr = stderr_path.read_text(encoding="utf-8", errors="replace")
    stdout_path.unlink(missing_ok=True)
    stderr_path.unlink(missing_ok=True)
    if spawn_error is not None:
        return {
            "ok": False,
            "returncode": None,
            "wall_ms": round(wall_ms, 1),
            "error": spawn_error,
            "stdout": "",
            "stderr": "",
            "command": [part.replace(str(ROOT), ".") for part in command],
        }
    return {
        "ok": not timed_out,
        "returncode": returncode,
        "wall_ms": round(wall_ms, 1),
        "peak_rss_bytes": peak_rss,
        "rss_method": "sum of process-tree working sets sampled at 50 ms (Windows)"
        if sys.platform == "win32"
        else "ru_maxrss of reaped children (POSIX, per-process ceiling)",
        "stdout": stdout,
        "stderr": stderr[-4000:],
        "error": f"timed out after {timeout}s" if timed_out else None,
        "command": [part.replace(str(ROOT), ".") for part in command],
    }


def _timed_runs(
    make_command: Any,
    *,
    cwd: Path,
    repeats: int,
    before_each: Any = None,
    acceptable_codes: tuple[int, ...] = (0,),
) -> dict[str, Any]:
    """Run the same measurement `repeats` times and keep the median wall time."""

    runs: list[dict[str, Any]] = []
    for _ in range(max(1, repeats)):
        if before_each is not None:
            before_each()
        run = run_measured(make_command(), cwd=cwd, env=_child_env())
        run["ok"] = bool(run.get("ok")) and run.get("returncode") in acceptable_codes
        runs.append(run)
    if not runs:
        return {}
    successful = [run for run in runs if run.get("ok")]
    source = successful[0] if successful else runs[0]
    walls = [run["wall_ms"] for run in successful]
    result = dict(source)
    result["ok"] = bool(successful)
    result["wall_ms"] = round(statistics.median(walls), 1) if walls else 0.0
    result["repeats"] = len(runs)
    result["wall_ms_all"] = [round(wall, 1) for wall in walls]
    if not successful:
        result["error"] = str(source.get("error") or source.get("stderr", ""))[-600:]
    return result


def _tool_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    probes: dict[str, list[str]] = {
        "sentinel": [sys.executable, "-m", "sentinel2", "--version"],
        "ruff": ["ruff", "--version"],
        "bandit": ["bandit", "--version"],
        "semgrep": ["semgrep", "--version"],
    }
    for name, command in probes.items():
        try:
            result = subprocess.run(
                command,
                cwd=str(ROOT),
                env=_child_env(),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            versions[name] = "unavailable"
            continue
        text = (result.stdout or result.stderr).strip().splitlines()
        versions[name] = text[0].strip() if text else "unknown"
    return versions


# --------------------------------------------------------------------------
# normalisation
# --------------------------------------------------------------------------


def rel_path(raw: str, repo: Path) -> str:
    """Repo-relative POSIX path for a path reported by any tool."""

    text = str(raw).replace("\\", "/")
    prefix = repo.as_posix().rstrip("/") + "/"
    if text.startswith(prefix):
        text = text[len(prefix) :]
    try:
        candidate = Path(raw)
        if candidate.is_absolute():
            text = candidate.relative_to(repo).as_posix()
    except (ValueError, OSError):
        pass
    return text.lstrip("./")


def ruff_severity(code: str) -> str:
    """Map a ruff code onto the shared scale.

    Ruff publishes no severities, so the mapping is stated here rather than
    buried: parse failures and undefined names are `high`, everything else in
    the default set is `medium`, unused-import style noise is `low`.
    """

    if code.startswith("E9"):
        return "high"
    if code in {"F821", "F822", "F823"}:
        return "high"
    if code.startswith("F4"):
        return "low"
    return "medium"


def severity_rank(value: str) -> int:
    order = {name: index for index, name in enumerate(SEVERITY_ORDER)}
    return order.get(str(value).lower(), 0)


def parse_sentinel(payload: dict[str, Any], repo: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, finding in enumerate(payload.get("findings", [])):
        rows.append(
            {
                "tool": "sentinel",
                "rule": finding.get("rule_id"),
                "file": rel_path(finding.get("file") or "", repo),
                "line": int(finding.get("line") or 0),
                "severity": str(finding.get("severity") or "info"),
                "title": finding.get("title"),
                "rank": index + 1,
            }
        )
    return rows


def parse_ruff(stdout: str, repo: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(stdout or "[]")
    except ValueError:
        return []
    rows: list[dict[str, Any]] = []
    for item in payload if isinstance(payload, list) else []:
        code = str(item.get("code") or "")
        rows.append(
            {
                "tool": "ruff",
                "rule": code,
                "file": rel_path(item.get("filename") or "", repo),
                "line": int(((item.get("location") or {}).get("row")) or 0),
                "severity": ruff_severity(code),
                "title": item.get("message"),
            }
        )
    return rows


def parse_bandit(stdout: str, repo: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(stdout or "{}")
    except ValueError:
        return []
    rows: list[dict[str, Any]] = []
    for item in payload.get("results", []) if isinstance(payload, dict) else []:
        rows.append(
            {
                "tool": "bandit",
                "rule": item.get("test_id"),
                "file": rel_path(item.get("filename") or "", repo),
                "line": int(item.get("line_number") or 0),
                "severity": str(item.get("issue_severity") or "low").lower(),
                "title": item.get("issue_text"),
                "confidence": str(item.get("issue_confidence") or "").lower(),
            }
        )
    return rows


def parse_semgrep(stdout: str, repo: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(stdout or "{}")
    except ValueError:
        return []
    mapping = {"ERROR": "high", "WARNING": "medium", "INFO": "info"}
    rows: list[dict[str, Any]] = []
    for item in payload.get("results", []) if isinstance(payload, dict) else []:
        extra = item.get("extra") or {}
        rows.append(
            {
                "tool": "semgrep",
                "rule": item.get("check_id"),
                "file": rel_path(item.get("path") or "", repo),
                "line": int(((item.get("start") or {}).get("line")) or 0),
                "severity": mapping.get(str(extra.get("severity") or "").upper(), "info"),
                "title": extra.get("message"),
            }
        )
    return rows


def count_by_severity(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = {name: 0 for name in SEVERITY_ORDER}
    for row in rows:
        counts[row["severity"]] = counts.get(row["severity"], 0) + 1
    return counts


def files_at_least(rows: list[dict[str, Any]], minimum: str) -> set[str]:
    best: dict[str, str] = {}
    for row in rows:
        current = best.get(row["file"])
        if current is None or severity_rank(row["severity"]) > severity_rank(current):
            best[row["file"]] = row["severity"]
    return {path for path, level in best.items() if severity_rank(level) >= severity_rank(minimum)}


# --------------------------------------------------------------------------
# corpus run
# --------------------------------------------------------------------------


def load_corpus() -> dict[str, Any]:
    if not CORPUS_FILE.is_file():
        raise SystemExit(f"missing corpus manifest: {CORPUS_FILE}")
    return json.loads(CORPUS_FILE.read_text(encoding="utf-8"))


def resolve_repo(entry: dict[str, Any]) -> Path | None:
    raw = entry.get("path")
    if not raw:
        return None
    repo = (ROOT / raw).resolve() if not Path(raw).is_absolute() else Path(raw)
    return repo if repo.is_dir() else None


def _remove_index(repo: Path) -> None:
    for name in ("quality.db", "quality.db-wal", "quality.db-shm"):
        (repo / ".sentinel" / name).unlink(missing_ok=True)


def run_sentinel(repo: Path, mode: str, out: Path, repeats: int = 1) -> tuple[dict[str, Any], dict[str, Any]]:
    """Measure one Sentinel run mode.

    `cold` removes the index before *every* repeat, so its median is a real
    cold median rather than one cold run followed by warm ones.
    """

    def command() -> list[str]:
        return [
            sys.executable,
            "-m",
            "sentinel2",
            "scan" if mode in {"cold", "warm"} else "report",
            str(repo),
            "--format",
            "json",
            "--out",
            str(out),
            "--quiet",
        ]

    before_each = (lambda: _remove_index(repo)) if mode == "cold" else None
    measurement = _timed_runs(command, cwd=ROOT, repeats=repeats, before_each=before_each)
    payload: dict[str, Any] = {}
    if out.is_file():
        try:
            payload = json.loads(out.read_text(encoding="utf-8"))
        except ValueError:
            payload = {}
    summary = payload.get("summary", {})
    scan = payload.get("scan", {})
    measurement.update(
        {
            "findings": len(payload.get("findings", [])),
            "band": summary.get("band"),
            "overall_score": summary.get("overall_score"),
            "confidence": summary.get("confidence"),
            "mode": scan.get("mode"),
            "files_total": scan.get("files_total"),
            "files_analyzed": scan.get("files_analyzed"),
            "files_cached": scan.get("files_cached"),
            "index_bytes": scan.get("index_size_bytes"),
            "coverage": scan.get("coverage"),
        }
    )
    return measurement, payload


def run_competitor(name: str, repo: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    # Every one of these analyzers exits 1 to mean "I found something", so a
    # non-zero code is only a failure above 1.
    acceptable = (0, 1)
    if name == "ruff":
        command = ["ruff", "check", "--isolated", "--no-cache", "--output-format", "json", str(repo)]
        measurement = _timed_runs(lambda: list(command), cwd=repo, repeats=1, acceptable_codes=acceptable)
        rows = parse_ruff(measurement.get("stdout", ""), repo) if measurement.get("ok") else []
    elif name == "bandit":
        command = ["bandit", "-r", str(repo), "-f", "json", "-q"]
        measurement = _timed_runs(lambda: list(command), cwd=repo, repeats=1, acceptable_codes=acceptable)
        rows = parse_bandit(measurement.get("stdout", ""), repo) if measurement.get("ok") else []
    elif name == "semgrep":
        command = [
            "semgrep",
            "scan",
            str(repo),
            "--config",
            "p/default",
            "--json",
            "--quiet",
            "--metrics=off",
            "--timeout",
            "30",
            "--jobs",
            "4",
        ]
        measurement = _timed_runs(lambda: list(command), cwd=repo, repeats=1, acceptable_codes=acceptable)
        rows = parse_semgrep(measurement.get("stdout", ""), repo) if measurement.get("ok") else []
    else:  # pragma: no cover - guarded by argparse choices
        raise SystemExit(f"unknown tool: {name}")
    measurement.pop("stdout", None)
    measurement["findings"] = len(rows)
    measurement["severity"] = count_by_severity(rows)
    measurement["files_flagged"] = len({row["file"] for row in rows})
    if not measurement.get("ok"):
        measurement["error"] = measurement.get("error") or measurement.get("stderr", "")[-500:]
    return measurement, rows


def cmd_corpus(args: argparse.Namespace) -> int:
    check_freeze()
    corpus = load_corpus()
    started = datetime.now(timezone.utc)
    run_dir = _run_dir(args, started)
    results: dict[str, Any] = {
        "run": started.isoformat(timespec="seconds"),
        "freeze": json.loads(FREEZE_FILE.read_text(encoding="utf-8")),
        "environment": _environment(),
        "repos": [],
    }
    for entry in corpus["repos"]:
        name = entry["name"]
        repo = resolve_repo(entry)
        if repo is None:
            print(f"  {name:<24} MISSING ({entry.get('path')})")
            continue
        print(f"  {name:<24} scanning ...", flush=True)
        out = run_dir / f"sentinel-{name}.json"
        cold, payload = run_sentinel(repo, "cold", out, repeats=args.repeats)
        warm, _ = run_sentinel(repo, "warm", out, repeats=args.repeats)
        report_only, _ = run_sentinel(repo, "report", out, repeats=1)
        sentinel_rows = parse_sentinel(payload, repo)
        row: dict[str, Any] = {
            "name": name,
            "path": entry.get("path"),
            "url": entry.get("url"),
            "tier": entry.get("tier"),
            "sentinel": {"cold": cold, "warm": warm, "report": report_only},
            "sentinel_findings": sentinel_rows,
            "top5": [finding for finding in sentinel_rows if finding["rank"] <= 5],
            "competitors": {},
        }
        for tool in ("ruff", "bandit", "semgrep"):
            if tool in args.skip:
                continue
            measurement, rows = run_competitor(tool, repo)
            row["competitors"][tool] = {"run": measurement, "findings": rows}
            print(
                f"  {name:<24} {tool:<9} {measurement.get('wall_ms', 0):>9.0f} ms  "
                f"{measurement.get('findings', 0):>6} findings  "
                f"{'ok' if measurement.get('ok') else 'FAILED'}",
                flush=True,
            )
        print(
            f"  {name:<24} sentinel   {cold.get('wall_ms', 0):>9.0f} ms cold / "
            f"{warm.get('wall_ms', 0):>7.0f} ms warm / {report_only.get('wall_ms', 0):>6.0f} ms report  "
            f"{len(sentinel_rows)} findings",
            flush=True,
        )
        results["repos"].append(row)
    results["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    path = run_dir / "corpus.json"
    path.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nwrote {path.relative_to(ROOT).as_posix()}")
    return 0


# --------------------------------------------------------------------------
# defect run
# --------------------------------------------------------------------------


def stage_defect_fixture(target: Path) -> Path:
    """Copy the committed fixture and add the two generated defects."""

    shutil.copytree(DEFECT_DIR, target, ignore=shutil.ignore_patterns("__pycache__", ".sentinel"))
    huge = target / "app" / "huge_module.py"
    lines = ['"""D18 - oversized source module, generated by the harness."""', ""]
    for index in range(1900):
        if index % 24 == 0:
            lines.append("")
            lines.append(f"def handler_{index}(value: int) -> int:")
            lines.append("    total = value")
            lines.append("    return total")
        else:
            lines.append(f"    # checkpoint {index}")
    huge.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Straight-line on purpose: Sentinel reports a function as MNT005 only when
    # its complexity stays under the MNT006/MNT007 threshold, so a long function
    # full of branches would be recorded as a complexity defect instead of a
    # length defect. One defect at a time is what a recall fixture needs.
    long_function = target / "app" / "long_function.py"
    body = [
        '"""D19 - a single function over 100 lines, generated by the harness."""',
        "",
        "",
        "def process(batch: list[int]) -> int:",
        "    total = 0",
    ]
    for index in range(116):
        body.append(f"    total = total + {index}")
    body.append("    return total")
    long_function.write_text("\n".join(body) + "\n", encoding="utf-8")
    return target


def matches_expected(row: dict[str, Any], expected: str, tool: str) -> bool:
    """Does a reported rule correspond to the rule we registered for a defect?

    Semgrep ids repeat their own name (`...eval-detected.eval-detected`), so a
    registered expectation may be a prefix or a suffix of the reported id.
    Matching is therefore substring-based and documented in the manifest.
    """

    rule = str(row.get("rule") or "").strip()
    wanted = str(expected).strip()
    if not rule or not wanted:
        return False
    if tool == "semgrep":
        return wanted == rule or wanted in rule or rule in wanted
    return rule == wanted


def recall_matrix(
    rows_by_tool: dict[str, list[dict[str, Any]]],
    defects: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    matrix: list[dict[str, Any]] = []
    for defect in defects:
        entry: dict[str, Any] = {
            "id": defect["id"],
            "title": defect["title"],
            "file": defect["file"],
            "category": defect["category"],
            "tools": {},
        }
        for tool in TOOLS:
            expected = (defect.get("expected") or {}).get(tool)
            if not expected:
                entry["tools"][tool] = {"status": "out", "expected": None}
                continue
            hits = [
                row
                for row in rows_by_tool.get(tool, [])
                if matches_expected(row, str(expected), tool)
                and (defect["file"] in {".", ""} or row["file"] == defect["file"])
            ]
            entry["tools"][tool] = {
                "status": "hit" if hits else "miss",
                "expected": expected,
                "line": hits[0]["line"] if hits else None,
                "evidence": (hits[0].get("title") if hits else None),
            }
        matrix.append(entry)
    return matrix


def summarise_recall(matrix: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for tool in TOOLS:
        scoped = [row["tools"][tool] for row in matrix if row["tools"][tool]["status"] != "out"]
        hits = [row for row in scoped if row["status"] == "hit"]
        summary[tool] = {
            "in_scope": len(scoped),
            "hits": len(hits),
            "misses": len(scoped) - len(hits),
            "recall": round(len(hits) / len(scoped), 3) if scoped else None,
            "out_of_scope": sum(1 for row in matrix if row["tools"][tool]["status"] == "out"),
            "missed_ids": [
                row["id"] for row in matrix if row["tools"][tool]["status"] == "miss"
            ],
        }
    return summary


def cmd_defects(args: argparse.Namespace) -> int:
    check_freeze()
    spec = json.loads(DEFECT_FILE.read_text(encoding="utf-8"))
    run_dir = _run_dir(args, None)
    temp = Path(tempfile.mkdtemp(prefix="sentinel-defects-"))
    try:
        fixture = stage_defect_fixture(temp / "defect_corpus")
        rows_by_tool: dict[str, list[dict[str, Any]]] = {}
        runs: dict[str, Any] = {}
        out = run_dir / "sentinel-defects.json"
        cold, payload = run_sentinel(fixture, "cold", out, repeats=args.repeats)
        sentinel_rows = parse_sentinel(payload, fixture)
        rows_by_tool["sentinel"] = sentinel_rows
        runs["sentinel"] = cold
        print(f"  sentinel   {cold.get('wall_ms', 0):>8.0f} ms  {len(sentinel_rows)} findings", flush=True)
        for tool in ("ruff", "bandit", "semgrep"):
            if tool in args.skip:
                continue
            measurement, rows = run_competitor(tool, fixture)
            rows_by_tool[tool] = rows
            runs[tool] = measurement
            print(
                f"  {tool:<10} {measurement.get('wall_ms', 0):>8.0f} ms  {len(rows)} findings  "
                f"{'ok' if measurement.get('ok') else 'FAILED'}",
                flush=True,
            )
        matrix = recall_matrix(rows_by_tool, spec["defects"])
        payload_out = {
            "run": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "freeze": json.loads(FREEZE_FILE.read_text(encoding="utf-8")),
            "environment": _environment(),
            "defects": matrix,
            "recall": summarise_recall(matrix),
            "runs": runs,
            "findings": rows_by_tool,
        }
        path = run_dir / "defects.json"
        path.write_text(json.dumps(payload_out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        _print_recall(payload_out["recall"])
        print(f"\nwrote {path.relative_to(ROOT).as_posix()}")
    finally:
        shutil.rmtree(temp, ignore_errors=True)
    return 0


def _print_recall(recall: dict[str, Any]) -> None:
    print("\n  tool        in-scope  hits  misses  recall")
    for tool in TOOLS:
        row = recall.get(tool, {})
        scope = row.get("in_scope", 0)
        value = row.get("recall")
        print(
            f"  {tool:<11}{scope:>9}{row.get('hits', 0):>6}{row.get('misses', 0):>8}"
            f"{('n/a' if value is None else f'{value:.0%}'):>8}   "
            f"missed: {', '.join(row.get('missed_ids', [])) or '-'}"
        )


# --------------------------------------------------------------------------
# evaluation: precision@5 and coverage
# --------------------------------------------------------------------------


def cmd_evaluate(args: argparse.Namespace) -> int:
    check_freeze()
    if not LABELS_FILE.is_file():
        raise SystemExit(f"missing labels: {LABELS_FILE.relative_to(ROOT).as_posix()}")
    labels = json.loads(LABELS_FILE.read_text(encoding="utf-8"))
    run_dir = Path(args.run) if args.run else _latest_run()
    if not run_dir.is_absolute():
        run_dir = ROOT / run_dir
    corpus_result = json.loads((run_dir / "corpus.json").read_text(encoding="utf-8"))

    rows: list[dict[str, Any]] = []
    coverage: dict[str, Any] = {}
    for repo in corpus_result["repos"]:
        name = repo["name"]
        labels_for_repo = (labels.get("repos") or {}).get(name, {}).get("findings", [])
        verdicts = [entry.get("verdict") for entry in labels_for_repo]
        genuine = sum(1 for verdict in verdicts if verdict == "genuine")
        actionable = sum(1 for entry in labels_for_repo if entry.get("actionable"))
        rows.append(
            {
                "repo": name,
                "labelled": len(labels_for_repo),
                "genuine": genuine,
                "debatable": sum(1 for verdict in verdicts if verdict == "debatable"),
                "not_genuine": sum(1 for verdict in verdicts if verdict == "not_genuine"),
                "actionable": actionable,
                "precision_at_5": round(genuine / 5, 2) if labels_for_repo else None,
            }
        )
        coverage[name] = _coverage(repo)

    labelled = [row for row in rows if row["precision_at_5"] is not None]
    payload = {
        "run": run_dir.name,
        "precision": rows,
        "summary": {
            "repos_labelled": len(labelled),
            "precision_at_5_mean": round(statistics.fmean(row["precision_at_5"] for row in labelled), 3)
            if labelled
            else None,
            "actionable_mean": round(statistics.fmean(row["actionable"] for row in labelled), 3)
            if labelled
            else None,
            "coverage": coverage,
        },
    }
    path = run_dir / "evaluation.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"\n  {'repo':<24}{'labelled':>9}{'genuine':>9}{'debatable':>11}{'false':>7}{'P@5':>7}")
    for row in rows:
        precision = "n/a" if row["precision_at_5"] is None else f"{row['precision_at_5']:.2f}"
        print(
            f"  {row['repo']:<24}{row['labelled']:>9}{row['genuine']:>9}"
            f"{row['debatable']:>11}{row['not_genuine']:>7}{precision:>7}"
        )
    if labelled:
        print(f"\n  mean Precision@5 : {payload['summary']['precision_at_5_mean']:.2f}")
        print(f"  mean actionable  : {payload['summary']['actionable_mean']:.2f}")
    print(f"\nwrote {path.relative_to(ROOT).as_posix()}")
    return 0


def _coverage(repo: dict[str, Any]) -> dict[str, Any]:
    """How much of the competitors' high-severity work the top five names."""

    top5 = {finding["file"] for finding in repo.get("top5", [])}
    competitor_high: dict[str, list[str]] = {}
    for tool, block in repo.get("competitors", {}).items():
        rows = block.get("findings", [])
        competitor_high[tool] = sorted(files_at_least(rows, "high"))
    covered: dict[str, Any] = {}
    for tool, files in competitor_high.items():
        missed = sorted(set(files) - top5)
        covered[tool] = {
            "high_severity_files": len(files),
            "in_sentinel_top5": len(files) - len(missed),
            "missed": missed[:20],
        }
    return covered


# --------------------------------------------------------------------------
# shared plumbing
# --------------------------------------------------------------------------


def _environment() -> dict[str, Any]:
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
        "tools": _tool_versions(),
        "rss_method": "sum of process-tree working sets sampled at 50 ms"
        if sys.platform == "win32"
        else "ru_maxrss of reaped children (per-process ceiling)",
    }


def _run_dir(args: argparse.Namespace, stamp: datetime | None) -> Path:
    if getattr(args, "run", None):
        path = Path(args.run)
    else:
        if stamp is None:
            stamp = datetime.now(timezone.utc)
        path = RESULTS_DIR / stamp.strftime("%Y-%m-%dT%H-%M-%SZ")
    # Resolve so later `path.relative_to(ROOT)` calls work for a relative
    # --run value passed on the command line.
    if not path.is_absolute():
        path = ROOT / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def _latest_run() -> Path:
    if not RESULTS_DIR.is_dir():
        raise SystemExit("no benchmark results yet; run `corpus` first")
    candidates = sorted(path for path in RESULTS_DIR.iterdir() if (path / "corpus.json").is_file())
    if not candidates:
        raise SystemExit("no corpus.json in benchmarks/results; run `corpus` first")
    return candidates[-1]


def cmd_status(_args: argparse.Namespace) -> int:
    if FREEZE_FILE.is_file():
        frozen = json.loads(FREEZE_FILE.read_text(encoding="utf-8"))
        try:
            check_freeze()
            state = "INTACT"
        except SystemExit as exc:
            state = f"CHANGED ({exc.args[0]})"
        print(f"freeze     : {state}")
        print(f"  frozen at: {frozen.get('frozen_at')}")
        print(f"  commit   : {frozen.get('git_commit')}")
        print(f"  analysis : {frozen.get('analysis_version')}")
    else:
        print("freeze     : none recorded")
    runs = sorted(path.name for path in RESULTS_DIR.iterdir() if path.is_dir()) if RESULTS_DIR.is_dir() else []
    print(f"runs       : {len(runs)}" + (f" (latest {runs[-1]})" if runs else ""))
    corpus = load_corpus()
    missing = [entry["name"] for entry in corpus["repos"] if resolve_repo(entry) is None]
    print(f"corpus     : {len(corpus['repos'])} entries, missing on disk: {', '.join(missing) or 'none'}")
    if LABELS_FILE.is_file():
        labels = json.loads(LABELS_FILE.read_text(encoding="utf-8"))
        print(f"labels     : {', '.join(sorted(labels.get('repos', {}))) or 'none'}")
    else:
        print("labels     : not written")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("freeze", help="Record the frozen analysis package")
    corpus_parser = subparsers.add_parser("corpus", help="Run every tool over every corpus repository")
    corpus_parser.add_argument("--repeats", type=int, default=3, help="Sentinel cold/warm repeats (median)")
    corpus_parser.add_argument("--skip", nargs="*", default=[], choices=TOOLS[1:], help="Skip competitor tools")
    corpus_parser.add_argument("--run", help="Result directory (default: benchmarks/results/<timestamp>)")

    defects_parser = subparsers.add_parser("defects", help="Run every tool over the seeded-defect fixture")
    defects_parser.add_argument("--repeats", type=int, default=1, help="Sentinel repeats (median)")
    defects_parser.add_argument("--skip", nargs="*", default=[], choices=TOOLS[1:], help="Skip competitor tools")
    defects_parser.add_argument("--run", help="Result directory (default: benchmarks/results/<timestamp>)")

    evaluate_parser = subparsers.add_parser("evaluate", help="Score Precision@5 and top-5 coverage")
    evaluate_parser.add_argument("--run", help="Result directory (default: latest)")
    subparsers.add_parser("status", help="Show freeze, run and corpus state")

    args = parser.parse_args(argv)
    command = args.command or "status"
    if command == "freeze":
        payload = write_freeze()
        print(f"froze analysis package {payload['package_sha256'][:12]} ({payload['package_files']} files)")
        print(f"  analysis_version: {payload['analysis_version']}")
        print("  scoring is now frozen for this benchmark round")
        return 0
    if command == "corpus":
        return cmd_corpus(args)
    if command == "defects":
        return cmd_defects(args)
    if command == "evaluate":
        return cmd_evaluate(args)
    return cmd_status(args)


if __name__ == "__main__":
    raise SystemExit(main())
