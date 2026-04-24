from __future__ import annotations

import hashlib
import os
import re
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils import DEFAULT_AUDIT_RULES, DEFAULT_PATTERNS, merge_dicts, now_iso, read_json, write_json


@dataclass
class Checkpoint:
    timestamp: str
    file_hashes: Dict[str, str]
    file_list: List[str]
    summary: str
    issues: List[Dict[str, Any]]
    metrics: Dict[str, Any]
    health_score: int


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
    ) -> Dict[str, Dict[str, Any]]:
        """Scan the project directory and return per-file metadata."""

        ignored_dirs = {entry.lower() for entry in ignore_dirs}
        allowed_entries = {entry.lower() for entry in extensions}
        ignored_paths = [Path(path).resolve() for path in (ignore_paths or [])]
        files: Dict[str, Dict[str, Any]] = {}

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

                try:
                    stat = filepath.stat()
                    if stat.st_size > max_size:
                        continue

                    raw_content = filepath.read_bytes()
                except OSError:
                    continue

                rel_path = filepath.relative_to(self.root_dir).as_posix()
                analysis_bytes = raw_content[:analysis_sample_bytes] if fast_mode else raw_content
                content = analysis_bytes.decode("utf-8", errors="ignore")
                file_hash = hashlib.sha256(raw_content).hexdigest()
                line_count = raw_content.count(b"\n") + (1 if raw_content else 0)
                imports = self._extract_imports(content, ext)
                summary = self._extract_summary(content, ext, filepath.name)
                symbols = self._extract_symbols(content, ext)

                metadata = self._extract_metadata(content, ext, filepath.name)
                metadata.update(self._extract_doc_metadata(content, ext, filepath.name))

                files[rel_path] = {
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
                    "analysis_truncated": fast_mode and len(analysis_bytes) < len(raw_content),
                    "summary": summary,
                    "symbols": symbols,
                    "metadata": metadata,
                }
        return files

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

        deduped = []
        seen = set()
        for entry in imports:
            if entry not in seen:
                seen.add(entry)
                deduped.append(entry)
        return deduped[:20]

    def _extract_summary(self, content: str, ext: str, filename: str) -> str:
        if ext in {".md", ".txt"}:
            for raw_line in content.splitlines():
                line = raw_line.strip()
                if not line or line == "```":
                    continue
                if line.startswith("#"):
                    return line.lstrip("#").strip()[:160]
                return line[:160]

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

        return metadata

    def _extract_doc_metadata(self, content: str, ext: str, filename: str) -> Dict[str, Any]:
        """Extract documentation quality signals that can drift from code reality."""
        lower_name = filename.lower()
        if ext not in {".md", ".txt"} and lower_name not in {"readme.md", "readme.txt", "readme"}:
            return {}

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

        if not hits and empty_headings == 0:
            return {}

        return {
            "doc_drift_flags": hits[:6],
            "empty_heading_count": empty_headings,
        }

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
        understanding = self._build_project_understanding(file_data, metrics, structure, patterns, issues)
        risk_scores = self._score_file_risks(file_data, structure, issues)
        health_score = self._calculate_health_score(issues)

        return {
            "timestamp": now_iso(),
            "metrics": metrics,
            "structure": structure,
            "patterns": patterns,
            "architecture": architecture,
            "understanding": understanding,
            "issues": issues,
            "risk_scores": risk_scores,
            "risk_summary": self._summarize_risk_categories(issues, risk_scores),
            "health_score": health_score,
        }

    def _compute_metrics(self, files: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        total_lines = sum(info["line_count"] for info in files.values())
        total_size = sum(info["size"] for info in files.values())
        todos = sum(info["todo_count"] for info in files.values())
        ext_counts: Dict[str, int] = {}
        for info in files.values():
            ext = info["extension"]
            ext_counts[ext] = ext_counts.get(ext, 0) + 1

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
            "file_types": dict(sorted(ext_counts.items())),
            "avg_lines_per_file": total_lines // max(len(files), 1),
            "largest_files": largest_files,
        }

    def _analyze_structure(self, files: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        dirs = set()
        entry_points = []
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
            is_test_file = not hidden_dir_path and (
                any(part.lower() in {"tests", "test", "spec"} for part in parts[:-1])
                or lower_name.startswith("test_")
                or lower_name.endswith("_test.py")
                or ".spec." in lower_name
            )
            if is_test_file:
                test_files.append(filepath)

            if not hidden_dir_path and (
                lower_name in {"readme.md", "readme.txt", "readme"}
                or any(part.lower() in {"docs", "doc"} for part in parts[:-1])
                or lower_path.endswith(".md")
            ):
                documentation_files.append(filepath)

            if info.get("has_main") and not is_test_file:
                entry_points.append(filepath)

        return {
            "directories": sorted(dirs),
            "entry_points": sorted(entry_points),
            "config_files": sorted(config_files),
            "test_files": sorted(test_files),
            "documentation_files": sorted(documentation_files),
            "has_tests": bool(test_files),
            "has_docs": bool(documentation_files),
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
            "config_files": structure.get("config_files", []),
            "documentation_files": structure.get("documentation_files", []),
            "patterns": [pattern["name"] for pattern in patterns],
            "version_control": dict(self.git_context),
        }

    def _build_project_understanding(
        self,
        files: Dict[str, Dict[str, Any]],
        metrics: Dict[str, Any],
        structure: Dict[str, Any],
        patterns: List[Dict[str, str]],
        issues: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        project_name, description = self._detect_project_identity(files)
        frameworks = self._detect_frameworks(files)
        components = self._summarize_components(files)
        languages = self._detect_languages(metrics.get("file_types", {}))
        primary_language = self._detect_primary_language(metrics.get("file_types", {}), languages)
        project_type = self._infer_project_type(primary_language, structure, frameworks, components)
        purpose = self._infer_project_purpose(description, files, components)
        important_files = self._identify_important_files(files, metrics, structure)
        hotspots = self._identify_hotspots(metrics, structure, issues)
        workflow_hints = self._build_workflow_hints(structure, frameworks, files)

        summary_bits = [project_name or self.root_dir.name, "appears to be", project_type]
        summary = " ".join(bit for bit in summary_bits if bit).strip()
        if purpose:
            summary = f"{summary}. {purpose}"

        return {
            "project_name": project_name or self.root_dir.name,
            "project_type": project_type,
            "primary_language": primary_language,
            "languages": languages,
            "frameworks": frameworks,
            "purpose": purpose,
            "summary": summary,
            "main_components": components[:6],
            "important_files": important_files[:8],
            "hotspots": hotspots[:5],
            "workflow_hints": workflow_hints,
            "patterns": [pattern["name"] for pattern in patterns],
        }

    def _detect_project_identity(self, files: Dict[str, Dict[str, Any]]) -> tuple[str | None, str]:
        candidates = ["pyproject.toml", "package.json", "setup.py", "README.md", "readme.md"]
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
        all_imports = {
            imported.lower()
            for info in files.values()
            for imported in info.get("imports", [])
            if isinstance(imported, str)
        }
        frameworks = []
        for label, tokens in signals.items():
            if any(
                imported == token
                or imported.startswith(f"{token}.")
                or token in imported
                for token in tokens
                for imported in all_imports
            ):
                frameworks.append(label)

        if any(path.endswith("pyproject.toml") for path in files):
            frameworks.append("python_packaging")
        if any("tests/" in path or Path(path).name.lower().startswith("test_") for path in files):
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
            parts = Path(filepath).parts
            if len(parts) < 2 or any(part.startswith(".") for part in parts[:-1]):
                continue

            if parts[0] in {"tests", "test"}:
                key = "tests"
            elif parts[0] in {"docs", "doc"}:
                key = "docs"
            elif parts[0] in {"config", "scripts"}:
                key = parts[0]
            elif len(parts) >= 3:
                key = "/".join(parts[:2])
            else:
                key = parts[0]

            bucket = grouped.setdefault(
                key,
                {
                    "path": key,
                    "file_count": 0,
                    "line_count": 0,
                    "role": self._infer_component_role(key),
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
        }
        tail = key.split("/")[-1].lower()
        return role_map.get(tail, "application logic")

    def _infer_project_type(
        self,
        primary_language: str,
        structure: Dict[str, Any],
        frameworks: List[str],
        components: List[Dict[str, Any]],
    ) -> str:
        descriptors = [f"{primary_language} project" if primary_language != "unknown" else "software project"]
        component_paths = {component["path"] for component in components}

        if structure.get("entry_points"):
            descriptors.append("with a CLI or script entry point")
        if "fastapi" in frameworks or "flask" in frameworks or "django" in frameworks:
            descriptors.append("with a service/API layer")
        if any("dashboard" in path for path in component_paths):
            descriptors.append("with an operator dashboard")
        if structure.get("has_tests"):
            descriptors.append("and a test suite")

        return " ".join(descriptors)

    def _infer_project_purpose(
        self,
        description: str,
        files: Dict[str, Dict[str, Any]],
        components: List[Dict[str, Any]],
    ) -> str:
        if description and description.lower() not in {"readme", "sample project"}:
            return description.rstrip(".") + "."

        readme = files.get("README.md") or files.get("readme.md")
        if readme and readme.get("summary") and readme["summary"].lower() not in {"readme"}:
            return readme["summary"].rstrip(".") + "."

        core_components = [component["role"] for component in components if component["path"] not in {"tests", "docs"}]
        if core_components:
            visible = core_components[:3]
            if len(visible) == 1:
                return f"It is organized around {visible[0]}."
            return f"It is organized around {', '.join(visible[:-1])}, and {visible[-1]}."

        return "It provides application code that Sentinel can inspect, summarize, and guide."

    def _identify_important_files(
        self,
        files: Dict[str, Dict[str, Any]],
        metrics: Dict[str, Any],
        structure: Dict[str, Any],
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

        for path in ["README.md", "readme.md", "pyproject.toml", "package.json", "setup.py", "requirements.txt"]:
            add(path, "project definition")

        for item in metrics.get("largest_files", [])[:4]:
            add(item["file"], "high-leverage hotspot")

        return important

    def _identify_hotspots(
        self,
        metrics: Dict[str, Any],
        structure: Dict[str, Any],
        issues: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        issue_map = {}
        for issue in issues:
            if issue.get("file"):
                issue_map.setdefault(issue["file"], []).append(issue["message"])

        hotspots = []
        entry_points = set(structure.get("entry_points", []))
        for item in metrics.get("largest_files", [])[:5]:
            reasons = []
            if item["file"] in entry_points:
                reasons.append("entry point")
            reasons.extend(issue_map.get(item["file"], []))
            if not reasons:
                reasons.append(f"{item['lines']} lines")
            hotspots.append(
                {
                    "path": item["file"],
                    "reason": "; ".join(reasons),
                    "line_count": item["lines"],
                }
            )
        return hotspots

    def _build_workflow_hints(
        self,
        structure: Dict[str, Any],
        frameworks: List[str],
        files: Dict[str, Dict[str, Any]],
    ) -> List[str]:
        hints = []
        if structure.get("entry_points"):
            hints.append(f"Start execution tracing from {structure['entry_points'][0]}")
        if structure.get("has_tests"):
            if "pytest" in frameworks or any("pytest" in path for path in files):
                hints.append("Use the test suite as the fastest regression signal")
            else:
                hints.append("Use existing tests before making wide changes")
        if any(path in files for path in ["pyproject.toml", "package.json", "setup.py"]):
            hints.append("Read the project manifest before changing dependencies or startup flow")
        if any(path in files for path in ["README.md", "readme.md"]):
            hints.append("Use the README as the first source of product intent")
        return hints[:4]

    def _detect_languages(self, file_types: Dict[str, int]) -> List[str]:
        language_map = {
            ".py": "python",
            ".js": "javascript",
            ".ts": "typescript",
            ".json": "json",
            ".yaml": "yaml",
            ".yml": "yaml",
            ".toml": "toml",
            ".md": "markdown",
            ".sh": "shell",
            ".bash": "shell",
            "Dockerfile": "docker",
            "Makefile": "make",
        }
        languages = set()
        for ext in file_types:
            languages.add(language_map.get(ext, ext.lstrip(".") or ext.lower()))
        return sorted(languages)

    def _detect_primary_language(self, file_types: Dict[str, int], languages: List[str]) -> str:
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
        }
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
                category = "structural"
                detail = "consider reviewing module boundaries"
                if self._is_data_or_config_file(filepath, info):
                    category = "maintainability"
                    detail = "large data/config file; validate schema before splitting"
                issues.append(
                    {
                        "type": "large_file",
                        "severity": "medium",
                        "category": category,
                        "file": filepath,
                        "message": f"File is {info['line_count']} lines; {detail}",
                        "timestamp": now_iso(),
                    }
                )

            if info["size"] > size_threshold:
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
            if info.get("has_class") or info.get("has_function"):
                score += 6
                factors.append("executable code")
            coverage = self._classify_test_coverage(path, test_index)
            if Path(path).suffix == ".py" and coverage["status"] == "none":
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
                    "coverage": coverage,
                }
            )

        risks.sort(key=lambda item: item["score"], reverse=True)
        return risks[:20]

    def _build_test_index(self, test_files: set[str]) -> Dict[str, Any]:
        entries = []
        for test_path in sorted(test_files):
            stem = Path(test_path).stem.lower()
            normalized = stem.removeprefix("test_").removesuffix("_test")
            entries.append({"path": test_path, "stem": stem, "normalized": normalized})
        return {"entries": entries}

    def _classify_test_coverage(self, path: str, test_index: Dict[str, Any]) -> Dict[str, Any]:
        if Path(path).suffix != ".py":
            return {"status": "not_applicable", "test_file": None}

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

    def _summarize_risk_categories(
        self,
        issues: List[Dict[str, Any]],
        risk_scores: List[Dict[str, Any]],
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
            high_threshold = 20 if category == "maintainability" else 8
            if category == "test":
                high_threshold = 12
            if signals >= high_threshold:
                data["level"] = "high"
            elif signals >= 3:
                data["level"] = "medium"
            elif signals >= 1:
                data["level"] = "low"

        if any(issue.get("type") == "no_tests" for issue in issues):
            summary["test"]["level"] = "high"
        elif summary["test"]["signals"] == 0:
            summary["test"]["level"] = "good"

        if any(issue.get("type") == "no_entry_point" for issue in issues):
            summary["runtime"]["level"] = "medium"

        return summary

    def _calculate_health_score(self, issues: List[Dict[str, Any]]) -> int:
        severity_penalties = self.rules.get("health_penalties", DEFAULT_AUDIT_RULES["health_penalties"])
        type_penalties = self.rules.get(
            "health_penalties_by_type",
            DEFAULT_AUDIT_RULES["health_penalties_by_type"],
        )
        floors = self.rules.get("health_score_floors", DEFAULT_AUDIT_RULES["health_score_floors"])
        score = 100.0
        for issue in issues:
            issue_type = issue.get("type", "")
            if issue_type in type_penalties:
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

        return max(0, min(100, round(score)))

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
