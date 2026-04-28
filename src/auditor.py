from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Dict, List, Optional

from classify import (
    ARCHETYPE_APP,
    ARCHETYPE_CLI_SERVER,
    ARCHETYPE_DESKTOP_APP,
    ARCHETYPE_BROWSER_ENGINE,
    ARCHETYPE_FRAMEWORK_LIBRARY,
    ARCHETYPE_MONOREPO,
    ARCHETYPE_TEST_SUITE,
    ARCHETYPE_VENDOR_HEAVY,
    ARCHETYPE_GENERATED_HEAVY,
    ARCHETYPE_DOCUMENTATION_HEAVY,
    ARCHETYPE_MIXED_LANGUAGE,
    FileClassification,
    classifyFile,
    classifyComponentRole,
    classifyLargeFilePolicy,
    classifyRiskSurface,
    classifyRoleLabel,
    classifySurface,
    is_under_monorepo_root,
    monorepo_component_key,
    riskFromScore,
    detectRepoArchetype,
)
from utils import DEFAULT_AUDIT_RULES, DEFAULT_PATTERNS, merge_dicts, now_iso, read_json, write_json

SCAN_CACHE_VERSION = 1

HTML_TAG_RE = re.compile(r"<[^>]*>")
MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")

_BAD_IDENTITY_TEXT = {
    "application logic, application logic",
    "application logic.",
    "application logic and",
    "it is organized around application logic",
    "appears to be c++ project and a test suite",
    "appears to be c++ project",
}

_BAD_SECTION_HEADINGS = {
    "install", "installation", "getting started", "quickstart", "quick start",
    "usage", "api", "api reference", "overview", "introduction",
    "contributing", "contributor", "license", "licensing",
    "documentation", "references", "resources", "support",
    "roadmap", "changelog", "release notes", "faq",
    "architecture", "design", "configuration", "setup",
    "development", "building", "build", "testing", "deploy",
    "sponsor", "sponsors", "sponsorship", "funding", "donate",
    "why", "why?", "getting help", "help", "support",
    "features", "requirements", "prerequisites", "dependencies",
    "screenshots", "showcase",
}

_BAD_IDENTITY_STARTS = {
    "<div", "<p ", "<p>", "<img", "<a ", "<a>", "<picture", "<svg",
    "<h1", "<table", "<span", "<section", "<br", "<hr",
}

_BLOCKED_README_TITLE_WORDS = {
    "sponsor", "sponsors", "sponsorship", "funding", "donate", "donation",
    "badge", "badges", "build status", "coverage", "downloads",
    "why", "why?", "getting started", "quick start", "installation",
    "contributing", "documentation", "overview", "introduction",
    "license", "licensing", "changelog", "release notes", "roadmap",
    "support", "usage", "api", "faq", "help", "community",
}

_KNOWN_REPO_NAMES = {
    "tensorflow": "TensorFlow",
    "ollama": "Ollama",
    "ladybird": "Ladybird",
    "rust": "Rust",
    "llvm": "LLVM",
    "llvm-project": "LLVM",
    "fastapi": "FastAPI",
    "kubernetes": "Kubernetes",
    "k8s.io": "Kubernetes",
    "flask": "Flask",
    "django": "Django",
    "spring": "Spring",
    "react": "React",
    "next.js": "Next.js",
    "pytorch": "PyTorch",
    "numpy": "NumPy",
    "pandas": "Pandas",
    "scikit-learn": "scikit-learn",
    "home-assistant": "Home Assistant",
    "home-assistant.io": "Home Assistant",
    "vite": "Vite",
    "express": "Express",
    "tailwindcss": "Tailwind CSS",
}

def _strip_html(text: str) -> str:
    if not text:
        return ""
    return HTML_TAG_RE.sub("", text).strip()

def _strip_markdown(text: str) -> str:
    """Strip markdown link/image/formatting syntax for identity purposes."""
    if not text:
        return ""
    # Remove markdown links: [text](url) -> text
    text = MARKDOWN_LINK_RE.sub(r"\1", text)
    # Remove bold/italic markers
    text = text.replace("**", "").replace("__", "").replace("*", "").replace("_", "")
    # Remove inline code backticks
    text = text.replace("`", "")
    return text.strip()

def _is_bad_identity_text(value: str) -> bool:
    if not value or not value.strip():
        return True
    v = value.strip()
    vl = v.lower()
    if vl.startswith("<"):
        return True
    if vl.startswith("[![") or vl.startswith("!["):
        return True
    if vl.startswith("`") and vl.endswith("`"):
        return True
    if "|" in vl and ("--" in vl or "**" in vl or "[" in vl):
        return True
    if "align=\"center\"" in vl or "align='center'" in vl:
        return True
    if 'src="http' in vl or "src='http" in vl:
        return True
    if "raw.githubusercontent.com" in vl:
        return True
    if "shields.io" in vl:
        return True
    if "travis-ci." in vl or "github-ci" in vl or "codecov" in vl:
        return True
    if "badge" in vl and (".svg" in vl or ".png" in vl):
        return True
    for bad in _BAD_IDENTITY_TEXT:
        if bad in vl:
            return True
    for prefix in _BAD_IDENTITY_STARTS:
        if vl.startswith(prefix):
            return True
    if vl.startswith("<!--") and "-->" in vl:
        return True
    if vl in _BAD_SECTION_HEADINGS:
        return True
    # Reject decorative dash/separator-only text
    if all(c in "-_=~*#." for c in vl):
        return True
    return False


@dataclass
class Checkpoint:
    timestamp: str
    file_hashes: Dict[str, str]
    file_list: List[str]
    summary: str
    issues: List[Dict[str, Any]]
    metrics: Dict[str, Any]
    health_score: int
    health_score_data: Optional[Dict[str, Any]] = None


