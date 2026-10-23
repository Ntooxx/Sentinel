from __future__ import annotations

import json

from s2_helpers import write_project

from sentinel2.config import load_rules
from sentinel2.renderers import render
from sentinel2.scanner import scan_project


def test_unicode_paths_content_and_crlf_do_not_crash(tmp_path):
    root = write_project(
        tmp_path / "space and ünicode",
        {
            "módulo.py": 'valor = "ıİçğüş"\r\nprint(valor)\r\n',
            "docs/日本語.md": "# 日本語\r\n\r\nContenido.\r\n",
            "README.md": "# Ünicode\r\n",
            "pyproject.toml": '[project]\nname = "unicode-app"\ndependencies = []\n',
        },
    )
    report = scan_project(root, rules=load_rules(), quiet=True)
    output = render(report, "json")
    payload = json.loads(output)
    assert payload["project"]["name"] == "unicode-app"
    for fmt in ("text", "md", "html"):
        rendered = render(report, fmt, color=False)
        assert isinstance(rendered, str)


def test_report_contains_no_absolute_project_path(tmp_path):
    root = write_project(
        tmp_path / "absolute",
        {
            "app.py": "def main():\n    return 0\n",
            "README.md": "# App\n",
        },
    )
    report = scan_project(root, rules=load_rules(), quiet=True)
    encoded = json.dumps(report, ensure_ascii=False)
    assert str(root).replace("\\", "\\\\") not in encoded
    assert str(root) not in encoded
