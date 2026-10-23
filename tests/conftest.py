from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
TESTS = Path(__file__).resolve().parent
for directory in (SRC, TESTS):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

import pytest  # noqa: E402

from sentinel2.config import load_rules  # noqa: E402


@pytest.fixture
def rules():
    return load_rules()
