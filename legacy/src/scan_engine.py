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
    classifyComponentRole,
    classifyFile,
    classifyLargeFilePolicy,
    classifyRiskSurface,
    classifySurface,
    monorepo_component_key,
    riskFromScore,
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


def _strip_html(text: str) -> str:
    if not text:
        return ""
    return HTML_TAG_RE.sub("", text).strip()


def _strip_markdown(text: str) -> str:
    if not text:
        return ""
    text = MARKDOWN_LINK_RE.sub(r"\1", text)
    text = text.replace("**", "").replace("__", "").replace("*", "").replace("_", "")
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


def classify_path_context(filepath: str, info: Optional[Dict[str, Any]] = None) -> str:
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
    framework_sdk_dirs = {"core", "python", "compiler", "lite", "runtime", "api", "go", "java", "cc", "c", "tools"}
    if len(parts) >= 2 and parts[1] in framework_sdk_dirs:
        if parts[1] == "python":
            return "python_api"
        if parts[1] in {"core", "compiler", "lite", "runtime"}:
            return "runtime_source"
        return "first_party_source"
    return "application"


def _component_key_for_path(filepath: str) -> Optional[str]:
    parts = tuple(Path(filepath).parts)
    if len(parts) < 2 or any(part.startswith(".") for part in parts[:-1]):
        if parts and parts[0] == ".devcontainer":
            return parts[0]
        return None
    return monorepo_component_key(filepath)


def _component_role_for_context(key: str) -> str:
    return classifyComponentRole(key)


def _is_runtime_hotspot_not_entry(filepath: str) -> bool:
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
    if name == "build.rs":
        return False
    if stem in runtime_hotspot_names:
        return True
    return False


def _categorize_entry_point(filepath: str, info: Any = None) -> str:
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

    if any(part in {"assets", "asset", "static"} for part in parts):
        return "documentation"
    if any(part in {"docs", "doc", "documentation", "docs_src"} for part in parts):
        return "documentation"
    if any(part in {"examples", "example", "samples", "sample", "demo", "demos"} for part in parts):
        return "example"

    if any(part in {"api", "apis"} for part in parts) and fc.isRuntimeSource:
        return "runtime_surface"

    if name in {"setup.py", "package.json", "pyproject.toml"}:
        return "packaging"

    if name == "build.rs" or name.startswith("build.") or name.startswith("install."):
        return "build"

    if re.search(r"/tools/.+/main\.(cpp|cc|c)$", lower_path):
        return "build"
    if re.search(r"/tools/.+\.(cpp|cc|c)$", lower_path) and name.startswith(("clang", "llvm-", "lld", "llc", "opt", "bugpoint")):
        return "build"
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
    if re.search(r"/([^/]+)/(\1)\.go$", lower_path):
        return "runtime"
    if re.search(r"/cmd/[^/]+/[^/]+\.go$", lower_path) and (info or {}).get("has_main", False):
        return "runtime"
    if parts and parts[0] == "utilities":
        return "tooling"
    return "tooling"


def _categorize_hotspot(filepath: str, info: Dict[str, Any]) -> str:
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


class ScanEngine:
    """Handles file scanning, analysis, and caching."""

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
        except OSError:
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
                if _is_bad_identity_text(line):
                    continue
                if line.startswith("![") or line.startswith("[!"):
                    continue
                if 'src="http' in line.lower() or "src='http" in line.lower():
                    continue
                if "raw.githubusercontent.com" in line.lower():
                    continue
                if "shields.io" in line.lower():
                    continue
                if "|" in line and ("--" in line or "**" in line or "[!" in line):
                    continue
                if line.startswith("`") and line.endswith("`"):
                    continue
                if len(line) < 15 and not line.endswith("."):
                    continue
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
                line_no = content[:m.start()].count("\n")
                raw_lines = content.splitlines()
                if line_no < len(raw_lines):
                    matched_line = raw_lines[line_no].strip()[:100]
                    matched_lines.append((label, matched_line, line_no + 1))
                break

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