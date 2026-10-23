"""Command-line interface for Sentinel 2.0."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from . import __version__
from .errors import ReportError, ScanError

FORMATS = ("text", "md", "html", "json")


def _grade_arg(value: str) -> str:
    from .utils import parse_grade_threshold

    if parse_grade_threshold(value) is None:
        raise argparse.ArgumentTypeError(f"invalid quality band: {value}")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="project-sentinel",
        description="Sentinel 2.0 - local real-time repository quality report.",
    )
    parser.add_argument("--version", action="version", version=f"project-sentinel {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="{scan,report,watch}")

    scan_parser = subparsers.add_parser("scan", help="Build/update the index and print a quality report.")
    scan_parser.add_argument("path", nargs="?", default=".", help="Repository path (default: .)")
    scan_parser.add_argument("--format", choices=FORMATS, default="text", help="Output format (default: text)")
    scan_parser.add_argument("--out", help="Write output to a file")
    scan_parser.add_argument("--full", action="store_true", help="Re-analyse all source files fully; no fast sampling.")
    scan_parser.add_argument("--include-docs", action="store_true", help="Read documentation content, not only metadata.")
    scan_parser.add_argument("--all-findings", action="store_true", help="Print all findings, not only the top 5.")
    scan_parser.add_argument(
        "--fail-under",
        metavar="BAND",
        type=_grade_arg,
        help="Exit 3 if the quality band is below maintained/adequate/weak (a legacy grade letter also works).",
    )
    scan_parser.add_argument("--ignore", action="append", default=[], metavar="PATH", help="Exclude a project-relative path (repeatable).")
    scan_parser.add_argument("--no-color", action="store_true", help="Disable ANSI color.")
    scan_parser.add_argument("--quiet", action="store_true", help="Suppress logs and progress.")
    scan_parser.set_defaults(handler=_handle_scan)

    report_parser = subparsers.add_parser("report", help="Render the latest report from the index without scanning.")
    report_parser.add_argument("path", nargs="?", default=".", help="Repository path (default: .)")
    report_parser.add_argument("--format", choices=FORMATS, default="text", help="Output format (default: text)")
    report_parser.add_argument("--out", help="Write output to a file")
    report_parser.add_argument("--open", action="store_true", help="Open an HTML report in the default browser.")
    report_parser.add_argument("--refresh", action="store_true", help="Run a scan first, then render.")
    report_parser.add_argument("--all-findings", action="store_true", help="Print all findings, not only the top 5.")
    report_parser.add_argument("--ignore", action="append", default=[], metavar="PATH", help="Exclude a path when used with --refresh.")
    report_parser.add_argument("--no-color", action="store_true", help="Disable ANSI color.")
    report_parser.add_argument("--quiet", action="store_true", help="Suppress progress output.")
    report_parser.set_defaults(handler=_handle_report)

    watch_parser = subparsers.add_parser("watch", help="Keep the index and report live as files change.")
    watch_parser.add_argument("path", nargs="?", default=".", help="Repository path (default: .)")
    watch_parser.add_argument("--format", choices=FORMATS, default="html", help="Output format (default: html)")
    watch_parser.add_argument("--out", help="Output path (default: .sentinel/quality.html)")
    watch_parser.add_argument("--interval", type=float, default=1.0, metavar="SECONDS", help="Polling interval (default: 1.0)")
    watch_parser.add_argument("--debounce", type=int, default=300, metavar="MILLISECONDS", help="Debounce delay (default: 300)")
    watch_parser.add_argument("--include-docs", action="store_true", help="Read documentation content, not only metadata.")
    watch_parser.add_argument("--full", action="store_true", help="Re-analyse all source files fully on each change.")
    watch_parser.add_argument("--ignore", action="append", default=[], metavar="PATH", help="Exclude a path (repeatable).")
    watch_parser.add_argument("--no-color", action="store_true", help="Disable ANSI color.")
    watch_parser.add_argument("--quiet", action="store_true", help="Suppress logs and progress.")
    watch_parser.set_defaults(handler=_handle_watch)
    return parser


def _default_out(fmt: str) -> str:
    extension = {"text": "txt", "md": "md", "html": "html", "json": "json"}[fmt]
    return f".sentinel/quality.{extension}"


def _resolve_root(path: str) -> Path:
    root = Path(path).expanduser().resolve()
    if not root.exists():
        raise ReportError(f"path does not exist: {root}")
    if not root.is_dir():
        raise ReportError(f"path is not a directory: {root}")
    return root


def load_latest_report(root: Path) -> dict[str, Any]:
    from . import SCHEMA_VERSION, index

    database = index.db_path(root)
    if not database.is_file():
        raise ReportError(f"no index found at {database.relative_to(root).as_posix()}; run `project-sentinel scan .` first")
    try:
        conn = index.connect(database, readonly=True)
    except Exception as error:  # sqlite3 errors vary by platform
        raise ReportError(f"cannot open index {database}: {error}") from error
    try:
        try:
            row = index.latest_scan(conn)
        except Exception as error:  # sqlite3 errors vary by platform/build
            raise ReportError(f"the index is unreadable: {error}") from error
        if row is None:
            raise ReportError("the index is empty; run `project-sentinel scan .` first")
        try:
            report = json.loads(row["report_json"])
        except (TypeError, ValueError) as error:
            raise ReportError(f"the latest scan report is unreadable: {error}") from error
        if not isinstance(report, dict):
            raise ReportError("the latest scan report has an unexpected shape")
        if report.get("schema_version") != SCHEMA_VERSION:
            raise ReportError(
                f"the latest report uses schema {report.get('schema_version')!r}; "
                f"this build writes schema {SCHEMA_VERSION}. Run `project-sentinel scan .` to refresh it."
            )
        return report
    finally:
        conn.close()


def _write_or_print(text: str, out: str | None) -> Path | None:
    if not out:
        sys.stdout.write(text)
        sys.stdout.flush()
        return None
    from .utils import atomic_write

    path = Path(out).expanduser()
    atomic_write(path, text)
    return path


def _color_enabled(args: argparse.Namespace) -> bool:
    return bool(not getattr(args, "no_color", False) and not getattr(args, "out", None) and sys.stdout.isatty())


def _handle_scan(args: argparse.Namespace) -> int:
    from .config import load_rules
    from .renderers import render
    from .scanner import scan_project
    from .utils import parse_grade_threshold

    root = _resolve_root(args.path)
    rules = load_rules(root)
    if args.fail_under is not None and parse_grade_threshold(args.fail_under) is None:
        raise ReportError(f"invalid quality band: {args.fail_under}")
    report = scan_project(
        root,
        rules=rules,
        ignores=args.ignore,
        full=args.full,
        include_docs=args.include_docs,
        quiet=args.quiet,
    )
    output = render(
        report,
        args.format,
        color=_color_enabled(args),
        all_findings=args.all_findings,
    )
    _write_or_print(output, args.out)
    if args.fail_under is not None:
        threshold = parse_grade_threshold(args.fail_under)
        overall = report.get("summary", {}).get("overall_score")
        if threshold is None:
            raise ReportError(f"invalid quality band: {args.fail_under}")
        if not isinstance(overall, (int, float)) or int(overall) < int(threshold):
            print(
                f"quality gate failed: band {report.get('summary', {}).get('band', 'n/a')} "
                f"is below {args.fail_under}",
                file=sys.stderr,
            )
            return 3
    return 0


def _handle_report(args: argparse.Namespace) -> int:
    from .renderers import render

    root = _resolve_root(args.path)
    if args.open:
        args.format = "html"
        if not args.out:
            args.out = _default_out("html")
    if args.refresh:
        from .config import load_rules
        from .scanner import scan_project

        report = scan_project(
            root,
            rules=load_rules(root),
            ignores=args.ignore,
            include_docs=False,
            quiet=args.quiet,
        )
    else:
        report = load_latest_report(root)
    output = render(
        report,
        args.format,
        color=_color_enabled(args),
        all_findings=args.all_findings,
    )
    path = _write_or_print(output, args.out)
    if args.open:
        import webbrowser

        target = (path or Path(_default_out("html"))).expanduser().resolve()
        webbrowser.open(target.as_uri())
    return 0


def _handle_watch(args: argparse.Namespace) -> int:
    from .config import load_rules
    from .watch import watch_project

    root = _resolve_root(args.path)
    rules = load_rules(root)
    out = args.out or _default_out(args.format)
    return watch_project(
        root,
        rules=rules,
        fmt=args.format,
        out=Path(out),
        interval=float(args.interval),
        debounce_ms=int(args.debounce),
        include_docs=args.include_docs,
        full=args.full,
        ignores=args.ignore,
        quiet=args.quiet,
        color=_color_enabled(args),
    )


def main(argv: Sequence[str] | None = None) -> int:
    from .utils import configure_console

    configure_console()
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        return int(args.handler(args))
    except (ScanError, ReportError, OSError, ValueError, KeyError, sqlite3.Error) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("stopped", file=sys.stderr)
        return 0
