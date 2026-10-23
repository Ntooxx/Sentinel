from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = ROOT / "demo" / "server.py"


def _load_demo():
    spec = importlib.util.spec_from_file_location("sentinel_demo_server", SERVER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


demo = _load_demo()


def test_https_url_is_accepted():
    kind, value, label = demo.normalize_source("https://github.com/psf/requests")
    assert kind == "url"
    assert value == "https://github.com/psf/requests"
    assert label == "requests"


def test_git_suffix_is_trimmed_from_label():
    _, value, label = demo.normalize_source("https://github.com/psf/requests.git")
    assert value == "https://github.com/psf/requests.git"
    assert label == "requests"


def test_owner_repo_shorthand_becomes_github():
    kind, value, label = demo.normalize_source("psf/requests")
    assert kind == "url"
    assert value == "https://github.com/psf/requests"
    assert label == "psf/requests"


def test_ssh_and_git_protocols_are_treated_as_urls():
    assert demo.normalize_source("git@github.com:psf/requests.git")[0] == "url"
    assert demo.normalize_source("ssh://git@example.com/repo.git")[0] == "url"


def test_local_directory_is_accepted(tmp_path):
    kind, value, label = demo.normalize_source(str(tmp_path))
    assert kind == "path"
    assert Path(value) == tmp_path.resolve()
    assert label == tmp_path.resolve().name


def test_file_url_is_resolved_to_a_local_path(tmp_path):
    kind, value, _ = demo.normalize_source(tmp_path.as_uri())
    assert kind == "path"
    assert Path(value) == tmp_path.resolve()


def test_surrounding_quotes_are_stripped(tmp_path):
    kind, value, _ = demo.normalize_source(f'"{tmp_path}"')
    assert kind == "path"
    assert Path(value) == tmp_path.resolve()


def test_a_file_is_rejected(tmp_path):
    target = tmp_path / "not-a-repo.txt"
    target.write_text("hello", encoding="utf-8")
    with pytest.raises(demo.InputError, match="not a repository folder"):
        demo.normalize_source(str(target))


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "not a repo and not a path",
        "https://github.com/psf/requests\nextra",
        "x" * 501,
    ],
)
def test_unusable_input_is_rejected(value):
    with pytest.raises(demo.InputError):
        demo.normalize_source(value)


def test_slug_is_filesystem_safe():
    slug = demo._slug("https://github.com/Some Owner/repo?x=1")
    assert slug
    assert not set(slug) - set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")


def test_demo_chrome_is_appended_without_touching_the_report_body():
    job = demo.Job("psf/requests", "requests", "url", "https://github.com/psf/requests", False)
    original = "<!doctype html><html><body><main>report</main></body></html>"
    wrapped = demo._with_demo_chrome(original, job)
    assert wrapped.endswith("</body></html>")
    assert "<main>report</main>" in wrapped
    assert wrapped.count("<main>report</main>") == 1
    assert "Analyze another" in wrapped


def test_public_mode_refuses_local_paths(tmp_path):
    with pytest.raises(demo.InputError, match="only accepts public repository URLs"):
        demo.normalize_source(str(tmp_path), allow_local=False)


def test_public_mode_still_accepts_urls():
    kind, value, _ = demo.normalize_source("psf/requests", allow_local=False)
    assert kind == "url"
    assert value == "https://github.com/psf/requests"


def test_rate_limit_blocks_after_the_configured_number_of_jobs():
    ip = "203.0.113.9"
    demo._RATE_STATE.clear()
    try:
        for _ in range(demo.PUBLIC_RATE_LIMIT_JOBS):
            allowed, retry = demo.rate_limit_ok(ip)
            assert allowed
            assert retry == 0
        allowed, retry = demo.rate_limit_ok(ip)
        assert not allowed
        assert retry > 0
    finally:
        demo._RATE_STATE.clear()


def test_public_landing_page_hides_the_local_path_hint():
    assert "C:\\path\\to\\repo" in demo.INDEX_HTML
    assert "C:\\path\\to\\repo" not in demo.PUBLIC_INDEX_HTML
    assert "Public instance" in demo.PUBLIC_INDEX_HTML
