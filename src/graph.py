from __future__ import annotations

import ast
from pathlib import Path
from typing import Any


def module_name_for_path(path: str) -> str:
    clean = Path(path).with_suffix("")
    parts = [part for part in clean.parts if part != "__init__"]
    return ".".join(parts)


class _GraphVisitor(ast.NodeVisitor):
    def __init__(self, rel_path: str, module: str):
        self.rel_path = rel_path
        self.module = module
        self.scope: list[str] = []
        self.symbols: list[dict[str, Any]] = []
        self.calls: dict[str, list[dict[str, Any]]] = {}

    def visit_ClassDef(self, node: ast.ClassDef) -> Any:
        self._add_symbol("class", node.name, node)
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> Any:
        self._visit_function("function", node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> Any:
        self._visit_function("async_function", node)

    def visit_Call(self, node: ast.Call) -> Any:
        owner = self._current_owner()
        if owner:
            self.calls.setdefault(owner, []).append(
                {
                    "name": self._call_name(node.func),
                    "line": getattr(node, "lineno", 0),
                }
            )
        self.generic_visit(node)

    def _visit_function(self, kind: str, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._add_symbol(kind, node.name, node)
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def _add_symbol(
        self,
        kind: str,
        name: str,
        node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        qualname = ".".join([self.module, *self.scope, name])
        self.symbols.append(
            {
                "name": name,
                "qualname": qualname,
                "kind": kind,
                "path": self.rel_path,
                "line": getattr(node, "lineno", 0),
                "end_line": getattr(node, "end_lineno", getattr(node, "lineno", 0)),
                "parent": ".".join([self.module, *self.scope]) if self.scope else self.module,
                "doc": ast.get_docstring(node) or "",
            }
        )

    def _current_owner(self) -> str | None:
        if not self.scope:
            return None
        return ".".join([self.module, *self.scope])

    def _call_name(self, node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            base = self._call_name(node.value)
            return f"{base}.{node.attr}" if base else node.attr
        if isinstance(node, ast.Call):
            return self._call_name(node.func)
        return ""


def build_python_graph(project_dir: str | Path) -> dict[str, Any]:
    root = Path(project_dir).resolve()
    modules: dict[str, dict[str, Any]] = {}
    symbol_index: list[dict[str, Any]] = []
    import_graph: dict[str, list[str]] = {}
    call_graph: dict[str, list[dict[str, Any]]] = {}
    parse_errors: list[dict[str, Any]] = []

    for path in sorted(root.rglob("*.py")):
        if _is_ignored(path):
            continue
        rel_path = path.relative_to(root).as_posix()
        module = module_name_for_path(rel_path)
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
            tree = ast.parse(content, filename=rel_path)
        except SyntaxError as exc:
            parse_errors.append(
                {
                    "path": rel_path,
                    "line": exc.lineno or 0,
                    "message": exc.msg,
                }
            )
            continue
        except OSError as exc:
            parse_errors.append({"path": rel_path, "line": 0, "message": str(exc)})
            continue

        imports = _extract_ast_imports(tree)
        visitor = _GraphVisitor(rel_path, module)
        visitor.visit(tree)
        modules[module] = {
            "path": rel_path,
            "imports": imports,
            "symbol_count": len(visitor.symbols),
            "is_test": _is_test_path(rel_path),
        }
        import_graph[module] = imports
        symbol_index.extend(visitor.symbols)
        call_graph.update(visitor.calls)

    return {
        "modules": modules,
        "symbols": symbol_index,
        "import_graph": import_graph,
        "call_graph": call_graph,
        "test_relationships": _build_test_relationships(modules),
        "dependency_degree": _build_dependency_degree(import_graph, modules),
        "runtime_paths": _build_runtime_paths(import_graph, modules),
        "parse_errors": parse_errors,
        "summary": {
            "modules": len(modules),
            "symbols": len(symbol_index),
            "imports": sum(len(values) for values in import_graph.values()),
            "call_sites": sum(len(values) for values in call_graph.values()),
            "parse_errors": len(parse_errors),
        },
    }


def _extract_ast_imports(tree: ast.AST) -> list[str]:
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = "." * node.level + (node.module or "")
            imports.append(module)
    return _unique(imports)


def _build_test_relationships(modules: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    production = {
        Path(info["path"]).stem: module
        for module, info in modules.items()
        if not info.get("is_test")
    }
    relationships: list[dict[str, str]] = []
    for test_module, info in modules.items():
        if not info.get("is_test"):
            continue
        test_stem = Path(info["path"]).stem
        candidates = [test_stem.removeprefix("test_"), test_stem.removesuffix("_test")]
        for candidate in candidates:
            target = production.get(candidate)
            if target:
                relationships.append({"test": test_module, "target": target})
                break
    return relationships


def _build_dependency_degree(
    import_graph: dict[str, list[str]],
    modules: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    inbound = {module: 0 for module in modules}
    for imports in import_graph.values():
        for imported in imports:
            for module in modules:
                if imported == module or imported.endswith(f".{module}") or module.endswith(f".{imported}"):
                    inbound[module] += 1
    ranked = []
    for module, info in modules.items():
        ranked.append(
            {
                "module": module,
                "path": info["path"],
                "inbound": inbound.get(module, 0),
                "outbound": len(import_graph.get(module, [])),
            }
        )
    ranked.sort(key=lambda item: (item["inbound"] + item["outbound"], item["inbound"]), reverse=True)
    return ranked[:25]


def _build_runtime_paths(
    import_graph: dict[str, list[str]],
    modules: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    entry_modules = [
        module
        for module, info in modules.items()
        if not info.get("is_test") and Path(info.get("path", "")).name in {"main.py", "app.py", "sentinel.py"}
    ]
    paths: list[dict[str, Any]] = []
    for entry in entry_modules[:8]:
        visited = {entry}
        frontier = [(entry, [entry])]
        while frontier:
            current, chain = frontier.pop(0)
            if len(chain) >= 4:
                paths.append({"entry": entry, "path": chain})
                continue
            imports = import_graph.get(current, [])
            matched = [
                module
                for module in modules
                if module not in visited
                and any(imported == module or module.endswith(f".{imported}") for imported in imports)
            ]
            if not matched:
                paths.append({"entry": entry, "path": chain})
                continue
            for module in matched[:4]:
                visited.add(module)
                frontier.append((module, [*chain, module]))
    return paths[:25]


def _is_test_path(rel_path: str) -> bool:
    path = Path(rel_path)
    name = path.name.lower()
    return "tests" in [part.lower() for part in path.parts] or name.startswith("test_") or name.endswith("_test.py")


def _is_ignored(path: Path) -> bool:
    ignored = {
        ".git",
        ".kilo",
        ".kilocode",
        ".sentinel",
        ".venv",
        "venv",
        "__pycache__",
        "node_modules",
        "dist",
        "build",
    }
    return any(part in ignored for part in path.parts)


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        clean = value.strip()
        if clean and clean not in seen:
            seen.add(clean)
            deduped.append(clean)
    return deduped
