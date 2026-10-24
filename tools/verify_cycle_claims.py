#!/usr/bin/env python3
"""Independently verify an ARCH002 import-cycle claim against the source.

Sentinel's own cycle detector cannot be used to check Sentinel's cycle
findings, so this is a second, deliberately separate implementation with the
resolution rules stated in the docstring:

* ``import a.b``            -> edge to ``a.b`` if that module exists here;
* ``from a.b import X``     -> edge to ``a.b`` (and ``a.b.X`` when it exists);
* ``from . import X``       -> edge to the **sibling submodule** ``pkg.X`` only;
* ``from .mod import X``    -> edge to ``pkg.mod``;
* ``from pkg import X``     -> edge to ``pkg`` and to ``pkg.X`` when it exists.

``from . import X`` deliberately does *not* add an edge to the enclosing
package object. That edge exists implicitly for every submodule (a parent
package is always initialised first), so counting it would make almost any
package that imports its own submodules look cyclic. It is the reading used by
the usual dependency tools, and it is what this script is checking Sentinel's
claim against.

Run it against a checkout::

    python tools/verify_cycle_claims.py --root ../test-repos/requests/src --package requests
"""

from __future__ import annotations

import argparse
import ast
import sys
from collections import defaultdict
from pathlib import Path


def module_names(root: Path) -> dict[str, Path]:
    modules: dict[str, Path] = {}
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).with_suffix("")
        parts = list(rel.parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if not parts:
            continue
        modules[".".join(parts)] = path
    return modules


def build_graph(modules: dict[str, Path]) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = defaultdict(set)
    for name, path in modules.items():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        parts = name.split(".")
        is_package = path.name == "__init__.py"
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in modules:
                        graph[name].add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0:
                    base = node.module or ""
                    if base in modules:
                        graph[name].add(base)
                    for alias in node.names:
                        candidate = f"{base}.{alias.name}" if base else alias.name
                        if candidate in modules:
                            graph[name].add(candidate)
                    continue
                # Relative import: level 1 is the package that contains this
                # module, which is the module itself when it is __init__.py.
                containing = parts if is_package else parts[:-1]
                trim = node.level - 1
                if trim:
                    containing = containing[: len(containing) - trim] if trim <= len(containing) else []
                anchor = ".".join(containing)
                if node.module:
                    target = f"{anchor}.{node.module}" if anchor else node.module
                    if target in modules:
                        graph[name].add(target)
                    for alias in node.names:
                        candidate = f"{target}.{alias.name}"
                        if candidate in modules:
                            graph[name].add(candidate)
                else:
                    # from . import X -> the sibling submodule X, never the
                    # enclosing package object itself.
                    for alias in node.names:
                        candidate = f"{anchor}.{alias.name}" if anchor else alias.name
                        if candidate in modules:
                            graph[name].add(candidate)
    return graph


def strongly_connected(graph: dict[str, set[str]], nodes: set[str]) -> list[list[str]]:
    index = 0
    indices: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[list[str]] = []
    sys.setrecursionlimit(10000)

    def visit(node: str) -> None:
        nonlocal index
        indices[node] = lowlink[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for neighbour in sorted(graph.get(node, ())):
            if neighbour not in nodes:
                continue
            if neighbour not in indices:
                visit(neighbour)
                lowlink[node] = min(lowlink[node], lowlink[neighbour])
            elif neighbour in on_stack:
                lowlink[node] = min(lowlink[node], indices[neighbour])
        if lowlink[node] == indices[node]:
            component: list[str] = []
            while True:
                member = stack.pop()
                on_stack.discard(member)
                component.append(member)
                if member == node:
                    break
            components.append(sorted(component))

    for node in sorted(nodes):
        if node not in indices:
            visit(node)
    return [component for component in components if len(component) > 1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, help="Source root to walk, e.g. ../test-repos/requests/src")
    parser.add_argument("--package", required=True, help="Package name, e.g. requests")
    parser.add_argument(
        "--with-root-edges",
        action="store_true",
        help="Also treat 'from . import X' as an edge to the enclosing package (Sentinel's reading)",
    )
    args = parser.parse_args(argv)

    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        raise SystemExit(f"not a directory: {root}")
    modules = module_names(root)
    graph = build_graph(modules)

    if args.with_root_edges:
        # Reproduce the reading under test: every relative import additionally
        # points at the enclosing package object.
        for name, path in modules.items():
            if path.name == "__init__.py":
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            package = ".".join(name.split(".")[:-1])
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.level and package in modules:
                    graph[name].add(package)

    components = strongly_connected(graph, set(modules))
    root_importers = sorted(name for name in modules if args.package in graph.get(name, set()))

    print(f"modules analysed : {len(modules)}")
    print(f"edges            : {sum(len(targets) for targets in graph.values())}")
    print(f"module -> root   : {root_importers or 'NONE'}")
    print(f"cyclic components: {len(components)}")
    for component in components:
        print(f"  SCC of {len(component)}: {', '.join(component[:10])}{' ...' if len(component) > 10 else ''}")
    if not components:
        print("  none - no import cycle exists under these resolution rules")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
