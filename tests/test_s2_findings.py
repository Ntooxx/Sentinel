from __future__ import annotations

from s2_helpers import make_clean, make_messy, write_project

from sentinel2.config import load_rules
from sentinel2.findings import Finding, rank_top_findings, sort_findings
from sentinel2.scanner import scan_project


def test_messy_repository_produces_expected_source_findings(tmp_path):
    root = make_messy(tmp_path / "messy")
    report = scan_project(root, rules=load_rules(), quiet=True)
    rules = {finding["rule_id"] for finding in report["findings"]}
    assert {"MNT001", "MNT005", "MNT007", "MNT009", "TST001"} <= rules
    top = report["findings"][:5]
    assert top
    assert all(
        finding["dimension"] in {"maintainability", "tests"} for finding in top
    ), top
    assert not any(finding["rule_id"] in {"DOC001", "DEP001", "HYG001"} for finding in top)


def test_clean_repository_has_no_high_severity_findings(tmp_path):
    root = make_clean(tmp_path / "clean")
    report = scan_project(root, rules=load_rules(), quiet=True)
    assert not [finding for finding in report["findings"] if finding["severity"] == "high"]


def test_docs_translation_noise_never_outranks_source(tmp_path):
    root = write_project(
        tmp_path / "translated",
        {
            "README.md": "# App\n",
            "app.py": "def main():\n    return 0\n" + ("# TODO: clean up\n" * 20),
            "docs/zh/docs/index.md": "# 中文\n",
            "docs/zh/docs/guide.md": "# 指南\n",
        },
    )
    report = scan_project(root, rules=load_rules(), quiet=True)
    top_files = [finding["file"] for finding in report["findings"][:5] if finding["file"]]
    assert all("docs/zh" not in (path or "") for path in top_files)
    assert report["exclusions"]["docs_translations"] == 2


def test_architecture_entry_point_rule(tmp_path):
    root = write_project(
        tmp_path / "web",
        {
            "pyproject.toml": '[project]\nname = "web-service"\ndependencies = ["fastapi==1.0.0"]\n',
            "pkg/app.py": "def handler():\n    return {}\n",
            "README.md": "# Web\n",
            "tests/test_app.py": "from pkg.app import handler\n\ndef test_handler():\n    assert handler() == {}\n",
        },
    )
    report = scan_project(root, rules=load_rules(), quiet=True)
    assert any(finding["rule_id"] == "ARCH001" for finding in report["findings"])


def test_documentation_broken_link_rule(tmp_path):
    root = write_project(
        tmp_path / "docs",
        {
            "README.md": "# Docs\n\nSee [missing](docs/missing.md).\n",
            "app.py": "def main():\n    return 0\n",
            "tests/test_app.py": "from app import main\n\ndef test_main():\n    assert main() == 0\n",
            "pyproject.toml": '[project]\nname = "docs-link"\ndependencies = []\n',
        },
    )
    report = scan_project(root, rules=load_rules(), quiet=True)
    assert any(finding["rule_id"] == "DOC004" for finding in report["findings"])


def test_import_cycle_rule(tmp_path):
    root = write_project(
        tmp_path / "cycle",
        {
            "pkg/__init__.py": "",
            "pkg/a.py": "from pkg import b\n",
            "pkg/b.py": "from pkg import a\n",
            "tests/test_cycle.py": "from pkg import a\n\ndef test_ok():\n    assert a\n",
            "README.md": "# Cycle\n",
            "pyproject.toml": '[project]\nname = "cycle"\ndependencies = []\n',
        },
    )
    report = scan_project(root, rules=load_rules(), quiet=True)
    cycles = [finding for finding in report["findings"] if finding["rule_id"] == "ARCH002"]
    assert cycles


def test_secret_redaction_in_all_formats(tmp_path):
    secret = "sk-abcdefghijklmnopqrstuvwxyz123456"
    root = write_project(
        tmp_path / "secret",
        {
            "app.py": f'API_KEY = "{secret}"\n',
            "README.md": "# Secret\n",
        },
    )
    report = scan_project(root, rules=load_rules(), quiet=True)
    from sentinel2.renderers import render

    for fmt in ("text", "md", "html", "json"):
        output = render(report, fmt, color=False, all_findings=True)
        assert secret not in output
    findings = [finding for finding in report["findings"] if finding["rule_id"] == "SEC001"]
    assert findings
    assert any("[REDACTED]" in evidence for finding in findings for evidence in finding["evidence"])


def test_rank_top_findings_is_source_first_and_capped():
    findings = [
        Finding("DOC001", "docs", "medium", "high", "doc", None, 1, category="docs"),
        Finding("MNT001", "maintainability", "medium", "high", "m1", "a.py", 1, category="source"),
        Finding("MNT001", "maintainability", "medium", "high", "m2", "b.py", 1, category="source"),
        Finding("MNT001", "maintainability", "medium", "high", "m3", "c.py", 1, category="source"),
        Finding("ARCH002", "architecture", "medium", "high", "arch", "d.py", 1, category="source"),
        Finding("DEP001", "dependencies", "medium", "high", "dep", None, 1, category="project"),
    ]
    ranked = rank_top_findings(findings, limit=3)
    assert ranked
    assert all(finding.category == "source" for finding in ranked)
    assert len([finding for finding in ranked if finding.rule_id == "MNT001"]) <= 2
    ordered = sort_findings(findings)
    assert ordered[0].category == "source"
