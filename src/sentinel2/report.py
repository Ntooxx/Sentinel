"""Build the stable report object from an indexed scan snapshot."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from . import SCHEMA_VERSION
from .classify import detect_archetype, entry_points
from .findings import Finding, build_actions, rank_top_findings
from .utils import format_bytes


def _get(row: Any, key: str, default: Any = None) -> Any:
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return getattr(row, key, default)


def _components(files_rows: Sequence[Any]) -> list[str]:
    components: set[str] = set()
    for row in files_rows:
        if not int(_get(row, "is_source", 0) or 0):
            continue
        parts = str(_get(row, "path")).replace("\\", "/").split("/")
        if len(parts) > 1:
            if parts[0] == "src" and len(parts) > 2:
                components.add(parts[1])
            else:
                components.add(parts[0])
        elif parts:
            components.add(parts[0])
    return sorted(components)[:20]


def _hotspots(files_rows: Sequence[Any]) -> list[str]:
    source = [row for row in files_rows if int(_get(row, "is_source", 0) or 0)]

    def score(row: Any) -> float:
        return (
            float(_get(row, "lines", 0) or 0) / 100.0
            + float(_get(row, "max_complexity", 0) or 0)
            + float(_get(row, "size", 0) or 0) / 50000.0
            + float(_get(row, "churn", 0) or 0) * 2.0
        )

    ordered = sorted(source, key=lambda row: (-score(row), str(_get(row, "path"))))
    return [str(_get(row, "path")) for row in ordered[:5]]


def _coverage(
    coverage: Mapping[str, Any],
    excluded_counts: Mapping[str, int],
) -> dict[str, str]:
    return {
        "source": str(coverage.get("source", "n/a")),
        "tests": str(coverage.get("tests", "n/a")),
        "docs": str(coverage.get("docs", "n/a")),
        "vendor": "excluded" if excluded_counts.get("vendor", 0) else "none",
        "generated": "excluded" if excluded_counts.get("generated", 0) else "none",
        "examples": "excluded" if excluded_counts.get("example", 0) else "none",
    }


def build_report(
    *,
    root: Path,
    rules: Mapping[str, Any],
    files_rows: Sequence[Any],
    findings: Sequence[Finding],
    quality: Mapping[str, Any],
    manifest_meta: Mapping[str, Any],
    excluded_counts: Mapping[str, int],
    scan_meta: Mapping[str, Any],
    changes: Mapping[str, Any],
    git_info: Mapping[str, Any],
    skipped: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    archetype = str(quality.get("metrics", {}).get("archetype") or detect_archetype(root, files_rows, manifest_meta))
    project_name = str(manifest_meta.get("name") or root.name or "repository")
    source_rows = [row for row in files_rows if int(_get(row, "is_source", 0) or 0)]
    paths = [str(_get(row, "path")) for row in files_rows]
    top = rank_top_findings(findings, int(rules.get("top_findings", 5)))
    ordered_findings = list(findings)
    actions = build_actions(top)
    limitations = list(rules.get("limitations", []))
    if not git_info.get("available"):
        limitations.append("Churn requires a git repository; hotspot churn rules were skipped.")
    if int(scan_meta.get("files_skipped", 0) or 0):
        limitations.append(f"{int(scan_meta.get('files_skipped', 0))} file(s) were skipped; see coverage.")
    ceiling = quality.get("summary", {}).get("grade_ceiling")
    if ceiling:
        limitations.append(
            f"The overall score is capped at {ceiling}: the analysed code is too small to "
            "demonstrate the sustained quality the top of the scale implies."
        )
    sampled = sum(1 for row in source_rows if int(_get(row, "sampled", 0) or 0))
    if sampled:
        limitations.append(f"{sampled} source file(s) were sampled rather than read in full.")
    if quality.get("summary", {}).get("confidence") == "low":
        reasons = quality.get("summary", {}).get("confidence_reasons", [])
        if reasons:
            limitations.append("Low confidence: " + "; ".join(str(reason) for reason in reasons) + ".")
    limitations = list(dict.fromkeys(str(item) for item in limitations))

    report_findings = [finding.to_dict() for finding in ordered_findings]
    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": scan_meta.get("finished_at"),
        "project": {
            "name": project_name,
            "root": ".",
            "git": {
                "commit": git_info.get("commit"),
                "branch": git_info.get("branch"),
                "dirty": bool(git_info.get("dirty")),
            },
        },
        "scan": {
            "mode": scan_meta.get("mode", "cold"),
            "duration_ms": int(scan_meta.get("duration_ms", 0) or 0),
            "files_total": int(scan_meta.get("files_total", 0) or 0),
            "files_analyzed": int(scan_meta.get("files_analyzed", 0) or 0),
            "files_cached": int(scan_meta.get("files_cached", 0) or 0),
            "files_skipped": int(scan_meta.get("files_skipped", 0) or 0),
            "started_at": scan_meta.get("started_at"),
            "finished_at": scan_meta.get("finished_at"),
            "index_size_bytes": int(scan_meta.get("index_size_bytes", 0) or 0),
            "index_size_label": format_bytes(int(scan_meta.get("index_size_bytes", 0) or 0)),
            "coverage": _coverage(scan_meta.get("coverage", {}), excluded_counts),
        },
        "summary": dict(quality.get("summary", {})),
        "dimensions": list(quality.get("dimensions", [])),
        "metrics": dict(quality.get("metrics", {})),
        "findings": report_findings,
        "changes": {
            "since_scan_id": changes.get("since_scan_id"),
            "added": int(changes.get("added", 0) or 0),
            "modified": int(changes.get("modified", 0) or 0),
            "deleted": int(changes.get("deleted", 0) or 0),
            "quality_delta": int(changes.get("quality_delta", 0) or 0),
        },
        "architecture": {
            "archetype": archetype,
            "components": _components(files_rows),
            "entry_points": entry_points(root, paths, manifest_meta)[:10],
            "hotspots": _hotspots(files_rows),
        },
        "actions": actions,
        "exclusions": {
            "vendor": int(excluded_counts.get("vendor", 0) or 0),
            "generated": int(excluded_counts.get("generated", 0) or 0),
            "docs_translations": int(excluded_counts.get("doc_translation", 0) or 0),
            "examples": int(excluded_counts.get("example", 0) or 0),
            "reason": "Documentation translations, examples, generated files and vendored code do not drive source quality.",
        },
        "limitations": limitations,
        "rules_version": str(rules.get("rules_version", "quality-rules-1")),
    }
    if skipped:
        report["scan"]["skipped"] = [
            {
                "path": str(item.get("path")),
                "reason": str(item.get("reason")),
                "size": int(item.get("size", 0) or 0),
            }
            for item in skipped[:100]
        ]
    return report
