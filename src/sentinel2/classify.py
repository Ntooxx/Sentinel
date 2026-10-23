"""File roles, languages and repository archetypes."""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

LANGUAGE_BY_EXT: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".pyx": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".hxx": "cpp",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".scala": "scala",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".ps1": "powershell",
    ".lua": "lua",
    ".dart": "dart",
    ".md": "markdown",
    ".markdown": "markdown",
    ".rst": "rst",
    ".adoc": "asciidoc",
    ".asciidoc": "asciidoc",
    ".txt": "text",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".scss": "css",
    ".sass": "css",
    ".less": "css",
    ".json": "json",
    ".jsonc": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".ini": "ini",
    ".cfg": "ini",
    ".xml": "xml",
    ".sql": "sql",
    ".env": "dotenv",
    ".graphql": "graphql",
    ".proto": "protobuf",
    ".vue": "vue",
    ".svelte": "svelte",
}

SPECIAL_NAMES: dict[str, tuple[str, str]] = {
    "dockerfile": ("dockerfile", "config"),
    "makefile": ("makefile", "config"),
    "cmakelists.txt": ("cmake", "config"),
    "gemfile": ("ruby", "config"),
    "rakefile": ("ruby", "config"),
    "pipfile": ("toml", "config"),
    "go.mod": ("go", "config"),
    "go.sum": ("text", "config"),
}

SOURCE_LANGUAGES = {
    "python",
    "javascript",
    "typescript",
    "go",
    "rust",
    "c",
    "cpp",
    "java",
    "kotlin",
    "csharp",
    "ruby",
    "php",
    "swift",
    "scala",
    "shell",
    "powershell",
    "lua",
    "dart",
    "vue",
    "svelte",
    "sql",
    "graphql",
    "protobuf",
}

DOC_LANGUAGES = {"markdown", "rst", "asciidoc", "text"}
CONFIG_LANGUAGES = {"json", "yaml", "toml", "ini", "xml", "dotenv", "dockerfile", "makefile", "cmake"}

TEST_DIR_NAMES = {"test", "tests", "__tests__", "spec", "specs", "testing"}
TEST_FILE_RE = re.compile(
    r"(?i)(?:^test_.+\.(?:py|js|jsx|ts|tsx|mjs|cjs)$|.+_test\.(?:py|go|rb|exs)$|"
    r".+\.(?:test|spec)\.(?:js|jsx|ts|tsx|mjs|cjs)$|^conftest\.py$)"
)
FIXTURE_DIR_NAMES = {
    "test",
    "tests",
    "__tests__",
    "testing",
    "spec",
    "specs",
    "fixture",
    "fixtures",
    "testdata",
    "test_data",
    "test-data",
    "cert",
    "certs",
    "certificates",
    "pki",
    "example",
    "examples",
    "sample",
    "samples",
    "snippet",
    "snippets",
    "mock",
    "mocks",
    "stub",
    "stubs",
    "fake",
    "fakes",
    "dummy",
    "dummies",
    "demo",
    "demos",
    "benchmark",
    "benchmarks",
}
FIXTURE_FILE_RE = re.compile(
    r"(?i)(?:^test_|_test\.|_test$|(?:^|[_\-.])(?:fixture|sample|example|dummy|mock|fake|stub|demo|cert|certs)(?:[_\-.]|$))"
)
TRANSLATION_SUFFIX_RE = re.compile(
    r"(?i)\.([a-z]{2,3}(?:[-_][a-z]{2,4})?)\.(md|markdown|rst|adoc|asciidoc|txt)$"
)
TRANSLATION_NAME_RE = re.compile(
    r"(?i)^(?:readme|index|guide|faq|changelog|contributing)[_-]([a-z]{2,3}(?:[-_][a-z]{2,4})?)\.(md|rst|txt)$"
)

FRAMEWORK_NAMES = {
    "fastapi",
    "flask",
    "django",
    "starlette",
    "express",
    "koa",
    "nestjs",
    "next",
    "nuxt",
    "sveltekit",
    "rails",
    "laravel",
    "spring",
    "quarkus",
    "micronaut",
}
WEB_FRAMEWORK_DEPS = {
    "fastapi",
    "flask",
    "django",
    "starlette",
    "express",
    "koa",
    "next",
    "nuxt",
    "vue",
    "react-dom",
    "svelte",
    "@nestjs/core",
    "spring-boot",
    "actix-web",
    "axum",
}