class ProjectAuditor:
    """Audits project state and tracks changes between checkpoints."""

    def __init__(
        self,
        root_dir: str,
        checkpoint_path: str = "checkpoints.json",
        audit_rules_path: Optional[str] = None,
        patterns_path: Optional[str] = None,
    ):
        self.root_dir = Path(root_dir).resolve()
        self.checkpoint_path = Path(checkpoint_path).resolve()
        self.rules = self._load_rules(audit_rules_path)
        self.patterns_config = self._load_patterns(patterns_path)
        self.git_context = self._detect_git_context()
        self.checkpoints = self._load_checkpoints()
        self.scan_cache_path = self.checkpoint_path.with_name("scan_cache.json")
        self.scan_cache = self._load_scan_cache()
        self.last_cache_stats: Dict[str, int] = {"hits": 0, "misses": 0, "writes": 0}
        self.last_discovery_mode = "walk"

    def _load_rules(self, audit_rules_path: Optional[str]) -> Dict[str, Any]:
        if not audit_rules_path:
            return dict(DEFAULT_AUDIT_RULES)
        loaded = read_json(audit_rules_path, DEFAULT_AUDIT_RULES)
        if isinstance(loaded, dict):
            return merge_dicts(DEFAULT_AUDIT_RULES, loaded)
        return dict(DEFAULT_AUDIT_RULES)

    def _load_patterns(self, patterns_path: Optional[str]) -> Dict[str, Any]:
        if not patterns_path:
            return dict(DEFAULT_PATTERNS)
        loaded = read_json(patterns_path, DEFAULT_PATTERNS)
        if isinstance(loaded, dict):
            return merge_dicts(DEFAULT_PATTERNS, loaded)
        return dict(DEFAULT_PATTERNS)

    def _load_checkpoints(self) -> List[Dict[str, Any]]:
        loaded = read_json(self.checkpoint_path, [])
        return loaded if isinstance(loaded, list) else []

    def _save_checkpoints(self) -> None:
        write_json(self.checkpoint_path, self.checkpoints)

    def _load_scan_cache(self) -> Dict[str, Any]:
        loaded = read_json(self.scan_cache_path, {})
        return loaded if isinstance(loaded, dict) else {}

    def _save_scan_cache(self) -> None:
        write_json(self.scan_cache_path, self.scan_cache)

    def compute_file_hash(self, filepath: str) -> str:
        hasher = hashlib.sha256()
        try:
            with open(filepath, "rb") as handle:
                for chunk in iter(lambda: handle.read(8192), b""):
                    hasher.update(chunk)
        except (OSError, IOError):
            return "unreadable"
        return hasher.hexdigest()

    def scan_directory(
        self,
        ignore_dirs: List[str],
        extensions: List[str],
        max_size: int,
        ignore_paths: Optional[List[str]] = None,
        fast_mode: bool = False,
        analysis_sample_bytes: int = 65_536,
        use_git_discovery: bool = False,
    ) -> Dict[str, Dict[str, Any]]:
        """Scan the project directory and return per-file metadata."""
        log = logging.getLogger("sentinel")
        t0 = perf_counter()

        ignored_dirs = {entry.lower() for entry in ignore_dirs}
        allowed_entries = {entry.lower() for entry in extensions}
        ignored_paths = [Path(path).resolve() for path in (ignore_paths or [])]
        files: Dict[str, Dict[str, Any]] = {}
        next_cache: Dict[str, Any] = {}
        cache_stats = {"hits": 0, "misses": 0, "writes": 0}
        misses: list[tuple[Path, str, os.stat_result, str, str]] = []

        for filepath in self._iter_candidate_files(ignored_dirs, allowed_entries, ignored_paths, use_git_discovery):
            try:
                stat = filepath.stat()
                if stat.st_size > max_size:
                    continue
            except OSError:
                continue

            rel_path = filepath.relative_to(self.root_dir).as_posix()
            ext = filepath.suffix.lower()
            cache_key = f"{rel_path}|{stat.st_size}|{getattr(stat, 'st_mtime_ns', int(stat.st_mtime * 1_000_000_000))}"
            cached = self.scan_cache.get(cache_key)
            if isinstance(cached, dict) and self._cache_entry_matches_mode(cached, fast_mode, analysis_sample_bytes):
                info = dict(cached.get("info", {}))
                if info:
                    files[rel_path] = info
                    next_cache[cache_key] = cached
                    cache_stats["hits"] += 1
                    continue
            misses.append((filepath, rel_path, stat, ext, cache_key))

        cache_stats["misses"] = len(misses)
        workers = min(32, max(1, (os.cpu_count() or 4) * 2))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            for rel_path, info, cache_key, cache_entry in executor.map(
                lambda item: self._analyze_file_for_scan(item, fast_mode, analysis_sample_bytes),
                misses,
            ):
                if not rel_path or not info:
                    continue
                files[rel_path] = info
                next_cache[cache_key] = cache_entry
                cache_stats["writes"] += 1
        self.scan_cache = next_cache
        self.last_cache_stats = cache_stats
        self._save_scan_cache()
        t1 = perf_counter()
        log.debug(
            "Scan timing: discovery+analysis=%.3fs hits=%d misses=%d files=%d",
            t1 - t0, cache_stats["hits"], cache_stats["misses"], len(files),
        )
        return dict(sorted(files.items()))

    def _iter_candidate_files(
        self,
        ignored_dirs: set[str],
        allowed_entries: set[str],
        ignored_paths: List[Path],
        use_git_discovery: bool,
    ) -> List[Path]:
        if use_git_discovery:
            git_files = self._git_candidate_files(ignored_dirs, allowed_entries, ignored_paths)
            if git_files is not None:
                self.last_discovery_mode = "git"
                return git_files

        self.last_discovery_mode = "walk"
        candidates: list[Path] = []
        for root, dirs, filenames in os.walk(self.root_dir):
            root_path = Path(root).resolve()
            dirs[:] = [
                dirname
                for dirname in dirs
                if dirname.lower() not in ignored_dirs
                and not self._is_ignored_path(root_path / dirname, ignored_paths)
            ]

            for filename in filenames:
                filepath = root_path / filename
                if self._is_ignored_path(filepath, ignored_paths):
                    continue
                ext = filepath.suffix.lower()
                lower_name = filepath.name.lower()
                if allowed_entries and ext not in allowed_entries and lower_name not in allowed_entries:
                    continue
                candidates.append(filepath)
        return sorted(candidates, key=lambda path: path.relative_to(self.root_dir).as_posix())

    def _git_candidate_files(
        self,
        ignored_dirs: set[str],
        allowed_entries: set[str],
        ignored_paths: List[Path],
    ) -> Optional[List[Path]]:
        try:
            result = subprocess.run(
                ["git", "ls-files", "-co", "--exclude-standard"],
                cwd=self.root_dir,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode != 0:
            return None

        candidates: list[Path] = []
        for raw in result.stdout.splitlines():
            rel = raw.strip()
            if not rel:
                continue
            path = (self.root_dir / rel).resolve()
            if not path.is_file() or self._is_ignored_path(path, ignored_paths):
                continue
            parts_lower = [part.lower() for part in Path(rel).parts[:-1]]
            if any(part in ignored_dirs for part in parts_lower):
                continue
            ext = path.suffix.lower()
            lower_name = path.name.lower()
            if allowed_entries and ext not in allowed_entries and lower_name not in allowed_entries:
                continue
            candidates.append(path)
        return sorted(candidates, key=lambda path: path.relative_to(self.root_dir).as_posix())

    def _analyze_file_for_scan(
        self,
        item: tuple[Path, str, os.stat_result, str, str],
        fast_mode: bool,
        analysis_sample_bytes: int,
    ) -> tuple[str, Dict[str, Any], str, Dict[str, Any]]:
        filepath, rel_path, stat, ext, cache_key = item
        try:
            # Stream file: read only what we need for analysis, hash incrementally, count lines incrementally
            hasher = hashlib.sha256()
            line_count = 0
            analysis_buffer = bytearray()
            max_analysis = analysis_sample_bytes if fast_mode else stat.st_size

            with open(filepath, "rb") as handle:
                while True:
                    chunk = handle.read(65536)
                    if not chunk:
                        break
                    hasher.update(chunk)
                    line_count += chunk.count(b"\n")
                    if len(analysis_buffer) < max_analysis:
                        remaining = max_analysis - len(analysis_buffer)
                        analysis_buffer.extend(chunk[:remaining])

            # Handle final line if file doesn't end with newline
            if stat.st_size > 0:
                with open(filepath, "rb") as handle:
                    handle.seek(max(0, stat.st_size - 1))
                    last_byte = handle.read(1)
                    if last_byte and last_byte != b"\n":
                        line_count += 1

            file_hash = hasher.hexdigest()
            analysis_bytes = bytes(analysis_buffer)
            content = analysis_bytes.decode("utf-8", errors="ignore")

        except OSError:
            return "", {}, cache_key, {}

        imports = self._extract_imports(content, ext)
        summary = self._extract_summary(content, ext, filepath.name)
        symbols = self._extract_symbols(content, ext)

        metadata = self._extract_metadata(content, ext, filepath.name)
        metadata.update(self._extract_doc_metadata(content, ext, filepath.name))

        info = {
            "size": stat.st_size,
            "extension": ext or filepath.name,
            "line_count": line_count,
            "modified": now_iso() if not stat.st_mtime else self._mtime_to_iso(stat.st_mtime),
            "hash": file_hash,
            "has_main": self._detect_main(content, ext, filepath.name),
            "has_class": bool(re.search(r"\bclass\s+\w+", content)),
            "has_function": bool(
                re.search(r"\bdef\s+\w+|\bfunction\s+\w+|\bconst\s+\w+\s*=\s*\(", content)
            ),
            "imports": imports,
            "import_count": len(imports),
            "todo_count": self._count_action_markers(content),
            "analysis_truncated": fast_mode and stat.st_size > analysis_sample_bytes,
            "summary": summary,
            "symbols": symbols,
            "metadata": metadata,
        }
        cache_entry = {
            "version": SCAN_CACHE_VERSION,
            "mode": "fast" if fast_mode else "full",
            "analysis_sample_bytes": analysis_sample_bytes,
            "mtime_ns": getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000)),
            "size": stat.st_size,
            "info": info,
        }
        return rel_path, info, cache_key, cache_entry

    def _cache_entry_matches_mode(self, entry: Dict[str, Any], fast_mode: bool, analysis_sample_bytes: int) -> bool:
        if entry.get("version") != SCAN_CACHE_VERSION:
            return False
        mode = entry.get("mode")
        if mode == "full":
            return True
        return (
            fast_mode
            and mode == "fast"
            and int(entry.get("analysis_sample_bytes", 0) or 0) == int(analysis_sample_bytes)
        )

    def _is_ignored_path(self, candidate: Path, ignored_paths: List[Path]) -> bool:
        resolved = candidate.resolve()
        for ignored in ignored_paths:
            if resolved == ignored or ignored in resolved.parents:
                return True
        return False

    def _mtime_to_iso(self, mtime: float) -> str:
        return datetime.fromtimestamp(mtime).astimezone().isoformat(timespec="seconds")

    def _classify_path_context(self, filepath: str, info: Optional[Dict[str, Any]] = None) -> str:
        fc = classifyFile(filepath, info)
        if fc.isTest:
            return "test"
        if fc.isTestRunner:
            return "test"
        if fc.isFixture:
            return "test_data"
        if fc.isGeneratedSdk:
            return "generated_sdk"
        if fc.isGenerated:
            return "generated_sdk"
        if fc.isVendor:
            return "vendor_generated"
        if fc.isLocalization:
            return "localization_resource"
        if fc.isSpecification:
            return "specification_documentation"
        if fc.isDocumentation:
            return "documentation"
        if fc.isBuildTooling:
            return "build_tooling"
        if fc.isGenerator:
            return "generator"
        if fc.isEnvironmentSetup:
            return "environment"
        if fc.isDependencyLock:
            return "vendor_generated"
        if fc.isConfig:
            return "data_or_config"

        lower_path = filepath.replace("\\", "/").lower()
        parts = tuple(p.lower() for p in Path(filepath).parts)

        if "wpt-import" in lower_path or "fixtures" in lower_path:
            return "test_data"
        if any(part in {"tests", "test", "spec"} for part in parts):
            return "test"
        if "/meta/" in lower_path:
            if "/generators/" in lower_path:
                return "generator"
            if "/linters/" in lower_path:
                return "lint_tooling"
            if "/lagom/" in lower_path:
                if "/fuzzers/" in lower_path or "/fuzzer/" in lower_path:
                    return "lint_tooling"
                return "build_tooling"
            if "/cmake/" in lower_path:
                return "build_tooling"
            if "/utils/" in lower_path:
                return "build_tooling"
            name_lower = Path(filepath).name.lower()
            if name_lower.startswith("import") and "test" in name_lower:
                return "test"
            if name_lower.endswith(".sh") and "wpt" in name_lower:
                return "test"
            return "build_tooling"
        if "/libraries/libweb/" in lower_path:
            return "browser_engine"
        if "/libraries/libjs/" in lower_path:
            return "javascript_engine"
        if "/libraries/libmain/" in lower_path:
            return "runtime_entry"
        if "/libraries/libtest/" in lower_path:
            return "test_support"
        if parts and parts[0] == "libraries":
            return "first_party_source"
        if parts and parts[0] == "ak":
            return "core_utility"
        if parts and parts[0] in {"services", "ui", "cmd", "api", "server"}:
            return "runtime_source"
        if parts and parts[0] in {"utilities", "scripts", "tools"}:
            name_lower = Path(filepath).name.lower()
            if "test" in name_lower and "runner" in name_lower:
                return "test"
            return "tooling"
        if any(part in {"demo-vault", "demo-vault-v2", "demo", "samples", "examples"} for part in parts):
            return "test_data"
        # Framework/library repo second-level subdirectory recognition
        framework_sdk_dirs = {"core", "python", "compiler", "lite", "runtime", "api", "go", "java", "cc", "c", "tools"}
        if len(parts) >= 2 and parts[1] in framework_sdk_dirs:
            if parts[1] == "python":
                return "python_api"
            if parts[1] in {"core", "compiler", "lite", "runtime"}:
                return "runtime_source"
            return "first_party_source"
        return "application"

    def _component_key_for_path(self, filepath: str) -> Optional[str]:
        parts = tuple(Path(filepath).parts)
        if len(parts) < 2 or any(part.startswith(".") for part in parts[:-1]):
            if parts and parts[0] == ".devcontainer":
                return parts[0]
            return None
        return monorepo_component_key(filepath)

    def _component_role_for_context(self, key: str) -> str:
        return classifyComponentRole(key)

    def _is_runtime_hotspot_not_entry(self, filepath: str) -> bool:
        lower_path = filepath.replace("\\", "/").lower()
        name = Path(filepath).name.lower()
        stem = Path(filepath).stem.lower()
        runtime_hotspot_names = {
            "provider", "providers", "models", "model",
            "parsing", "parser", "cache", "caching",
            "session", "sessions", "middleware",
            "registry", "store", "storage",
            "database", "db", "connection",
            "service", "services",
            "handler", "handlers",
            "config", "configuration",
            "logging", "logger",
            "metrics", "telemetry",
            "plugin", "plugins",
            "extension", "extensions",
        }
        # build.rs is always build/tooling
        if name == "build.rs":
            return False
        # Provider/model/cache/session-style files are runtime hotspots, not entry points
        if stem in runtime_hotspot_names:
            return True
        return False

    def _categorize_entry_point(self, filepath: str, info: Any = None) -> str:
        fc = classifyFile(filepath, info)
        lower_path = filepath.replace("\\", "/").lower()
        parts = tuple(p.lower() for p in Path(filepath).parts)
        name = Path(filepath).name.lower()

        if fc.isTest or fc.isFixture:
            return "test"
        if fc.isTestRunner:
            return "test"
        if fc.isGeneratedSdk or fc.isGenerated:
            return "generator"
        if fc.isGenerator:
            return "generator"
        if fc.isBuildTooling:
            return "build"
        if fc.isEnvironmentSetup:
            return "environment"
        if fc.isDocumentation:
            return "documentation"
        if fc.isDependencyLock:
            return "build"
        if fc.isVendor:
            return "build"

        if any(part == ".devcontainer" for part in parts) or ".devcontainer" in lower_path:
            return "environment"
        if any(part in {"tests", "test", "spec", "e2e", "unittests", "unit"} for part in parts) or "libtest" in lower_path:
            return "test"
        if parts and parts[0] == "meta":
            if len(parts) > 1 and parts[1] == "generators":
                return "generator"
            return "build"

        # Assets, examples, and docs are NOT runtime entry points
        if any(part in {"assets", "asset", "static"} for part in parts):
            return "documentation"
        # Catch docs_src/, docs/, doc/ paths — these are doc examples, not runtime
        if any(part in {"docs", "doc", "documentation", "docs_src"} for part in parts):
            return "documentation"
        # Examples are example entry points, not primary runtime
        if any(part in {"examples", "example", "samples", "sample", "demo", "demos"} for part in parts):
            return "example"

        # API surfaces for framework/library repos
        if any(part in {"api", "apis"} for part in parts) and fc.isRuntimeSource:
            return "runtime_surface"

        if name in {"setup.py", "package.json", "pyproject.toml"}:
            return "packaging"

        if name == "build.rs" or name.startswith("build.") or name.startswith("install."):
            return "build"

        # Compiler/toolchain driver detection — LLVM-style clang/tools, llvm/tools
        if re.search(r"/tools/.+/main\.(cpp|cc|c)$", lower_path):
            return "build"
        if re.search(r"/tools/.+\.(cpp|cc|c)$", lower_path) and name.startswith(("clang", "llvm-", "lld", "llc", "opt", "bugpoint")):
            return "build"
        # Rust bootstrap, cargo, rustc drivers
        if "bootstrap/" in lower_path and name.startswith(("bootstrap", "configure")):
            return "build"
        if "src/tools/" in lower_path and name in {"cargo.rs", "rustc.rs", "rustdoc.rs"}:
            return "build"
        if "src/bootstrap/" in lower_path:
            return "build"

        if parts and parts[0] == "libraries" and len(parts) > 1 and parts[1] == "libmain":
            return "runtime"
        if parts and parts[0] in {"services", "ui"}:
            return "runtime"
        if parts and parts[0] == "libraries":
            return "runtime"
        if name in {"main.rs", "main.go", "main.ts", "index.ts", "index.js", "app.ts", "app.js", "server.ts", "server.js", "main.py", "server.py", "app.py", "cli.py"}:
            return "runtime"
        if re.search(r"/cmd/[^/]+/main\.(go|rs)$", lower_path):
            return "runtime"
        if "src-tauri/src/main.rs" in lower_path:
            return "runtime"
        if any(part in {"src", "source", "app", "cmd"} for part in parts) and name.startswith("main."):
            return "runtime"
        # Go pattern: cmd/<binary>/<binary>.go is also a runtime entry point
        if re.search(r"/([^/]+)/(\1)\.go$", lower_path):
            return "runtime"
        # Go pattern: any .go file with func main() in a cmd/ directory
        if re.search(r"/cmd/[^/]+/[^/]+\.go$", lower_path) and (info or {}).get("has_main", False):
            return "runtime"
        if parts and parts[0] == "utilities":
            return "tooling"
        return "tooling"

    def _categorize_hotspot(self, filepath: str, info: Dict[str, Any]) -> str:
        surface = classifyRiskSurface(filepath, info)
        surface_to_group = {
            "runtime": "runtime",
            "build_tooling": "build_tooling",
            "generator": "generator",
            "test_runner": "test_runner",
            "test_data": "test_data",
            "test": "test_runner",
            "documentation": "documentation",
            "vendor": "vendor",
            "generated": "build_tooling",
            "environment_setup": "build_tooling",
            "config": "build_tooling",
            "unknown": "runtime",
        }
        return surface_to_group.get(surface, "runtime")

    def _count_action_markers(self, content: str) -> int:
        count = 0
        marker_re = re.compile(r"\b(?:TODO|FIXME|HACK|XXX)\b\s*[:(-]", re.IGNORECASE)
        ignored_phrases = (
            "todo/fixme",
            "todo markers",
            "todo marker",
            "todo_count",
            "open_todos",
            "action markers",
        )
        for line in content.splitlines():
            lowered = line.lower()
            if any(phrase in lowered for phrase in ignored_phrases):
                continue
            if marker_re.search(line):
                count += 1
        return count

    def _detect_main(self, content: str, ext: str, filename: str) -> bool:
        if ext == ".py":
            return "if __name__ == \"__main__\"" in content or "if __name__ == '__main__'" in content
        if ext in {".js", ".ts"}:
            return "require.main === module" in content or "process.argv" in content
        if ext in {".sh", ".bash"}:
            return content.startswith("#!") or "main()" in content
        if ext in {".cpp", ".c", ".cc", ".cxx"}:
            return bool(re.search(r"\b(?:int|auto|void)\s+main\s*\(", content))
        if ext == ".go":
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith("func main("):
                    return True
            return False
        if ext == ".rs":
            # Only count as entry point if it's actually a standalone main function, not just "fn main(" inside a module
            # A true main.rs entry point has fn main() at module level, not indented inside impl blocks
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith("fn main(") and not stripped.startswith("fn main_"):
                    return True
            return False
        return False

    def _extract_imports(self, content: str, ext: str) -> List[str]:
        imports: List[str] = []
        if ext == ".py":
            imports.extend(re.findall(r"^\s*import\s+([\w.]+)", content, re.MULTILINE))
            imports.extend(re.findall(r"^\s*from\s+([\w.]+)\s+import", content, re.MULTILINE))
        elif ext in {".js", ".ts"}:
            imports.extend(
                re.findall(
                    r"^\s*import(?:.+from\s+)?[\"']([^\"']+)[\"']",
                    content,
                    re.MULTILINE,
                )
            )
            imports.extend(re.findall(r"require\(\s*[\"']([^\"']+)[\"']\s*\)", content))
        elif ext in {".cpp", ".c", ".h", ".hpp", ".cc", ".cxx", ".hh", ".hxx"}:
            # Match #include <header> or #include "header"
            imports.extend(re.findall(r'^\s*#include\s*[<"]([^>"]+)[>"]', content, re.MULTILINE))
        elif ext == ".go":
            imports.extend(re.findall(r'^\s*import\s+["\']([^"\']+)["\']', content, re.MULTILINE))
            imports.extend(re.findall(r'^\s*import\s*\(\s*$', content, re.MULTILINE))
        elif ext == ".rs":
            imports.extend(re.findall(r"^\s*use\s+([A-Za-z0-9_:]+)", content, re.MULTILINE))

        deduped = []
        seen = set()
        for entry in imports:
            if entry not in seen:
                seen.add(entry)
                deduped.append(entry)
        return deduped[:20]

    def _extract_summary(self, content: str, ext: str, filename: str) -> str:
        if ext in {".md", ".txt"}:
            found_title = False
            for raw_line in content.splitlines():
                line = raw_line.strip()
                if not line or line == "```":
                    continue
                if line.startswith("#"):
                    found_title = True
                    continue
                if not found_title:
                    continue
                # Skip raw HTML lines (badges, div align blocks, etc.)
                if _is_bad_identity_text(line):
                    continue
                # Skip lines that look like image URLs or markdown images
                if line.startswith("![") or line.startswith("[!"):
                    continue
                if 'src="http' in line.lower() or "src='http" in line.lower():
                    continue
                if "raw.githubusercontent.com" in line.lower():
                    continue
                if "shields.io" in line.lower():
                    continue
                # Skip markdown table rows (contain pipe + formatting chars)
                if "|" in line and ("--" in line or "**" in line or "[!" in line):
                    continue
                # Skip backtick-only or bold-only lines that are table artifacts
                if line.startswith("`") and line.endswith("`"):
                    continue
                # Skip short lines that are likely navigation/sidebar artifacts
                if len(line) < 15 and not line.endswith("."):
                    continue
                # Clean markdown link artifacts before returning
                cleaned = MARKDOWN_LINK_RE.sub(r"\1", line)
                cleaned = re.sub(r'\[([^\]]*)\]\([^)]*\)?', r'\1', cleaned)
                if cleaned.strip():
                    return cleaned[:160]
            return ""

        if ext == ".py":
            doc_match = re.search(r'^\s*(?:"""|\'\'\')\s*(.+?)\s*(?:"""|\'\'\')', content, re.DOTALL)
            if doc_match:
                return doc_match.group(1).strip().splitlines()[0][:160]
            if filename.lower() == "pyproject.toml":
                return self._summary_from_metadata(self._extract_metadata(content, ext, filename))
            if filename.lower() == "setup.py":
                return self._summary_from_metadata(self._extract_metadata(content, ext, filename))

        if ext == ".toml" or filename.lower() == "pyproject.toml":
            return self._summary_from_metadata(self._extract_metadata(content, ext, filename))

        if ext == ".json" or filename.lower() == "package.json":
            return self._summary_from_metadata(self._extract_metadata(content, ext, filename))

        return ""

    def _extract_symbols(self, content: str, ext: str) -> List[str]:
        if ext == ".py":
            matches = re.findall(r"^\s*(?:async\s+def|def|class)\s+([A-Za-z_][A-Za-z0-9_]*)", content, re.MULTILINE)
        elif ext in {".js", ".ts"}:
            matches = re.findall(
                r"^\s*(?:export\s+)?(?:async\s+)?(?:function|class|const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)",
                content,
                re.MULTILINE,
            )
        elif ext == ".go":
            matches = re.findall(
                r"^\s*(?:func|type|struct|interface)\s+([A-Za-z_][A-Za-z0-9_]*)",
                content,
                re.MULTILINE,
            )
        elif ext in {".cpp", ".c", ".h", ".hpp", ".cc", ".cxx", ".hh", ".hxx"}:
            # Match function definitions: return_type function_name(
            # Also match class definitions: class ClassName
            # This is a basic heuristic and might miss complex declarations
            func_matches = re.findall(r"^\s*(?:\w+\s+)*(\w+)::(\w+)?\s*\(", content, re.MULTILINE)
            simple_funcs = re.findall(r"^\s*(?:\w+\s+)+(\w+)\s*\(", content, re.MULTILINE)
            class_matches = re.findall(r"^\s*class\s+(\w+)", content, re.MULTILINE)
            matches = [m[0] for m in func_matches if m[0]] + simple_funcs + class_matches
        elif ext == ".rs":
            matches = re.findall(r"^\s*(?:pub\s+)?(?:async\s+)?(?:fn|struct|enum|trait)\s+([A-Za-z_][A-Za-z0-9_]*)", content, re.MULTILINE)
        else:
            matches = []

        deduped = []
        seen = set()
        for name in matches:
            if name not in seen:
                seen.add(name)
                deduped.append(name)
        return deduped[:8]

    def _extract_metadata(self, content: str, ext: str, filename: str) -> Dict[str, str]:
        metadata: Dict[str, str] = {}
        lower_name = filename.lower()

        if ext == ".toml" or lower_name == "pyproject.toml":
            name_match = re.search(r'^\s*name\s*=\s*["\']([^"\']+)["\']', content, re.MULTILINE)
            desc_match = re.search(r'^\s*description\s*=\s*["\']([^"\']+)["\']', content, re.MULTILINE)
            if name_match:
                metadata["name"] = name_match.group(1).strip()
            if desc_match:
                metadata["description"] = desc_match.group(1).strip()
        elif ext == ".json" or lower_name == "package.json":
            name_match = re.search(r'"name"\s*:\s*"([^"]+)"', content)
            desc_match = re.search(r'"description"\s*:\s*"([^"]+)"', content)
            if name_match:
                metadata["name"] = name_match.group(1).strip()
            if desc_match:
                metadata["description"] = desc_match.group(1).strip()
        elif lower_name == "setup.py":
            name_match = re.search(r'name\s*=\s*["\']([^"\']+)["\']', content)
            desc_match = re.search(r'description\s*=\s*["\']([^"\']+)["\']', content)
            if name_match:
                metadata["name"] = name_match.group(1).strip()
            if desc_match:
                metadata["description"] = desc_match.group(1).strip()
        elif lower_name == "go.mod":
            name_match = re.search(r'^module\s+(\S+)', content, re.MULTILINE)
            if name_match:
                metadata["name"] = name_match.group(1).strip()
        elif lower_name == "cmakelists.txt":
            # Extract project name from CMakeLists.txt: project(ProjectName)
            name_match = re.search(r'project\s*\(\s*([A-Za-z0-9_-]+)', content, re.IGNORECASE)
            if name_match:
                metadata["name"] = name_match.group(1).strip()
        elif lower_name == "cargo.toml":
            name_match = re.search(r'^\s*name\s*=\s*["\']([^"\']+)["\']', content, re.MULTILINE)
            desc_match = re.search(r'^\s*description\s*=\s*["\']([^"\']+)["\']', content, re.MULTILINE)
            if name_match:
                metadata["name"] = name_match.group(1).strip()
            if desc_match:
                metadata["description"] = desc_match.group(1).strip()

        return metadata

    def _extract_doc_metadata(self, content: str, ext: str, filename: str) -> Dict[str, Any]:
        """Extract documentation quality signals that can drift from code reality."""
        lower_name = filename.lower()
        if ext not in {".md", ".txt"} and lower_name not in {"readme.md", "readme.txt", "readme"}:
            return {}

        metadata: Dict[str, Any] = {}
        title_match = re.search(r"^\s*#{1,6}\s+(.+)$", content, re.MULTILINE)
        if title_match:
            metadata["doc_title"] = title_match.group(1).strip()[:160]

        lowered = content.lower()
        placeholder_patterns: list[tuple[str, str]] = [
            (r"\btbd\b", "TBD"),
            (r"\bcoming soon\b", "coming soon"),
            (r"\bplaceholder\s*[:\-]", "placeholder label"),
            (r"\bnot implemented yet\b", "not implemented"),
            (r"\bstub\b", "stub"),
            (r"\bfill me\b", "fill me"),
            (r"\[\s*\]", "empty bracket"),
            (r"^\s*-\s*$", "empty bullet"),
        ]
        hits: list[str] = []
        matched_lines: list[tuple[str, str, int]] = []
        for pattern, label in placeholder_patterns:
            for m in re.finditer(pattern, lowered, re.MULTILINE):
                hits.append(pattern)
                # Find the actual line content
                line_no = content[:m.start()].count("\n")
                raw_lines = content.splitlines()
                if line_no < len(raw_lines):
                    matched_line = raw_lines[line_no].strip()[:100]
                    matched_lines.append((label, matched_line, line_no + 1))
                break  # one match per pattern is enough

        empty_headings = 0
        lines = content.splitlines()
        for index, line in enumerate(lines[:-1]):
            if not line.strip().startswith("#"):
                continue
            next_nonempty = ""
            for candidate in lines[index + 1 : index + 5]:
                if candidate.strip():
                    next_nonempty = candidate.strip()
                    break
            if not next_nonempty or next_nonempty in {"-", "1.", "2.", "3."}:
                empty_headings += 1

        if hits or empty_headings:
            metadata["doc_drift_flags"] = hits[:6]
            metadata["empty_heading_count"] = empty_headings
            metadata["doc_drift_matches"] = matched_lines[:3]

        return metadata

    def _summary_from_metadata(self, metadata: Dict[str, str]) -> str:
        if metadata.get("name") and metadata.get("description"):
            return f"{metadata['name']}: {metadata['description']}"[:160]
        if metadata.get("description"):
            return metadata["description"][:160]
        if metadata.get("name"):
            return metadata["name"][:160]
        return ""

    def audit_project(self, file_data: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        """Perform a comprehensive project audit."""
        log = logging.getLogger("sentinel")
        t0 = perf_counter()

        metrics = self._compute_metrics(file_data)
        t1 = perf_counter()
        structure = self._analyze_structure(file_data)
        t2 = perf_counter()
        patterns = self._detect_patterns(file_data)
        architecture = self._summarize_architecture(metrics, structure, patterns)
        t3 = perf_counter()
        issues = self._find_issues(file_data, metrics, structure)
        risk_scores = self._score_file_risks(file_data, structure, issues)
        t4 = perf_counter()
        scan_coverage = self._evaluate_scan_coverage(file_data, structure)
        if scan_coverage.get("warning"):
            issues.append(
                {
                    "type": "scan_coverage",
                    "severity": "medium",
                    "category": "structural",
                    "file": None,
                    "message": scan_coverage["warning"],
                    "timestamp": now_iso(),
                }
            )
        understanding = self._build_project_understanding(
            file_data,
            metrics,
            structure,
            patterns,
            issues,
            risk_scores,
            scan_coverage,
        )

        # Second normalization gate — catch any identity leaks from _build_project_understanding
        raw_name = str(understanding.get("project_name") or self.root_dir.name)
        raw_type = str(understanding.get("project_type") or "Software project")
        raw_purpose = str(understanding.get("purpose") or "")
        raw_summary = str(understanding.get("summary") or "")
        identity = self._normalize_identity(
            project_name=raw_name,
            project_type=raw_type,
            purpose=raw_purpose,
            summary=raw_summary,
            description="",
        )
        understanding["project_name"] = identity["project_name"]
        understanding["project_type"] = identity["project_type"]
        understanding["purpose"] = identity["purpose"]
        understanding["summary"] = identity["summary"]

        health_score_data = self._calculate_health_score(issues, metrics, {})
        maintainability_pct = health_score_data.get("breakdown", {}).get("maintainability_percent", 85)
        risk_summary = self._summarize_risk_categories(issues, risk_scores, structure, maintainability_pct)
        # Update health score risk summary so it includes the maintainability risk from the breakdown
        risk_summary.setdefault("maintainability", {})["level"] = riskFromScore(maintainability_pct)
        # Recalculate health score with updated risk summary that includes correct maintainability
        health_score_data = self._calculate_health_score(issues, metrics, risk_summary)

        t5 = perf_counter()
        log.debug(
            "Audit timing: metrics=%.3fs structure=%.3fs patterns=%.3fs issues=%.3fs coverage=%.3fs total=%.3fs",
            t1 - t0, t2 - t1, t3 - t2, t4 - t3, t5 - t4, t5 - t0,
        )

        return {
            "timestamp": now_iso(),
            "metrics": metrics,
            "structure": structure,
            "scan_coverage": scan_coverage,
            "patterns": patterns,
            "architecture": architecture,
            "understanding": understanding,
            "issues": issues,
            "risk_scores": risk_scores,
            "risk_groups": self._group_risk_scores(risk_scores, file_data),
            "risk_summary": risk_summary,
            "health_score": health_score_data["score"],
            "health_score_data": health_score_data,
            "performance": {
                "audit_seconds": round(t5 - t0, 4),
                "file_count": len(file_data),
            },
        }

    def _compute_metrics(self, files: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        total_lines = sum(info["line_count"] for info in files.values())
        total_size = sum(info["size"] for info in files.values())
        todos = sum(info["todo_count"] for info in files.values())
        ext_counts: Dict[str, int] = {}

        # Categorize TODOs
        todo_categories = {
            "first_party_source": 0,
            "tooling": 0,
            "tests_fixtures": 0,
            "docs": 0,
            "vendor_generated": 0,
        }

        for path, info in files.items():
            ext = info["extension"]
            ext_counts[ext] = ext_counts.get(ext, 0) + 1

            todo_count = info.get("todo_count", 0)
            if todo_count > 0:
                fc = classifyFile(path, info)
                if fc.isTest or fc.isTestRunner or fc.isFixture:
                    todo_categories["tests_fixtures"] += todo_count
                elif fc.isDocumentation or fc.isSpecification:
                    todo_categories["docs"] += todo_count
                elif fc.isVendor or fc.isGenerated or fc.isGeneratedSdk:
                    todo_categories["vendor_generated"] += todo_count
                elif fc.isBuildTooling or fc.isGenerator or fc.isConfig:
                    todo_categories["tooling"] += todo_count
                elif fc.isLocalization or fc.isDependencyLock:
                    todo_categories["vendor_generated"] += todo_count
                elif fc.isEnvironmentSetup:
                    todo_categories["tooling"] += todo_count
                elif fc.isRuntimeSource:
                    todo_categories["first_party_source"] += todo_count
                else:
                    todo_categories["first_party_source"] += todo_count

        largest_files = sorted(
            (
                {"file": path, "lines": info["line_count"], "size": info["size"]}
                for path, info in files.items()
            ),
            key=lambda item: (item["lines"], item["size"]),
            reverse=True,
        )[:5]

        return {
            "total_files": len(files),
            "total_lines": total_lines,
            "total_size_bytes": total_size,
            "open_todos": todos,
            "todo_categories": todo_categories,
            "file_types": dict(sorted(ext_counts.items())),
            "avg_lines_per_file": total_lines // max(len(files), 1),
            "largest_files": largest_files,
        }

    _KNOWN_MAJOR_GO_BINARIES = {
        "kube-apiserver", "kubelet", "kube-controller-manager",
        "kube-scheduler", "kubectl", "kube-proxy", "kubeadm",
    }

    def _calculate_entry_point_score(self, filepath: str, parts: tuple[str, ...], info: Any = None) -> int:
        """Calculate a score for an entry point based on its directory."""
        score = 100  # Base score

        # Check directory patterns
        lower_parts = tuple(part.lower() for part in parts)
        fp = Path(filepath)
        name_lower = fp.stem.lower()
        # For Go cmd/<binary>/main.go and similar patterns, use parent dir as binary name
        parent_name = fp.parent.name.lower() if fp.parent and fp.parent.name.lower() != name_lower else name_lower
        category = self._categorize_entry_point(filepath)

        # High weight for source directories
        if any(part in {"src", "source", "app", "cmd", "main"} for part in lower_parts):
            score += 50

        # Boost known major binaries (kube-apiserver, kubectl, etc.)
        binary_name = parent_name if parent_name != name_lower else name_lower
        if binary_name in self._KNOWN_MAJOR_GO_BINARIES:
            score += 80

        # High weight for Libraries directories
        if any(part == "libraries" for part in lower_parts):
            score += 40

        # Medium weight for runtime directories
        if any(part in {"runtime", "bin", "sbin"} for part in lower_parts):
            score += 20

        if category == "runtime":
            score += 25
        elif category == "runtime_surface":
            score += 20
        elif category == "example":
            score -= 35
        elif category == "build":
            score += 5
        elif category == "generator":
            score -= 15
        elif category == "test":
            score -= 45
        elif category == "environment":
            score -= 55
        elif category == "packaging":
            score -= 5

        # Low weight for test directories
        if any(part in {"tests", "test", "spec"} for part in lower_parts):
            score -= 50

        # Low weight for documentation directories
        if any(part in {"docs", "documentation"} for part in lower_parts):
            score -= 30

        # Low weight for environment directories
        if any(part in {".devcontainer", "scripts", "tools"} for part in lower_parts):
            score -= 20

        # Low weight for build/generator directories
        if any(part in {"build", "meta", "generators"} for part in lower_parts):
            score -= 10

        # Penalize examples and generators more strongly
        if any(part in {"examples", "example", "samples", "sample", "demo", "demos"} for part in lower_parts):
            score -= 60

        # Penalize assets/static directories — never real entry points
        if any(part in {"assets", "asset", "static"} for part in lower_parts):
            score -= 80

        return max(0, score)

    def _analyze_structure(self, files: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        dirs = set()
        entry_points = []
        entry_point_details = []
        entry_points_by_category: Dict[str, List[Dict[str, Any]]] = {}
        config_files = []
        test_files = []
        documentation_files = []

        for filepath, info in files.items():
            parts = Path(filepath).parts
            hidden_dir_path = any(part.startswith(".") for part in parts[:-1])
            parent = str(Path(filepath).parent).replace("\\", "/")
            if parent and parent != "." and not hidden_dir_path:
                dirs.add(parent)

            if Path(filepath).name.lower() in {
                "config.json",
                ".env",
                "settings.py",
                "pyproject.toml",
                "package.json",
                "requirements.txt",
                "setup.py",
            }:
                config_files.append(filepath)

            lower_path = filepath.lower()
            lower_name = Path(filepath).name.lower()
            ext = Path(filepath).suffix.lower()
            is_test_file = not hidden_dir_path and (
                any(part.lower() in {"tests", "test", "spec", "unittests", "unit"} for part in parts[:-1])
                or lower_name.startswith("test_")
                or lower_name.endswith("_test.py")
                or (ext == ".go" and lower_name.endswith("_test.go"))
                or ".spec." in lower_name
                or "wpt-import" in lower_path
            )
            if is_test_file:
                test_files.append(filepath)

            if not hidden_dir_path and (
                lower_name in {"readme.md", "readme.txt", "readme"}
                or any(part.lower() in {"docs", "doc", "documentation"} for part in parts[:-1])
                or lower_path.endswith(".md")
            ):
                documentation_files.append(filepath)

            fc = classifyFile(filepath, info)
            is_runtime_entry = fc.isRuntimeEntryCandidate or info.get("has_main", False)
            if is_runtime_entry and not is_test_file and not fc.isTest and not fc.isTestRunner and not fc.isFixture and not fc.isGeneratedSdk and not fc.isGenerated:
                entry_points.append(filepath)
                score = self._calculate_entry_point_score(filepath, parts, info)
                category = self._categorize_entry_point(filepath)
                detail = {
                    "path": filepath,
                    "score": score,
                    "category": category,
                }
                entry_point_details.append(detail)
                entry_points_by_category.setdefault(category, []).append(detail)

        ordered_entry_points: List[Dict[str, Any]] = []
        for category in ["runtime", "runtime_surface", "example", "build", "generator", "tooling", "test", "environment", "packaging", "documentation"]:
            values = sorted(entry_points_by_category.get(category, []), key=lambda item: item["score"], reverse=True)
            entry_points_by_category[category] = values
            ordered_entry_points.extend(values)

        return {
            "directories": sorted(dirs),
            "entry_points": [item["path"] for item in ordered_entry_points],
            "entry_point_details": ordered_entry_points,
            "entry_points_by_category": {
                category: [item["path"] for item in values]
                for category, values in entry_points_by_category.items()
                if values
            },
            "config_files": sorted(config_files),
            "test_files": sorted(test_files),
            "documentation_files": sorted(documentation_files),
            "has_tests": bool(test_files),
            "has_docs": bool(documentation_files),
        }

    def _evaluate_scan_coverage(
        self,
        files: Dict[str, Dict[str, Any]],
        structure: Dict[str, Any],
    ) -> Dict[str, Any]:
        category_lines = {
            "source": 0,
            "tests": 0,
            "tooling": 0,
            "documentation": 0,
            "environment": 0,
            "data": 0,
        }

        for path, info in files.items():
            context = self._classify_path_context(path, info)
            lines = int(info.get("line_count", 0))
            if context in {"browser_engine", "javascript_engine", "runtime_entry", "first_party_source", "core_utility", "runtime_source", "application"}:
                category_lines["source"] += lines
            elif context in {"test", "test_support"}:
                category_lines["tests"] += lines
            elif context == "test_data":
                category_lines["tests"] += lines
                category_lines["data"] += lines
            elif context in {"build_tooling", "generator", "lint_tooling", "tooling"}:
                category_lines["tooling"] += lines
            elif context in {"documentation", "specification_documentation"}:
                category_lines["documentation"] += lines
            elif context == "environment":
                category_lines["environment"] += lines
            elif context in {"resources", "data_or_config", "vendor_generated", "generated_sdk", "localization_resource"}:
                category_lines["data"] += lines

        candidate_dirs = [
            "Libraries/LibWeb",
            "Libraries/LibJS",
            "AK",
            "Meta",
            "src",
            "Source",
            "Services",
            "UI",
            "app",
            "cmd",
            "api",
            "server",
            "llama",
            "ml",
        ]
        directory_coverage = []
        underrepresented = []
        for relative in candidate_dirs:
            target = self.root_dir / relative
            if not target.exists():
                continue
            actual_files = 0
            for _, _, filenames in os.walk(target):
                actual_files += len(filenames)
            scanned_files = sum(1 for path in files if path == relative or path.startswith(f"{relative}/"))
            ratio = 1.0 if actual_files == 0 else scanned_files / actual_files
            directory_coverage.append(
                {
                    "path": relative,
                    "scanned_files": scanned_files,
                    "actual_files": actual_files,
                    "coverage_ratio": round(ratio, 3),
                }
            )
            if actual_files >= 100 and ratio < 0.25:
                underrepresented.append(relative)

        total_lines = max(1, sum(category_lines.values()))
        test_ratio = category_lines["tests"] / total_lines
        source_ratio = category_lines["source"] / total_lines

        # Detect if Go-related files are present on disk but not scanned
        has_go_mod_on_disk = (self.root_dir / "go.mod").exists()
        has_go_files_scanned = any(Path(path).suffix == ".go" for path in files)
        has_go_mod_scanned = any(name == "go.mod" for name in (Path(path).name for path in files))

        warning = ""
        if has_go_mod_on_disk and not has_go_files_scanned:
            warning = (
                "Scan coverage note: Go module detected (go.mod) but no Go source files "
                "scanned. Expected Go source directories like cmd/, api/, server/ may be excluded."
            )
        elif has_go_mod_on_disk and not has_go_mod_scanned:
            warning = (
                "Scan coverage note: go.mod exists but was not included in the scan. "
                "Results may be affected by extension filtering."
            )
        elif underrepresented:
            dirs_str = ", ".join(underrepresented[:4])
            warning = (
                f"Scan coverage note: expected directories {dirs_str} "
                f"are below previous baseline."
            )
        elif test_ratio >= 0.7 and source_ratio <= 0.2 and source_ratio > 0:
            warning = (
                "Scan coverage note: tests represent a large share of scanned lines while "
                "source directories are lightly represented."
            )
        elif source_ratio == 0 and total_lines > 100:
            warning = (
                "Scan coverage note: no first-party source lines detected; "
                "all scanned content appears to be tests, data, or tooling."
            )

        return {
            "category_lines": category_lines,
            "test_line_ratio": round(test_ratio, 3),
            "source_line_ratio": round(source_ratio, 3),
            "directory_coverage": directory_coverage,
            "underrepresented_directories": underrepresented,
            "warning": warning,
            "entry_point_categories": structure.get("entry_points_by_category", {}),
        }

    def _detect_patterns(self, files: Dict[str, Dict[str, Any]]) -> List[Dict[str, str]]:
        detected = []
        for definition in self.patterns_config.get("patterns", []):
            if self._pattern_matches(definition, files):
                detected.append(
                    {
                        "name": definition.get("name", "unnamed_pattern"),
                        "description": definition.get("description", ""),
                    }
                )
        return detected

    def _pattern_matches(self, definition: Dict[str, Any], files: Dict[str, Dict[str, Any]]) -> bool:
        required_dirs = {value.lower() for value in definition.get("requires_directories", [])}
        if required_dirs:
            top_level_dirs = {Path(path).parts[0].lower() for path in files if len(Path(path).parts) > 1}
            if not required_dirs.issubset(top_level_dirs):
                return False

        has_file_scope_filter = any(
            key in definition
            for key in ["extension_in", "file_name_in", "path_contains_any", "requires_main", "import_contains_any"]
        )
        if not has_file_scope_filter:
            return True

        extension_in = {value.lower() for value in definition.get("extension_in", [])}
        file_name_in = {value.lower() for value in definition.get("file_name_in", [])}
        path_contains_any = [value.lower() for value in definition.get("path_contains_any", [])]
        import_contains_any = [value.lower() for value in definition.get("import_contains_any", [])]
        requires_main = bool(definition.get("requires_main"))

        for filepath, info in files.items():
            lower_path = filepath.lower()
            lower_name = Path(filepath).name.lower()

            if extension_in and info.get("extension", "").lower() not in extension_in:
                continue
            if file_name_in and lower_name not in file_name_in:
                continue
            if path_contains_any and not any(token in lower_path for token in path_contains_any):
                continue
            if requires_main and not info.get("has_main"):
                continue
            if import_contains_any and not any(
                any(token in imported.lower() for token in import_contains_any)
                for imported in info.get("imports", [])
            ):
                continue
            return True

        return False

    def _summarize_architecture(
        self,
        metrics: Dict[str, Any],
        structure: Dict[str, Any],
        patterns: List[Dict[str, str]],
    ) -> Dict[str, Any]:
        return {
            "root_dir": str(self.root_dir),
            "languages": self._detect_languages(metrics.get("file_types", {})),
            "directories": structure.get("directories", []),
            "entry_points": structure.get("entry_points", []),
            "entry_points_by_category": structure.get("entry_points_by_category", {}),
            "config_files": structure.get("config_files", []),
            "documentation_files": structure.get("documentation_files", []),
            "patterns": [pattern["name"] for pattern in patterns],
            "version_control": dict(self.git_context),
        }

    def _calculate_confidence_reasons(
        self,
        project_name: str | None,
        description: str,
        primary_language: str,
        languages: List[str],
        files: Dict[str, Dict[str, Any]],
        structure: Dict[str, Any],
        hotspots: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Calculate confidence reasons for various aspects of project understanding."""

        # Language confidence
        language_confidence = "high"
        language_reason = f"Primary language detected: {primary_language}"

        source_cpp = sum(
            1
            for path, info in files.items()
            if self._classify_path_context(path, info) in {"browser_engine", "javascript_engine", "runtime_entry", "first_party_source", "core_utility", "runtime_source"}
            and Path(path).suffix.lower() in {".cpp", ".c", ".h", ".hpp", ".cc", ".cxx", ".hh", ".hxx"}
        )
        js_in_tests = sum(
            1
            for path, info in files.items()
            if self._classify_path_context(path, info) in {"test", "test_data"} and Path(path).suffix.lower() in {".js", ".ts"}
        )
        if primary_language.lower() in {"c++", "c"} and source_cpp > 10:
            language_reason += "; C++ dominates first-party source directories"
        elif primary_language.lower() == "javascript" and js_in_tests > 0:
            language_reason += "; JavaScript appears mostly in tests and fixtures"

        # Entry point confidence
        entry_confidence = "high"
        runtime_entries = structure.get("entry_points_by_category", {}).get("runtime", [])
        build_entries = structure.get("entry_points_by_category", {}).get("build", []) + structure.get("entry_points_by_category", {}).get("generator", [])
        if runtime_entries:
            entry_reason = f"Runtime entry points detected from source files such as {runtime_entries[0]}"
        elif build_entries:
            entry_confidence = "medium"
            entry_reason = f"No clear runtime entry point detected; build or generator entry points start at {build_entries[0]}"
        else:
            entry_confidence = "medium"
            entry_reason = "No explicit entry points found, inferred from source structure"

        # Project identity confidence
        identity_confidence = "high" if project_name else "medium"
        identity_reason = f"Project name: {project_name}" if project_name else "Project name inferred from directory"

        # Risk confidence
        risk_confidence = "high"
        risk_reason = "Risk assessment based on file structure and issues"

        return {
            "language": {
                "level": language_confidence,
                "reason": language_reason,
            },
            "entry_points": {
                "level": entry_confidence,
                "reason": entry_reason,
            },
            "identity": {
                "level": identity_confidence,
                "reason": identity_reason,
            },
            "risk": {
                "level": risk_confidence,
                "reason": risk_reason,
            },
        }

    def _normalize_identity(
        self,
        project_name: str,
        project_type: str,
        purpose: str,
        summary: str,
        description: str,
    ) -> dict:
        """Single normalization layer. Guarantees no HTML/filler leaks into identity fields."""
        cleaned_name = _strip_html(str(project_name))
        cleaned_name = _strip_markdown(cleaned_name)
        if _is_bad_identity_text(cleaned_name) or not cleaned_name:
            cleaned_name = self.root_dir.name

        # Known repo name mapping (case-insensitive lookup)
        name_lower = cleaned_name.lower()
        if name_lower in _KNOWN_REPO_NAMES:
            cleaned_name = _KNOWN_REPO_NAMES[name_lower]

        # Truncate project name to first sentence, but preserve module/path-style names
        if "." in cleaned_name and not cleaned_name.endswith("."):
            if "/" not in cleaned_name and "\\" not in cleaned_name:
                cleaned_name = cleaned_name.split(".")[0].strip()
        elif cleaned_name.endswith("."):
            cleaning = cleaned_name[:-1].strip()
            if cleaning and not _is_bad_identity_text(cleaning):
                cleaned_name = cleaning
        # Enforce max length for project name
        if len(cleaned_name) > 48:
            # Try to find shorter form: first markdown link text or first word
            words = cleaned_name.split()
            if len(words) > 4:
                cleaned_name = " ".join(words[:4])
            else:
                cleaned_name = self.root_dir.name

        cleaned_type = _strip_html(str(project_type))
        cleaned_type = _strip_markdown(cleaned_type)
        if _is_bad_identity_text(cleaned_type):
            cleaned_type = "Software project"

        cleaned_purpose = _strip_html(str(purpose))
        cleaned_purpose = _strip_markdown(cleaned_purpose)
        if _is_bad_identity_text(cleaned_purpose) or not cleaned_purpose:
            cleaned_purpose = "Purpose could not be confidently inferred from README."

        cleaned_summary = _strip_html(str(summary))
        cleaned_summary = _strip_markdown(cleaned_summary)
        if _is_bad_identity_text(cleaned_summary) or not cleaned_summary:
            cleaned_summary = f"{cleaned_name} appears to be {cleaned_type}."
            if cleaned_purpose and "confidently inferred" not in cleaned_purpose.lower():
                cleaned_summary = f"{cleaned_summary} {cleaned_purpose}"

        return {
            "project_name": cleaned_name,
            "project_type": cleaned_type,
            "purpose": cleaned_purpose,
            "summary": cleaned_summary,
            "description": _strip_html(str(description)),
        }

    def _build_project_understanding(
        self,
        files: Dict[str, Dict[str, Any]],
        metrics: Dict[str, Any],
        structure: Dict[str, Any],
        patterns: List[Dict[str, str]],
        issues: List[Dict[str, Any]],
        risk_scores: List[Dict[str, Any]],
        scan_coverage: Dict[str, Any],
    ) -> Dict[str, Any]:
        project_name, description = self._detect_project_identity(files)
        frameworks = self._detect_frameworks(files)
        components = self._summarize_components(files)
        languages = self._detect_languages(metrics.get("file_types", {}))
        primary_language = self._detect_primary_language(metrics.get("file_types", {}), languages, files)
        project_type = self._infer_project_type(primary_language, structure, frameworks, components, files)
        archetype_data = detectRepoArchetype(files, components, primary_language, frameworks)
        archetype = archetype_data["primaryArchetype"]
        secondary_archetypes = archetype_data.get("secondaryArchetypes", [])
        purpose = self._infer_project_purpose(description, files, components)
        hotspot_groups = self._identify_hotspots(files, metrics, structure, issues, risk_scores)
        primary_hotspots = hotspot_groups.get("runtime", []) + hotspot_groups.get("build_tooling", [])
        important_files = self._identify_important_files(files, metrics, structure, primary_hotspots)
        workflow_hints = self._build_workflow_hints(structure, frameworks, files, archetype)

        confidence_reasons = self._calculate_confidence_reasons(
            project_name, description, primary_language, languages, files, structure, primary_hotspots
        )

        raw_name = project_name or self.root_dir.name
        summary_bits = [raw_name, "appears to be", project_type]
        summary = " ".join(bit for bit in summary_bits if bit).strip()
        if purpose:
            summary = f"{summary}. {purpose}"

        # Single normalization layer — never let dirty values escape
        identity = self._normalize_identity(
            project_name=raw_name,
            project_type=project_type,
            purpose=purpose,
            summary=summary,
            description=description or "",
        )

        return {
            "project_name": identity["project_name"],
            "project_type": identity["project_type"],
            "archetype": archetype,
            "archetype_secondary": secondary_archetypes,
            "primary_language": primary_language,
            "languages": languages,
            "frameworks": frameworks,
            "purpose": identity["purpose"],
            "summary": identity["summary"],
            "main_components": components[:12],
            "important_files": important_files[:8],
            "hotspots": primary_hotspots[:5],
            "hotspot_groups": {name: items[:5] for name, items in hotspot_groups.items() if items},
            "entry_points_by_category": structure.get("entry_points_by_category", {}),
            "workflow_hints": workflow_hints,
            "patterns": [pattern["name"] for pattern in patterns],
            "confidence_reasons": confidence_reasons,
            "scan_coverage": scan_coverage,
        }

    def _validate_readme_title(self, title: str) -> bool:
        """Return True if title is usable as a project name."""
        if not title:
            return False
        t = title.strip()
        tl = t.lower()
        if len(t) < 2:
            return False
        # Reject HTML, badges, images
        if _is_bad_identity_text(t):
            return False
        if t.startswith("<"):
            return False
        if "align=" in tl:
            return False
        if "src=" in tl:
            return False
        if "badge" in tl or "shield" in tl:
            return False
        if "sponsor" in tl or "funding" in tl or "donate" in tl:
            return False
        # Reject question headings: "Why Rust?", "What is FastAPI?"
        if "?" in tl:
            return False
        if tl.startswith(("why ", "what ", "how ", "who ", "where ", "when ")):
            return False
        # Reject section/heading keywords
        words = set(tl.split())
        if words & _BLOCKED_README_TITLE_WORDS:
            return False
        if tl in _BAD_SECTION_HEADINGS:
            return False
        # Reject overly long titles (> 6 words) unless they match a common pattern
        if len(t.split()) > 6:
            return False
        return True

    def _detect_project_identity(self, files: Dict[str, Dict[str, Any]]) -> tuple[str | None, str]:
        """Ranked identity resolver.

        Priority:
          1. Known repo name from directory name (_KNOWN_REPO_NAMES)
          2. Package metadata: Cargo.toml > pyproject.toml > package.json > setup.py > go.mod > CMakeLists.txt
          3. Validated README heading
          4. Directory name fallback
        """
        project_name = None
        description = ""

        # Tier 0: Known repo name from directory
        dir_name = self.root_dir.name
        dir_lower = dir_name.lower()
        if dir_lower in _KNOWN_REPO_NAMES:
            project_name = _KNOWN_REPO_NAMES[dir_lower]

        # Tier 1: Package manifests (ordered by reliability)
        manifest_priority = ["Cargo.toml", "pyproject.toml", "package.json", "setup.py", "go.mod", "CMakeLists.txt"]
        for candidate in manifest_priority:
            info = files.get(candidate)
            if not info:
                continue
            metadata = info.get("metadata", {})
            raw_name = metadata.get("name", "")
            if raw_name:
                cleaned = raw_name.strip()
                if cleaned:
                    if cleaned.startswith("@") or cleaned.startswith("com."):
                        cleaned = cleaned.split("/")[-1] if "/" in cleaned else cleaned
                    # Use first valid manifest name found
                    if not project_name or project_name == dir_name:
                        project_name = cleaned
                        break

        # Tier 2: Description from package metadata
        for candidate in manifest_priority:
            info = files.get(candidate)
            if not info:
                continue
            metadata = info.get("metadata", {})
            if not description and metadata.get("description"):
                raw = metadata["description"]
                if raw and not _is_bad_identity_text(raw):
                    description = raw
                    break

        # Tier 3: Description from README summary
        if not description:
            for rname in ("README.md", "readme.md"):
                info = files.get(rname)
                if info and info.get("summary"):
                    raw = info["summary"]
                    cleaned = HTML_TAG_RE.sub("", raw)
                    cleaned = MARKDOWN_LINK_RE.sub(r"\1", cleaned)
                    cleaned = re.sub(r'\[([^\]]*)\]\([^)]*\)?', r'\1', cleaned)
                    cleaned = re.sub(r'src="[^"]*"', "", cleaned)
                    cleaned = re.sub(r"src='[^']*'", "", cleaned)
                    cleaned = re.sub(r'https?://[^\s]+', "", cleaned).strip()
                    if cleaned and not _is_bad_identity_text(cleaned):
                        description = cleaned
                        break

        # Tier 4: README heading as project name (only if no better name found)
        if not project_name or project_name == dir_name:
            for rname in ("README.md", "readme.md"):
                info = files.get(rname)
                if not info:
                    continue
                metadata = info.get("metadata", {})
                raw_title = metadata.get("doc_title", "")
                if raw_title:
                    cleaned_title = _strip_html(raw_title)
                    cleaned_title = _strip_markdown(cleaned_title)
                    if self._validate_readme_title(cleaned_title):
                        project_name = cleaned_title
                        break

        # Tier 5: Directory name fallback
        if not project_name:
            project_name = dir_name

        return project_name, description

    def _detect_frameworks(self, files: Dict[str, Dict[str, Any]]) -> List[str]:
        signals = {
            "fastapi": ["fastapi", "uvicorn"],
            "flask": ["flask"],
            "django": ["django"],
            "pytest": ["pytest"],
            "unittest": ["unittest"],
            "pydantic": ["pydantic"],
            "click": ["click"],
            "typer": ["typer"],
            "redis": ["redis"],
            "sqlalchemy": ["sqlalchemy"],
            "streamlit": ["streamlit"],
            "react": ["react"],
            "nextjs": ["next"],
            "express": ["express"],
        }

        def matches_token(imported: str, token: str) -> bool:
            if imported == token or imported.startswith(f"{token}.") or imported.startswith(f"{token}/"):
                return True
            pieces = [piece for piece in re.split(r"[^a-z0-9]+", imported) if piece]
            return token in pieces

        framework_scores: Dict[str, float] = {label: 0.0 for label in signals}
        framework_primary_support: Dict[str, float] = {label: 0.0 for label in signals}
        for path, info in files.items():
            imports = [imported.lower() for imported in info.get("imports", []) if isinstance(imported, str)]
            if not imports:
                continue
            context = self._classify_path_context(path, info)
            if context in {"test", "test_data", "documentation", "vendor_generated"}:
                weight = 0.1
            elif context in {"browser_engine", "javascript_engine", "runtime_entry", "first_party_source", "core_utility", "runtime_source"}:
                weight = 1.0
            elif context in {"build_tooling", "generator", "lint_tooling", "tooling"}:
                weight = 0.5
            else:
                weight = 0.4

            for label, tokens in signals.items():
                if any(
                    matches_token(imported, token)
                    for token in tokens
                    for imported in imports
                ):
                    framework_scores[label] += weight
                    if context not in {"test", "test_data", "documentation", "vendor_generated"}:
                        framework_primary_support[label] += weight

        frameworks = [
            label
            for label, score in framework_scores.items()
            if score >= 1.0 and (framework_primary_support[label] >= 1.0 or label in {"pytest", "unittest"})
        ]

        # Suppress redundant test labels: unittest is implied by test_suite
        if "unittest" in frameworks and "test_suite" in frameworks:
            frameworks.remove("unittest")

        # Next.js detection: require strong structural evidence
        # Import-based "next" token matches are too noisy (can match unrelated tokens)
        # Only flag nextjs if next.config.*, pages/ or app/ dir, or next in package.json deps
        def _check_nextjs_evidence(files) -> bool:
            if any(Path(fname).name.lower().startswith("next.config") for fname in files):
                return True
            if any(fname.lower().startswith("pages/") or fname.lower().startswith("app/") for fname in files):
                return True
            if "package.json" in files:
                pkg_path = self.root_dir / "package.json"
                try:
                    pkg_text = pkg_path.read_text(encoding="utf-8", errors="ignore")
                    pkg_data = json.loads(pkg_text)
                    deps = set(pkg_data.get("dependencies", {}))
                    dev_deps = set(pkg_data.get("devDependencies", {}))
                    if "next" in deps or "next" in dev_deps:
                        return True
                except (OSError, json.JSONDecodeError):
                    pass
            return False

        has_nextjs_evidence = _check_nextjs_evidence(files)
        if "nextjs" in frameworks and not has_nextjs_evidence:
            frameworks.remove("nextjs")
        elif has_nextjs_evidence and "nextjs" not in frameworks:
            frameworks.append("nextjs")

        if any(path.endswith("pyproject.toml") for path in files):
            frameworks.append("python_packaging")
        if any(path.endswith("Cargo.toml") or path.endswith(".rs") for path in files):
            frameworks.append("rust_tooling")
        if any(Path(path).name == "CMakeLists.txt" for path in files):
            frameworks.append("cmake_build")
        if any("tests/" in path.lower() or Path(path).name.lower().startswith("test_") for path in files):
            frameworks.append("test_suite")

        deduped = []
        seen = set()
        for framework in frameworks:
            if framework not in seen:
                seen.add(framework)
                deduped.append(framework)
        return deduped

    def _summarize_components(self, files: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
        grouped: Dict[str, Dict[str, Any]] = {}
        for filepath, info in files.items():
            key = self._component_key_for_path(filepath)
            if not key:
                continue

            bucket = grouped.setdefault(
                key,
                {
                    "path": key,
                    "file_count": 0,
                    "line_count": 0,
                    "role": self._component_role_for_context(key),
                    "representative_files": [],
                    "symbols": [],
                },
            )
            bucket["file_count"] += 1
            bucket["line_count"] += int(info.get("line_count", 0))
            bucket["representative_files"].append((filepath, int(info.get("line_count", 0))))
            bucket["symbols"].extend(info.get("symbols", []))

        components = []
        for component in grouped.values():
            representative = sorted(component["representative_files"], key=lambda item: item[1], reverse=True)
            deduped_symbols = []
            seen_symbols = set()
            for symbol in component["symbols"]:
                if symbol not in seen_symbols:
                    seen_symbols.add(symbol)
                    deduped_symbols.append(symbol)
            components.append(
                {
                    "path": component["path"],
                    "role": component["role"],
                    "file_count": component["file_count"],
                    "line_count": component["line_count"],
                    "representative_files": [item[0] for item in representative[:3]],
                    "symbols": deduped_symbols[:5],
                }
            )

        components.sort(key=lambda item: (item["line_count"], item["file_count"]), reverse=True)

        # Split large top-level directories one level deeper if they have meaningful subdirectories
        split_components: List[Dict[str, Any]] = []
        for component in components:
            if "/" in component["path"] or component["file_count"] <= 3:
                split_components.append(component)
                continue
            # Try to split by second-level directory
            sub_buckets: Dict[str, Dict[str, Any]] = {}
            for filepath, line_count in grouped.get(component["path"], {}).get("representative_files", []):
                parts = Path(filepath).parts
                if len(parts) >= 2:
                    sub_key = "/".join(parts[:2])
                else:
                    sub_key = component["path"]
                if sub_key == component["path"]:
                    continue
                if sub_key not in sub_buckets:
                    sub_buckets[sub_key] = {
                        "path": sub_key,
                        "file_count": 0,
                        "line_count": 0,
                        "role": classifyComponentRole(sub_key),
                        "representative_files": [],
                        "symbols": [],
                    }
                sub_buckets[sub_key]["file_count"] += 1
                sub_buckets[sub_key]["line_count"] += line_count
                sub_buckets[sub_key]["representative_files"].append((filepath, line_count))
            if sub_buckets and len(sub_buckets) >= 1:
                for sub in sub_buckets.values():
                    sub["representative_files"] = sorted(sub["representative_files"], key=lambda item: item[1], reverse=True)[:3]
                split_components.extend(sorted(sub_buckets.values(), key=lambda item: (item["line_count"], item["file_count"]), reverse=True))
            else:
                split_components.append(component)

        if split_components:
            components = split_components
            components.sort(key=lambda item: (item["line_count"], item["file_count"]), reverse=True)

        # Filter out tiny components (<= 2 files) unless they are meaningful
        # but keep at least 3 components to avoid empty reports
        if len(components) > 3:
            meaningful = [c for c in components if c["file_count"] > 2]
            if len(meaningful) >= 3:
                components = meaningful

        return components[:15]

    def _infer_component_role(self, key: str) -> str:
        return classifyComponentRole(key)

    def _infer_project_type(
        self,
        primary_language: str,
        structure: Dict[str, Any],
        frameworks: List[str],
        components: List[Dict[str, Any]],
        files: Dict[str, Dict[str, Any]],
    ) -> str:
        component_paths = {component["path"].lower() for component in components}
        descriptors: List[str] = []
        archetype_data = detectRepoArchetype(files, components, primary_language, frameworks)
        archetype = archetype_data["primaryArchetype"]

        # Detect framework subdirectory structure (applies to framework_library AND app archetypes)
        framework_subdir_roots = {"core", "python", "compiler", "lite", "runtime", "api", "go", "java", "c", "cc", "tools"}
        second_level_dirs = set()
        for p in files:
            parts = p.split("/")
            if len(parts) >= 2:
                second_level_dirs.add(parts[1].lower())
        framework_matches = second_level_dirs & framework_subdir_roots

        if archetype == ARCHETYPE_BROWSER_ENGINE:
            descriptors.append("C++ browser engine / web browser project")
        elif archetype == ARCHETYPE_DESKTOP_APP:
            descriptors.append("TypeScript/Tauri desktop app" if any(p.endswith(".ts") for p in files) else "Desktop application")
        elif archetype == ARCHETYPE_CLI_SERVER:
            descriptors.append("Go-based CLI/server application" if primary_language == "go" else "CLI/server application")
        elif archetype == ARCHETYPE_MONOREPO:
            descriptors.append("Monorepo")
        elif archetype == ARCHETYPE_FRAMEWORK_LIBRARY:
            # ML framework / compiler detection
            ml_framework_signals = {"python", "compiler", "lite", "core"} & framework_matches
            if len(ml_framework_signals) >= 2:
                if "python" in framework_matches and "core" in framework_matches:
                    descriptors.append(f"{primary_language} machine learning framework with compiler, runtime, API, and extensive test infrastructure")
                else:
                    descriptors.append("mixed-language framework with compiler, runtime, API, and test infrastructure")
            elif len(framework_matches) >= 2:
                descriptors.append("mixed-language framework with compiler, runtime, API, and test infrastructure")
            elif primary_language in {"python", "c++"}:
                descriptors.append(f"{primary_language} framework/library" if primary_language == "python" else "C++ framework/library")
            elif primary_language == "go":
                descriptors.append("Go framework/library")
            else:
                descriptors.append(f"{primary_language} framework/library" if primary_language != "unknown" else "Framework/library")
        elif archetype == ARCHETYPE_APP:
            # Even for "app" archetype, check for framework subdirectory signals
            ml_framework_signals = {"python", "compiler", "lite", "core"} & framework_matches
            if len(ml_framework_signals) >= 2:
                if "python" in framework_matches and "core" in framework_matches:
                    descriptors.append(f"{primary_language} machine learning framework with compiler, runtime, API, and extensive test infrastructure")
                else:
                    descriptors.append(f"{primary_language} framework with compiler, runtime, API, and test infrastructure")
            else:
                descriptors.append(f"{primary_language} application" if primary_language != "unknown" else "Application")
        else:
            descriptors.append(f"{primary_language} project" if primary_language != "unknown" else "Software project")

        # Detect multiple languages from files
        languages = set()
        for p in files:
            ext = Path(p).suffix.lower()
            if ext in {".py", ".js", ".ts", ".rs", ".go", ".cpp", ".c", ".h", ".hpp", ".cc", ".java", ".kt", ".rb", ".cs"}:
                languages.add(ext)
        if len(languages) >= 2 and archetype not in {ARCHETYPE_BROWSER_ENGINE, ARCHETYPE_DESKTOP_APP}:
            ext_to_name = {".py": "Python", ".js": "JavaScript", ".ts": "TypeScript", ".rs": "Rust", ".go": "Go", ".cpp": "C++", ".c": "C", ".cc": "C++", ".java": "Java", ".kt": "Kotlin"}
            lang_names = sorted({ext_to_name.get(e, e.lstrip(".")) for e in languages})
            if len(lang_names) <= 3 and primary_language not in {"unknown"}:
                if archetype == ARCHETYPE_FRAMEWORK_LIBRARY:
                    descriptors.append(f"multi-language: {'/'.join(lang_names)}")
                else:
                    descriptors[0] = f"{'/'.join(lang_names)} project"

        tooling = []
        if "python_packaging" in frameworks or any(path.startswith("Meta/") for path in structure.get("entry_points", [])):
            tooling.append("Python")
        if "rust_tooling" in frameworks:
            tooling.append("Rust")
        if tooling:
            descriptors.append(f"with {'/'.join(tooling)} tooling")

        if "fastapi" in frameworks or "flask" in frameworks or "django" in frameworks:
            descriptors.append("with a service/API layer")
        if any("dashboard" in path for path in component_paths):
            descriptors.append("with an operator dashboard")

        has_js_tests = any(path.startswith("Tests/LibJS") or path.startswith("Tests/LibWeb") for path in structure.get("test_files", []))
        if has_js_tests and "libraries/libweb" in component_paths:
            descriptors.append("and a large JavaScript/Web Platform test suite")
        elif structure.get("has_tests"):
            # Check if test infrastructure is already mentioned in the type descriptor
            type_desc = " ".join(descriptors).lower()
            if "test infrastructure" not in type_desc and "test suite" not in type_desc:
                descriptors.append("with extensive test infrastructure")

        has_frontend = any(path in component_paths for path in {"src", "ui", "web", "frontend", "app/ui"})
        has_backend = any(path in component_paths for path in {"src-tauri", "api", "backend", "server"})
        if has_frontend and has_backend and archetype not in {ARCHETYPE_BROWSER_ENGINE, ARCHETYPE_DESKTOP_APP}:
            descriptors.append("with frontend and backend components")
        elif has_frontend and archetype not in {ARCHETYPE_BROWSER_ENGINE, ARCHETYPE_DESKTOP_APP}:
            descriptors.append("with frontend components")
        elif has_backend and archetype not in {ARCHETYPE_BROWSER_ENGINE, ARCHETYPE_DESKTOP_APP}:
            descriptors.append("with backend components")

        return " ".join(descriptors)

    _GENERIC_WELCOME_PHRASES = {
        "welcome to", "welcome!", "hello!", "hi there",
        "getting started", "this project is", "the project is",
        "this repository", "the repository",
    }

    def _infer_project_purpose(
        self,
        description: str,
        files: Dict[str, Dict[str, Any]],
        components: List[Dict[str, Any]],
    ) -> str:
        # Strip image/URL pollution before processing
        desc = description.strip()
        desc = HTML_TAG_RE.sub("", desc)
        desc = MARKDOWN_LINK_RE.sub(r"\1", desc)
        # Strip broken/cross-line markdown links: [text]( or [text](
        desc = re.sub(r'\[([^\]]*)\]\([^)]*\)?', r'\1', desc)
        desc = re.sub(r'src="[^"]*"', "", desc)
        desc = re.sub(r"src='[^']*'", "", desc)
        desc = re.sub(r'https?://[^\s]+', "", desc).strip()
        # Skip decorative separators (dashes, underscores, etc.)
        if desc and all(c in "-_=~*#." for c in desc):
            desc = ""

        html_or_build_patterns = [
            "cmake_minimum_required",
            "project(",
            "cmake ",
            "<p ", "<p>", "<img ", "<div ", "</",
            "[![", "![",
            "<!--",
        ]
        lowered_description = desc.lower()
        if any(pattern in lowered_description for pattern in html_or_build_patterns):
            desc = ""
            lowered_description = ""

        low_signal_descriptions = {
            "",
            "readme",
            "sample project",
            "application logic, application logic, application logic",
            "application logic",
            "----",
            "---",
        }
        if lowered_description and lowered_description not in low_signal_descriptions:
            result = desc.rstrip(".") + "."
            if "application logic" in result.lower():
                return "Purpose could not be confidently inferred from README."
            # Skip generic welcome/greeting phrases as project purpose
            if any(result.lower().startswith(p) for p in self._GENERIC_WELCOME_PHRASES):
                desc = ""
                lowered_description = ""
            else:
                return result

        # Try README body extraction — look for first real paragraph after heading
        for rname in ("README.md", "readme.md"):
            info = files.get(rname)
            if not info:
                continue
            raw = info.get("raw_content", "")
            if not raw:
                # Use the original file content if available
                filepath = self.root_dir / rname
                if filepath.exists():
                    try:
                        raw = filepath.read_text(encoding="utf-8", errors="ignore")
                    except OSError:
                        pass
            if raw:
                for raw_line in raw.splitlines():
                    line = raw_line.strip()
                    if not line or line.startswith(("#", "<", "!", "`", "-", "|", "[")):
                        continue
                    if len(line) < 20:
                        continue
                    if any(p in line.lower() for p in ("http://", "https://", "src=", "badge", "sponsor", "donate")):
                        continue
                    cleaned = HTML_TAG_RE.sub("", line).strip()
                    cleaned = MARKDOWN_LINK_RE.sub(r"\1", cleaned)
                    # Skip lines that are only dashes, underscores, or decorative chars
                    if cleaned and all(c in "-_=~*#." for c in cleaned):
                        continue
                    if len(cleaned) >= 20 and not _is_bad_identity_text(cleaned):
                        result = cleaned.rstrip(".") + "."
                        if "application logic" not in result.lower() and not any(result.lower().startswith(p) for p in self._GENERIC_WELCOME_PHRASES):
                            return result
                break

        # Fallback: use summary (already cleaned)
        readme = files.get("README.md") or files.get("readme.md")
        summary = readme.get("summary", "") if readme else ""
        if summary:
            summary_clean = HTML_TAG_RE.sub("", summary)
            summary_clean = MARKDOWN_LINK_RE.sub(r"\1", summary_clean)
            summary_clean = re.sub(r'\[([^\]]*)\]\([^)]*\)?', r'\1', summary_clean)
            summary_clean = re.sub(r'src="[^"]*"', "", summary_clean)
            summary_clean = re.sub(r"src='[^']*'", "", summary_clean)
            summary_clean = re.sub(r'https?://[^\s]+', "", summary_clean).strip()
            summary_lower = summary_clean.lower()
            if summary_lower in {"", "---", "----", "---.", "----."}:
                pass  # Skip decorative separators
            elif not any(pattern in summary_lower for pattern in html_or_build_patterns) and summary_lower not in {"readme"} and "application logic" not in summary_lower and not any(summary_lower.startswith(p) for p in self._GENERIC_WELCOME_PHRASES):
                return summary_clean.rstrip(".") + "."

        # Fallback: use README doc_title as purpose signal (only if title has a subtitle/tagline)
        if readme:
            metadata = readme.get("metadata", {})
            doc_title = metadata.get("doc_title", "")
            if doc_title:
                cleaned_title = _strip_html(doc_title)
                cleaned_title = _strip_markdown(cleaned_title)
                if cleaned_title and not _is_bad_identity_text(cleaned_title):
                    # Only use title with a subtitle via colon or em-dash (e.g. "Kubernetes: Production-Grade Container Orchestration")
                    subtitle_match = re.match(r'^[^:]+:\s*(.+)$', cleaned_title)
                    if not subtitle_match:
                        subtitle_match = re.match(r'^[^—–-]+[—–-]\s*(.+)$', cleaned_title)
                    if subtitle_match:
                        subtitle = subtitle_match.group(1).strip()
                        if len(subtitle) >= 15 and not _is_bad_identity_text(subtitle):
                            return subtitle.rstrip(".") + "."

        component_paths = {component["path"].lower() for component in components}
        if "libraries/libweb" in component_paths and "libraries/libjs" in component_paths:
            return "Browser engine and web platform runtime with build tooling and extensive standards tests."

        # Build purpose from project type and components
        core_components = [component for component in components if component["path"] not in {"tests", "docs"}]
        if core_components:
            visible_roles = [c["role"] for c in core_components[:4]]
            non_generic = [r for r in visible_roles if "application logic" not in r.lower() and "unknown" not in r.lower()]
            if non_generic:
                if len(non_generic) == 1:
                    purpose = f"It is organized around {non_generic[0]}."
                else:
                    purpose = f"It is organized around {', '.join(non_generic[:-1])}, and {non_generic[-1]}."
                if "application logic" in purpose.lower():
                    return "Purpose could not be confidently inferred from README."
                tech_hints = []
                has_react = any("react" in (c.get("role") or "").lower() for c in core_components)
                has_rust = any("rust" in (c.get("role") or "").lower() for c in core_components)
                if has_react:
                    tech_hints.append("React frontend")
                if has_rust:
                    tech_hints.append("Rust backend")
                if tech_hints:
                    purpose += f" Built with {' and '.join(tech_hints)}."
                return purpose

        return "Purpose could not be confidently inferred from README."

    def _identify_important_files(
        self,
        files: Dict[str, Dict[str, Any]],
        metrics: Dict[str, Any],
        structure: Dict[str, Any],
        hotspots: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        important: List[Dict[str, Any]] = []
        seen = set()

        def add(path: str, reason: str) -> None:
            if path not in files or path in seen:
                return
            seen.add(path)
            entry = {"path": path, "reason": reason}
            if files[path].get("summary"):
                entry["summary"] = files[path]["summary"]
            if files[path].get("symbols"):
                entry["symbols"] = files[path]["symbols"][:4]
            important.append(entry)

        for path in structure.get("entry_points", []):
            add(path, "entry point")

        for path in ["README.md", "readme.md", "pyproject.toml", "package.json", "setup.py", "requirements.txt", "go.mod"]:
            add(path, "project definition")

        for item in hotspots[:4]:
            add(item["path"], "high-leverage hotspot")

        for item in metrics.get("largest_files", [])[:4]:
            if self._categorize_hotspot(item["file"], files[item["file"]]) != "runtime":
                continue
            add(item["file"], "high-leverage hotspot")

        return important

    def _identify_hotspots(
        self,
        files: Dict[str, Dict[str, Any]],
        metrics: Dict[str, Any],
        structure: Dict[str, Any],
        issues: List[Dict[str, Any]],
        risk_scores: List[Dict[str, Any]],
    ) -> Dict[str, List[Dict[str, Any]]]:
        issue_map = {}
        for issue in issues:
            if issue.get("file"):
                issue_map.setdefault(issue["file"], []).append(issue["message"])

        entry_point_scores = {
            item["path"]: item.get("score", 0)
            for item in structure.get("entry_point_details", [])
        }
        risk_map = {item["file"]: item for item in risk_scores}
        groups: Dict[str, List[Dict[str, Any]]] = {
            "runtime": [],
            "build_tooling": [],
            "generator": [],
            "test_runner": [],
            "vendor": [],
            "test_data": [],
            "documentation": [],
        }
        ranked_candidates: List[tuple[float, str, Dict[str, Any]]] = []

        for path, info in files.items():
            fc = classifyFile(path, info)
            if fc.isVendor or fc.isDependencyLock or fc.isGeneratedSdk or fc.isLocalization:
                continue
            group = self._categorize_hotspot(path, info)
            if group not in groups:
                continue
            # Only first-party runtime source should appear in runtime hotspots
            if group == "runtime":
                if fc.isTest or fc.isTestRunner or fc.isFixture or fc.isGenerator or fc.isBuildTooling or fc.isEnvironmentSetup or fc.isDocumentation or fc.isSpecification or fc.isConfig:
                    continue
                lower_path = path.replace("\\", "/").lower()
                if any(part in lower_path for part in {"/examples/", "/example/", "/samples/", "/sample/", "/demo/", "/demos/", "/tests/", "/test/", "/e2e/", "/integration/", "/smoke/", "/fixtures/", "/testdata/", "/wpt-import/", "/assets/", "/asset/", "/static/", "/docs_src/"}):
                    continue
            reasons = []
            if path in entry_point_scores:
                reasons.append("entry point")
            reasons.extend(issue_map.get(path, []))
            if not reasons:
                reasons.append(f"{info.get('line_count', 0)} lines")

            score = float(risk_map.get(path, {}).get("score", 0))
            score += min(float(info.get("line_count", 0)) / 40.0, 120.0)
            score += min(float(info.get("size", 0)) / 50000.0, 20.0)
            score += len(issue_map.get(path, [])) * 8
            score += float(entry_point_scores.get(path, 0)) / 10.0

            if group == "runtime":
                score += 10
                if fc.isConfig or fc.isDependencyLock or fc.isDocumentation:
                    score -= 40
            elif group == "test_data":
                score -= 10
            elif group == "documentation":
                score -= 15

            ranked_candidates.append(
                (
                    score,
                    path,
                    {
                        "path": path,
                        "reason": "; ".join(reasons),
                        "line_count": int(info.get("line_count", 0)),
                    },
                )
            )

        ranked_candidates.sort(key=lambda item: (item[0], item[2]["line_count"]), reverse=True)

        for _, path, item in ranked_candidates:
            group = self._categorize_hotspot(path, files[path])
            bucket = groups[group]
            if any(existing["path"] == path for existing in bucket):
                continue
            bucket.append(item)
            if len(bucket) >= 5:
                groups[group] = bucket

        return groups

    def _build_workflow_hints(
        self,
        structure: Dict[str, Any],
        frameworks: List[str],
        files: Dict[str, Dict[str, Any]],
        archetype: str = "",
    ) -> List[str]:
        hints = []
        entry_points_by_category = structure.get("entry_points_by_category", {})
        runtime_entries = entry_points_by_category.get("runtime", [])
        build_entries = entry_points_by_category.get("build", []) + entry_points_by_category.get("generator", [])
        environment_entries = entry_points_by_category.get("environment", [])

        if archetype in (ARCHETYPE_FRAMEWORK_LIBRARY,):
            hints.append("Framework/library repo detected. Choose the relevant API/runtime surface before editing.")
            dirs_lower = {d.lower() for d in structure.get("directories", [])}
            if any("api" in d for d in dirs_lower):
                hints.append("For API changes, start from the API layer.")
            if any("core" in d or "runtime" in d for d in dirs_lower):
                hints.append("For runtime/core changes, start from the runtime/core directories.")
            if any("compiler" in d for d in dirs_lower):
                hints.append("For compiler/lowering changes, start from the compiler directories.")
            if any("lite" in d or "mobile" in d for d in dirs_lower):
                hints.append("For mobile/lite runtime changes, start from the lite/mobile directories.")
            if any("go" in d for d in dirs_lower):
                hints.append("For Go bindings/tools, start from the go/ directory.")
            if any("tools" in d or "build" in d for d in dirs_lower):
                hints.append("For tooling/build changes, start from the tools/build directories.")
        elif archetype == ARCHETYPE_MONOREPO:
            hints.append("Monorepo: split by product/package first.")
            dirs = structure.get("directories", [])
            packages = sorted({p.split("/")[1] for p in dirs if "/" in p and p.split("/")[0].lower() in {"packages", "apps", "services", "crates"}})
            if packages:
                hints.append(f"Packages detected: {', '.join(packages[:4])}")
        elif archetype in (ARCHETYPE_BROWSER_ENGINE,):
            hints.append("Browser engine: start from the real runtime/browser entry point.")
        elif runtime_entries and archetype != ARCHETYPE_FRAMEWORK_LIBRARY:
            hints.append(f"Start runtime tracing from {runtime_entries[0]} (primary detected runtime entry point)")
        elif build_entries and archetype != ARCHETYPE_FRAMEWORK_LIBRARY:
            hints.append(f"Start build or generator tracing from {build_entries[0]}")
        if structure.get("has_tests"):
            if "pytest" in frameworks or any("pytest" in path for path in files):
                hints.append("Use the test suite as the fastest regression signal")
            else:
                hints.append("Use existing tests before making wide changes")
        if any(path in files for path in ["pyproject.toml", "package.json", "setup.py"]):
            hints.append("Read the project manifest before changing dependencies or startup flow")
        if any(path in files for path in ["README.md", "readme.md"]):
            hints.append("Use the README as the first source of product intent")
        if any(name == "go.mod" for name in (Path(path).name for path in files)):
            hints.append("This is a Go project — look for cmd/, api/, and server/ for entry points")
        if environment_entries:
            hints.append(f"Environment setup lives in {environment_entries[0]}")
        return hints[:5]

    def _detect_languages(self, file_types: Dict[str, int]) -> List[str]:
        language_map = {
            ".py": "python",
            ".js": "javascript",
            ".ts": "typescript",
            ".rs": "rust",
            ".go": "go",
            "go.mod": "go",
            "go.sum": "go",
            ".json": "json",
            ".yaml": "yaml",
            ".yml": "yaml",
            ".toml": "toml",
            ".md": "markdown",
            ".sh": "shell",
            ".bash": "shell",
            "Dockerfile": "docker",
            "Makefile": "make",
            ".cpp": "c++",
            ".c": "c++",
            ".h": "c++",
            ".hpp": "c++",
            ".cc": "c++",
            ".cxx": "c++",
            ".hh": "c++",
            ".hxx": "c++",
            ".cmake": "cmake",
        }
        languages = set()
        for ext in file_types:
            languages.add(language_map.get(ext, ext.lstrip(".") or ext.lower()))
        return sorted(languages)

    def _detect_primary_language(self, file_types: Dict[str, int], languages: List[str], files: Dict[str, Dict[str, Any]] = None) -> str:
        priority = {
            ".py": ("python", 100),
            ".ts": ("typescript", 95),
            ".js": ("javascript", 90),
            ".rs": ("rust", 85),
            ".go": ("go", 80),
            ".java": ("java", 75),
            ".kt": ("kotlin", 70),
            ".rb": ("ruby", 65),
            ".php": ("php", 60),
            ".cs": ("csharp", 55),
            ".cpp": ("c++", 95),
            ".c": ("c++", 95),
            ".h": ("c++", 95),
            ".hpp": ("c++", 95),
            ".cc": ("c++", 95),
            ".cxx": ("c++", 95),
            ".hh": ("c++", 95),
            ".hxx": ("c++", 95),
        }

        if files:
            # Weighted language detection based on file paths
            weighted_scores: Dict[str, int] = {}

            for path, info in files.items():
                ext = info.get("extension", "")
                language, base_score = priority.get(ext, (None, 0))
                if not language:
                    continue

                weight = 1.0
                context = self._classify_path_context(path, info)

                if context in {"test", "test_data", "documentation"}:
                    weight = 0.15
                elif context in {"vendor_generated", "resources", "data_or_config"}:
                    weight = 0.25
                elif context == "environment":
                    weight = 0.2
                elif context in {"browser_engine", "javascript_engine", "runtime_entry", "first_party_source", "core_utility", "runtime_source", "python_api"}:
                    weight = 2.2
                elif context in {"build_tooling", "generator", "lint_tooling", "tooling"}:
                    weight = 0.75

                weighted_scores[language] = weighted_scores.get(language, 0) + int(base_score * weight)

            if any(Path(path).name == "CMakeLists.txt" for path in files):
                weighted_scores["c++"] = weighted_scores.get("c++", 0) + 80
            if any(path.endswith("Cargo.toml") for path in files):
                weighted_scores["rust"] = weighted_scores.get("rust", 0) + 35
            if any(Path(path).name in {"go.mod", "go.sum"} for path in files):
                weighted_scores["go"] = weighted_scores.get("go", 0) + 120
            if any(Path(path).name == "go.mod" for path in files):
                weighted_scores["go"] = weighted_scores.get("go", 0) + 50
            if any("/cmd/" in path or path.startswith("cmd/") for path in files):
                weighted_scores["go"] = weighted_scores.get("go", 0) + 60

            if weighted_scores:
                sorted_langs = sorted(weighted_scores.items(), key=lambda x: x[1], reverse=True)
                top_lang, top_score = sorted_langs[0]

                if len(sorted_langs) >= 2:
                    second_lang, second_score = sorted_langs[1]

                    # Special case: Python + C++ polyglot repos (e.g. TensorFlow)
                    py = weighted_scores.get("python", 0)
                    cpp = weighted_scores.get("c++", 0)
                    if py > 0 and cpp > 0 and {top_lang, second_lang} == {"python", "c++"}:
                        min_count = min(py, cpp)
                        if min_count >= 2000:  # meaningful Python+C++ repo like TensorFlow
                            return "Python/C++"

                    if second_score >= top_score * 0.30 and second_score > 0:
                        lang_display = {"python": "Python", "c++": "C++", "go": "Go", "rust": "Rust", "typescript": "TypeScript", "javascript": "JavaScript", "java": "Java", "kotlin": "Kotlin", "ruby": "Ruby", "csharp": "C#"}
                        d1 = lang_display.get(top_lang, top_lang)
                        d2 = lang_display.get(second_lang, second_lang)
                        return f"{d1}/{d2}"
                return top_lang

        # Fallback to old logic if no files provided
        ranked = []
        for ext, count in file_types.items():
            language, score = priority.get(ext, (None, 0))
            if language:
                ranked.append((count, score, language))
        if ranked:
            ranked.sort(reverse=True)
            return ranked[0][2]
        return languages[0] if languages else "unknown"

    def _detect_git_context(self) -> Dict[str, Any]:
        try:
            inside = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=self.root_dir,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if inside.returncode != 0 or inside.stdout.strip() != "true":
                return {"enabled": False}

            branch = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=self.root_dir,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            status = subprocess.run(
                ["git", "status", "--short"],
                cwd=self.root_dir,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            return {
                "enabled": True,
                "branch": branch.stdout.strip() or "detached",
                "dirty": bool(status.stdout.strip()),
            }
        except (OSError, subprocess.SubprocessError):
            return {"enabled": False}

    def _find_issues(
        self,
        files: Dict[str, Dict[str, Any]],
        metrics: Dict[str, Any],
        structure: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        issues = []
        line_threshold = int(self.rules.get("large_file_line_threshold", 500))
        size_threshold = int(self.rules.get("large_file_size_threshold", 100_000))

        for filepath, info in files.items():
            fc = classifyFile(filepath, info)

            if info["todo_count"] > 0:
                issues.append(
                    {
                        "type": "todo",
                        "severity": "low",
                        "category": "maintainability",
                        "file": filepath,
                        "message": f"Contains {info['todo_count']} TODO/FIXME markers",
                        "timestamp": now_iso(),
                    }
                )

            if info["line_count"] > line_threshold:
                if fc.isVendor or fc.isDependencyLock:
                    continue
                policy = classifyLargeFilePolicy(filepath, info)
                is_config_or_doc = fc.isConfig or fc.isDocumentation or fc.isLocalization or fc.isSpecification
                if "{lines}" in policy:
                    message = policy.format(lines=info['line_count'])
                else:
                    message = policy
                issues.append(
                    {
                        "type": "large_file",
                        "severity": "medium",
                        "category": "maintainability" if is_config_or_doc else "structural",
                        "file": filepath,
                        "message": message,
                        "timestamp": now_iso(),
                    }
                )

            if info["size"] > size_threshold:
                if fc.isVendor or fc.isDependencyLock:
                    continue
                is_config_or_doc = fc.isConfig or fc.isDocumentation or fc.isLocalization or fc.isSpecification
                category = "maintainability" if is_config_or_doc else "structural"
                issues.append(
                    {
                        "type": "large_file_size",
                        "severity": "medium",
                        "category": category,
                        "file": filepath,
                        "message": f"File is {info['size'] // 1024}KB",
                        "timestamp": now_iso(),
                    }
                )

            metadata = info.get("metadata", {})
            drift_flags = metadata.get("doc_drift_flags") or []
            empty_headings = int(metadata.get("empty_heading_count", 0) or 0)
            doc_matches = metadata.get("doc_drift_matches") or []
            if drift_flags or empty_headings >= 3:
                details = []
                confidence = "medium"
                if drift_flags:
                    # Include evidence: matched phrase and line
                    if doc_matches:
                        evidence = "; ".join(f'"{label}" at line {ln}' for label, _, ln in doc_matches[:2])
                        details.append(f"possible drift indicator: {evidence}")
                        confidence = "low"  # placeholder text could be intentional in some contexts
                    else:
                        details.append("placeholder-like patterns")
                if empty_headings:
                    details.append(f"{empty_headings} empty-looking headings")
                fc = classifyFile(filepath, info)
                if fc.isGeneratedSdk or fc.isLocalization:
                    category = "structural"
                    detail = "Large generated sdk/localization file; review for readability and drift: " + ", ".join(details)
                else:
                    category = "maintainability"
                    if any("possible drift indicator" in d for d in details):
                        detail = "Documentation may contain drift: " + "; ".join(details)
                    else:
                        detail = "Documentation may be stale or scaffold-like: " + ", ".join(details)
                issues.append(
                    {
                        "type": "doc_code_drift",
                        "severity": "low" if confidence == "low" else "medium",
                        "category": category,
                        "file": filepath,
                        "message": detail,
                        "timestamp": now_iso(),
                    }
                )

        if not structure.get("has_tests"):
            issues.append(
                {
                    "type": "no_tests",
                    "severity": "high",
                    "category": "test",
                    "file": None,
                    "message": "No test files detected in project",
                    "timestamp": now_iso(),
                }
            )

        has_readme = any(Path(path).name.lower() in {"readme.md", "readme.txt", "readme"} for path in files)
        if not has_readme:
            issues.append(
                {
                    "type": "no_readme",
                    "severity": "medium",
                    "category": "structural",
                    "file": None,
                    "message": "No README file found",
                    "timestamp": now_iso(),
                }
            )

        archetype_data = detectRepoArchetype(files, [], "", [])
        if archetype_data["primaryArchetype"] in (ARCHETYPE_FRAMEWORK_LIBRARY, ARCHETYPE_MONOREPO):
            pass
        elif metrics.get("total_files", 0) > 3 and not structure.get("entry_points"):
            issues.append(
                {
                    "type": "no_entry_point",
                    "severity": "low",
                    "category": "runtime",
                    "file": None,
                    "message": "No clear entry point detected",
                    "timestamp": now_iso(),
                }
            )

        return issues

    def _score_file_risks(
        self,
        files: Dict[str, Dict[str, Any]],
        structure: Dict[str, Any],
        issues: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        entry_points = set(structure.get("entry_points", []))
        test_files = set(structure.get("test_files", []))
        test_index = self._build_test_index(test_files)
        issue_map: Dict[str, list[str]] = {}
        for issue in issues:
            if issue.get("file"):
                issue_map.setdefault(issue["file"], []).append(issue.get("type", "issue"))

        risks: list[dict[str, Any]] = []
        for path, info in files.items():
            fc = classifyFile(path, info)

            if path in test_files:
                continue
            if fc.isTest or fc.isTestRunner or fc.isFixture:
                continue
            if fc.isGeneratedSdk or fc.isGenerated:
                continue
            if fc.isVendor:
                continue
            if fc.isDependencyLock:
                continue
            if fc.isLocalization:
                continue
            if fc.isDocumentation or fc.isSpecification:
                continue
            if fc.isGenerator:
                continue
            if fc.isEnvironmentSetup:
                continue
            if fc.isBuildTooling:
                continue
            # Exclude examples and assets
            lower_path = path.replace("\\", "/").lower()
            if any(part in lower_path for part in {"/examples/", "/example/", "/samples/", "/sample/", "/demo/", "/demos/", "/assets/", "/asset/", "/static/"}):
                continue
            # Strict name-based exclusions — bypass classification gaps
            name_lower = Path(path).name.lower()
            ext_lower = Path(path).suffix.lower()
            if ext_lower in {".cc", ".cpp", ".cxx"} and name_lower.endswith(("_test.cc", "_test.cpp", "_test.cxx")):
                continue
            if ext_lower in {".cc", ".cpp", ".cxx"} and name_lower.endswith(("_gen.cc", "_gen.cpp", "_gen.cxx")):
                continue
            if ext_lower == ".go" and name_lower.endswith("_test.go"):
                continue
            if ext_lower == ".py" and name_lower.endswith("_test.py"):
                continue
            if name_lower.startswith("gen_") or "/gen_" in lower_path:
                continue
            if "/genop/" in lower_path:
                continue
            if name_lower.startswith("requirements") and name_lower.endswith(".txt"):
                continue

            risk_surface = classifyRiskSurface(path, info)

            score = 0
            factors: list[str] = []

            if path in entry_points:
                score += 30
                factors.append("entry point")
            line_count = int(info.get("line_count", 0))
            if line_count > 500 and (fc.isConfig or fc.isDependencyLock or fc.isDocumentation or fc.isLocalization):
                score += 8
                factors.append("large data/config file")
            elif line_count > 250:
                score += 10
                factors.append("moderate size")
            import_count = int(info.get("import_count", 0))
            if import_count >= 12:
                score += 15
                factors.append("many imports")
            elif import_count >= 6:
                score += 8
                factors.append("several imports")
            if info.get("has_main"):
                score += 10
                factors.append("runtime surface")
            if info.get("has_class") or info.get("has_function"):
                if not fc.isDocumentation:
                    score += 6
                    factors.append("executable code")
                else:
                    score += 2
                    factors.append("contains code examples")
            coverage = self._classify_test_coverage(path, test_index)
            if Path(path).suffix in {".py", ".go"} and coverage["status"] == "none":
                score += 12
                factors.append("no obvious paired test")
            if issue_map.get(path):
                score += 8 * len(issue_map[path])
                factors.extend(issue_map[path][:2])

            if score <= 0:
                continue
            if score >= 55:
                level = "high"
            elif score >= 30:
                level = "medium"
            else:
                level = "low"
            seen_factors: set[str] = set()
            deduped_factors: list[str] = []
            for f in factors:
                if f not in seen_factors:
                    seen_factors.add(f)
                    deduped_factors.append(f)
            risks.append(
                {
                    "file": path,
                    "score": min(score, 100),
                    "level": level,
                    "factors": deduped_factors[:6],
                    "risk_categories": self._risk_categories(path, info, deduped_factors),
                    "surface": risk_surface,
                    "coverage": coverage,
                }
            )

        risks.sort(key=lambda item: item["score"], reverse=True)
        return risks[:80]

    def _build_test_index(self, test_files: set[str]) -> Dict[str, Any]:
        entries = []
        for test_path in sorted(test_files):
            stem = Path(test_path).stem.lower()
            ext = Path(test_path).suffix.lower()
            # Go: server_test.go -> stem="server_test", normalized="server"
            if ext == ".go":
                normalized = stem.removesuffix("_test") if stem.endswith("_test") else stem
            else:
                normalized = stem.removeprefix("test_").removesuffix("_test")
            entries.append({"path": test_path, "stem": stem, "normalized": normalized})
        return {"entries": entries}

    def _classify_test_coverage(self, path: str, test_index: Dict[str, Any]) -> Dict[str, Any]:
        fc = classifyFile(path)
        ext = Path(path).suffix.lower()

        if ext not in {".py", ".go"}:
            return {"status": "not_applicable", "test_file": None}

        if ext == ".go":
            stem = Path(path).stem.lower()  # e.g. "server" from "server.go"
            expected_test = f"{stem}_test.go"
            for entry in test_index.get("entries", []):
                if entry["path"].endswith(expected_test) or entry["normalized"] == stem:
                    return {"status": "paired", "test_file": entry["path"]}
            return {"status": "none", "test_file": None}

        stem = Path(path).stem.lower()
        for entry in test_index.get("entries", []):
            if entry["normalized"] == stem:
                return {"status": "paired", "test_file": entry["path"]}

        for entry in test_index.get("entries", []):
            normalized = entry["normalized"]
            if stem and (stem in normalized.split("_") or normalized in stem):
                return {"status": "related", "test_file": entry["path"]}

        return {"status": "none", "test_file": None}

    def _risk_categories(self, path: str, info: Dict[str, Any], factors: List[str]) -> List[str]:
        fc = classifyFile(path, info)
        categories = set()

        if any(factor in factors for factor in {"large file", "moderate size", "many imports", "several imports"}):
            categories.add("structural")

        if "large data/config file" in factors:
            categories.add("maintainability")

        if any(factor in factors for factor in {"entry point", "runtime surface"}):
            categories.add("runtime")

        if "no obvious paired test" in factors:
            categories.add("test")

        surface = classifySurface(path, info)
        if surface == "runtime" or (Path(path).suffix == ".py" and (info.get("has_class") or info.get("has_function"))):
            categories.add("runtime")

        if fc.role == "documentation":
            categories.add("maintainability")

        return sorted(categories) or ["maintainability"]

    def _risk_surface(self, path: str, info: Dict[str, Any]) -> str:
        return classifyRiskSurface(path, info)

    def _group_risk_scores(
        self,
        risk_scores: List[Dict[str, Any]],
        files: Dict[str, Dict[str, Any]],
    ) -> Dict[str, List[Dict[str, Any]]]:
        groups: Dict[str, List[Dict[str, Any]]] = {
            "runtime": [],
            "runtime_surface": [],
            "build_tooling": [],
            "generator": [],
            "test_runner": [],
            "test_data": [],
            "documentation": [],
            "specification": [],
            "vendor": [],
            "generated_sdk": [],
            "dependency_lock": [],
            "environment_setup": [],
            "config": [],
            "example": [],
            "other": [],
        }
        for risk in risk_scores:
            path = risk.get("file", "")
            surface = risk.get("surface") or classifyRiskSurface(path, files.get(path, {}))
            if surface not in groups:
                surface = "other"
            groups[surface].append(risk)
        return {key: values[:10] for key, values in groups.items() if values}

    def _summarize_risk_categories(
        self,
        issues: List[Dict[str, Any]],
        risk_scores: List[Dict[str, Any]],
        structure: Dict[str, Any] = None,
        maintainability_percent: Optional[int] = None,
    ) -> Dict[str, Any]:
        summary: Dict[str, Any] = {
            "structural": {"level": "low", "signals": 0},
            "runtime": {"level": "unknown", "signals": 0},
            "test": {"level": "unknown", "signals": 0},
            "security": {"level": "not_assessed", "signals": 0},
            "maintainability": {"level": "low", "signals": 0},
        }

        for issue in issues:
            category = issue.get("category") or "maintainability"
            if category not in summary:
                summary[category] = {"level": "low", "signals": 0}
            summary[category]["signals"] += 1
            if issue.get("type") in {"large_file", "large_file_size"} and category != "maintainability":
                summary["maintainability"]["signals"] += 1

        for risk in risk_scores:
            for category in risk.get("risk_categories", []):
                if category not in summary:
                    summary[category] = {"level": "low", "signals": 0}
                summary[category]["signals"] += 1

        for category, data in summary.items():
            signals = int(data.get("signals", 0))
            if category == "security" and signals == 0:
                continue
            if category == "test":
                continue
            if category == "maintainability" and maintainability_percent is not None:
                # Use the same function as health scoring to avoid contradiction
                data["level"] = riskFromScore(maintainability_percent)
            else:
                high_threshold = 20 if category == "maintainability" else 8
                if signals >= high_threshold:
                    data["level"] = "high"
                elif signals >= 3:
                    data["level"] = "medium"
                elif signals >= 1:
                    data["level"] = "low"

        # Test signal: use structure test detection for more accurate reporting
        has_test_files = structure is not None and bool(structure.get("has_tests"))
        test_files_count = len(structure.get("test_files", [])) if structure else 0

        # Count e2e, integration, smoke vs fixture
        e2e_count = 0
        fixture_count = 0
        if structure:
            for tf in structure.get("test_files", []):
                lower_tf = tf.lower()
                if "/e2e/" in lower_tf or "e2e" in lower_tf.split("/")[-1]:
                    e2e_count += 1
                if "/fixtures/" in lower_tf or "/testdata/" in lower_tf or "/wpt-import/" in lower_tf:
                    fixture_count += 1

        has_coverage_warning = any(issue.get("type") == "scan_coverage" for issue in issues)

        if any(issue.get("type") == "no_tests" for issue in issues):
            if has_coverage_warning:
                summary["test"]["level"] = "missing"
                summary["test"]["reason"] = "No automated test suite was detected — scan coverage may be incomplete or test detection uncertain"
            else:
                summary["test"]["level"] = "missing"
                summary["test"]["reason"] = "No automated test suite was detected"
        elif has_test_files and test_files_count > 5:
            parts = []
            if e2e_count > 0:
                parts.append(f"{e2e_count} end-to-end test file(s)")
            if fixture_count > 0:
                parts.append(f"{fixture_count} test fixture file(s)")
            if parts:
                summary["test"]["level"] = "strong"
                summary["test"]["reason"] = "Several real test directories/files detected including " + "; ".join(parts)
            else:
                summary["test"]["level"] = "strong"
                summary["test"]["reason"] = f"Several real test directories/files detected ({test_files_count} test files)"
        elif has_test_files or test_files_count > 0:
            summary["test"]["level"] = "present"
            summary["test"]["reason"] = "present — coverage unknown"
        else:
            summary["test"]["level"] = "missing"
            summary["test"]["reason"] = "missing"

        if any(issue.get("type") == "no_entry_point" for issue in issues):
            summary["runtime"]["level"] = "medium"

        return summary

    def _calculate_health_score(
        self,
        issues: List[Dict[str, Any]],
        metrics: Dict[str, Any],
        risk_summary: Dict[str, Any],
    ) -> Dict[str, Any]:
        severity_penalties = self.rules.get("health_penalties", DEFAULT_AUDIT_RULES["health_penalties"])
        type_penalties = self.rules.get(
            "health_penalties_by_type",
            DEFAULT_AUDIT_RULES["health_penalties_by_type"],
        )
        floors = self.rules.get("health_score_floors", DEFAULT_AUDIT_RULES["health_score_floors"])
        score = 100.0

        # Check if security was assessed
        security_assessed = any(issue.get("category") == "security" for issue in issues)

        # Determine maturity — used for penalty discounts and scoring
        total_lines = max(1, metrics.get("total_lines", 0))
        total_files = max(1, metrics.get("total_files", 0))
        is_mature = total_lines >= 50000 or total_files >= 2000

        # Group issues by type to avoid over-penalizing many low-severity items
        issue_type_counts: Dict[str, int] = {}
        for issue in issues:
            itype = issue.get("type", "")
            if itype == "todo":
                continue  # TODOs are handled via maintainability score
            issue_type_counts[itype] = issue_type_counts.get(itype, 0) + 1

        # Apply penalties per issue TYPE (not per file) to avoid over-penalization
        for itype, count in issue_type_counts.items():
            if itype in type_penalties:
                penalty = float(type_penalties[itype]) * min(count, 10)  # cap per-type at 10x
                # Reduce large_file penalties for mature repos (50k+ lines or 2k+ files)
                if itype in {"large_file", "large_file_size"} and is_mature:
                    penalty *= 0.5
            else:
                # Use the median severity of this type
                sevs = [issue.get("severity", "low") for issue in issues if issue.get("type") == itype]
                median_sev = "medium" if len(sevs) > 5 else (sevs[0] if sevs else "low")
                penalty = float(severity_penalties.get(median_sev, 1)) * min(count, 5)
            score -= penalty

        # Use maintainability as the primary health anchor
        todo_categories = metrics.get("todo_categories", {})

        # TODO density: TODOs per 1000 lines — penalize by density, not raw count
        todo_density = metrics.get("open_todos", 0) / total_lines * 1000
        density_penalty = min(35, todo_density * 6) if not is_mature else min(25, todo_density * 4)

        maintainability = max(55, min(100, round(100 - density_penalty - min(20, len(issues) / 60))))
        documentation_todos = int(todo_categories.get("docs", 0) or 0)
        documentation_drift = sum(1 for issue in issues if issue.get("type") == "doc_code_drift")
        large_docs = sum(
            1
            for issue in issues
            if issue.get("type") == "large_file" and self._classify_path_context(str(issue.get("file") or "")) == "documentation"
        )
        documentation = max(0, min(100, 80 - min(30, documentation_todos * 3)))
        runtime_level = risk_summary.get("runtime", {}).get("level", "unknown")
        test_signal = risk_summary.get("test", {}).get("level", "unknown")

        # Compute health from maintainability + modifiers
        modifiers = 0
        if test_signal == "strong":
            modifiers += 5
        elif test_signal in ("missing", "none", "low"):
            modifiers -= 10
        if runtime_level == "high":
            modifiers -= 5
        elif runtime_level == "medium":
            modifiers -= 2
        if documentation <= 30:
            modifiers -= 5
        if not security_assessed:
            modifiers -= 5

        # Mature repos with strong tests get a small bonus
        if is_mature and test_signal == "strong":
            modifiers += 3

        final_score = max(30, min(100, round(maintainability + modifiers)))

        has_scan_coverage_warning = any(issue.get("type") == "scan_coverage" for issue in issues)
        if has_scan_coverage_warning:
            explanation = (
                "Health score may be unreliable due to incomplete scan coverage. "
                "Maintainability appears strong, but test presence and runtime complexity "
                "could not be fully assessed."
            )
        else:
            if final_score >= 70:
                explanation = (
                    "Score reflects repo maintenance risk, not project quality. "
                    "Strong test and architecture signals improve confidence, while TODO volume, oversized files, "
                    "runtime complexity, and unassessed security reduce it."
                )
            elif final_score >= 50:
                explanation = (
                    "Score reflects repo maintenance risk, not project quality. "
                    "TODO density and runtime complexity weigh on the score, "
                    "while test infrastructure and documentation quality provide some confidence."
                )
            else:
                explanation = (
                    "Score reflects repo maintenance risk, not project quality. "
                    "Significant TODO volume, runtime complexity, or documentation gaps "
                    "reduce confidence. Review the highest-risk areas before broad changes."
                )

        maintainability_risk_level = riskFromScore(maintainability)

        # Add confidence label for huge repos
        confidence_label = None
        confidence_reason = ""
        if total_lines >= 100000 or total_files >= 10000:
            confidence_label = "low_confidence"
            confidence_reason = "large repo — scan samples, does not read every file fully"
        elif total_lines >= 50000:
            confidence_label = "moderate_confidence"
            confidence_reason = "large repo — scan samples may miss some patterns"

        # Check if entry point detection was uncertain
        has_confidence_reasons = any(
            reason.get("level") == "medium"
            for reason in (risk_summary.get("entry_points_confidence", {}) or {}).values()
        )
        if has_confidence_reasons and not confidence_reason:
            confidence_reason = "entry-point uncertainty"
            confidence_label = confidence_label or "moderate_confidence"

        return {
            "score": final_score,
            "security_assessed": security_assessed,
            "reason": f"Security {'was assessed' if security_assessed else 'was not assessed'}",
            "explanation": explanation,
            "confidence_label": confidence_label or "normal",
            "confidence_reason": confidence_reason,
            "breakdown": {
                "maintainability_percent": maintainability,
                "maintainability_risk": maintainability_risk_level,
                "runtime_complexity": runtime_level,
                "test_signal": test_signal,
                "todo_density": round(todo_density, 2),
                "lines_scanned": total_lines,
                "documentation_percent": documentation,
                "documentation_reason": (
                    f"{documentation_drift} documentation file(s) show heuristic drift indicators; "
                    f"sample matches before treating them as stale. "
                    f"{large_docs} large documentation file(s) may need review; {documentation_todos} documentation TODO(s)"
                ),
                "security": "assessed" if security_assessed else "not_assessed",
            },
        }

    def create_checkpoint(self, file_data: Dict[str, Dict[str, Any]], audit: Dict[str, Any]) -> Dict[str, Any]:
        file_hashes = {path: info["hash"] for path, info in file_data.items()}
        checkpoint = Checkpoint(
            timestamp=now_iso(),
            file_hashes=file_hashes,
            file_list=sorted(file_data.keys()),
            summary=f"Checkpoint: {len(file_data)} files, health {audit['health_score']}%",
            issues=audit["issues"],
            metrics=audit["metrics"],
            health_score=audit["health_score"],
            health_score_data=audit.get("health_score_data"),
        )
        serialized = asdict(checkpoint)
        self.checkpoints.append(serialized)
        self._save_checkpoints()
        return serialized

    def diff_from_last_checkpoint(self, current_files: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        if not self.checkpoints:
            return {
                "is_first_scan": True,
                "new_files": sorted(current_files.keys()),
                "modified_files": [],
                "deleted_files": [],
                "new_count": len(current_files),
                "modified_count": 0,
                "deleted_count": 0,
                "summary": "First scan - no previous checkpoint",
            }

        last = self.checkpoints[-1]
        old_hashes = last.get("file_hashes", {})
        old_files = set(old_hashes.keys())
        new_files = set(current_files.keys())

        added = new_files - old_files
        deleted = old_files - new_files
        common = new_files & old_files
        modified = [path for path in common if current_files[path]["hash"] != old_hashes.get(path)]

        return {
            "is_first_scan": False,
            "new_files": sorted(added),
            "modified_files": sorted(modified),
            "deleted_files": sorted(deleted),
            "new_count": len(added),
            "modified_count": len(modified),
            "deleted_count": len(deleted),
            "last_checkpoint_time": last.get("timestamp"),
            "summary": (
                f"+{len(added)} new, ~{len(modified)} modified, "
                f"-{len(deleted)} deleted since last checkpoint"
            ),
        }

    def is_significant_change(self, diff: Dict[str, Any]) -> bool:
        if diff.get("is_first_scan"):
            return True

        thresholds = self.rules.get(
            "significant_change_thresholds",
            DEFAULT_AUDIT_RULES["significant_change_thresholds"],
        )
        return (
            diff.get("new_count", 0) >= int(thresholds.get("new_files", 1))
            or diff.get("modified_count", 0) >= int(thresholds.get("modified_files", 1))
            or diff.get("deleted_count", 0) >= int(thresholds.get("deleted_files", 1))
        )
