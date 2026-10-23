from __future__ import annotations

import time

from s2_helpers import make_clean

from sentinel2 import index
from sentinel2.cli import load_latest_report
from sentinel2.config import load_rules
from sentinel2.scanner import scan_project


def test_warm_scan_analyzes_zero_unchanged_files(tmp_path):
    root = make_clean(tmp_path / "clean")
    rules = load_rules()
    scan_project(root, rules=rules, quiet=True)
    second = scan_project(root, rules=rules, quiet=True)
    assert second["scan"]["files_analyzed"] == 0
    assert second["scan"]["files_cached"] == second["scan"]["files_total"]


def test_changed_file_is_reanalyzed_and_reported(tmp_path):
    root = make_clean(tmp_path / "clean")
    rules = load_rules()
    scan_project(root, rules=rules, quiet=True)
    (root / "src" / "clean_app" / "core.py").write_text(
        "def add(left: int, right: int) -> int:\n"
        "    if left < 0:\n"
        "        return right\n"
        "    return left + right\n",
        encoding="utf-8",
    )
    report = scan_project(root, rules=rules, quiet=True)
    assert report["scan"]["files_analyzed"] >= 1
    assert report["changes"]["modified"] >= 1


def test_deleted_and_renamed_files_are_removed_from_index(tmp_path):
    root = make_clean(tmp_path / "clean")
    rules = load_rules()
    scan_project(root, rules=rules, quiet=True)
    (root / "src" / "clean_app" / "core.py").rename(root / "src" / "clean_app" / "maths.py")
    report = scan_project(root, rules=rules, quiet=True)
    assert report["changes"]["deleted"] >= 1
    conn = index.connect(index.db_path(root), readonly=True)
    try:
        paths = {row["path"] for row in conn.execute("SELECT path FROM files")}
    finally:
        conn.close()
    assert "src/clean_app/core.py" not in paths
    assert "src/clean_app/maths.py" in paths


def test_include_docs_reanalyses_canonical_docs_when_mode_changes(tmp_path):
    root = make_clean(tmp_path / "clean")
    rules = load_rules()
    (root / "docs").mkdir()
    (root / "docs" / "guide.md").write_text("[missing](../docs/nope.md)\n", encoding="utf-8")
    without_docs = scan_project(root, rules=rules, quiet=True, include_docs=False)
    assert not any(finding["rule_id"] == "DOC004" for finding in without_docs["findings"])
    with_docs = scan_project(root, rules=rules, quiet=True, include_docs=True)
    assert any(finding["rule_id"] == "DOC004" for finding in with_docs["findings"])
    back_to_metadata = scan_project(root, rules=rules, quiet=True, include_docs=False)
    assert not any(finding["rule_id"] == "DOC004" for finding in back_to_metadata["findings"])


def test_report_does_not_rescan_or_touch_database(tmp_path):
    root = make_clean(tmp_path / "clean")
    scan_project(root, rules=load_rules(), quiet=True)
    database = index.db_path(root)
    before = database.stat().st_mtime_ns
    time.sleep(0.02)
    first = load_latest_report(root)
    second = load_latest_report(root)
    after = database.stat().st_mtime_ns
    assert before == after
    assert first["scan"]["files_analyzed"] == second["scan"]["files_analyzed"]
    assert first["summary"] == second["summary"]
