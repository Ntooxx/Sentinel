"""Polling watch mode with debounce and atomic report writes."""

from __future__ import annotations

import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .discover import discover, snapshot_signature
from .renderers import render
from .scanner import ScanError, scan_project
from .utils import atomic_write, is_within, relpath, report_fingerprint


def _watch_ignores(root: Path, out: Path, ignores: Sequence[str]) -> list[str]:
    combined = list(ignores)
    try:
        resolved_out = out.expanduser().resolve()
        if is_within(root, resolved_out):
            combined.append(relpath(resolved_out, root))
    except OSError:
        pass
    return list(dict.fromkeys(combined))


def _write_report(out: Path, text: str) -> None:
    atomic_write(out, text)


def watch_project(
    root: Path,
    *,
    rules: Mapping[str, Any],
    fmt: str = "html",
    out: Path = Path(".sentinel/quality.html"),
    interval: float = 1.0,
    debounce_ms: int = 300,
    include_docs: bool = False,
    full: bool = False,
    ignores: Sequence[str] = (),
    quiet: bool = False,
    color: bool = False,
    max_seconds: float | None = None,
    on_update: Callable[[dict[str, Any]], None] | None = None,
) -> int:
    """Scan once, then poll for changes and keep *out* current.

    Returns 0 on a clean stop. Errors during a refresh keep the last good report.
    """

    root = root.resolve()
    out = out.expanduser()
    if not out.is_absolute():
        out = (root / out).resolve()
    watch_ignores = _watch_ignores(root, out, ignores)
    interval = max(0.05, float(interval))
    debounce = max(0.0, float(debounce_ms) / 1000.0)
    started = time.monotonic()
    last_report: dict[str, Any] | None = None
    last_fingerprint = ""
    refresh_seconds = 2 if fmt == "html" else None

    if not quiet:
        print(f"Watching {root} -> {out} (interval {interval:.2f}s, Ctrl+C to stop)", file=sys.stderr)

    try:
        report = scan_project(
            root,
            rules=rules,
            ignores=watch_ignores,
            full=full,
            include_docs=include_docs,
            quiet=quiet,
        )
        last_report = report
        last_fingerprint = report_fingerprint(report)
        _write_report(out, render(report, fmt, color=color, refresh_seconds=refresh_seconds))
        if on_update:
            on_update(report)
        if not quiet:
            print(f"initial report written: {out}", file=sys.stderr)
    except (ScanError, OSError) as error:
        if not quiet:
            print(f"watch error: {error}", file=sys.stderr)
        return 1

    previous_snapshot = None
    try:
        previous_snapshot = snapshot_signature(discover(root, rules, watch_ignores, quick=True))
    except OSError:
        previous_snapshot = None

    while True:
        if max_seconds is not None and time.monotonic() - started >= max_seconds:
            return 0
        time.sleep(interval)
        try:
            current = discover(root, rules, watch_ignores, quick=True)
            signature = snapshot_signature(current)
            if previous_snapshot is not None and signature == previous_snapshot:
                continue
            previous_snapshot = signature
            if debounce:
                time.sleep(debounce)
                current = discover(root, rules, watch_ignores, quick=True)
                previous_snapshot = snapshot_signature(current)
            report = scan_project(
                root,
                rules=rules,
                ignores=watch_ignores,
                full=full,
                include_docs=include_docs,
                quiet=True,
            )
            fingerprint = report_fingerprint(report)
            if fingerprint == last_fingerprint and last_report is not None:
                continue
            last_report = report
            last_fingerprint = fingerprint
            _write_report(out, render(report, fmt, color=color, refresh_seconds=refresh_seconds))
            if on_update:
                on_update(report)
            if not quiet:
                print(f"report updated: {out}", file=sys.stderr)
        except KeyboardInterrupt:
            raise
        except (ScanError, OSError, ValueError) as error:
            if not quiet:
                print(f"watch refresh failed; keeping the last good report: {error}", file=sys.stderr)
            continue
