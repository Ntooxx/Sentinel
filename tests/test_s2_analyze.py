from __future__ import annotations

import json

from s2_helpers import write_project

from sentinel2.analyze import inspect_candidate, merge_manifest_meta
from sentinel2.config import load_rules
from sentinel2.discover import discover
from sentinel2.utils import deep_merge


def _analyse(root, relative: str, rules=None):
    rules = rules or load_rules()
    result = discover(root, rules)
    candidate = next(item for item in result.candidates if item.path == relative)
    analysis, cache_hit = inspect_candidate(root, candidate, rules)
    assert not cache_hit
    assert analysis is not None
    return analysis


def test_python_ast_imports_symbols_and_complexity(tmp_path):
    write_project(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/mod.py": (
                "from .other import thing\n"
                "import os\n\n\n"
                "class Service:\n"
                "    def run(self, value):\n"
                "        if value:\n"
                "            for item in value:\n"
                "                if item > 1:\n"
                "                    return item\n"
                "        return None\n\n\n"
                "def helper(value):\n"
                "    if value:\n"
                "        return 1\n"
                "    return 0\n"
            ),
            "pkg/other.py": "thing = 1\n",
        },
    )
    analysis = _analyse(tmp_path, "pkg/mod.py")
    assert analysis.parse_error is None
    assert ("pkg.other", "from") in analysis.imports
    assert ("os", "import") in analysis.imports
    names = {symbol.qualname for symbol in analysis.symbols}
    assert {"Service", "Service.run", "helper"} <= names
    run = next(symbol for symbol in analysis.symbols if symbol.qualname == "Service.run")
    assert run.kind == "method"
    assert run.complexity >= 4
    assert analysis.max_complexity == run.complexity


def test_python_parse_error_is_recorded(tmp_path):
    write_project(tmp_path, {"broken.py": "def nope(:\n    return 1\n"})
    analysis = _analyse(tmp_path, "broken.py")
    assert analysis.parse_error
    assert "line" in analysis.parse_error.lower()


def test_non_python_heuristics(tmp_path):
    write_project(
        tmp_path,
        {
            "app.ts": (
                "import { x } from './x';\n"
                "export class Thing {\n"
                "  run(value: number) {\n"
                "    if (value > 0) { return value; }\n"
                "    return 0;\n"
                "  }\n"
                "}\n"
            ),
            "main.go": 'package main\nimport "fmt"\nfunc main() { fmt.Println("hi") }\n',
        },
    )
    ts = _analyse(tmp_path, "app.ts")
    assert any(symbol.name == "Thing" for symbol in ts.symbols)
    assert any("x" in imported for imported, _kind in ts.imports)
    go = _analyse(tmp_path, "main.go")
    assert any(symbol.name == "main" for symbol in go.symbols)
    assert any(imported == "fmt" for imported, _kind in go.imports)


def test_manifest_and_readme_metadata(tmp_path):
    write_project(
        tmp_path,
        {
            "pyproject.toml": (
                "[project]\n"
                'name = "sample"\n'
                'version = "0.1.0"\n'
                'dependencies = ["requests>=2.0", "pydantic==2.5.0"]\n\n'
                "[project.scripts]\n"
                'sample = "sample:main"\n'
            ),
            "README.md": "# Sample\n\nSee [missing](docs/missing.md) and [ok](README.md).\nTODO: add usage\n",
        },
    )
    manifest = _analyse(tmp_path, "pyproject.toml")
    assert manifest.meta["manifest_kind"] == "pyproject"
    assert manifest.meta["name"] == "sample"
    assert "requests>=2.0" in manifest.meta["unpinned"]
    assert "pydantic==2.5.0" not in manifest.meta["unpinned"]
    assert manifest.meta["has_scripts"] is True

    readme = _analyse(tmp_path, "README.md")
    assert readme.meta["headings"] == 1
    assert readme.meta["placeholders"] >= 1
    assert any("docs/missing.md" in broken for broken in readme.meta["broken_links"])


def test_secret_and_dangerous_patterns_are_redacted(tmp_path):
    write_project(
        tmp_path,
        {"app.py": 'API_KEY = "sk-abcdefghijklmnopqrstuvwxyz123456"\nvalue = eval("1+1")\nDEBUG = True\n'},
    )
    analysis = _analyse(tmp_path, "app.py")
    secrets = analysis.meta["secrets"]
    assert secrets
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in json.dumps(secrets)
    assert analysis.meta["dangerous"]
    assert analysis.meta["debug"]


def test_sampling_limit(tmp_path):
    write_project(tmp_path, {"big.py": "x = 1\n" * 2000})
    rules = deep_merge(load_rules(), {"analyze_full_limit": 500})
    analysis = _analyse(tmp_path, "big.py", rules)
    assert analysis.sampled is True
    assert analysis.parse_error is None
    assert analysis.lines > 500


def test_manifest_merge(tmp_path):
    write_project(
        tmp_path,
        {
            "pyproject.toml": '[project]\nname = "one"\ndependencies = ["a>=1"]\n',
            "package.json": '{"name": "two", "dependencies": {"b": "^2.0.0"}}',
        },
    )
    rules = load_rules()
    result = discover(tmp_path, rules)
    analyses = []
    for candidate in result.candidates:
        analysis, _ = inspect_candidate(tmp_path, candidate, rules)
        assert analysis is not None
        analyses.append(analysis)
    merged = merge_manifest_meta(
        [{"meta_json": json.dumps(analysis.meta)} for analysis in analyses]
    )
    assert merged["name"] in {"one", "two"}
    assert len(merged["dependencies"]) >= 2
    assert merged["unpinned"]