@dataclass(frozen=True)
class Classification:
    language: str
    role: str
    is_source: bool = False
    is_test: bool = False
    is_doc: bool = False
    is_vendor: bool = False
    is_generated: bool = False
    is_config: bool = False
    is_translation: bool = False
    is_example: bool = False


@lru_cache(maxsize=200_000)
def _parts(rel_path: str) -> tuple[str, ...]:
    return tuple(part for part in rel_path.replace("\\", "/").split("/") if part)


@lru_cache(maxsize=64)
def _lower_set(values: tuple[str, ...]) -> frozenset[str]:
    return frozenset(str(item).lower() for item in values)


def _rule_set(rules: Mapping[str, Any], key: str) -> frozenset[str]:
    prepared = rules.get("_prepared_sets")
    if isinstance(prepared, Mapping) and key in prepared:
        return prepared[key]
    values = rules.get("exclude", {}).get(key, [])
    return _lower_set(tuple(str(item) for item in values))


@lru_cache(maxsize=64)
def _pattern_regex(patterns: tuple[str, ...]) -> re.Pattern[str] | None:
    if not patterns:
        return None
    return re.compile("|".join(f"(?:{fnmatch.translate(pattern)})" for pattern in patterns))


def _matches_any(path: str, patterns: Iterable[str]) -> bool:
    regex = _pattern_regex(tuple(patterns))
    return bool(regex and regex.match(path))


def _is_manifest_name(name: str) -> bool:
    lowered = name.lower()
    return (
        lowered in {
            "pyproject.toml",
            "setup.py",
            "setup.cfg",
            "pipfile",
            "package.json",
            "cargo.toml",
            "go.mod",
            "pom.xml",
            "build.gradle",
            "composer.json",
            "gemfile",
            "mix.exs",
            "cmakelists.txt",
        }
        or (lowered.startswith("requirements") and lowered.endswith(".txt"))
    )


def _is_config_path(rel_path: str, name: str) -> bool:
    lowered = name.lower()
    if _is_manifest_name(name):
        return True
    if lowered in {
        "tox.ini",
        "pytest.ini",
        "mypy.ini",
        "ruff.toml",
        ".ruff.toml",
        "pyrightconfig.json",
        "nexus.json",
        "tsconfig.json",
        "jsconfig.json",
        ".gitignore",
        ".gitattributes",
        ".editorconfig",
        ".pre-commit-config.yaml",
        "docker-compose.yml",
        "docker-compose.yaml",
        "compose.yml",
        "compose.yaml",
        ".env",
        "license",
        "licence",
        "copying",
        "notice",
    }:
        return True
    if lowered.startswith(".env."):
        return True
    if rel_path.startswith(".github/workflows/") and lowered.endswith((".yml", ".yaml")):
        return True
    return bool(rel_path.startswith(".github/actions/"))


def _locale_is_translation(locale: str, rules: Mapping[str, Any]) -> bool:
    normalized = locale.lower().replace("_", "-")
    if normalized in {"en", "en-us", "en-gb", "en-ca", "en-au"}:
        return False
    base = normalized.split("-")[0]
    return base in _rule_set(rules, "translation_locales")


def is_translation_path(rel_path: str, rules: Mapping[str, Any]) -> bool:
    parts = _parts(rel_path)
    lowered_parts = [part.lower() for part in parts]
    translation_dirs = _rule_set(rules, "translation_dirs")
    if any(part in translation_dirs for part in lowered_parts):
        return True

    if "docs" in lowered_parts:
        index = lowered_parts.index("docs")
        remainder = lowered_parts[index + 1 :]
        # FastAPI-style docs/<locale>/docs/... and docs/<locale>/...
        if remainder and _locale_is_translation(remainder[0], rules):
            return True

    name = parts[-1] if parts else ""
    suffix = TRANSLATION_SUFFIX_RE.search(name)
    if suffix and _locale_is_translation(suffix.group(1), rules):
        return True
    name_match = TRANSLATION_NAME_RE.search(name)
    return bool(name_match and _locale_is_translation(name_match.group(1), rules))


