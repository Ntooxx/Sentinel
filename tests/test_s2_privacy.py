from __future__ import annotations

import socket
import urllib.request

from s2_helpers import make_clean, write_project

from sentinel2.config import load_rules
from sentinel2.renderers import render
from sentinel2.scanner import scan_project


def test_scan_and_render_do_not_touch_the_network(tmp_path, monkeypatch):
    def fail(*_args, **_kwargs):
        raise AssertionError("network access is not allowed")

    monkeypatch.setattr(socket, "socket", fail)
    monkeypatch.setattr(urllib.request, "urlopen", fail)
    root = make_clean(tmp_path / "clean")
    report = scan_project(root, rules=load_rules(), quiet=True)
    for fmt in ("text", "md", "html", "json"):
        render(report, fmt, color=False)


def test_report_paths_are_relative_and_secrets_are_redacted(tmp_path):
    secret = "AKIAABCDEFGHIJKLMNOP"
    root = write_project(
        tmp_path / "private",
        {
            "app.py": f'AWS_ACCESS_KEY = "{secret}"\n',
            "README.md": "# Private\n",
        },
    )
    report = scan_project(root, rules=load_rules(), quiet=True)
    for finding in report["findings"]:
        if finding["file"]:
            assert not finding["file"].startswith(str(root))
            assert ":" not in finding["file"][:2]
    for fmt in ("text", "md", "html", "json"):
        output = render(report, fmt, color=False, all_findings=True)
        assert secret not in output
        assert str(root) not in output


def test_scan_only_writes_inside_sentinel_directory(tmp_path):
    root = make_clean(tmp_path / "clean")
    before = {path.relative_to(root) for path in root.rglob("*") if path.is_file()}
    scan_project(root, rules=load_rules(), quiet=True)
    after = {path.relative_to(root) for path in root.rglob("*") if path.is_file()}
    created = {path.as_posix() for path in after - before}
    assert created
    assert all(path.startswith(".sentinel/") for path in created)
