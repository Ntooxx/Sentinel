#!/usr/bin/env python3
"""Measure Sentinel 2.0 cold, warm and report-only latency.

This is a developer tool, not a public command. It runs the same CLI entry
point users run, then reports both wall time and the scan duration stored in
the JSON report.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _remove_index(repo: Path) -> None:
    for name in ("quality.db", "quality.db-wal", "quality.db-shm"):
        path = repo / ".sentinel" / name
        try:
            path.unlink()
        except FileNotFoundError:
            continue


def _run(args: list[str], out: Path) -> tuple[float, dict[str, Any] | None]:
    command = [sys.executable, "-m", "sentinel2", *args, "--format", "json", "--out", str(out), "--quiet"]
    started = time.perf_counter()
    result = subprocess.run(
        command,
        cwd=str(ROOT),
        env=_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
        check=False,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    if result.returncode != 0:
        raise SystemExit(f"command failed ({result.returncode}): {' '.join(command)}\n{result.stderr}")
    try:
        payload = json.loads(out.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        payload = None
    return elapsed_ms, payload


def _scan_measurement(repo: Path, out: Path) -> dict[str, Any]:
    wall_ms, payload = _run(["scan", str(repo)], out)
    scan = (payload or {}).get("scan", {})
    summary = (payload or {}).get("summary", {})
    return {
        "wall_ms": round(wall_ms, 1),
        "scan_ms": scan.get("duration_ms"),
        "mode": scan.get("mode"),
        "files_total": scan.get("files_total"),
        "files_analyzed": scan.get("files_analyzed"),
        "files_cached": scan.get("files_cached"),
        "files_skipped": scan.get("files_skipped"),
        "index_bytes": scan.get("index_size_bytes"),
        "band": summary.get("band"),
    }


def _report_measurement(repo: Path, out: Path) -> dict[str, Any]:
    wall_ms, payload = _run(["report", str(repo)], out)
    summary = (payload or {}).get("summary", {})
    return {"wall_ms": round(wall_ms, 1), "band": summary.get("band")}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="Repository to benchmark")
    parser.add_argument("--mode", choices=("cold", "warm", "report", "all"), default="all")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    parser.add_argument("--check", action="store_true", help="Exit 1 when the documented budget is exceeded")
    args = parser.parse_args(argv)

    repo = Path(args.repo).expanduser().resolve()
    if not repo.is_dir():
        raise SystemExit(f"not a directory: {repo}")
    results: dict[str, Any] = {"repo": str(repo), "mode": args.mode}
    temp_dir = Path(tempfile.mkdtemp(prefix="sentinel-benchmark-"))
    out = temp_dir / "report.json"
    try:
        if args.mode in {"cold", "all"}:
            _remove_index(repo)
            results["cold"] = _scan_measurement(repo, out)
        if args.mode in {"warm", "all"}:
            results["warm"] = _scan_measurement(repo, out)
        if args.mode in {"report", "all"}:
            results["report"] = _report_measurement(repo, out)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print(f"Sentinel benchmark - {repo}")
        for name in ("cold", "warm", "report"):
            if name in results:
                data = results[name]
                print(
                    f"  {name:<7} wall {data['wall_ms']:>8.1f} ms"
                    + (f" | scan {data['scan_ms']} ms" if "scan_ms" in data else "")
                    + (f" | {data.get('files_analyzed')} analyzed / {data.get('files_cached')} cached" if "files_analyzed" in data else "")
                    + (f" | band {data.get('band')}" if data.get("band") else "")
                )

    if args.check:
        failures: list[str] = []
        for name, budget in (("cold", 2000), ("warm", 400), ("report", 100)):
            if name in results and results[name]["wall_ms"] > budget * 1.2:
                failures.append(f"{name} {results[name]['wall_ms']:.0f}ms > {budget}ms budget (+20%)")
        if failures:
            print("\n".join(failures), file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