def is_example_path(rel_path: str, rules: Mapping[str, Any]) -> bool:
    part_names = _rule_set(rules, "example_dirs")
    parts = [part.lower() for part in _parts(rel_path)]
    if any(part in part_names for part in parts):
        return True
    # Python snippets under docs are examples, not package source.
    return bool("docs" in parts and parts[-1].endswith((".py", ".js", ".ts", ".go", ".rs")))


def is_vendor_path(rel_path: str, rules: Mapping[str, Any]) -> bool:
    part_names = _rule_set(rules, "vendor_dirs")
    return any(part.lower() in part_names for part in _parts(rel_path))


def is_generated_path(rel_path: str, rules: Mapping[str, Any]) -> bool:
    parts = [part.lower() for part in _parts(rel_path)]
    generated_dirs = _rule_set(rules, "generated_dirs")
    if any(part in generated_dirs for part in parts):
        return True
    patterns = _rule_set(rules, "generated_patterns")
    if not patterns:
        return False
    name = parts[-1] if parts else ""
    if not any(char in name for char in (".", "-")):
        return False
    return _matches_any(name, patterns)


def is_test_path(rel_path: str) -> bool:
    parts = [part.lower() for part in _parts(rel_path)]
    if any(part in TEST_DIR_NAMES for part in parts[:-1]):
        return True
    return bool(parts and TEST_FILE_RE.match(parts[-1]))


def is_fixture_path(rel_path: str) -> bool:
    """True when a path holds test data, certificates, samples or other fixtures.

    A secret-like match in fixture context is far more likely to be deliberate
    test material than a leaked credential, so it is reported but weighted down.
    Anything outside these contexts is treated as live code and scored fully.
    """

    parts = [part.lower() for part in _parts(rel_path)]
    if any(part in FIXTURE_DIR_NAMES for part in parts[:-1]):
        return True
    return bool(parts and FIXTURE_FILE_RE.search(parts[-1]))


def language_for_path(rel_path: str) -> str:
    name = rel_path.replace("\\", "/").rsplit("/", 1)[-1]
    lowered = name.lower()
    if lowered in SPECIAL_NAMES:
        return SPECIAL_NAMES[lowered][0]
    if lowered.startswith("dockerfile."):
        return "dockerfile"
    if lowered in {"makefile", "gnumakefile"}:
        return "makefile"
    if lowered.endswith(".d.ts"):
        return "typescript"
    dot = lowered.rfind(".")
    suffix = lowered[dot:] if dot > 0 else ""
    return LANGUAGE_BY_EXT.get(suffix, "other")


def classify_path(rel_path: str, rules: Mapping[str, Any]) -> Classification:
    normalized = rel_path.replace("\\", "/")
    parts = _parts(normalized)
    name = parts[-1] if parts else ""
    language = language_for_path(normalized)

    if is_generated_path(normalized, rules):
        return Classification(language=language, role="generated", is_generated=True, is_config=language in CONFIG_LANGUAGES)
    if is_vendor_path(normalized, rules):
        return Classification(language=language, role="vendor", is_vendor=True)
    if is_translation_path(normalized, rules):
        return Classification(language=language, role="doc_translation", is_doc=True, is_translation=True)
    if is_example_path(normalized, rules):
        return Classification(language=language, role="example", is_example=True)
    if is_test_path(normalized):
        return Classification(language=language, role="test", is_test=True)
    if _is_config_path(normalized, name):
        return Classification(language=language, role="config", is_config=True)

    lowered = name.lower()
    is_readme = lowered.startswith("readme") or lowered in {
        "contributing.md",
        "architecture.md",
        "security.md",
        "changelog.md",
        "code_of_conduct.md",
        "code-of-conduct.md",
    }
    if language in DOC_LANGUAGES or is_readme or "docs" in {part.lower() for part in parts[:-1]}:
        return Classification(language=language, role="doc", is_doc=True)

    if language in SOURCE_LANGUAGES:
        return Classification(language=language, role="source", is_source=True)

    return Classification(language=language, role="other")


