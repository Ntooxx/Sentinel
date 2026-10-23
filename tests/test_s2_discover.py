from __future__ import annotations

import os

import pytest
from s2_helpers import write_project

from sentinel2.config import load_rules
from sentinel2.discover import discover, snapshot_signature
from sentinel2.utils import deep_merge


def test_walk_discovery_ignores_defaults_user_ignores_and_gitignore(tmp_path):
    write_project(
        tmp_path,
        {
            "app.py": "print('ok')\n",
            "README.md": "# App\n",
            ".gitignore": "ignored.py\n",
            "ignored.py": "print('ignored')\n",
            ".sentinel/state.txt": "runtime\n",
            "node_modules/pkg/index.js": "module.exports = 1;\n",
            "user_skip/data.py": "x = 1\n",
            "vendor/lib.py": "x = 1\n",
            "generated/schema.py": "x = 1\n",
            "docs_src/example.py": "x = 1\n",
        },
    )
    rules = load_rules()
    result = discover(tmp_path, rules, ignores=["user_skip"])
    paths = {candidate.path for candidate in result.candidates}
    assert "app.py" in paths
    assert ".gitignore" in paths
    assert "ignored.py" not in paths
    assert "user_skip/data.py" not in paths
    assert ".sentinel/state.txt" not in paths
    assert "node_modules/pkg/index.js" not in paths
    assert result.excluded_counts["vendor"] == 1
    assert result.excluded_counts["generated"] == 1
    assert result.excluded_counts["example"] == 1


def test_binary_and_oversized_are_recorded(tmp_path):
    write_project(
        tmp_path,
        {
            "app.py": "print('ok')\n",
            "image.bin": b"PNG\x00\x01\x02",
            "large.py": "x = 1\n" * 200,
        },
    )
    rules = deep_merge(load_rules(), {"max_file_size": 100})
    result = discover(tmp_path, rules, quick=False, probe_binary=True)
    skipped = {item["path"]: item["reason"] for item in result.skipped}
    assert skipped["image.bin"] == "binary"
    assert skipped["large.py"] == "oversized"
    assert {candidate.path for candidate in result.candidates} == {"app.py"}


def test_quick_discovery_matches_signature(tmp_path):
    write_project(tmp_path, {"app.py": "x = 1\n", "README.md": "# App\n"})
    rules = load_rules()
    full = discover(tmp_path, rules)
    quick = discover(tmp_path, rules, quick=True)
    assert snapshot_signature(full) == snapshot_signature(quick)


def test_unicode_paths_and_content_are_preserved(tmp_path):
    write_project(tmp_path, {"módulo.py": 'valor = "ıİç"\n', "docs/日本語.md": "# 日本語\n"})
    rules = load_rules()
    result = discover(tmp_path, rules)
    paths = {candidate.path for candidate in result.candidates}
    assert "módulo.py" in paths
    assert "docs/日本語.md" in paths


def test_symlink_file_is_skipped_when_supported(tmp_path):
    target = write_project(tmp_path, {"real.py": "x = 1\n"}) / "real.py"
    link = tmp_path / "link.py"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available on this platform")
    rules = load_rules()
    result = discover(tmp_path, rules)
    assert "real.py" in {candidate.path for candidate in result.candidates}
    assert "link.py" not in {candidate.path for candidate in result.candidates}
