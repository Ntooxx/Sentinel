from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from s2_helpers import make_clean, make_messy

from sentinel2.config import load_rules
from sentinel2.renderers import render
from sentinel2.scanner import scan_project


def _scan(root):
    return scan_project(root, rules=load_rules(), quiet=True)


def test_published_json_schema_is_parseable():
    schema_path = Path(__file__).resolve().parents[1] / "docs" / "report.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert schema["properties"]["schema_version"]["const"] == 2
    assert "findings" in schema["required"]


def test_json_contract(tmp_path):
    report = _scan(make_clean(tmp_path / "clean"))
    assert report["schema_version"] == 2
    assert report["rules_version"]
    assert {"project", "scan", "summary", "dimensions", "metrics", "findings", "changes", "architecture", "actions"} <= set(report)
    assert report["scan"]["coverage"]["vendor"] in {"none", "excluded"}
    for finding in report["findings"]:
        assert {"id", "rule_id", "dimension", "severity", "confidence", "title", "evidence", "why", "recommendation"} <= set(finding)
    parsed = json.loads(render(report, "json"))
    assert parsed["schema_version"] == 2


def test_text_markdown_and_html_renderers(tmp_path):
    report = _scan(make_clean(tmp_path / "clean"))
    text = render(report, "text", color=False)
    markdown = render(report, "md")
    html = render(report, "html")
    assert "SENTINEL QUALITY REPORT" in text
    assert "WHAT TO FIX FIRST" in text
    assert markdown.startswith("# Sentinel Quality Report")
    assert "| Dimension | Grade | Summary |" in markdown
    assert html.startswith("<!doctype html>")
    assert "<style>" in html
    assert "http://" not in html.split("<style>")[0]
    assert len(text.splitlines()) <= 200
    assert len(text.encode("utf-8")) <= 12 * 1024


def test_html_escapes_repository_controlled_strings(tmp_path):
    report = _scan(make_clean(tmp_path / "clean"))
    injected = deepcopy(report)
    injected["project"]["name"] = '<script>alert("project")</script>'
    if injected["findings"]:
        injected["findings"][0]["title"] = '<script>alert("finding")</script>'
        injected["findings"][0]["file"] = '<script>alert("file")</script>'
    html = render(injected, "html")
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html
    assert 'alert("project")' not in html


def test_markdown_and_text_show_top_five_by_default(tmp_path):
    report = _scan(make_messy(tmp_path / "messy"))
    assert len(report["findings"]) >= 5
    text = render(report, "text", color=False)
    assert text.count("\n") < 250
    assert "more finding(s)" in text
    markdown = render(report, "md")
    assert "## What to fix first" in markdown
    assert "more finding" not in markdown or "Top findings" in markdown


def test_report_is_deterministic_except_timestamps(tmp_path):
    root = make_clean(tmp_path / "clean")
    first = _scan(root)
    second = _scan(root)
    for key in ("summary", "dimensions", "metrics", "findings", "architecture", "actions"):
        assert first[key] == second[key]
