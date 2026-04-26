from __future__ import annotations

import hashlib
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils import DEFAULT_AUDIT_RULES, DEFAULT_PATTERNS, merge_dicts, now_iso, read_json, write_json

SCAN_CACHE_VERSION = 1


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

    def _path_parts(self, filepath: str) -> tuple[str, ...]:
        return Path(filepath).parts

    def _path_parts_lower(self, filepath: str) -> tuple[str, ...]:
        return tuple(part.lower() for part in self._path_parts(filepath))

    def _is_generated_sdk_file(self, filepath: str) -> bool:
        lower_path = filepath.replace("\\", "/").lower()
        name = Path(filepath).name.lower()
        if "/gen/" in lower_path or "/generated/" in lower_path:
            return True
        if name.endswith(".gen.ts") or name.endswith(".generated.ts"):
            return True
        if name in {"sdk.gen.ts", "types.gen.ts", "client.gen.ts"}:
            return True
        if name.endswith(".gen.go") or name.endswith(".generated.go"):
            return True
        if name.endswith("_gen.go") or name.endswith("_generated.go"):
            return True
        parts = Path(filepath).parts
        if len(parts) >= 2 and parts[0].lower() in {"gen", "generated"}:
            return True
        return False

    def _is_localization_file(self, filepath: str) -> bool:
        lower_path = filepath.replace("\\", "/").lower()
        if "/i18n/" in lower_path or "/locales/" in lower_path or "/translations/" in lower_path:
            return True
        # Language-specific files inside i18n/locales folders
        lang_pattern = re.compile(r"/(?:i18n|locales|translations)/[^/]+\.\w+$")
        if lang_pattern.search(lower_path):
            parts = lower_path.split("/")
            name = parts[-1] if parts else ""
            stem = name.split(".")[0] if "." in name else name
            if stem in {"en", "fr", "de", "es", "it", "pt", "ru", "ja", "zh", "ko", "ar", "hi", "nl", "pl", "tr", "sv", "da", "fi", "nb", "cs", "hu", "ro", "uk", "el", "he", "th", "vi", "id", "ms", "tl", "bn", "ta", "te", "mr", "gu", "kn", "ml"}:
                return True
            return True
        return False

    def _is_specification_file(self, filepath: str) -> bool:
        lower_path = filepath.replace("\\", "/").lower()
        ext = Path(filepath).suffix.lower()
        name = Path(filepath).name.lower()
        if ext != ".md":
            return False
        # Files in spec/ or adr/ directories are spec docs
        if "/specs/" in lower_path or "/adr/" in lower_path:
            return True
        # Files in docs/ that contain spec-related keywords in the filename
        if "/docs/" in lower_path:
            spec_keywords = ["spec", "design", "architecture", "adr", "proposal"]
            if any(kw in name for kw in spec_keywords):
                return True
            return False
        # Standalone markdown files anywhere with spec-related keywords in the filename
        spec_keywords = ["spec", "design", "architecture", "adr", "proposal"]
        if any(kw in name for kw in spec_keywords):
            return True
        return False

    def _classify_path_context(self, filepath: str, info: Optional[Dict[str, Any]] = None) -> str:
        lower_path = filepath.replace("\\", "/").lower()
        parts = self._path_parts_lower(filepath)
        ext = str((info or {}).get("extension") or Path(filepath).suffix).lower()
        name = Path(filepath).name.lower()

        # Generated SDK — check before vendor to catch project-owned generated code
        if self._is_generated_sdk_file(filepath):
            return "generated_sdk"

        # Localization/resource files
        if self._is_localization_file(filepath):
            return "localization_resource"

        # Specification/documentation markdown files
        if self._is_specification_file(filepath):
            return "specification_documentation"

        # Generated/bundled assets — highest priority to catch early
        if "/src-tauri/gen/" in lower_path:
            return "vendor_generated"
        if any(token in lower_path for token in ["vendor/", "third_party/", "3rdparty/", "node_modules/"]):
            return "vendor_generated"
        if name in {"package-lock.json", "pnpm-lock.yaml", "yarn.lock", "cargo.lock"}:
            return "vendor_generated"
        if ext == ".map" or (ext == ".js" and "min" in name):
            return "vendor_generated"

        if any(part == ".devcontainer" for part in parts):
            return "environment"
        if name == "build.rs" or name.startswith("build.") or name.startswith("configure.") or name.startswith("install."):
            return "build_tooling"
        if "generator" in name or name.startswith("generate_") or name.startswith("gen_"):
            return "generator"
        if "/rust/build.rs" in lower_path or "/bytecode/asminterpreter/gen_" in lower_path:
            return "generator"
        if "/meta/generators/" in lower_path:
            return "generator"
        if "/meta/linters/" in lower_path:
            return "lint_tooling"
        if "/meta/" in lower_path:
            return "build_tooling"
        if "/utilities/" in lower_path or "/scripts/" in lower_path or "/tools/" in lower_path:
            return "tooling"
        if "/documentation/" in lower_path or "/docs/" in lower_path:
            return "documentation"
        if "/tests/" in lower_path or "/test/" in lower_path or "/wpt-import/" in lower_path:
            if "wpt-import" in lower_path or ext in {".json", ".txt", ".html"}:
                return "test_data"
            return "test"
        if "/libraries/libweb/" in lower_path:
            return "browser_engine"
        if "/libraries/libjs/" in lower_path:
            return "javascript_engine"
        if "/libraries/libmain/" in lower_path:
            return "runtime_entry"
        if ext == ".go" and name.endswith("_test.go"):
            return "test"
        if any(part in {"tests", "test", "spec"} for part in parts) or name.startswith("test_") or ".spec." in name:
            if "wpt-import" in lower_path or ext in {".json", ".txt", ".html"}:
                return "test_data"
            return "test"
        if "wpt-import" in lower_path or "fixtures" in lower_path:
            return "test_data"
        if any(part in {"docs", "doc", "documentation"} for part in parts) or ext == ".md":
            return "documentation"
        if parts and parts[0] == "meta":
            if len(parts) > 1 and parts[1] == "generators":
                return "generator"
            if len(parts) > 1 and parts[1] == "linters":
                return "lint_tooling"
            return "build_tooling"
        if parts and parts[0] == "libraries":
            if len(parts) > 1 and parts[1] == "libweb":
                return "browser_engine"
            if len(parts) > 1 and parts[1] == "libjs":
                return "javascript_engine"
            if len(parts) > 1 and parts[1] == "libmain":
                return "runtime_entry"
            if len(parts) > 1 and parts[1] == "libtest":
                return "test_support"
            return "first_party_source"
        if parts and parts[0] == "ak":
            return "core_utility"
        if len(parts) > 1 and parts[0] == "base" and parts[1] == "res":
            return "resources"
        if parts and parts[0] in {"services", "ui", "cmd", "api", "server"}:
            return "runtime_source"
        if parts and parts[0] in {"utilities", "scripts", "tools"}:
            return "tooling"
        # e2e directories
        if any(part == "e2e" for part in parts):
            return "test"
        # demo/sample data directories
        if any(part in {"demo-vault", "demo-vault-v2", "demo", "samples", "examples"} for part in parts):
            return "test_data"
        if ext in {".json", ".yaml", ".yml", ".toml", ".ini", ".cfg"}:
            return "data_or_config"
        return "application"

    def _component_key_for_path(self, filepath: str) -> Optional[str]:
        parts = self._path_parts(filepath)
        lower_parts = self._path_parts_lower(filepath)
        if len(parts) < 2 or any(part.startswith(".") for part in parts[:-1]):
            if parts and parts[0] == ".devcontainer":
                return parts[0]
            return None

        top = lower_parts[0]
        if top in {"tests", "test"}:
            return "/".join(parts[:2]) if len(parts) >= 2 else parts[0]
        if top in {"docs", "doc", "documentation"}:
            return parts[0]
        if top == ".devcontainer":
            return parts[0]
        if top == "meta":
            if len(parts) >= 2 and lower_parts[1] in {"generators", "linters", "cmake"}:
                return "/".join(parts[:2])
            return parts[0]
        if top == "libraries":
            return "/".join(parts[:2]) if len(parts) >= 2 else parts[0]
        if top == "base" and len(parts) >= 2 and lower_parts[1] == "res":
            return "/".join(parts[:2])
        if top in {"services", "utilities", "ui", "cmd", "api", "server"} and len(parts) >= 2:
            return "/".join(parts[:2])

        # Monorepo splitting: packages/, apps/, services/, crates/, modules/, libs/
        monorepo_roots = {"packages", "apps", "services", "crates", "modules", "libs"}
        if top in monorepo_roots and len(parts) >= 2:
            second_level = lower_parts[1] if len(lower_parts) > 1 else ""
            if second_level:
                return "/".join(parts[:2])

        return parts[0]

    def _component_role_for_context(self, key: str) -> str:
        lower_key = key.lower()
        specific_roles = {
            "vendor": "third-party or vendored dependencies — track only, do not refactor by default",
            "cmd": "Go CLI entry points and command definitions",
            "api": "API surface and request handling",
            "server": "server runtime and HTTP handlers",
            "libraries/libcompress": "compression and archive codecs",
            "libraries/libcore": "platform and event-loop utilities",
            "libraries/libcrypto": "cryptography and certificate handling",
            "libraries/libdatabase": "database and storage layer",
            "libraries/libdevtools": "developer tools integration",
            "libraries/libdiff": "diff and patch utilities",
            "libraries/libdns": "DNS protocol and resolution",
            "libraries/libfilesystem": "filesystem abstraction",
            "libraries/libgc": "garbage collector infrastructure",
            "libraries/libgfx": "graphics, fonts, and image codecs",
            "libraries/libhttp": "HTTP networking stack",
            "libraries/libidl": "IDL parsing and bindings support",
            "libraries/libimagedecoderclient": "image decoder client bindings",
            "libraries/libipc": "interprocess communication",
            "libraries/libline": "command-line editing utilities",
            "libraries/libmedia": "media playback and container support",
            "libraries/libregex": "regular expression engine",
            "libraries/librequests": "network request orchestration",
            "libraries/libsyntax": "syntax parsing and highlighting",
            "libraries/libtextcodec": "text encoding data and codecs",
            "libraries/libthreading": "threading and concurrency primitives",
            "libraries/libtls": "TLS and secure transport",
            "libraries/libunicode": "Unicode data and text processing",
            "libraries/liburl": "URL parsing and canonicalization",
            "libraries/libwasm": "WebAssembly runtime and validation",
            "libraries/libwebsocket": "WebSocket protocol support",
            "libraries/libwebview": "browser embedding and view integration",
            "libraries/libxml": "XML parsing and DOM support",
            "services/imagedecoder": "image decoder service",
            "services/requestserver": "network request service",
            "services/webcontent": "page runtime service",
            "services/webdriver": "WebDriver automation service",
            "services/webworker": "worker runtime service",
            "utilities": "developer command-line utilities",
            "meta/utils": "build and developer utilities",
            "meta/lagom": "host-side tools and standalone apps",
            "meta/cmake": "build system integration",
            "base/res": "resources, default config, and assets",
            "tests/ak": "core utility tests",
        }
        if lower_key in specific_roles:
            return specific_roles[lower_key]
        if lower_key.startswith("tests/libweb"):
            return "test suite / WPT fixtures"
        if lower_key.startswith("tests/libjs"):
            return "JavaScript engine tests"
        if lower_key.startswith("tests"):
            return "test suite / test infrastructure"
        if lower_key.startswith("documentation") or lower_key.startswith("docs"):
            return "documentation"
        if lower_key.startswith(".devcontainer"):
            return "development environment"
        if lower_key.startswith("meta/generators"):
            return "code generation tooling"
        if lower_key.startswith("meta/linters"):
            return "lint tooling"
        if lower_key.startswith("meta/cmake"):
            return "build tooling"
        if lower_key.startswith("meta"):
            return "build and developer tooling"
        if lower_key.startswith("libraries/libweb"):
            return "browser engine code"
        if lower_key.startswith("libraries/libjs"):
            return "JavaScript engine code"
        if lower_key.startswith("libraries/libmain"):
            return "runtime entrypoint support"
        if lower_key.startswith("libraries/libtest"):
            return "test support library"
        if lower_key == "ak":
            return "core utility library"
        if lower_key.startswith("base/res"):
            return "resources and default assets"
        if lower_key.startswith("services"):
            return "runtime services"
        if lower_key.startswith("utilities"):
            return "developer utilities"
        if lower_key.startswith("ui"):
            return "user interface"
        return self._infer_component_role(key)

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

    def _categorize_entry_point(self, filepath: str) -> str:
        lower_path = filepath.replace("\\", "/").lower()
        parts = self._path_parts_lower(filepath)
        name = Path(filepath).name.lower()

        if any(part == ".devcontainer" for part in parts):
            return "environment"
        if any(part in {"tests", "test", "spec", "e2e"} for part in parts) or "libtest" in lower_path:
            return "test"
        if parts and parts[0] == "meta":
            if len(parts) > 1 and parts[1] == "generators":
                return "generator"
            return "build"
        if (
            name.startswith("generate_")
            or name.startswith("gen_")
            or "codegen" in lower_path
            or "generator" in lower_path
            or "asmintgen" in lower_path
        ):
            return "generator"
        if any(token in lower_path for token in ["cmake/", "/build", "/configure", "/install", "toolchain", "vcpkg"]):
            return "build"
        if name in {"setup.py", "package.json", "pyproject.toml"}:
            return "packaging"

        # Generated SDK files are never runtime entry points
        if self._is_generated_sdk_file(filepath):
            return "generator"

        # Runtime hotspot names are not entry points
        if self._is_runtime_hotspot_not_entry(filepath):
            return "tooling"

        if name == "build.rs" or name.startswith("build.") or name.startswith("install."):
            return "build"
        if parts and parts[0] == "libraries" and len(parts) > 1 and parts[1] == "libmain":
            return "runtime"
        if parts and parts[0] in {"services", "ui"}:
            return "runtime"
        if parts and parts[0] == "libraries":
            return "runtime"
        if any(part in {"src", "source", "app", "cmd"} for part in parts):
            return "runtime"
        if parts and parts[0] == "utilities":
            return "tooling"
        return "tooling"

    def _categorize_hotspot(self, filepath: str, info: Dict[str, Any]) -> str:
        context = self._classify_path_context(filepath, info)
        if context in {"test", "test_data", "test_support"}:
            return "test_data"
        if context in {"documentation", "specification_documentation"}:
            return "documentation"
        if context in {"vendor_generated", "generated_sdk"}:
            return "vendor"
        if context in {"environment", "build_tooling", "generator", "lint_tooling", "tooling"}:
            return "build_tooling"
        if context == "localization_resource":
            return "build_tooling"
        return "runtime"

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
            heading = ""
            for raw_line in content.splitlines():
                line = raw_line.strip()
                if not line or line == "```":
                    continue
                if line.startswith("#"):
                    if not heading:
                        heading = line.lstrip("#").strip()[:160]
                    continue
                return line[:160]
            return heading

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

        return metadata

    def _extract_doc_metadata(self, content: str, ext: str, filename: str) -> Dict[str, Any]:
        """Extract documentation quality signals that can drift from code reality."""
        lower_name = filename.lower()
        if ext not in {".md", ".txt"} and lower_name not in {"readme.md", "readme.txt", "readme"}:
            return {}

        metadata: Dict[str, Any] = {}
        title_match = re.search(r"^\s*#\s+(.+)$", content, re.MULTILINE)
        if title_match:
            metadata["doc_title"] = title_match.group(1).strip()[:160]

        lowered = content.lower()
        placeholder_patterns = [
            r"\btbd\b",
            r"\bcoming soon\b",
            r"\bplaceholder\s*[:\-]",
            r"\bnot implemented yet\b",
            r"\bstub\b",
            r"\bfill me\b",
            r"\[\s*\]",
            r"^\s*-\s*$",
        ]
        hits: list[str] = []
        for pattern in placeholder_patterns:
            if re.search(pattern, lowered, re.MULTILINE):
                hits.append(pattern)

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

        metrics = self._compute_metrics(file_data)
        structure = self._analyze_structure(file_data)
        patterns = self._detect_patterns(file_data)
        architecture = self._summarize_architecture(metrics, structure, patterns)
        issues = self._find_issues(file_data, metrics, structure)
        risk_scores = self._score_file_risks(file_data, structure, issues)
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
        health_score_data = self._calculate_health_score(issues, metrics, {})
        maintainability_pct = health_score_data.get("breakdown", {}).get("maintainability_percent", 85)
        risk_summary = self._summarize_risk_categories(issues, risk_scores, structure, maintainability_pct)
        # Update health score risk summary so it includes the maintainability risk from the breakdown
        risk_summary.setdefault("maintainability", {})["level"] = self._maintainability_score_to_risk(maintainability_pct)
        # Recalculate health score with updated risk summary that includes correct maintainability
        health_score_data = self._calculate_health_score(issues, metrics, risk_summary)

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

            # Categorize based on path
            todo_count = info.get("todo_count", 0)
            if todo_count > 0:
                lower_path = path.lower()
                if any(token in lower_path for token in ["tests/", "test_", "spec", "wpt-import"]):
                    todo_categories["tests_fixtures"] += todo_count
                elif any(token in lower_path for token in ["docs", "documentation", ".md"]):
                    todo_categories["docs"] += todo_count
                elif any(token in lower_path for token in ["vendor", "generated", "dist", "build", "gen/"]):
                    todo_categories["vendor_generated"] += todo_count
                elif any(token in lower_path for token in [".sh", ".bash", "makefile", "dockerfile", "config", "meta"]):
                    todo_categories["tooling"] += todo_count
                elif any(token in lower_path for token in ["/i18n/", "/locales/", "/translations/"]):
                    todo_categories["vendor_generated"] += todo_count
                elif "/specs/" in lower_path or "/adr/" in lower_path:
                    todo_categories["docs"] += todo_count
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

    def _calculate_entry_point_score(self, filepath: str, parts: tuple[str, ...]) -> int:
        """Calculate a score for an entry point based on its directory."""
        score = 100  # Base score

        # Check directory patterns
        lower_parts = tuple(part.lower() for part in parts)
        category = self._categorize_entry_point(filepath)

        # High weight for source directories
        if any(part in {"src", "source", "app", "cmd", "main"} for part in lower_parts):
            score += 50

        # High weight for Libraries directories
        if any(part == "libraries" for part in lower_parts):
            score += 40

        # Medium weight for runtime directories
        if any(part in {"runtime", "bin", "sbin"} for part in lower_parts):
            score += 20

        if category == "runtime":
            score += 25
        elif category == "build":
            score += 5
        elif category == "generator":
            score += 8
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
                any(part.lower() in {"tests", "test", "spec"} for part in parts[:-1])
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

            if info.get("has_main") and not is_test_file:
                entry_points.append(filepath)
                # Calculate score based on directory
                score = self._calculate_entry_point_score(filepath, parts)
                category = self._categorize_entry_point(filepath)
                detail = {
                    "path": filepath,
                    "score": score,
                    "category": category,
                }
                entry_point_details.append(detail)
                entry_points_by_category.setdefault(category, []).append(detail)

        ordered_entry_points: List[Dict[str, Any]] = []
        for category in ["runtime", "build", "generator", "tooling", "test", "environment", "packaging"]:
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
                "Scan coverage warning: Go module detected (go.mod exists on disk) but no Go source files "
                "were scanned. Project identity and test detection may be incomplete."
            )
        elif has_go_mod_on_disk and not has_go_mod_scanned:
            warning = (
                "Scan coverage warning: go.mod exists on disk but was not included in the scan. "
                "Results may be affected by filtering."
            )
        elif underrepresented:
            if test_ratio >= 0.55:
                warning = (
                    "Scan coverage warning: Tests dominate this scan and major source directories "
                    "appear underrepresented. Results may be incomplete or affected by filtering."
                )
            else:
                warning = (
                    "Scan coverage warning: Major source directories appear underrepresented. "
                    "Results may be incomplete or affected by filtering."
                )
        elif test_ratio >= 0.7 and source_ratio <= 0.2:
            warning = (
                "Scan coverage warning: Tests represent a large share of scanned lines while "
                "source directories are lightly represented. Results may be incomplete."
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
        project_type = self._infer_project_type(primary_language, structure, frameworks, components)
        purpose = self._infer_project_purpose(description, files, components)
        hotspot_groups = self._identify_hotspots(files, metrics, structure, issues, risk_scores)
        primary_hotspots = hotspot_groups.get("runtime", []) + hotspot_groups.get("build_tooling", [])
        important_files = self._identify_important_files(files, metrics, structure, primary_hotspots)
        workflow_hints = self._build_workflow_hints(structure, frameworks, files)

        # Calculate confidence reasons
        confidence_reasons = self._calculate_confidence_reasons(
            project_name, description, primary_language, languages, files, structure, primary_hotspots
        )

        summary_bits = [project_name or self.root_dir.name, "appears to be", project_type]
        summary = " ".join(bit for bit in summary_bits if bit).strip()
        if purpose:
            summary = f"{summary}. {purpose}"
        # Clean up duplicate project names in summary
        if project_name and summary.lower().startswith(project_name.lower() + " appears to be " + project_name.lower()):
            summary = summary[len(project_name) + 1:].strip()
            summary = summary[0].upper() + summary[1:] if summary else ""

        return {
            "project_name": project_name or self.root_dir.name,
            "project_type": project_type,
            "primary_language": primary_language,
            "languages": languages,
            "frameworks": frameworks,
            "purpose": purpose,
            "summary": summary,
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

    def _detect_project_identity(self, files: Dict[str, Dict[str, Any]]) -> tuple[str | None, str]:
        candidates = ["pyproject.toml", "package.json", "setup.py", "go.mod", "CMakeLists.txt", "README.md", "readme.md"]
        project_name = None
        description = ""

        for candidate in candidates:
            info = files.get(candidate)
            if not info:
                continue
            metadata = info.get("metadata", {})
            if not project_name and metadata.get("name"):
                project_name = metadata["name"]
            if not description and metadata.get("description"):
                description = metadata["description"]
            if not description and info.get("summary"):
                description = info["summary"]
            # For README files, use the summary as the project name if no name is found
            if not project_name and candidate.lower() in {"readme.md", "readme"}:
                project_name = metadata.get("doc_title") or info.get("summary")
            elif candidate.lower() in {"readme.md", "readme"}:
                readme_title = metadata.get("doc_title")
                if readme_title and project_name and project_name == project_name.lower():
                    project_name = readme_title

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
            if score >= 1.0 and (framework_primary_support[label] >= 0.5 or label in {"pytest", "unittest"})
        ]

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
        return components

    def _infer_component_role(self, key: str) -> str:
        role_map = {
            "core": "runtime orchestration",
            "agents": "agent behaviors and coordination",
            "memory": "state and persistence",
            "dashboard": "operator UI and reporting",
            "models": "data models and schemas",
            "tests": "regression safety and validation",
            "docs": "documentation and onboarding",
            "config": "configuration and defaults",
            "scripts": "automation and tooling",
            "language": "language, prompts, and communication",
            "evolution": "optimization and strategy logic",
            "api": "API surface and request handling",
            "web": "web application surface",
            "e2e": "end-to-end tests",
            "demo-vault": "demo/sample data",
            "demo-vault-v2": "demo/sample data",
            "demo": "demo/sample data",
            "samples": "demo/sample data",
            "examples": "demo/sample data",
            "cmd": "Go CLI entry points",
            "server": "server runtime and HTTP handlers",
            "llama": "native C++/GGML/llama.cpp backend",
            "ml": "native ML backend components",
            "vendor": "third-party or vendored dependencies",
            "opencode": "CLI / AI coding agent core",
        }
        tail = key.split("/")[-1].lower()
        if tail in role_map:
            return role_map[tail]
        # Monorepo subpackage heuristic
        if "/" in key:
            parts = key.split("/")
            pkg_root = parts[0].lower()
            sub = parts[1].lower()
            if pkg_root in {"packages", "apps", "services", "crates", "modules", "libs"}:
                # Try known subpackage names
                sub_roles = {
                    "sdk": "generated/client SDK",
                    "app": "frontend application",
                    "cli": "CLI application",
                    "web": "web application",
                    "console": "console/web app",
                    "desktop": "desktop shell",
                    "server": "server runtime",
                    "api": "API service",
                    "core": "core library",
                    "shared": "shared utilities",
                    "types": "type definitions",
                    "utils": "utility modules",
                    "ui": "user interface components",
                    "containers": "container/build tooling",
                    "mobile": "mobile application",
                }
                if sub in sub_roles:
                    return sub_roles[sub]
                return f"{sub} package"
        return "application logic"

    def _infer_project_type(
        self,
        primary_language: str,
        structure: Dict[str, Any],
        frameworks: List[str],
        components: List[Dict[str, Any]],
    ) -> str:
        component_paths = {component["path"].lower() for component in components}
        descriptors: List[str] = []

        if primary_language == "c++" and "libraries/libweb" in component_paths:
            descriptors.append("C++ browser engine / web browser project")
        elif primary_language == "go":
            descriptors.append("Go-based CLI/server application")
            has_backend_components = any(
                "llama" in path or "ggml" in path or "backend" in path or "ml/" in path
                for path in component_paths
            )
            if has_backend_components:
                descriptors.append("with native C++/GGML/llama.cpp backend components")
        else:
            descriptors.append(f"{primary_language} project" if primary_language != "unknown" else "software project")

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
            descriptors.append("and a test suite")

        # Add frontend/backend architecture hints from components
        has_frontend = any(path in component_paths for path in {"src", "ui", "web", "frontend", "app/ui"})
        has_backend = any(path in component_paths for path in {"src-tauri", "api", "backend", "server"})
        if has_frontend and has_backend:
            descriptors.append("with frontend and backend components")
        elif has_frontend:
            descriptors.append("with frontend components")
        elif has_backend:
            descriptors.append("with backend components")

        # Detect React UI
        if any("ui" in path or "app" in path for path in component_paths):
            has_react = any(
                "package.json" in path or Path(path).name == "package.json"
                for path in structure.get("config_files", [])
            )
            if has_react and not any(descriptor.startswith("React") for descriptor in descriptors):
                pass

        return " ".join(descriptors)

    def _infer_project_purpose(
        self,
        description: str,
        files: Dict[str, Dict[str, Any]],
        components: List[Dict[str, Any]],
    ) -> str:
        lowered_description = description.strip().lower()

        build_config_keywords = [
            "cmake_minimum_required",
            "project(",
            "cmake ",
            "cmake_minimum_required(version",
        ]
        if any(keyword in lowered_description for keyword in build_config_keywords):
            lowered_description = ""

        low_signal_descriptions = {
            "",
            "readme",
            "sample project",
        }
        if lowered_description and lowered_description not in low_signal_descriptions:
            return description.rstrip(".") + "."

        readme = files.get("README.md") or files.get("readme.md")
        if readme and readme.get("summary") and readme["summary"].lower() not in {"readme"}:
            return readme["summary"].rstrip(".") + "."

        component_paths = {component["path"].lower() for component in components}
        if "libraries/libweb" in component_paths and "libraries/libjs" in component_paths:
            return "Browser engine and web platform runtime with build tooling and extensive standards tests."

        # Build richer purpose from component roles and detected frameworks
        core_components = [component for component in components if component["path"] not in {"tests", "docs"}]
        if core_components:
            visible_roles = [c["role"] for c in core_components[:3]]
            if len(visible_roles) == 1:
                purpose = f"It is organized around {visible_roles[0]}."
            else:
                purpose = f"It is organized around {', '.join(visible_roles[:-1])}, and {visible_roles[-1]}."

            # Add framework/tech hints
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

        return "It provides application code that Sentinel can inspect, summarize, and guide."

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
            "vendor": [],
            "test_data": [],
            "documentation": [],
        }
        ranked_candidates: List[tuple[float, str, Dict[str, Any]]] = []

        for path, info in files.items():
            group = self._categorize_hotspot(path, info)
            if group not in groups:
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
                if self._is_data_or_config_file(path, info):
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
    ) -> List[str]:
        hints = []
        entry_points_by_category = structure.get("entry_points_by_category", {})
        runtime_entries = entry_points_by_category.get("runtime", [])
        build_entries = entry_points_by_category.get("build", []) + entry_points_by_category.get("generator", [])
        environment_entries = entry_points_by_category.get("environment", [])

        if runtime_entries:
            hints.append(f"Start runtime tracing from {runtime_entries[0]}")
        elif build_entries:
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
        return hints[:4]

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
                elif context in {"browser_engine", "javascript_engine", "runtime_entry", "first_party_source", "core_utility", "runtime_source"}:
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
                return max(weighted_scores.items(), key=lambda x: x[1])[0]

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
            context = self._classify_path_context(filepath, info)
            is_vendor = context == "vendor_generated"

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
                if is_vendor:
                    continue
                if context == "generated_sdk":
                    category = "maintainability"
                    detail = "Generated SDK/client file; regenerate from source schema instead of editing manually."
                elif context == "localization_resource":
                    category = "maintainability"
                    detail = "Large localization/resource file; large by design. Review only if translation loading or schema changes."
                elif context == "specification_documentation":
                    category = "maintainability"
                    detail = "Large documentation/specification file; review for readability and drift, not source module boundaries."
                elif self._is_data_or_config_file(filepath, info):
                    category = "maintainability"
                    if self._is_documentation_file(filepath, info):
                        detail = "Large documentation file; review for readability if frequently edited."
                    else:
                        detail = "Large config/data file; validate schema before editing. Do not refactor like source code."
                else:
                    category = "structural"
                    detail = "File is " + str(info['line_count']) + " lines; consider reviewing module boundaries"
                issues.append(
                    {
                        "type": "large_file",
                        "severity": "medium",
                        "category": category,
                        "file": filepath,
                        "message": detail,
                        "timestamp": now_iso(),
                    }
                )

            if info["size"] > size_threshold:
                if is_vendor:
                    continue
                category = "maintainability" if self._is_data_or_config_file(filepath, info) else "structural"
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
            if drift_flags or empty_headings >= 3:
                details = []
                if drift_flags:
                    details.append("placeholder language")
                if empty_headings:
                    details.append(f"{empty_headings} empty-looking headings")
                issues.append(
                    {
                        "type": "doc_code_drift",
                        "severity": "medium",
                        "category": "maintainability",
                        "file": filepath,
                        "message": "Documentation may be stale or scaffold-like: "
                        + ", ".join(details),
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

        if metrics.get("total_files", 0) > 3 and not structure.get("entry_points"):
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

    def _is_documentation_file(self, filepath: str, info: Dict[str, Any]) -> bool:
        ext = str(info.get("extension") or Path(filepath).suffix).lower()
        name = Path(filepath).name.lower()
        return ext in {".md", ".txt"} or name in {"readme.md", "readme.txt", "readme"}

    def _is_data_or_config_file(self, filepath: str, info: Dict[str, Any]) -> bool:
        ext = str(info.get("extension") or Path(filepath).suffix).lower()
        name = Path(filepath).name.lower()
        return ext in {".json", ".yaml", ".yml", ".toml", ".ini", ".cfg"} or name in {
            "dockerfile",
            "makefile",
        }

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
            if path in test_files:
                continue
            context = self._classify_path_context(path, info)
            if context in {"vendor_generated", "generated_sdk", "localization_resource", "specification_documentation"}:
                continue
            score = 0
            factors: list[str] = []

            if path in entry_points:
                score += 30
                factors.append("entry point")
            line_count = int(info.get("line_count", 0))
            if line_count > 500:
                if self._is_data_or_config_file(path, info):
                    score += 8
                    factors.append("large data/config file")
                else:
                    score += 20
                    factors.append("large file")
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
            # Only add "executable code" for non-documentation files
            if info.get("has_class") or info.get("has_function"):
                if not self._is_documentation_file(path, info):
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
            risks.append(
                {
                    "file": path,
                    "score": min(score, 100),
                    "level": level,
                    "factors": factors[:6],
                    "risk_categories": self._risk_categories(path, info, factors),
                    "surface": self._risk_surface(path, info),
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
        categories = set()
        if any(factor in factors for factor in {"large file", "moderate size", "many imports", "several imports"}):
            categories.add("structural")
        if "large data/config file" in factors:
            categories.add("maintainability")
        if any(factor in factors for factor in {"entry point", "runtime surface"}):
            categories.add("runtime")
        if "no obvious paired test" in factors:
            categories.add("test")
        if Path(path).suffix == ".py" and (info.get("has_class") or info.get("has_function")):
            categories.add("runtime")
        return sorted(categories) or ["maintainability"]

    def _risk_surface(self, path: str, info: Dict[str, Any]) -> str:
        context = self._classify_path_context(path, info)
        if context in {"browser_engine", "javascript_engine", "runtime_entry", "runtime_source", "first_party_source", "core_utility"}:
            return "runtime"
        if context in {"build_tooling", "generator", "lint_tooling", "tooling", "environment"}:
            return "build_tooling"
        if context in {"test", "test_data", "test_support"}:
            return "test"
        if context == "documentation":
            return "documentation"
        return "runtime" if "runtime" in self._risk_categories(path, info, []) else "other"

    def _group_risk_scores(
        self,
        risk_scores: List[Dict[str, Any]],
        files: Dict[str, Dict[str, Any]],
    ) -> Dict[str, List[Dict[str, Any]]]:
        groups: Dict[str, List[Dict[str, Any]]] = {
            "runtime": [],
            "build_tooling": [],
            "test": [],
            "documentation": [],
            "other": [],
        }
        for risk in risk_scores:
            path = risk.get("file", "")
            surface = risk.get("surface") or self._risk_surface(path, files.get(path, {}))
            if surface not in groups:
                surface = "other"
            groups[surface].append(risk)
        return {key: values[:10] for key, values in groups.items() if values}

    @staticmethod
    def _maintainability_score_to_risk(maintainability_pct: int) -> str:
        if maintainability_pct >= 85:
            return "low"
        elif maintainability_pct >= 65:
            return "medium"
        return "high"

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
                data["level"] = self._maintainability_score_to_risk(maintainability_percent)
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

        if any(issue.get("type") == "no_tests" for issue in issues):
            has_coverage_warning = any(issue.get("type") == "scan_coverage" for issue in issues)
            if has_coverage_warning:
                summary["test"]["level"] = "missing"
                summary["test"]["reason"] = "No automated test suite was detected — scan coverage may be incomplete or test detection uncertain"
            else:
                summary["test"]["level"] = "missing"
                summary["test"]["reason"] = "No automated test suite was detected"
        elif summary["test"]["signals"] >= 12:
            summary["test"]["level"] = "strong"
            summary["test"]["reason"] = "Large test surface and related test signals were detected"
        elif summary["test"]["signals"] >= 3 or test_files_count >= 5:
            summary["test"]["level"] = "present"
            summary["test"]["reason"] = "Some test files or test relationships were detected"
        elif has_test_files or test_files_count > 0:
            summary["test"]["level"] = "present"
            summary["test"]["reason"] = "Test files detected, but coverage not measured"
        else:
            summary["test"]["level"] = "unknown"
            summary["test"]["reason"] = "No strong test signal was inferred from risk scoring"

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

        for issue in issues:
            issue_type = issue.get("type", "")
            if issue_type in type_penalties:
                # Skip security penalties if security was not assessed
                if issue.get("category") == "security" and not security_assessed:
                    continue
                score -= float(type_penalties[issue_type])
            else:
                score -= float(severity_penalties.get(issue.get("severity", "low"), 0))

        if issues:
            blocking_categories = {"runtime", "test", "security"}
            blocking_types = {"no_tests", "no_entry_point"}
            has_blocking_issue = any(
                issue.get("category") in blocking_categories or issue.get("type") in blocking_types
                for issue in issues
            )
            if not has_blocking_issue:
                score = max(score, float(floors.get("maintainability_only", 70)))
            elif any(issue.get("type") == "no_tests" for issue in issues):
                score = max(score, 45)

        todo_categories = metrics.get("todo_categories", {})
        maintainability = max(55, min(100, round(100 - min(35, metrics.get("open_todos", 0) / 120) - min(20, len(issues) / 60))))
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

        has_scan_coverage_warning = any(issue.get("type") == "scan_coverage" for issue in issues)
        if has_scan_coverage_warning:
            explanation = (
                "Health score may be unreliable due to incomplete scan coverage. "
                "Maintainability appears strong, but test presence and runtime complexity "
                "could not be fully assessed."
            )
        else:
            explanation = (
                "Strong test and architecture signals improve the score, while TODO volume, oversized files, "
                "runtime complexity, and unassessed security reduce confidence."
            )

        maintainability_risk_level = self._maintainability_score_to_risk(maintainability)

        # Ensure health score is never below maintainability - 30 to avoid contradiction
        final_score = max(0, min(100, round(score)))
        if maintainability - final_score > 40:
            final_score = max(final_score, maintainability - 40)

        return {
            "score": final_score,
            "security_assessed": security_assessed,
            "reason": f"Security {'was assessed' if security_assessed else 'was not assessed'}",
            "explanation": explanation,
            "breakdown": {
                "maintainability_percent": maintainability,
                "maintainability_risk": maintainability_risk_level,
                "runtime_complexity": runtime_level,
                "test_signal": test_signal,
                "documentation_percent": documentation,
                "documentation_reason": (
                    f"stale/scaffold-like signals found in {documentation_drift} documentation file(s); "
                    f"{large_docs} large documentation file(s) need review; {documentation_todos} documentation TODO(s)"
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
