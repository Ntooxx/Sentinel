from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from s2_helpers import make_clean, make_messy

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC)
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def run_cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "sentinel2", *args],
        cwd=str(cwd or ROOT),
        env=_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )


def test_version_and_help_surface():
    version = run_cli("--version")
    assert version.returncode == 0
    assert "project-sentinel 2.0.0" in version.stdout

    help_result = run_cli("--help")
    assert help_result.returncode == 0
    for command in ("scan", "report", "watch"):
        assert command in help_result.stdout
    for removed in ("retrieve", "dashboard", "kilo-refresh", "analyze-url"):
        assert removed not in help_result.stdout


def test_python_sentinel_launcher_matches_cli():
    result = subprocess.run(
        [sys.executable, str(ROOT / "sentinel.py"), "--version"],
        cwd=str(ROOT),
        env=_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    assert result.returncode == 0
    assert "project-sentinel 2.0.0" in result.stdout


def test_scan_report_and_json_output(tmp_path):
    root = make_clean(tmp_path / "clean")
    output = tmp_path / "report.json"
    scan = run_cli("scan", str(root), "--format", "json", "--out", str(output), "--quiet")
    assert scan.returncode == 0, scan.stderr
    assert output.is_file()
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 2
    assert payload["scan"]["files_total"] >= 3

    report = run_cli("report", str(root), "--format", "json")
    assert report.returncode == 0, report.stderr
    again = json.loads(report.stdout)
    assert again["summary"] == payload["summary"]


def test_report_without_index_fails_cleanly(tmp_path):
    result = run_cli("report", str(tmp_path), "--format", "json")
    assert result.returncode == 1
    assert "no index found" in result.stderr.lower()


def test_invalid_path_and_usage_exit_codes(tmp_path):
    missing = run_cli("scan", str(tmp_path / "missing"), "--quiet")
    assert missing.returncode == 1
    bad_usage = run_cli("scan", str(tmp_path), "--format", "nope")
    assert bad_usage.returncode == 2
    bad_grade = run_cli("scan", str(tmp_path), "--fail-under", "Z")
    assert bad_grade.returncode == 2


def test_quality_gate_exit_code(tmp_path):
    root = make_messy(tmp_path / "messy")
    result = run_cli("scan", str(root), "--fail-under", "A", "--quiet")
    assert result.returncode == 3
    assert "quality gate failed" in result.stderr


def test_unicode_path_and_output_do_not_crash_cli(tmp_path):
    root = tmp_path / "space and ünicode"
    root.mkdir()
    (root / "módulo.py").write_text('value = "ıİçğüş"\n', encoding="utf-8")
    (root / "README.md").write_text("# Ünicode\n", encoding="utf-8")
    result = run_cli("scan", str(root), "--format", "json", "--quiet")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["project"]["name"]


def test_html_report_is_self_contained(tmp_path):
    root = make_clean(tmp_path / "clean")
    output = tmp_path / "quality.html"
    result = run_cli("scan", str(root), "--format", "html", "--out", str(output), "--quiet")
    assert result.returncode == 0, result.stderr
    html = output.read_text(encoding="utf-8")
    assert html.startswith("<!doctype html>")
    assert "<style>" in html
    assert "<link" not in html
    assert "http" not in html.split("<style>")[0]
