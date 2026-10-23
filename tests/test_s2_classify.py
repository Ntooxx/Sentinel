from __future__ import annotations

from sentinel2.classify import classify_path, detect_archetype, is_translation_path
from sentinel2.config import load_rules
from sentinel2.utils import module_name_for_path


def test_source_test_doc_and_config_roles():
    rules = load_rules()
    assert classify_path("src/pkg/core.py", rules).is_source
    assert classify_path("tests/test_core.py", rules).is_test
    assert classify_path("README.md", rules).is_doc
    assert classify_path("pyproject.toml", rules).is_config
    assert classify_path("docs/guide.md", rules).role == "doc"


def test_translation_and_example_roles():
    rules = load_rules()
    assert is_translation_path("docs/zh/docs/index.md", rules)
    assert is_translation_path("README.pt-BR.md", rules)
    assert not is_translation_path("docs/en/docs/index.md", rules)
    assert classify_path("docs/zh/docs/index.md", rules).is_translation
    assert classify_path("docs_src/tutorial.py", rules).is_example
    assert classify_path("examples/demo.py", rules).is_example


def test_vendor_generated_and_minified_roles():
    rules = load_rules()
    assert classify_path("vendor/lib.js", rules).is_vendor
    assert classify_path("generated/schema.py", rules).is_generated
    assert classify_path("static/app.min.js", rules).is_generated
    assert classify_path("node_modules/pkg/index.js", rules).is_vendor


def test_language_detection_by_extension():
    rules = load_rules()
    assert classify_path("app.py", rules).language == "python"
    assert classify_path("app.ts", rules).language == "typescript"
    assert classify_path("main.go", rules).language == "go"
    assert classify_path("main.rs", rules).language == "rust"
    assert classify_path("Dockerfile", rules).language == "dockerfile"


def test_module_name_for_src_layout():
    assert module_name_for_path("src/pkg/core.py") == "pkg.core"
    assert module_name_for_path("pkg/__init__.py") == "pkg"
    assert module_name_for_path("src/pkg/__init__.py") == "pkg"


def test_archetype_detection(tmp_path):
    rules = load_rules()

    class Row(dict):
        pass

    rows = [
        Row(path="app.py", is_source=1, is_test=0, is_doc=0),
        Row(path="tests/test_app.py", is_source=0, is_test=1, is_doc=0),
    ]
    assert detect_archetype(tmp_path, rows, {"name": "my-cli", "has_scripts": True}) == "cli"
    assert detect_archetype(tmp_path, rows, {"name": "fastapi"}) == "framework_library"
    docs = [Row(path="README.md", is_source=0, is_test=0, is_doc=1)]
    assert detect_archetype(tmp_path, docs, {}) == "docs_only"
    assert detect_archetype(tmp_path, [], {}) == "unknown"
    assert rules["weights"]["architecture"] > 0
