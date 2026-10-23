from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from sentinel2.config import load_rules
from sentinel2.scanner import scan_project
from sentinel2.utils import deep_merge


def write_project(root: Path, files: Mapping[str, str | bytes]) -> Path:
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8", newline="")
    return root


def clean_files() -> dict[str, str]:
    return {
        "pyproject.toml": (
            "[project]\n"
            'name = "clean-app"\n'
            'version = "1.0.0"\n'
            'dependencies = ["requests==2.31.0"]\n\n'
            "[project.scripts]\n"
            'clean-app = "clean_app.__main__:main"\n'
        ),
        "README.md": (
            "# Clean App\n\n"
            "A small clean application used by the Sentinel test suite.\n\n"
            "## Usage\n\n"
            "Run `clean-app` from the project root.\n\n"
            "## Testing\n\n"
            "Run `python -m pytest`.\n"
        ),
        "LICENSE": "MIT License\n\nCopyright (c) Sentinel tests\n",
        ".gitignore": "__pycache__/\n.sentinel/\n",
        "poetry.lock": "",
        ".github/workflows/ci.yml": "name: ci\non: [push]\njobs:\n  test:\n    runs-on: ubuntu-latest\n",
        "src/clean_app/__init__.py": '"""Clean app package."""\n\n__version__ = "1.0.0"\n',
        "src/clean_app/core.py": "def add(left: int, right: int) -> int:\n    return left + right\n",
        "src/clean_app/__main__.py": (
            "from .core import add\n\n\n"
            "def main() -> int:\n"
            "    print(add(1, 2))\n"
            "    return 0\n\n\n"
            'if __name__ == "__main__":\n'
            "    raise SystemExit(main())\n"
        ),
        "tests/test_core.py": (
            "from clean_app import __version__\n"
            "from clean_app.__main__ import main\n"
            "from clean_app.core import add\n\n\n"
            "def test_add() -> None:\n"
            "    assert add(1, 2) == 3\n\n\n"
            "def test_version() -> None:\n"
            "    assert __version__\n"
        ),
        "tests/test_main.py": (
            "from clean_app.__main__ import main\n\n\n"
            "def test_main() -> None:\n"
            "    assert main() == 0\n"
        ),
    }


def messy_files() -> dict[str, str]:
    huge = "".join(f"value_{index} = {index}\n" for index in range(620))
    tangled = ["def tangled(value):", "    score = 0"]
    for index in range(30):
        tangled.append(f"    if value > {index}:")
        tangled.append("        score += 1")
    tangled.append("    return score")
    long_function = ["def long_one(value):", "    total = 0"]
    for index in range(120):
        long_function.append(f"    total += value + {index}")
    long_function.append("    return total")
    return {
        "big.py": huge,
        "complex.py": "\n".join(tangled) + "\n",
        "long.py": "\n".join(long_function) + "\n",
        "broken.py": "def broken(:\n    return 1\n",
    }


def docs_only_files() -> dict[str, str]:
    return {
        "README.md": "# Docs Only\n\nThis repository contains documentation and no source code.\n",
        "docs/guide.md": "# Guide\n\nSee the [README](../README.md).\n",
    }


def make_clean(root: Path) -> Path:
    return write_project(root, clean_files())


def make_messy(root: Path) -> Path:
    return write_project(root, messy_files())


def make_docs_only(root: Path) -> Path:
    return write_project(root, docs_only_files())


def scan(
    root: Path,
    *,
    rules: Mapping[str, Any] | None = None,
    include_docs: bool = False,
    full: bool = False,
) -> dict[str, Any]:
    effective = rules or load_rules(root)
    return scan_project(root, rules=effective, include_docs=include_docs, full=full, quiet=True)


def merge_rules(overrides: Mapping[str, Any]) -> dict[str, Any]:
    return deep_merge(load_rules(), overrides)
