from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, TOOLS / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bundle_tool = _load("sentinel_build_web_bundle", "build_web_bundle.py")
page_tool = _load("sentinel_build_web_page", "build_web_page.py")


def test_bundle_contains_the_package_and_its_rules(tmp_path):
    payload = bundle_tool.build(ROOT / "src" / "sentinel2", tmp_path / "bundle.json")
    files = payload["files"]
    assert payload["package"] == "sentinel2"
    assert payload["schema"] == 1
    assert "sentinel2/__init__.py" in files
    assert "sentinel2/scanner.py" in files
    assert "sentinel2/renderers/html.py" in files
    # Missing this file would break scanning in the browser.
    assert "sentinel2/quality_rules.json" in files
    # Caches must not be shipped.
    assert not any("__pycache__" in path for path in files)
    assert not any(path.endswith(".pyc") for path in files)


def test_bundled_python_sources_are_importable_text(tmp_path):
    payload = bundle_tool.build(ROOT / "src" / "sentinel2", tmp_path / "bundle.json")
    for path, text in payload["files"].items():
        assert isinstance(text, str)
        assert text, path
    assert "SCHEMA_VERSION" in payload["files"]["sentinel2/__init__.py"]


def test_inlined_page_cannot_break_out_of_the_script_tag(tmp_path):
    """A raw '<' in the JSON would open an HTML-like comment inside <script>."""
    payload = bundle_tool.build(ROOT / "src" / "sentinel2", tmp_path / "bundle.json")
    source = tmp_path / "index.html"
    source.write_text("<body>run<!-- SENTINEL_BUNDLE_MARKER --><p>x</p></body>", encoding="utf-8")
    out = tmp_path / "inlined.html"
    blob = bundle_tool.inline_html(source, payload, out)

    assert "<" not in blob
    assert "</script>" not in blob
    text = out.read_text(encoding="utf-8")
    assert "window.__SENTINEL_BUNDLE__=" in text
    assert "</script>" in text  # the tag itself survives
    assert text.count("</script>") == 1
    # The escaped JSON must still decode to the same files.
    decoded = json.loads(blob.replace("\\u003c", "<"))
    assert decoded["files"].keys() == payload["files"].keys()


def test_inline_html_requires_the_marker(tmp_path):
    payload = {"files": {}}
    source = tmp_path / "index.html"
    source.write_text("<body>no marker here</body>", encoding="utf-8")
    with pytest.raises(SystemExit, match="SENTINEL_BUNDLE_MARKER"):
        bundle_tool.inline_html(source, payload, tmp_path / "out.html")


def test_page_injection_adds_the_runner_and_nav_link(tmp_path):
    source = tmp_path / "index.html"
    source.write_text(
        '<a class="nav-link" href="#report">Report</a>\n'
        "    <!-- ================= PERFORMANCE ================= -->\n",
        encoding="utf-8",
    )
    block = tmp_path / "block.html"
    block.write_text('<div class="run-shell" id="run"></div>', encoding="utf-8")
    out = tmp_path / "out.html"
    page = page_tool.build(source, block, out)

    assert 'id="run"' in page
    assert 'href="#run">Run it</a>' in page
    # The runner block must land before the Performance section it anchors on.
    assert page.index('id="run"') < page.index("PERFORMANCE")
    assert out.read_text(encoding="utf-8") == page


def test_page_injection_is_not_applied_twice(tmp_path):
    source = tmp_path / "index.html"
    source.write_text(
        "    <!-- ================= PERFORMANCE ================= -->\n", encoding="utf-8"
    )
    block = tmp_path / "block.html"
    block.write_text('<div id="run"></div>', encoding="utf-8")
    first = tmp_path / "first.html"
    page_tool.build(source, block, first)
    with pytest.raises(SystemExit, match="already contains"):
        page_tool.build(first, block, tmp_path / "second.html")


def test_committed_landing_page_has_the_runner_wired_up():
    page = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert 'id="run"' in page
    assert "SENTINEL_BUNDLE_MARKER" in page
    assert "window.SentinelLive" in page
    assert 'href="#run"' in page
