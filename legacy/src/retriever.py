from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from graph import build_python_graph
from utils import estimate_text_tokens


def retrieve_context(
    project_dir: str | Path,
    files: dict[str, dict[str, Any]],
    query: str,
    *,
    goal: str = "next",
    limit: int = 6,
    snippet_lines: int = 2,
) -> dict[str, Any]:
    query_terms = _terms(query)
    if not query_terms:
        raise ValueError("retrieve requires a non-empty --query value")

    root = Path(project_dir).resolve()
    graph = build_python_graph(root)
    scored_files = _score_files(files, query_terms, goal)
    top_files = scored_files[: max(1, limit)]
    top_paths = [item["path"] for item in top_files]
    symbols = _score_symbols(graph.get("symbols", []), query_terms, top_paths)[: max(1, limit * 2)]
    top_files, top_paths = _include_symbol_files(top_files, top_paths, files, symbols, limit)
    snippets = _collect_snippets(root, top_paths, query_terms, snippet_lines=snippet_lines)
    import_hints = _select_graph_edges(graph.get("import_graph", {}), top_paths)
    call_hints = _select_call_edges(graph.get("call_graph", {}), query_terms, top_paths)
    text = _render_retrieval_text(query, goal, top_files, symbols, snippets, import_hints, call_hints)

    full_tokens = sum(int(info.get("size", 0)) for info in files.values()) // 4
    retrieved_tokens = estimate_text_tokens(text)
    savings = 0 if full_tokens <= 0 else max(0, round((1 - (retrieved_tokens / full_tokens)) * 100))

    return {
        "query": query,
        "goal": goal,
        "files": top_files,
        "symbols": symbols,
        "snippets": snippets,
        "import_hints": import_hints,
        "call_hints": call_hints,
        "graph_summary": graph.get("summary", {}),
        "estimated_full_context_tokens": full_tokens,
        "estimated_retrieved_tokens": retrieved_tokens,
        "estimated_token_savings_percent": savings,
        "text": text,
    }