def manifest_kind(rel_path: str) -> str | None:
    name = rel_path.replace("\\", "/").rsplit("/", 1)[-1].lower()
    if name == "pyproject.toml":
        return "pyproject"
    if name == "setup.py" or name == "setup.cfg" or name == "pipfile":
        return "python"
    if name.startswith("requirements") and name.endswith(".txt"):
        return "requirements"
    if name == "package.json":
        return "npm"
    if name == "cargo.toml":
        return "cargo"
    if name == "go.mod":
        return "go"
    if name == "pom.xml":
        return "maven"
    if name in {"build.gradle", "build.gradle.kts"}:
        return "gradle"
    if name == "composer.json":
        return "composer"
    if name == "gemfile":
        return "gem"
    if name == "mix.exs":
        return "mix"
    if name == "cmakelists.txt":
        return "cmake"
    return None


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return getattr(row, key, default)


def _entry_point_evidence(paths: Iterable[str], manifest_meta: Mapping[str, Any]) -> list[str]:
    evidence: list[str] = []
    path_set = set(paths)
    for candidate in (
        "src/main.py",
        "src/__main__.py",
        "main.py",
        "app.py",
        "cli.py",
        "manage.py",
        "index.js",
        "src/index.js",
        "src/main.rs",
        "main.go",
        "cmd/main.go",
    ):
        if candidate in path_set:
            evidence.append(candidate)
    for path in sorted(path_set):
        if path.endswith("/__main__.py") or path.endswith("/main.go") or path.endswith("/main.rs"):
            evidence.append(path)
    if manifest_meta.get("has_scripts"):
        evidence.append(str(manifest_meta.get("manifest_path") or "manifest scripts"))
    if manifest_meta.get("bin"):
        evidence.append(str(manifest_meta.get("manifest_path") or "package bin"))
    return sorted(dict.fromkeys(evidence), key=lambda path: (path.count("/"), path))


def detect_archetype(
    _root: Path,
    file_rows: Iterable[Any],
    manifest_meta: Mapping[str, Any] | None = None,
) -> str:
    """Classify the repository shape from indexed file roles and manifest facts."""

    manifest_meta = manifest_meta or {}
    rows = list(file_rows)
    paths = [str(_row_get(row, "path", "")) for row in rows]
    source_files = sum(1 for row in rows if int(_row_get(row, "is_source", 0) or 0))
    doc_files = sum(1 for row in rows if int(_row_get(row, "is_doc", 0) or 0))
    if source_files == 0:
        return "docs_only" if doc_files else "unknown"

    lower_paths = [path.lower() for path in paths]
    top_dirs = {path.split("/", 1)[0] for path in lower_paths if "/" in path}
    manifest_dirs = {
        path.rsplit("/", 1)[0] for path in lower_paths if path.endswith(("pyproject.toml", "package.json", "cargo.toml", "go.mod"))
    }
    if {"packages", "apps"} & top_dirs or len(manifest_dirs) >= 3:
        return "monorepo"

    entry_points = _entry_point_evidence(paths, manifest_meta)
    name = str(manifest_meta.get("name") or "").lower()
    dependencies = {str(dep).lower().split("==")[0].split(">=")[0].split("[")[0].strip() for dep in manifest_meta.get("dependencies", [])}
    if name in FRAMEWORK_NAMES:
        return "framework_library"
    if entry_points and any(
        path.endswith(("__main__.py", "main.py", "cli.py", "main.go", "main.rs", "index.js"))
        for path in entry_points
    ):
        return "cli"
    if manifest_meta.get("has_scripts") or manifest_meta.get("bin"):
        return "cli"
    if dependencies & WEB_FRAMEWORK_DEPS:
        return "web_app"
    if manifest_meta.get("manifest_kind") in {"pyproject", "python", "requirements", "npm", "cargo", "go"}:
        return "library"
    return "application"


def entry_points(_root: Path, paths: Iterable[str], manifest_meta: Mapping[str, Any]) -> list[str]:
    """Public helper used by the report architecture section."""

    return _entry_point_evidence(paths, manifest_meta)
