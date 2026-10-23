from __future__ import annotations

import time

import pytest
from s2_helpers import make_clean

from sentinel2.config import load_rules
from sentinel2.scanner import scan_project


@pytest.mark.perf
def test_small_fixture_cold_and_warm_budget(tmp_path):
    root = make_clean(tmp_path / "clean")
    rules = load_rules()
    started = time.perf_counter()
    scan_project(root, rules=rules, quiet=True)
    cold = time.perf_counter() - started
    started = time.perf_counter()
    warm_report = scan_project(root, rules=rules, quiet=True)
    warm = time.perf_counter() - started
    assert cold < 1.0
    assert warm < 0.5
    assert warm_report["scan"]["files_analyzed"] == 0
    assert warm_report["scan"]["files_cached"] == warm_report["scan"]["files_total"]
