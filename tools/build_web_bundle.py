"""Bundle the sentinel2 package as JSON for the in-browser (Pyodide) runner.

GitHub Pages cannot run a Python server, so the web runner downloads Python as
WebAssembly (Pyodide) and executes the real package inside the page. Pyodide
cannot install our wheel, so the package source ships as a single JSON file of
``{relative_path: text}``.

Usage:
    python tools/build_web_bundle.py --out _site/pkg/bundle.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SUFFIXES = {".py", ".json"}


def build(src: Path, out: Path) -> dict[str, object]:
    files: dict[str, str] = {}
    for path in sorted(src.rglob("*")):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts:
            continue
        if path.suffix not in SUFFIXES:
            continue
        relative = path.relative_to(src.parent).as_posix()
        files[relative] = path.read_text(encoding="utf-8")
    payload: dict[str, object] = {
        "schema": 1,
        "python": "3.12",
        "package": src.name,
        "files": files,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return payload


def inline_html(source: Path, payload: dict[str, object], out: Path) -> str:
    """Write *source* with the bundle inlined, so it works from file:// with no server."""
    marker = "<!-- SENTINEL_BUNDLE_MARKER -->"
    page = source.read_text(encoding="utf-8")
    if marker not in page:
        raise SystemExit(f"{source} has no {marker}")
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    script = '<script id="sentinel-bundle">window.__SENTINEL_BUNDLE__=' + blob + ";</script>"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page.replace(marker, script, 1), encoding="utf-8")
    return blob


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bundle sentinel2 for the browser runner.")
    parser.add_argument("--src", default=str(REPO_ROOT / "src" / "sentinel2"))
    parser.add_argument("--out", required=True)
    parser.add_argument("--inline-html", help="Also inline the bundle into this landing page.")
    parser.add_argument("--inline-out", help="Where to write the inlined landing page.")
    args = parser.parse_args(argv)
    payload = build(Path(args.src), Path(args.out))
    size = Path(args.out).stat().st_size
    print(f"{args.out}: {len(payload['files'])} files, {size / 1024:.1f} KB")
    if args.inline_html:
        if not args.inline_out:
            parser.error("--inline-out is required with --inline-html")
        inline_html(Path(args.inline_html), payload, Path(args.inline_out))
        print(f"{args.inline_out}: {Path(args.inline_out).stat().st_size / 1024:.1f} KB (bundle inlined)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
