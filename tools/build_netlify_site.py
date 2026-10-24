"""Assemble the Netlify publish folder for the landing page.

``web/index.html`` is the single source for the landing page and carries the
in-browser runner, but no inlined package: the runner downloads
``pkg/bundle.json`` next to the page. A static deploy therefore needs two files
in one directory, which is exactly what this tool produces. The workspace-root
``index.netlify.html`` is a mirror of ``web/index.html`` and is refreshed with
``--mirror-netlify`` so the two never drift.

Usage:
    python tools/build_netlify_site.py                     # -> <repo>/../public
    python tools/build_netlify_site.py --mirror-netlify    # also refresh the mirror
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PAGE = REPO_ROOT / "web" / "index.html"
MIRROR = REPO_ROOT.parent / "index.netlify.html"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_web_bundle import build  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the Netlify publish folder.")
    parser.add_argument("--src", default=str(REPO_ROOT / "src" / "sentinel2"))
    parser.add_argument("--page", default=str(DEFAULT_PAGE))
    parser.add_argument("--out-dir", default=str(REPO_ROOT.parent / "public"))
    parser.add_argument(
        "--mirror-netlify",
        action="store_true",
        help=f"also copy the page to {MIRROR}",
    )
    args = parser.parse_args(argv)

    page = Path(args.page)
    if not page.is_file():
        parser.error(f"landing page not found: {page}")
    if "run-shell" not in page.read_text(encoding="utf-8"):
        parser.error(f"{page} has no in-browser runner (id=\"run\"); refusing to publish it")

    out = Path(args.out_dir)
    (out / "pkg").mkdir(parents=True, exist_ok=True)

    payload = build(Path(args.src), out / "pkg" / "bundle.json")
    shutil.copyfile(page, out / "index.html")
    if args.mirror_netlify:
        shutil.copyfile(page, MIRROR)

    bundle_kb = (out / "pkg" / "bundle.json").stat().st_size / 1024
    print(f"{out / 'index.html'}: landing page")
    print(f"{out / 'pkg' / 'bundle.json'}: {len(payload['files'])} files, {bundle_kb:.1f} KB")
    if args.mirror_netlify:
        print(f"{MIRROR}: mirror")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