def _include_symbol_files(
    top_files: list[dict[str, Any]],
    top_paths: list[str],
    files: dict[str, dict[str, Any]],
    symbols: list[dict[str, Any]],
    limit: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    for symbol in symbols:
        path = symbol.get("path")
        if not path or path in top_paths or path not in files:
            continue
        if len(top_files) >= limit:
            top_paths.pop()
            top_files.pop()
        info = files[path]
        top_files.append(
            {
                "path": path,
                "score": symbol.get("score", 1),
                "lines": info.get("line_count", 0),
                "summary": info.get("summary", ""),
                "symbols": info.get("symbols", [])[:8],
                "imports": info.get("imports", [])[:8],
            }
        )
        top_paths.append(path)
    return top_files[:limit], top_paths[:limit]


def _score_files(files: dict[str, dict[str, Any]], terms: list[str], goal: str) -> list[dict[str, Any]]:
    scored: list[dict[str, Any]] = []
    goal_boosts = {
        "debug": ["test", "error", "exception", "fix", "bug"],
        "test": ["test", "pytest", "unittest"],
        "document": ["readme", "docs", "guide"],
        "review": ["test", "main", "core"],
    }
    boost_terms = goal_boosts.get(goal, [])
    for path, info in files.items():
        haystack = " ".join(
            [
                path,
                info.get("summary", ""),
                " ".join(info.get("symbols", [])),
                " ".join(info.get("imports", [])),
            ]
        ).lower()
        score = sum(_term_score(haystack, term) for term in terms)
        score += sum(1 for term in boost_terms if term in haystack)
        if score <= 0:
            continue
        if info.get("has_main"):
            score += 1
        if info.get("has_function") or info.get("has_class"):
            score += 1
        scored.append(
            {
                "path": path,
                "score": score,
                "lines": info.get("line_count", 0),
                "summary": info.get("summary", ""),
                "symbols": info.get("symbols", [])[:8],
                "imports": info.get("imports", [])[:8],
            }
        )
    scored.sort(key=lambda item: (item["score"], -int(item.get("lines", 0))), reverse=True)
    return scored


def _score_symbols(symbols: list[dict[str, Any]], terms: list[str], top_paths: list[str]) -> list[dict[str, Any]]:
    scored: list[dict[str, Any]] = []
    for symbol in symbols:
        haystack = " ".join(
            [
                symbol.get("name", ""),
                symbol.get("qualname", ""),
                symbol.get("kind", ""),
                symbol.get("path", ""),
                symbol.get("doc", ""),
            ]
        ).lower()
        score = sum(_term_score(haystack, term) for term in terms)
        if symbol.get("path") in top_paths:
            score += 2
        if score <= 0:
            continue
        entry = dict(symbol)
        entry["score"] = score
        scored.append(entry)
    scored.sort(key=lambda item: item["score"], reverse=True)
    return scored


def _collect_snippets(
    root: Path,
    paths: list[str],
    terms: list[str],
    *,
    snippet_lines: int,
) -> list[dict[str, Any]]:
    snippets: list[dict[str, Any]] = []
    for rel_path in paths:
        path = root / rel_path
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        matched_indexes = [
            index
            for index, line in enumerate(lines)
            if any(term in line.lower() for term in terms)
        ]
        for index in matched_indexes[:2]:
            start = max(0, index - snippet_lines)
            end = min(len(lines), index + snippet_lines + 1)
            snippets.append(
                {
                    "path": rel_path,
                    "start_line": start + 1,
                    "end_line": end,
                    "text": "\n".join(f"{line_no + 1}: {lines[line_no]}" for line_no in range(start, end)),
                }
            )
        if len(snippets) >= 12:
            break
    return snippets


def _select_graph_edges(graph: dict[str, list[str]], paths: list[str]) -> dict[str, list[str]]:
    selected: dict[str, list[str]] = {}
    for module, imports in graph.items():
        module_path = module.replace(".", "/") + ".py"
        if module_path in paths or any(path.endswith(module_path) for path in paths):
            selected[module] = imports[:10]
    return selected


def _select_call_edges(
    graph: dict[str, list[dict[str, Any]]],
    terms: list[str],
    paths: list[str],
) -> dict[str, list[dict[str, Any]]]:
    selected: dict[str, list[dict[str, Any]]] = {}
    for owner, calls in graph.items():
        owner_path = "/".join(owner.split(".")[:-1]) + ".py"
        haystack = owner.lower()
        if owner_path in paths or any(path.endswith(owner_path) for path in paths) or any(term in haystack for term in terms):
            selected[owner] = calls[:12]
    return selected


def _render_retrieval_text(
    query: str,
    goal: str,
    files: list[dict[str, Any]],
    symbols: list[dict[str, Any]],
    snippets: list[dict[str, Any]],
    import_hints: dict[str, list[str]],
    call_hints: dict[str, list[dict[str, Any]]],
) -> str:
    lines = [
        "SENTINEL RETRIEVAL",
        f"Query: {query}",
        f"Goal: {goal}",
        "",
        "Relevant Files:",
    ]
    lines.extend(
        f"- {item['path']} (score {item['score']}, {item['lines']} lines): {item.get('summary', '')}"
        for item in files
    )
    lines.append("")
    lines.append("Relevant Symbols:")
    if symbols:
        lines.extend(
            f"- {symbol['qualname']} [{symbol['kind']}] at {symbol['path']}:{symbol['line']}"
            for symbol in symbols[:12]
        )
    else:
        lines.append("- None matched")
    lines.append("")
    lines.append("Snippets:")
    if snippets:
        for snippet in snippets:
            lines.append(f"--- {snippet['path']}:{snippet['start_line']}")
            lines.append(snippet["text"])
    else:
        lines.append("- No direct line matches; start with the relevant files above.")
    lines.append("")
    lines.append("Import Hints:")
    if import_hints:
        lines.extend(f"- {module}: {', '.join(imports) if imports else '(no imports)'}" for module, imports in import_hints.items())
    else:
        lines.append("- None")
    lines.append("")
    lines.append("Call Hints:")
    if call_hints:
        for owner, calls in call_hints.items():
            visible = ", ".join(call["name"] for call in calls if call.get("name")) or "(no calls)"
            lines.append(f"- {owner}: {visible}")
    else:
        lines.append("- None")
    return "\n".join(lines).rstrip() + "\n"


def _terms(query: str) -> list[str]:
    stopwords = {
        "a",
        "an",
        "and",
        "are",
        "for",
        "from",
        "how",
        "implemented",
        "in",
        "is",
        "of",
        "on",
        "or",
        "the",
        "to",
        "what",
        "where",
        "which",
        "who",
        "why",
        "with",
    }
    return [
        term
        for term in re.findall(r"[a-zA-Z0-9_]+", query.lower())
        if len(term) > 1 and term not in stopwords
    ]


def _term_score(haystack: str, term: str) -> int:
    if term not in haystack:
        return 0
    return 4 if re.search(rf"\b{re.escape(term)}\b", haystack) else 1
