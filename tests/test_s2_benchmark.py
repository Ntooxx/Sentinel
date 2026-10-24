from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCHMARKS = ROOT / "benchmarks"
DEFECT_FILE = BENCHMARKS / "fixtures" / "defect_corpus" / "defects.json"
VERDICTS = {"genuine", "debatable", "not_genuine"}


def _load_bench():
    spec = importlib.util.spec_from_file_location("sentinel_competitive_bench", ROOT / "tools" / "competitive_bench.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_freeze_manifest_matches_the_analysis_package():
    """The corpus is only meaningful while scoring is frozen.

    If this fails, analysis code changed after the round was frozen: either
    restore it, or start a new round with
    `python tools/competitive_bench.py freeze` and record why in
    benchmarks/REPORT.md.
    """

    bench = _load_bench()
    frozen = json.loads(bench.FREEZE_FILE.read_text(encoding="utf-8"))
    digest, files = bench._package_digest()

    assert frozen["package_sha256"] == digest, (
        "the analysis package changed after benchmarks/freeze.json was written; "
        "a benchmark run on a moving target is not a measurement. "
        "Restore the frozen package or start a new round with "
        "`python tools/competitive_bench.py freeze`."
    )
    assert frozen["package_files"] == len(files)
    assert frozen["analysis_version"] is not None


def test_freeze_refuses_to_run_on_a_changed_package(tmp_path, monkeypatch):
    bench = _load_bench()
    monkeypatch.setattr(bench, "FREEZE_FILE", tmp_path / "missing.json")
    try:
        bench.check_freeze()
    except SystemExit as exc:
        assert "freeze" in str(exc)
    else:
        raise AssertionError("check_freeze must refuse to run without a freeze record")


def test_defect_manifest_matches_the_fixture_files():
    spec = json.loads(DEFECT_FILE.read_text(encoding="utf-8"))
    bench = _load_bench()
    fixture = DEFECT_FILE.parent
    generated = {entry["file"] for entry in spec.get("generated", [])}
    ids = [entry["id"] for entry in spec["defects"]]

    assert len(ids) == len(set(ids)), "defect ids must be unique"
    assert set(ids).isdisjoint(generated), "generated ids must not collide with committed ones"
    for entry in spec["defects"]:
        assert set(entry["expected"]) <= set(bench.TOOLS), f"{entry['id']} names an unknown tool"
        assert entry["rationale"], f"{entry['id']} has no rationale"
        if entry["file"] in {".", ""} or entry["id"].startswith("DECOY"):
            continue
        if entry["file"] in generated:
            continue
        assert (fixture / entry["file"]).is_file(), f"{entry['id']} points at a missing file"
    # Every tool is exercised as both in and out of scope, otherwise the recall
    # denominator is trivial.
    for tool in bench.TOOLS:
        in_scope = [entry for entry in spec["defects"] if entry["expected"].get(tool)]
        assert in_scope, f"no defect is in scope for {tool}"
        assert len(in_scope) < len(spec["defects"]), f"no defect is out of scope for {tool}"


def test_corpus_manifest_is_five_real_repositories():
    corpus = json.loads((BENCHMARKS / "corpus.json").read_text(encoding="utf-8"))
    repos = corpus["repos"]

    assert len(repos) == 5
    assert len({entry["name"] for entry in repos}) == 5
    for entry in repos:
        assert entry["url"].startswith("https://"), entry["name"]
        assert entry["path"].startswith(".."), "the corpus lives outside this repository"
        assert isinstance(entry["tier"], int)


def test_labels_cover_every_top_five_finding():
    labels = json.loads((BENCHMARKS / "labels.json").read_text(encoding="utf-8"))
    corpus = json.loads((BENCHMARKS / "corpus.json").read_text(encoding="utf-8"))
    names = {entry["name"] for entry in corpus["repos"]}

    assert set(labels["repos"]) == names, "every corpus repository needs labels"
    for name, block in labels["repos"].items():
        findings = block["findings"]
        assert len(findings) == 5, f"{name}: top 5 must be labelled, got {len(findings)}"
        assert [item["rank"] for item in findings] == [1, 2, 3, 4, 5]
        for item in findings:
            assert item["verdict"] in VERDICTS, f"{name} rank {item['rank']}: bad verdict"
            assert item["reviewer_note"], f"{name} rank {item['rank']}: verdict without evidence"
            assert isinstance(item["actionable"], bool)
        assert labels["reviewed_on"], "labels need a review date"


def test_recall_matrix_separates_hits_misses_and_out_of_scope():
    bench = _load_bench()
    defects = [
        {"id": "D01", "title": "t", "file": "a.py", "category": "security", "expected": {"sentinel": "SEC002"}},
        {"id": "D02", "title": "t", "file": "b.py", "category": "security", "expected": {"ruff": "F821"}},
        {"id": "D03", "title": "t", "file": ".", "category": "tests", "expected": {"sentinel": "TST001"}},
    ]
    rows = {
        "sentinel": [
            {"tool": "sentinel", "rule": "SEC002", "file": "a.py", "line": 3, "severity": "medium", "title": "x"},
            {"tool": "sentinel", "rule": "TST001", "file": "", "line": 0, "severity": "high", "title": "y"},
        ],
        "ruff": [],
        "bandit": [],
        "semgrep": [],
    }

    matrix = bench.recall_matrix(rows, defects)
    assert matrix[0]["tools"]["sentinel"]["status"] == "hit"
    assert matrix[0]["tools"]["ruff"]["status"] == "out"
    assert matrix[1]["tools"]["ruff"]["status"] == "miss"
    assert matrix[1]["tools"]["sentinel"]["status"] == "out"
    # Repo-level defects match on the rule alone, without a file.
    assert matrix[2]["tools"]["sentinel"]["status"] == "hit"

    summary = bench.summarise_recall(matrix)
    assert summary["sentinel"] == {
        "in_scope": 2,
        "hits": 2,
        "misses": 0,
        "recall": 1.0,
        "out_of_scope": 1,
        "missed_ids": [],
    }
    assert summary["ruff"]["in_scope"] == 1
    assert summary["ruff"]["misses"] == 1
    assert summary["bandit"]["in_scope"] == 0
    assert summary["bandit"]["recall"] is None


def test_severity_normalisation_and_coverage_helpers():
    bench = _load_bench()

    assert bench.ruff_severity("E999") == "high"
    assert bench.ruff_severity("F821") == "high"
    assert bench.ruff_severity("F401") == "low"
    assert bench.ruff_severity("E722") == "medium"
    assert bench.severity_rank("high") > bench.severity_rank("medium")

    rows = [
        {"file": "src/a.py", "severity": "low"},
        {"file": "src/a.py", "severity": "high"},
        {"file": "src/b.py", "severity": "low"},
    ]
    assert bench.files_at_least(rows, "high") == {"src/a.py"}
    assert bench.files_at_least(rows, "medium") == {"src/a.py"}
    assert bench.count_by_severity(rows) == {"info": 0, "low": 2, "medium": 0, "high": 1}


def test_sentinel_top_five_is_the_ranked_prefix():
    bench = _load_bench()
    payload = {
        "findings": [
            {"rule_id": "MNT001", "file": "a.py", "line": 1, "severity": "medium", "title": "t"}
            for _ in range(12)
        ]
    }
    rows = bench.parse_sentinel(payload, Path("/repo"))

    assert [row["rank"] for row in rows] == list(range(1, 13))
    assert [row["rank"] for row in rows if row["rank"] <= 5] == [1, 2, 3, 4, 5]
