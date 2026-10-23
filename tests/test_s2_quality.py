from __future__ import annotations

from s2_helpers import make_clean, make_docs_only, make_messy

from sentinel2.config import load_rules
from sentinel2.scanner import scan_project
from sentinel2.utils import band_for, grade_for


def test_grade_bands():
    assert grade_for(100) == "A+"
    assert grade_for(97) == "A+"
    assert grade_for(93) == "A"
    assert grade_for(90) == "A-"
    assert grade_for(87) == "B+"
    assert grade_for(83) == "B"
    assert grade_for(80) == "B-"
    assert grade_for(73) == "C"
    assert grade_for(60) == "D"
    assert grade_for(59) == "F"


def test_quality_bands_are_coarse():
    assert band_for(100) == "maintained"
    assert band_for(80) == "maintained"
    assert band_for(79) == "adequate"
    assert band_for(70) == "adequate"
    assert band_for(69) == "weak"
    assert band_for(60) == "weak"
    assert band_for(59) == "unmaintained"
    assert band_for(0) == "unmaintained"
    # A+ and B- are the same product claim; the fine grade is no longer published.
    assert band_for(97) == band_for(80)


def test_clean_project_is_healthy(tmp_path):
    root = make_clean(tmp_path / "clean")
    report = scan_project(root, rules=load_rules(), quiet=True)
    assert report["summary"]["band"] == "maintained"
    assert report["summary"]["confidence"] == "high"
    assert report["summary"]["provisional"] is True
    assert report["metrics"]["source_files"] == 3
    assert report["metrics"]["test_files"] == 2


def test_messy_project_scores_lower_than_clean(tmp_path):
    clean = scan_project(make_clean(tmp_path / "clean"), rules=load_rules(), quiet=True)
    messy = scan_project(make_messy(tmp_path / "messy"), rules=load_rules(), quiet=True)
    assert messy["summary"]["overall_score"] < clean["summary"]["overall_score"]


def test_docs_only_repository_has_no_source_grade(tmp_path):
    root = make_docs_only(tmp_path / "docs")
    report = scan_project(root, rules=load_rules(), quiet=True)
    dimensions = {dimension["id"]: dimension for dimension in report["dimensions"]}
    for dimension_id in ("architecture", "maintainability", "tests"):
        assert dimensions[dimension_id]["assessed"] is False
        assert dimensions[dimension_id]["grade"] == "n/a"
    assert dimensions["docs"]["assessed"] is True
    assert report["summary"]["band"] != "n/a"


def test_parse_error_lowers_confidence(tmp_path):
    root = make_messy(tmp_path / "broken")
    report = scan_project(root, rules=load_rules(), quiet=True)
    assert report["metrics"]["parse_errors"] >= 1
    # One parse error in four source files keeps source coverage high, but the
    # parse-success ratio drops below the high-confidence threshold.
    assert report["summary"]["confidence"] in {"medium", "low"}


def test_empty_repository_has_no_invented_grade(tmp_path):
    root = tmp_path / "empty"
    root.mkdir()
    report = scan_project(root, rules=load_rules(), quiet=True)
    assert report["summary"]["band"] == "n/a"
    assert report["summary"]["overall_score"] is None
    assert all(not dimension["assessed"] for dimension in report["dimensions"])


def test_weights_are_renormalised_for_docs_only(tmp_path):
    root = make_docs_only(tmp_path / "docs")
    report = scan_project(root, rules=load_rules(), quiet=True)
    assessed = [dimension for dimension in report["dimensions"] if dimension["assessed"]]
    assert {dimension["id"] for dimension in assessed} == {"docs", "dependencies"}
    assert report["summary"]["overall_score"] is not None
