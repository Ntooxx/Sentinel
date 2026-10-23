#!/usr/bin/env python3
"""Compatibility launcher for the single Sentinel 2.0 CLI."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from sentinel2.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
