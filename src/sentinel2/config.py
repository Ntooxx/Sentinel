"""Defaults and configuration loading for Sentinel 2.0.

Thresholds live here once. A project may override them with
``config/quality_rules.json``; the defaults below are merged with that file.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

from .utils import deep_merge

DEFAULT_RULES: dict[str, Any] = {
    "rules_version": "quality-rules-2",
    "calibrated": False,
    "analysis_version": 9,
    "schema_version": 1,
    "retention_scans": 20,
    "top_findings": 5,
    "max_workers": 32,
    "max_file_size": 5 * 1024 * 1024,
    "analyze_full_limit": 1024 * 1024,
    "binary_probe_bytes": 8192,
    # How much a finding moves its dimension, scaled by how much we trust it and
    # how much of production code it affects. Findings are always reported; these
    # factors only decide how much they cost the score.
    "confidence_penalty_factor": {"high": 1.0, "medium": 0.65, "low": 0.35},
    "context_penalty_factor": {"live": 1.0, "fixture": 0.1},
    # A Python file with no functions and no classes that is mostly literal data
    # (generated tables, benchmark payloads) is data, not maintainable source.
    "data_blob_min_lines": 1000,
    # Population offset used when converting an affected count into a share.
    # See quality._dimension_penalties: it keeps small repositories from being
    # punished for a single problem and large ones from being forgiven theirs.
    "penalty_reference_scale": 50,
    # A family covering a population at or above this many files is judged on
    # that population alone, without the reference-scale offset. The offset is
    # what protects a five-file repository from one bad function; applied to a
    # 2341-file test suite it made 84 very large test files indistinguishable
    # from three, because 84 / (2341 + 150) is a 3% share that the half-share
    # curve flattened to nothing.
    "penalty_population_floor": 100,
    # A repository with an assessed dimension that genuinely fails is not good
    # overall. Below this score the weakest dimension pulls the overall toward it.
    "weak_dimension_threshold": 70,
    "weak_dimension_pull": 0.5,
    # Top grades imply evidence of sustained engineering quality. A trivially
    # small codebase cannot demonstrate that, so it is held below A+. Anything
    # with real size is scored on its merits; capping harder than this pins every
    # mid-sized repository to one grade and destroys the ranking.
    "grade_ceiling": [
        {"min_analysed_lines": 0, "max_grade": "A"},
        {"min_analysed_lines": 2000, "max_grade": "A+"},
    ],
    # The overall quality claim is a coarse band, not a letter grade. Five of the
    # six dimensions saturate near A+ on maintained code, so a fine overall grade
    # was manufacturing resolution the evidence does not contain. Bands allow
    # ties: two repositories can both be "maintained" without being ranked.
    # Thresholds are score floors, highest first.
    "bands": [
        {"min_score": 80, "label": "maintained"},
        {"min_score": 70, "label": "adequate"},
        {"min_score": 60, "label": "weak"},
        {"min_score": 0, "label": "unmaintained"},
    ],
    # Calibrated against config/calibration_corpus.json. Test depth is the most
    # reliable static signal of engineering quality, so it carries the largest
    # share; documentation and architecture rarely separate repositories and are
    # weighted down accordingly.
    "weights": {
        "architecture": 0.15,
        "maintainability": 0.25,
        "tests": 0.30,
        "docs": 0.10,
        "dependencies": 0.10,
        "security": 0.10,
    },
    "thresholds": {
        "file_lines_medium": 500,
        "file_lines_high": 2000,
        "file_size_medium": 100 * 1024,
        "file_size_high": 250 * 1024,
        "function_lines": 100,
        "complexity_medium": 15,
        "complexity_high": 25,
        "complexity_peak_reference": 50,
        "todo_density_per_1000_lines": 8.0,
        "todo_count_min": 5,
        "test_file_lines": 1000,
        "test_todo_count": 10,
        "readme_large_bytes": 50 * 1024,
        "readme_unstructured_lines": 200,
        "readme_placeholder_min": 1,
        "test_ratio": {
            "library": 0.35,
            "framework_library": 0.35,
            "application": 0.40,
            "cli": 0.35,
            "web_app": 0.40,
            "monorepo": 0.30,
            "default": 0.30,
        },
        "min_source_files_for_ratio": 5,
        "source_files_tiny": 20,
        "arch_load_modules": 60,
        "arch_load_half": 120,
        "hotspot_count": 10,
        "hotspot_no_test_count": 5,
        "churn_days": 90,
        "churn_commits": 500,
        "churn_min": 3,
        "large_excluded_count": 100,
        "security_max_findings": 5,
        "broken_links_max": 10,
        "public_surface_min_exports": 1,
    },
    "severity_by_rule": {
        "ARCH001": "medium",
        "ARCH002": "medium",
        "ARCH003": "low",
        "ARCH004": "low",
        "ARCH005": "medium",
        "MNT001": "medium",
        "MNT002": "high",
        "MNT003": "medium",
        "MNT004": "high",
        "MNT005": "medium",
        "MNT006": "medium",
        "MNT007": "high",
        "MNT008": "low",
        "MNT009": "high",
        "MNT010": "low",
        "TST001": "high",
        "TST002": "medium",
        "TST003": "medium",
        "TST004": "low",
        "TST005": "low",
        "TST006": "medium",
        "DOC001": "medium",
        "DOC002": "medium",
        "DOC003": "low",
        "DOC004": "low",
        "DOC005": "low",
        "DOC006": "info",
        "DEP001": "medium",
        "DEP002": "low",
        "DEP003": "low",
        "DEP004": "medium",
        "HYG001": "low",
        "HYG002": "low",
        "HYG003": "low",
        "HYG004": "info",
        "SEC001": "high",
        "SEC002": "medium",
        "SEC003": "medium",
        "HOT001": "medium",
        "HOT002": "medium",
        "HOT003": "low",
    },
    # Population each family saturates against. "count" means the family is a
    # defect whose severity does not depend on repository size (unparseable
    # files, leaked credentials, absent tests); the other entries measure how
    # much of that population is affected.
    "penalty_denominator": {
        "architecture": "source_files",
        "arch_load": "source_files",
        "oversized": "source_files",
        "complexity": "source_files",
        "todos": "count",
        "parse": "count",
        "encoding": "source_files",
        "missing_tests": "count",
        "test_coverage": "count",
        "test_mapping": "test_files",
        "test_hygiene": "test_files",
        "docs": "count",
        "dependencies": "count",
        "hygiene": "count",
        "secrets": "count",
        "security": "source_files",
        "hotspots": "source_files",
    },
    # Share at which a family reaches half its cap. 0 means the share is already
    # a proportion (test shortfall), so the cap is applied directly.
    "penalty_half_share": {
        "architecture": 0.10,
        "arch_load": 1.5,
        "oversized": 0.15,
        "complexity": 0.15,
        "todos": 1.0,
        "parse": 3.0,
        "encoding": 0.10,
        "missing_tests": 0.5,
        "test_coverage": 0.0,
        "test_mapping": 0.15,
        "test_hygiene": 0.20,
        "docs": 1.0,
        "dependencies": 1.0,
        "hygiene": 1.0,
        "secrets": 0.5,
        "security": 0.25,
        "hotspots": 0.20,
    },
    "penalty_families": {
        "architecture": ["ARCH001", "ARCH002", "ARCH003", "ARCH004"],
        "arch_load": ["ARCH005"],
        "oversized": ["MNT001", "MNT002", "MNT003", "MNT004"],
        "complexity": ["MNT005", "MNT006", "MNT007"],
        "todos": ["MNT008"],
        "parse": ["MNT009"],
        "encoding": ["MNT010"],
        "missing_tests": ["TST001"],
        "test_coverage": ["TST002"],
        "test_mapping": ["TST003", "TST006"],
        "test_hygiene": ["TST004", "TST005"],
        "docs": ["DOC001", "DOC002", "DOC003", "DOC004", "DOC005", "DOC006"],
        "dependencies": ["DEP001", "DEP002", "DEP003", "DEP004"],
        "hygiene": ["HYG001", "HYG002", "HYG003", "HYG004"],
        "secrets": ["SEC001"],
        "security": ["SEC002", "SEC003"],
        "hotspots": ["HOT001", "HOT002", "HOT003"],
    },
    # Maximum penalty per rule family within a dimension. These were the
    # blueprint's suggested values, widened during calibration: with a cap of 10
    # the architecture, docs and dependency dimensions could never drop below 90,
    # so 45% of the weighting was constant and grades did not discriminate.
    "penalty_caps": {
        "architecture": 20,
        "arch_load": 18,
        "oversized": 20,
        "complexity": 25,
        "todos": 5,
        "parse": 30,
        "encoding": 4,
        "missing_tests": 40,
        "test_coverage": 55,
        "test_mapping": 20,
        "test_hygiene": 5,
        "docs": 20,
        "dependencies": 15,
        "hygiene": 5,
        "secrets": 40,
        "security": 15,
        "hotspots": 5,
    },
    "family_by_rule": {
        "ARCH001": "architecture",
        "ARCH002": "architecture",
        "ARCH003": "architecture",
        "ARCH004": "architecture",
        "ARCH005": "arch_load",
        "MNT001": "oversized",
        "MNT002": "oversized",
        "MNT003": "oversized",
        "MNT004": "oversized",
        "MNT005": "complexity",
        "MNT006": "complexity",
        "MNT007": "complexity",
        "MNT008": "todos",
        "MNT009": "parse",
        "MNT010": "encoding",
        "TST001": "missing_tests",
        "TST002": "test_coverage",
        "TST003": "test_mapping",
        "TST004": "test_hygiene",
        "TST005": "test_hygiene",
        "TST006": "test_mapping",
        "DOC001": "docs",
        "DOC002": "docs",
        "DOC003": "docs",
        "DOC004": "docs",
        "DOC005": "docs",
        "DOC006": "docs",
        "DEP001": "dependencies",
        "DEP002": "dependencies",
        "DEP003": "dependencies",
        "DEP004": "dependencies",
        "HYG001": "hygiene",
        "HYG002": "hygiene",
        "HYG003": "hygiene",
        "HYG004": "hygiene",
        "SEC001": "secrets",
        "SEC002": "security",
        "SEC003": "security",
        "HOT001": "hotspots",
        "HOT002": "hotspots",
        "HOT003": "hotspots",
    },
    "exclude": {
        "default_dirs": [
            ".git",
            ".sentinel",
            "node_modules",
            "__pycache__",
            ".venv",
            "venv",
            "dist",
            "build",
            "target",
            ".tox",
            ".nox",
            ".mypy_cache",
            ".ruff_cache",
            ".pytest_cache",
            ".idea",
            ".vscode",
            "coverage",
            "htmlcov",
            "sentinel-url-reports",
            ".next",
            ".nuxt",
            ".cache",
            # Archived, no-longer-maintained code carries no maintenance cost and
            # should not drive a quality score, for the same reason dist/build/
            # target do not. Sentinel keeps its own retired 1.x tree here.
            "legacy",
        ],
        "vendor_dirs": [
            "vendor",
            "vendors",
            "third_party",
            "third-party",
            "thirdparty",
            "external",
            "extern",
            "deps",
            "bower_components",
            "site-packages",
            "node_modules",
            ".yarn",
        ],
        "generated_dirs": ["generated", "gen", "dist", "build", "target", "out", "coverage", "htmlcov", ".next", ".nuxt"],
        "example_dirs": ["docs_src", "examples", "example", "samples", "sample", "demos"],
        "translation_dirs": ["translations", "translation", "i18n", "locales", "locale", "lang"],
        "generated_patterns": [
            "*.min.js",
            "*.min.css",
            "*.map",
            "*_pb2.py",
            "*_pb2_grpc.py",
            "*.g.dart",
            "*.freezed.dart",
            "*.designer.cs",
            "*.generated.*",
        ],
        "translation_locales": [
            "ar",
            "bg",
            "bn",
            "ca",
            "cs",
            "da",
            "de",
            "el",
            "es",
            "et",
            "fa",
            "fi",
            "fr",
            "he",
            "hi",
            "hr",
            "hu",
            "id",
            "it",
            "ja",
            "ka",
            "kk",
            "ko",
            "lt",
            "lv",
            "ms",
            "nl",
            "no",
            "pl",
            "pt",
            "ro",
            "ru",
            "sk",
            "sl",
            "sr",
            "sv",
            "th",
            "tr",
            "uk",
            "ur",
            "vi",
            "zh",
        ],
        "canonical_doc_names": [
            "README.md",
            "README.rst",
            "README.txt",
            "CONTRIBUTING.md",
            "ARCHITECTURE.md",
            "SECURITY.md",
            "CHANGELOG.md",
        ],
    },
    "manifest_names": [
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "requirements.txt",
        "requirements-dev.txt",
        "Pipfile",
        "package.json",
        "Cargo.toml",
        "go.mod",
        "pom.xml",
        "build.gradle",
        "composer.json",
        "Gemfile",
        "mix.exs",
        "CMakeLists.txt",
    ],
    "lockfile_names": [
        "poetry.lock",
        "Pipfile.lock",
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "bun.lockb",
        "Cargo.lock",
        "go.sum",
        "composer.lock",
        "Gemfile.lock",
        "mix.lock",
    ],
    "license_names": ["LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING", "COPYING.md", "NOTICE"],
    "ci_paths": [
        ".github/workflows",
        ".gitlab-ci.yml",
        ".travis.yml",
        "azure-pipelines.yml",
        "Jenkinsfile",
        ".circleci",
        "bitbucket-pipelines.yml",
    ],
    "archetype_overrides": {
        "library": {"description": "Reusable package with no runtime entry point"},
        "cli": {"description": "Command-line application"},
        "web_app": {"description": "Web service or application"},
        "monorepo": {"description": "Multiple packages or applications"},
        "docs_only": {"description": "Documentation or data repository"},
        "unknown": {"description": "Unclassified repository"},
    },
    "limitations": [
        "Security checks are basic pattern checks only; they are not a security audit.",
        "Documentation content is read only for README unless --include-docs is used.",
        "Churn requires a git repository and recent history.",
        "Non-Python languages use conservative heuristics, not deep semantic analysis.",
    ],
}


def prepare_rules(rules: dict[str, Any]) -> dict[str, Any]:
    """Attach cached sets/regex-free lookups used on every classified path."""

    exclude = rules.get("exclude", {})
    prepared: dict[str, frozenset[str]] = {}
    if isinstance(exclude, dict):
        for key, values in exclude.items():
            if isinstance(values, list) and values and all(isinstance(item, str) for item in values):
                prepared[key] = frozenset(item.lower() for item in values)
    rules["_prepared_sets"] = prepared
    return rules


def load_rules(project_root: Path | None = None) -> dict[str, Any]:
    """Load defaults plus an optional project-level rules file."""

    rules = copy.deepcopy(DEFAULT_RULES)
    candidates: list[Path] = [Path(__file__).with_name("quality_rules.json")]
    if project_root is not None:
        candidates.append(project_root / "config" / "quality_rules.json")
        candidates.append(project_root / ".sentinel" / "quality_rules.json")
    env_path = os.environ.get("SENTINEL_RULES")
    if env_path:
        candidates.append(Path(env_path))

    for candidate in candidates:
        try:
            if not candidate.is_file():
                continue
            payload = json.loads(candidate.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                rules = deep_merge(rules, payload)
        except (OSError, ValueError):
            continue
    return prepare_rules(rules)


def write_default_rules(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(DEFAULT_RULES, indent=2) + "\n", encoding="utf-8")
