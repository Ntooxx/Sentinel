"""Insert the in-browser runner block into the Sentinel landing page.

The landing page started life as the Netlify page (a single self-contained
index.html). This tool takes such a page and injects the runner block from
``web/_runner.block.html`` before the Performance section, plus a nav link, so
the page can be regenerated if the source page changes.

Usage:
    python tools/build_web_page.py --source <index.html> --out web/index.html
"""

from __future__ import annotations

import argparse
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BLOCK = REPO_ROOT / "web" / "_runner.block.html"
ANCHOR = "    <!-- ================= PERFORMANCE ================= -->"
NAV_MARKER = '<a class="nav-link" href="#report">Report</a>'
NAV_LINK = '<a class="nav-link" href="#report">Report</a>\n        <a class="nav-link" href="#run">Run it</a>'


def build(source: Path, block: Path, out: Path) -> str:
    page = source.read_text(encoding="utf-8")
    injected = block.read_text(encoding="utf-8").rstrip() + "\n\n"
    if ANCHOR not in page:
        raise SystemExit(f"anchor not found in {source}: expected a Performance section marker")
    if "id=\"run\"" in page:
        raise SystemExit(f"{source} already contains a runner block")
    page = page.replace(ANCHOR, injected + ANCHOR, 1)
    if NAV_MARKER in page:
        page = page.replace(NAV_MARKER, NAV_LINK, 1)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    return page


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inject the in-browser runner into the landing page.")
    parser.add_argument("--source", required=True, help="Base landing page (the Netlify/desktop index.html).")
    parser.add_argument("--block", default=str(BLOCK), help="Runner block to inject.")
    parser.add_argument("--out", required=True, help="Where to write the resulting page.")
    args = parser.parse_args(argv)
    page = build(Path(args.source), Path(args.block), Path(args.out))
    print(f"{args.out}: {len(page) / 1024:.1f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
