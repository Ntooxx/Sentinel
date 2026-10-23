from __future__ import annotations

import json
from pathlib import Path

from s2_helpers import make_clean

from sentinel2.config import load_rules
from sentinel2.scanner import scan_project

GOLDEN_PATH = Path(__file__).resolve().parents[1] / "tests" / "golden" / "clean_python_app.json"
GOLDEN = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


def test_clean_fixture_matches_golden_snapshot(tmp_path):
    root = make_clean(tmp_path / "clean")
    report = scan_project(root, rules=load_rules(), quiet=True)
    actual = {key: report[key] for key in GOLDEN}
    assert actual == GOLDEN
