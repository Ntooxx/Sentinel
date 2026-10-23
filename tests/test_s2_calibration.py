from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from s2_helpers import clean_files, make_clean, write_project

from sentinel2.config import load_rules
from sentinel2.scanner import scan_project

ROOT = Path(__file__).resolve().parents[1]


def _report(root: Path, files: dict[str, str]) -> dict:
    return scan_project(write_project(root, files), rules=load_rules(), quiet=True)


def _dimensions(report: dict) -> dict[str, dict]:
    return {item["id"]: item for item in report["dimensions"]}


def _load_calibrate():
    spec = importlib.util.spec_from_file_location("sentinel_calibrate", ROOT / "tools" / "calibrate.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_fixture_secrets_are_reported_but_do_not_fail_security(tmp_path):
    files = clean_files()
    files["tests/test_core.py"] = (
        "from clean_app.core import add\n\n"
        'AWS_ACCESS_KEY = "AKIAABCDEFGHIJKLMNOP"\n\n'
        "def test_add() -> None:\n"
        "    assert add(1, 2) == 3\n"
    )
    report = _report(tmp_path / "fixture", files)
    dimensions = _dimensions(report)

    assert any(finding["rule_id"] == "SEC001" for finding in report["findings"])
    assert dimensions["security"]["score"] >= 90
    # The raw value must never surface; only redacted evidence is allowed.
    assert "AKIAABCDEFGHIJKLMNOP" not in json.dumps(report, ensure_ascii=False)
    assert any("[REDACTED]" in item for finding in report["findings"] if finding["rule_id"] == "SEC001" for item in finding["evidence"])


def test_live_provider_secret_lowers_security_substantially(tmp_path):
    files = clean_files()
    files["src/clean_app/config.py"] = 'SETTINGS = {"key": "AKIAABCDEFGHIJKLMNOP"}\n'
    report = _report(tmp_path / "live", files)
    dimensions = _dimensions(report)

    assert any(
        finding["rule_id"] == "SEC001" and finding["file"] == "src/clean_app/config.py" for finding in report["findings"]
    )
    assert dimensions["security"]["score"] <= 80


def test_docstring_assignment_is_not_a_secret(tmp_path):
    files = clean_files()
    files["src/clean_app/doc.py"] = (
        '"""Example.\n\n    >>> token = \'variable|default:"Default value"|date:"Y-m-d"\'\n"""\n'
    )
    report = _report(tmp_path / "docstring", files)

    assert not any(finding["rule_id"] == "SEC001" for finding in report["findings"])


def test_generated_data_blobs_are_not_oversized_source(tmp_path):
    files = clean_files()
    files["src/clean_app/table.py"] = "DATA = {\n" + "".join(f"    {index}: {index},\n" for index in range(1200)) + "}\n"
    report = _report(tmp_path / "data", files)

    assert not any(
        finding["rule_id"] in {"MNT001", "MNT002"} and finding["file"] == "src/clean_app/table.py"
        for finding in report["findings"]
    )


def test_a_failing_dimension_pulls_the_overall_down(tmp_path):
    clean = scan_project(make_clean(tmp_path / "clean"), rules=load_rules(), quiet=True)
    files = clean_files()
    for index in range(120):
        files[f"src/clean_app/broken_{index}.py"] = "def broken(:\n    return 1\n"
    broken = _report(tmp_path / "broken", files)

    weights = load_rules()["weights"]
    assessed = [item for item in broken["dimensions"] if item["assessed"] and item["score"] is not None]
    assert assessed
    total = sum(weights[item["id"]] for item in assessed)
    weighted_average = round(sum(item["score"] * weights[item["id"]] for item in assessed) / total)
    weakest = min(item["score"] for item in assessed)

    # The overall must sit below the plain weighted average, pulled toward the
    # failing dimension rather than being rescued by the healthy ones.
    assert broken["summary"]["overall_score"] < weighted_average
    assert broken["summary"]["overall_score"] <= weakest + 30
    assert broken["summary"]["overall_score"] < clean["summary"]["overall_score"]


def test_tiny_repository_cannot_reach_the_top_grade(tmp_path):
    report = scan_project(make_clean(tmp_path / "clean"), rules=load_rules(), quiet=True)

    assert report["summary"]["grade_ceiling"] == "A"
    # The ceiling caps the score, which is what keeps a tiny repository out of
    # the top of the scale. Both A and A+ map to the "maintained" band.
    assert report["summary"]["overall_score"] <= 93
    assert report["summary"]["band"] == "maintained"


def test_test_coverage_shortfall_scales_with_the_gap(tmp_path):
    almost = clean_files()
    almost.pop("tests/test_main.py")
    almost_report = _report(tmp_path / "almost", almost)
    bare = clean_files()
    bare.pop("tests/test_main.py")
    bare.pop("tests/test_core.py")
    bare_report = _report(tmp_path / "bare", bare)

    almost_tests = _dimensions(almost_report)["tests"]["score"]
    bare_tests = _dimensions(bare_report)["tests"]["score"]
    assert bare_tests <= almost_tests


def test_calibration_helpers():
    calibrate = _load_calibrate()

    assert calibrate._band_distance("maintained", "adequate") == 1
    assert calibrate._band_distance("maintained", "unmaintained") == 3
    assert calibrate._band_distance("maintained", "n/a") is None

    rows = [
        {"tier": 1, "band": "maintained"},
        {"tier": 1, "band": "maintained"},
        {"tier": 2, "band": "maintained"},
        {"tier": 4, "band": "adequate"},
        {"tier": 0, "band": "maintained"},
    ]
    ranges = calibrate._tier_band_ranges(rows)
    assert list(ranges) == [1, 2, 4]
    assert ranges[1] == (3, 3)
    # Ties between tiers are allowed: tier 2 sharing the maintained band with
    # tier 1 is not an inversion.
    assert calibrate._tier_inversions(ranges) == []
    # A worse tier reaching a band a better tier never reaches is an inversion.
    assert calibrate._tier_inversions({1: (3, 3), 2: (2, 2), 4: (3, 3)})

    assert round(calibrate._spearman([1.0, 2.0, 3.0, 4.0], [10.0, 20.0, 30.0, 40.0]), 3) == 1.0
    assert round(calibrate._spearman([1.0, 2.0, 3.0, 4.0], [40.0, 30.0, 20.0, 10.0]), 3) == -1.0
    assert calibrate._spearman([1.0], [1.0]) is None


def test_calibration_corpus_matches_the_six_dimensions():
    spec = json.loads((ROOT / "config" / "calibration_corpus.json").read_text(encoding="utf-8"))
    acceptance = spec["acceptance"]

    assert len(spec["repos"]) >= acceptance["min_repos"] + 1
    assert acceptance["max_band_distance"] >= 1
    assert acceptance["require_tier_separation"] is True
    assert all("rationale" in entry for entry in spec["repos"])
    assert any(entry.get("docs_only") for entry in spec["repos"])
