"""Per-file facts: Python AST, conservative language heuristics, manifests, docs."""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .classify import Classification, classify_path, manifest_kind
from .utils import (
    count_lines,
    decode_bytes,
    find_dangerous_patterns,
    find_debug_patterns,
    find_secrets,
    module_name_for_path,
    sha256_bytes,
)

TODO_RE = re.compile(r"\b(?:TODO|FIXME|XXX|HACK)\b")
MARKDOWN_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
PLACEHOLDER_RE = re.compile(
    r"(?i)\b(?:TODO|TBD|FIXME|lorem ipsum|coming soon|placeholder|work in progress|under construction)\b"
)


class AnalysisError(Exception):
    """Raised when a candidate cannot be read or analysed."""


@dataclass
class SymbolFact:
    name: str
    qualname: str
    kind: str
    line: int
    end_line: int
    complexity: int = 0
    length: int = 0


@dataclass
class FileAnalysis:
    path: str
    language: str
    role: str
    size: int
    lines: int
    mtime_ns: int
    content_hash: str | None
    classification: Classification
    parse_error: str | None = None
    encoding_replaced: bool = False
    todos: int = 0
    symbols: list[SymbolFact] = field(default_factory=list)
    imports: list[tuple[str, str]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    sampled: bool = False

    @property
    def max_complexity(self) -> int:
        return max((symbol.complexity for symbol in self.symbols), default=0)

    @property
    def max_function_length(self) -> int:
        return max((symbol.length for symbol in self.symbols if symbol.kind != "class"), default=0)

    @property
    def function_count(self) -> int:
        return sum(1 for symbol in self.symbols if symbol.kind != "class")

    @property
    def class_count(self) -> int:
        return sum(1 for symbol in self.symbols if symbol.kind == "class")


class _ComplexityVisitor(ast.NodeVisitor):
    """Approximate cyclomatic complexity without descending into nested scopes."""

    def __init__(self) -> None:
        self.score = 1

    def visit_FunctionDef(self, _node: ast.FunctionDef) -> None:  # noqa: N802
        return

    def visit_AsyncFunctionDef(self, _node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        return

    def visit_Lambda(self, _node: ast.Lambda) -> None:  # noqa: N802
        return

    def visit_ClassDef(self, _node: ast.ClassDef) -> None:  # noqa: N802
        return

    def visit_If(self, node: ast.If) -> None:  # noqa: N802
        self.score += 1
        self.generic_visit(node)

    def visit_IfExp(self, node: ast.IfExp) -> None:  # noqa: N802
        self.score += 1
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:  # noqa: N802
        self.score += 1
        self.generic_visit(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:  # noqa: N802
        self.score += 1
        self.generic_visit(node)

    def visit_While(self, node: ast.While) -> None:  # noqa: N802
        self.score += 1
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:  # noqa: N802
        self.score += 1
        self.generic_visit(node)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:  # noqa: N802
        self.score += max(0, len(node.values) - 1)
        self.generic_visit(node)

    def visit_Match(self, node: ast.Match) -> None:  # noqa: N802
        self.score += max(0, len(node.cases) - 1)
        self.generic_visit(node)

    def visit_comprehension(self, node: ast.comprehension) -> None:  # noqa: N802
        self.score += 1 + len(node.ifs)
        self.generic_visit(node)


def _end_line(node: ast.AST, fallback: int) -> int:
    return int(getattr(node, "end_lineno", fallback) or fallback)


def _resolve_imports(tree: ast.AST, rel_path: str) -> list[tuple[str, str]]:
    module = module_name_for_path(rel_path)
    is_package = rel_path.endswith("__init__.py")
    package_parts = module.split(".") if is_package else module.split(".")[:-1]
    imports: set[tuple[str, str]] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add((alias.name, "import"))
        elif isinstance(node, ast.ImportFrom):
            module_name = node.module or ""
            if node.level:
                base = list(package_parts)
                trim = node.level - 1
                if trim > 0:
                    base = base[:-trim] if trim <= len(base) else []
                if module_name:
                    base.extend(module_name.split("."))
                resolved = ".".join(part for part in base if part)
            else:
                resolved = module_name
            if resolved:
                imports.add((resolved, "from"))
            for alias in node.names:
                if alias.name == "*" or not resolved:
                    continue
                imports.add((f"{resolved}.{alias.name}", "from"))
    return sorted(imports)


def _python_symbols(tree: ast.AST) -> list[SymbolFact]:
    symbols: list[SymbolFact] = []

    def visit_body(body: Iterable[ast.stmt], parent: str = "", in_class: bool = False) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                qualname = f"{parent}.{node.name}" if parent else node.name
                end = _end_line(node, node.lineno)
                symbols.append(
                    SymbolFact(
                        name=node.name,
                        qualname=qualname,
                        kind="class",
                        line=node.lineno,
                        end_line=end,
                        length=end - node.lineno + 1,
                    )
                )
                visit_body(node.body, qualname, in_class=True)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualname = f"{parent}.{node.name}" if parent else node.name
                end = _end_line(node, node.lineno)
                visitor = _ComplexityVisitor()
                for statement in node.body:
                    visitor.visit(statement)
                kind = "method" if in_class else "function"
                symbols.append(
                    SymbolFact(
                        name=node.name,
                        qualname=qualname,
                        kind=kind,
                        line=node.lineno,
                        end_line=end,
                        complexity=visitor.score,
                        length=end - node.lineno + 1,
                    )
                )
                visit_body(node.body, qualname, in_class=False)
            elif isinstance(node, (ast.If, ast.For, ast.While, ast.Try, ast.With)):
                # Nested declarations inside compound statements still belong to the file.
                for child in ast.iter_child_nodes(node):
                    if isinstance(child, (ast.stmt, ast.excepthandler)):
                        visit_body([child], parent, in_class)

    visit_body(tree.body)
    return symbols


def _analyse_python(analysis: FileAnalysis, text: str) -> None:
    try:
        tree = ast.parse(text, filename=analysis.path)
    except (SyntaxError, ValueError, RecursionError) as error:
        if isinstance(error, SyntaxError):
            analysis.parse_error = f"{error.msg} (line {error.lineno or 1})"
        else:
            analysis.parse_error = f"{type(error).__name__}: {error}"
        return
    analysis.imports = _resolve_imports(tree, analysis.path)
    analysis.symbols = _python_symbols(tree)


@dataclass(frozen=True)
class _RegexLanguage:
    symbol_patterns: tuple[tuple[str, re.Pattern[str]], ...]
    import_patterns: tuple[re.Pattern[str], ...]


_REGEX_LANGUAGES: dict[str, _RegexLanguage] = {
    "javascript": _RegexLanguage(
        symbol_patterns=(
            ("function", re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)")),
            ("method", re.compile(r"^\s*(?:export\s+)?(?:async\s+)?([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{")),
            ("class", re.compile(r"^\s*(?:export\s+)?class\s+([A-Za-z_$][\w$]*)")),
            ("function", re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>")),
            ("interface", re.compile(r"^\s*(?:export\s+)?interface\s+([A-Za-z_$][\w$]*)")),
            ("type", re.compile(r"^\s*(?:export\s+)?type\s+([A-Za-z_$][\w$]*)")),
        ),
        import_patterns=(
            re.compile(r"^\s*import\s+(?:[^'\"]*?\s+from\s+)?['\"]([^'\"]+)['\"]"),
            re.compile(r"\brequire\(\s*['\"]([^'\"]+)['\"]\s*\)"),
            re.compile(r"^\s*export\s+[^'\"]*?\s+from\s+['\"]([^'\"]+)['\"]"),
        ),
    ),
    "typescript": _RegexLanguage(
        symbol_patterns=(
            ("function", re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)")),
            ("class", re.compile(r"^\s*(?:export\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)")),
            ("interface", re.compile(r"^\s*(?:export\s+)?interface\s+([A-Za-z_$][\w$]*)")),
            ("type", re.compile(r"^\s*(?:export\s+)?type\s+([A-Za-z_$][\w$]*)")),
            ("function", re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>")),
            ("method", re.compile(r"^\s*(?:public|private|protected|static|async|readonly|\s)*([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*(?::\s*[^{]+)?\{")),
        ),
        import_patterns=(
            re.compile(r"^\s*import\s+(?:[^'\"]*?\s+from\s+)?['\"]([^'\"]+)['\"]"),
            re.compile(r"\brequire\(\s*['\"]([^'\"]+)['\"]\s*\)"),
            re.compile(r"^\s*export\s+[^'\"]*?\s+from\s+['\"]([^'\"]+)['\"]"),
        ),
    ),
    "go": _RegexLanguage(
        symbol_patterns=(
            ("function", re.compile(r"^func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)")),
            ("class", re.compile(r"^type\s+([A-Za-z_]\w*)\s+(?:struct|interface)\b")),
        ),
        import_patterns=(
            re.compile(r"^\s*import\s+(?:\w+\s+)?\"([^\"]+)\""),
            re.compile(r"^\s*(?:\w+\s+)?\"([^\"]+)\"\s*$"),
        ),
    ),
    "rust": _RegexLanguage(
        symbol_patterns=(
            ("function", re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+([A-Za-z_]\w*)")),
            ("class", re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:struct|enum|trait|union)\s+([A-Za-z_]\w*)")),
            ("type", re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?type\s+([A-Za-z_]\w*)")),
        ),
        import_patterns=(
            re.compile(r"^\s*use\s+([^;]+);"),
            re.compile(r"^\s*extern\s+crate\s+([A-Za-z_]\w*)"),
        ),
    ),
    "c": _RegexLanguage(
        symbol_patterns=(
            ("function", re.compile(r"^\s*(?:static\s+|inline\s+|extern\s+)*[A-Za-z_][\w\s\*]*\s+([A-Za-z_]\w*)\s*\([^;]*\)\s*\{?")),
            ("class", re.compile(r"^\s*(?:typedef\s+)?(?:struct|enum|union)\s+([A-Za-z_]\w*)")),
        ),
        import_patterns=(re.compile(r"^\s*#\s*include\s+[<\"]([^>\"]+)[>\"]"),),
    ),
    "cpp": _RegexLanguage(
        symbol_patterns=(
            ("function", re.compile(r"^\s*(?:static\s+|inline\s+|virtual\s+|explicit\s+)*[A-Za-z_][\w\s\*&:<>]*\s+([A-Za-z_]\w*)\s*\([^;]*\)\s*(?:const\s*)?\{?")),
            ("class", re.compile(r"^\s*(?:template\s*<[^>]*>\s*)?(?:class|struct|enum|union)\s+([A-Za-z_]\w*)")),
        ),
        import_patterns=(re.compile(r"^\s*#\s*include\s+[<\"]([^>\"]+)[>\"]"),),
    ),
}

_REGEX_LANGUAGES["java"] = _RegexLanguage(
    symbol_patterns=(
        ("class", re.compile(r"^\s*(?:public\s+|private\s+|protected\s+)?(?:final\s+|abstract\s+)?(?:class|interface|enum|record)\s+([A-Za-z_]\w*)")),
        ("method", re.compile(r"^\s*(?:public|private|protected|static|final|abstract|synchronized|\s)+[\w<>\[\],.?]+\s+([A-Za-z_]\w*)\s*\([^;]*\)\s*\{")),
    ),
    import_patterns=(re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+);"),),
)
_REGEX_LANGUAGES["kotlin"] = _RegexLanguage(
    symbol_patterns=(
        ("class", re.compile(r"^\s*(?:public|private|internal|data|sealed|open|abstract|\s)*(?:class|interface|object)\s+([A-Za-z_]\w*)")),
        ("function", re.compile(r"^\s*(?:public|private|internal|suspend|override|open|\s)*fun\s+([A-Za-z_]\w*)")),
    ),
    import_patterns=(re.compile(r"^\s*import\s+([\w.]+)"),),
)
_REGEX_LANGUAGES["csharp"] = _RegexLanguage(
    symbol_patterns=(
        ("class", re.compile(r"^\s*(?:public|private|internal|protected|sealed|abstract|static|\s)*(?:class|interface|struct|enum|record)\s+([A-Za-z_]\w*)")),
        ("method", re.compile(r"^\s*(?:public|private|internal|protected|static|virtual|async|override|\s)+[\w<>\[\],.?]+\s+([A-Za-z_]\w*)\s*\([^;]*\)\s*\{")),
    ),
    import_patterns=(re.compile(r"^\s*using\s+([\w.]+)\s*;"),),
)


def _brace_end(lines: list[str], start_index: int, max_lines: int = 400) -> int:
    """Best-effort closing line for a brace block."""

    start_indent = len(lines[start_index]) - len(lines[start_index].lstrip())
    for index in range(start_index + 1, min(len(lines), start_index + max_lines)):
        stripped = lines[index].strip()
        if not stripped:
            continue
        indent = len(lines[index]) - len(lines[index].lstrip())
        if stripped.startswith("}") and indent <= start_indent:
            return index + 1
    return start_index + 1


def _analyse_regex_language(analysis: FileAnalysis, text: str) -> None:
    definition = _REGEX_LANGUAGES.get(analysis.language)
    if definition is None:
        return
    lines = text.splitlines()
    symbols: list[SymbolFact] = []
    seen: set[tuple[str, int]] = set()
    for index, line in enumerate(lines):
        for kind, pattern in definition.symbol_patterns:
            match = pattern.match(line)
            if not match:
                continue
            name = match.group(1)
            if (name, index + 1) in seen:
                continue
            seen.add((name, index + 1))
            end = _brace_end(lines, index)
            body = "\n".join(lines[index:end])
            complexity = 1
            complexity += len(re.findall(r"\b(?:if|for|while|catch|case)\b", body))
            complexity += len(re.findall(r"&&|\|\|", body))
            symbols.append(
                SymbolFact(
                    name=name,
                    qualname=name,
                    kind=kind,
                    line=index + 1,
                    end_line=end,
                    complexity=complexity,
                    length=end - index,
                )
            )
            break
    analysis.symbols = symbols

    imports: set[tuple[str, str]] = set()
    for line in lines:
        for pattern in definition.import_patterns:
            match = pattern.search(line)
            if not match:
                continue
            imported = match.group(1).strip()
            if imported:
                imports.add((imported, "import"))
    analysis.imports = sorted(imports)


def _markdown_meta(analysis: FileAnalysis, text: str, root: Path, path: Path) -> None:
    lines = text.splitlines()
    headings = sum(1 for line in lines if re.match(r"^\s{0,3}#{1,6}\s", line))
    code_fences = sum(1 for line in lines if line.strip().startswith("```"))
    placeholders = len(PLACEHOLDER_RE.findall(text))
    links = MARKDOWN_LINK_RE.findall(text)
    broken: list[str] = []
    relative_count = 0
    base = path.parent
    for _label, target in links:
        target = target.strip()
        if not target or target.startswith(("#", "http://", "https://", "mailto:", "tel:", "data:")):
            continue
        clean = target.split("#", 1)[0].split("?", 1)[0]
        if not clean:
            continue
        relative_count += 1
        try:
            resolved = (base / clean).resolve()
            if resolved.is_relative_to(root.resolve()) is False:
                continue
            if not resolved.exists():
                broken.append(target)
        except (OSError, ValueError):
            broken.append(target)
    analysis.meta.update(
        {
            "headings": headings,
            "code_fences": code_fences,
            "placeholders": placeholders,
            "link_count": len(links),
            "relative_link_count": relative_count,
            "broken_links": broken[:20],
        }
    )


def _extract_quoted_list(block: str) -> list[str]:
    return [item for item in re.findall(r"[\"']([^\"']+)[\"']", block)]


def _is_pinned(spec: str) -> bool:
    spec = spec.strip()
    if not spec or spec in {"*", "latest"}:
        return False
    if "==" in spec or "===" in spec:
        return True
    if any(token in spec for token in (">=", "<=", "~=", "!=", ">", "<", "*", "^", "~")):
        return False
    return bool(re.search(r"\d", spec))


def _manifest_meta(analysis: FileAnalysis, text: str, kind: str) -> None:
    meta: dict[str, Any] = {"manifest_kind": kind, "manifest_path": analysis.path}
    dependencies: list[str] = []
    has_scripts = False
    name = ""
    version = ""
    bin_value: Any = None

    if kind == "pyproject":
        name_match = re.search(r'(?m)^\s*name\s*=\s*["\']([^"\']+)["\']', text)
        if name_match:
            name = name_match.group(1)
        version_match = re.search(r'(?m)^\s*version\s*=\s*["\']([^"\']+)["\']', text)
        if version_match:
            version = version_match.group(1)
        dep_blocks = re.findall(r"(?s)dependencies\s*=\s*\[(.*?)\]", text)
        for block in dep_blocks:
            dependencies.extend(_extract_quoted_list(block))
        has_scripts = bool(re.search(r"(?m)^\[(?:project|tool\.poetry)\.scripts\]", text))
    elif kind == "requirements":
        for line in text.splitlines():
            line = line.split("#", 1)[0].strip()
            if not line or line.startswith(("-r", "-c", "--", "git+", "http:", "https:")):
                continue
            dependencies.append(line)
    elif kind == "npm":
        try:
            payload = json.loads(text)
        except ValueError:
            payload = {}
        if isinstance(payload, dict):
            name = str(payload.get("name") or "")
            version = str(payload.get("version") or "")
            for section in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
                values = payload.get(section)
                if isinstance(values, dict):
                    dependencies.extend(f"{key}@{value}" for key, value in values.items())
            has_scripts = bool(payload.get("scripts"))
            bin_value = payload.get("bin")
    elif kind == "cargo":
        section = ""
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                section = stripped.strip("[]")
                continue
            if section == "package":
                if stripped.startswith("name") and "=" in stripped:
                    name = stripped.split("=", 1)[1].strip().strip('"')
                elif stripped.startswith("version") and "=" in stripped:
                    version = stripped.split("=", 1)[1].strip().strip('"')
            elif section in {"dependencies", "dev-dependencies", "build-dependencies"} and "=" in stripped:
                key, value = stripped.split("=", 1)
                version = value.strip().strip('"')
                dependencies.append(f"{key.strip()}@{version}")
        has_scripts = bool(re.search(r"(?m)^\[\[bin\]\]", text))
    elif kind == "go":
        module_match = re.search(r"(?m)^\s*module\s+(\S+)", text)
        name = module_match.group(1) if module_match else ""
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("require ") and " " in stripped:
                dependencies.append(stripped[len("require ") :].strip())
            elif re.match(r"^\S+\s+v\d", stripped):
                dependencies.append(stripped)
        has_scripts = bool(re.search(r"(?m)^package main$", text)) or analysis.path.endswith("main.go")
    elif kind in {"python", "composer", "gem", "mix"}:
        name_match = re.search(r'(?m)^\s*(?:name\s*=\s*|name\s*:\s*)?["\']([^"\']+)["\']', text)
        if name_match:
            name = name_match.group(1)
        if "install_requires" in text:
            block = text.split("install_requires", 1)[1][:4000]
            dependencies.extend(re.findall(r"[\"']([^\"']+)[\"']", block))
        has_scripts = "entry_points" in text or "console_scripts" in text

    meta.update(
        {
            "name": name,
            "version": version,
            "dependencies": list(dict.fromkeys(dependencies)),
            "unpinned": sorted({dep for dep in dependencies if not _is_pinned(dep)}),
            "has_scripts": has_scripts,
            "bin": bin_value,
        }
    )
    analysis.meta.update(meta)


def _should_read_content(classification: Classification, name: str, include_docs: bool) -> bool:
    if classification.is_source or classification.is_test or classification.is_config:
        return True
    if classification.is_doc:
        if include_docs:
            return True
        lowered = name.lower()
        return lowered.startswith("readme") or lowered in {
            "contributing.md",
            "architecture.md",
            "security.md",
            "changelog.md",
        }
    return False


def inspect_candidate(
    root: Path,
    candidate: Any,
    rules: Mapping[str, Any],
    previous_row: Mapping[str, Any] | None = None,
    include_docs: bool = False,
    force: bool = False,
) -> tuple[FileAnalysis | None, bool]:
    """Read and analyse a candidate.

    Returns ``(analysis, cache_hit)``. A cache hit means the stored row is still
    current and ``analysis`` is ``None``.
    """

    classification = getattr(candidate, "classification", None) or classify_path(candidate.path, rules)
    analysis_version = int(rules.get("analysis_version", 1))
    if (
        not force
        and previous_row is not None
        and int(previous_row["size"]) == int(candidate.size)
        and int(previous_row["mtime_ns"]) == int(candidate.mtime_ns)
        and int(previous_row["analysis_version"] or 0) == analysis_version
    ):
        return None, True

    path = candidate.abspath
    name = path.name
    base = FileAnalysis(
        path=candidate.path,
        language=classification.language,
        role=classification.role,
        size=int(candidate.size),
        lines=0,
        mtime_ns=int(candidate.mtime_ns),
        content_hash=None,
        classification=classification,
    )

    probe_bytes = int(rules.get("binary_probe_bytes", 8192))
    try:
        with path.open("rb") as handle:
            probe = handle.read(probe_bytes)
    except OSError as error:
        raise AnalysisError(f"unreadable: {error}") from error
    if b"\0" in probe:
        raise AnalysisError("binary")

    if not _should_read_content(classification, name, include_docs):
        base.meta["metadata_only"] = True
        return base, False

    try:
        data = path.read_bytes()
    except OSError as error:
        raise AnalysisError(f"unreadable: {error}") from error

    full_text, replaced = decode_bytes(data)
    full_limit = int(rules.get("analyze_full_limit", 1024 * 1024))
    sampled = len(data) > full_limit
    text = full_text[:full_limit] if sampled else full_text
    base.encoding_replaced = replaced
    base.content_hash = sha256_bytes(data)
    base.lines = count_lines(full_text)
    base.todos = len(TODO_RE.findall(text))
    base.sampled = sampled
    if sampled:
        base.meta["sampled"] = True

    if classification.language == "python" and not sampled:
        _analyse_python(base, text)
    elif classification.language in _REGEX_LANGUAGES:
        _analyse_regex_language(base, text)
    elif classification.language == "markdown" or name.lower().startswith("readme"):
        _markdown_meta(base, text, root, path)

    kind = manifest_kind(candidate.path)
    if kind:
        _manifest_meta(base, text, kind)

    if classification.is_source or classification.is_test or classification.is_config:
        base.meta["secrets"] = find_secrets(
            text, limit=int(rules.get("thresholds", {}).get("security_max_findings", 5))
        )
        base.meta["dangerous"] = find_dangerous_patterns(text)
        base.meta["debug"] = find_debug_patterns(text)

    return base, False


def merge_manifest_meta(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Merge manifest facts, preferring root manifests over nested fixtures."""

    merged: dict[str, Any] = {
        "name": "",
        "version": "",
        "dependencies": [],
        "unpinned": [],
        "has_scripts": False,
        "bin": None,
        "manifest_kind": None,
        "manifest_path": None,
    }
    manifests: list[tuple[str, dict[str, Any]]] = []
    for row in rows:
        try:
            meta_json = row["meta_json"]
        except (IndexError, KeyError, TypeError):
            meta_json = None
        if not meta_json:
            continue
        try:
            meta = json.loads(meta_json)
        except (TypeError, ValueError):
            continue
        try:
            path = str(row["path"])
        except (IndexError, KeyError, TypeError):
            path = str(meta.get("manifest_path") or "")
        if meta.get("manifest_kind"):
            manifests.append((path, meta))

    root_manifests = [item for item in manifests if "/" not in item[0]]
    selected = root_manifests if root_manifests else manifests
    dependencies: list[str] = []
    unpinned: list[str] = []
    for _path, meta in sorted(selected, key=lambda item: item[0]):
        if not merged["name"] and meta.get("name"):
            merged["name"] = meta["name"]
        if not merged["version"] and meta.get("version"):
            merged["version"] = meta["version"]
        if not merged["manifest_kind"]:
            merged["manifest_kind"] = meta["manifest_kind"]
            merged["manifest_path"] = meta.get("manifest_path")
        merged["has_scripts"] = bool(merged["has_scripts"] or meta.get("has_scripts"))
        if merged["bin"] is None and meta.get("bin") is not None:
            merged["bin"] = meta["bin"]
        dependencies.extend(meta.get("dependencies") or [])
        unpinned.extend(meta.get("unpinned") or [])
    merged["dependencies"] = sorted(dict.fromkeys(str(item) for item in dependencies))
    merged["unpinned"] = sorted(dict.fromkeys(str(item) for item in unpinned))
    return merged
