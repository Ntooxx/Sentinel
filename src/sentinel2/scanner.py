"""Scan pipeline: discover, cache-check, analyse, index, score, report."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from . import index
from .analyze import AnalysisError, inspect_candidate, merge_manifest_meta
from .classify import detect_archetype, entry_points
from .config import load_rules, prepare_rules
from .discover import Candidate, DiscoveryResult, discover
from .errors import ScanError
from .findings import detect_findings
from .quality import compute_quality
from .report import build_report
from .utils import git_churn, now_iso


def _percentage(covered: int, total: int) -> float:
    if total <= 0:
        return 1.0
    return covered / total


def _coverage(
    discovery: DiscoveryResult,
    files_rows: Sequence[Any],
    include_docs: bool,
) -> dict[str, Any]:
    source_files = sum(1 for row in files_rows if int(row["is_source"] or 0))
    test_files = sum(1 for row in files_rows if int(row["is_test"] or 0))
    skipped_source = sum(1 for item in discovery.skipped if item.get("role") == "source") + sum(
        1 for row in files_rows if row["skipped_reason"] and row["role"] == "source"
    )
    skipped_test = sum(1 for item in discovery.skipped if item.get("role") == "test") + sum(
        1 for row in files_rows if row["skipped_reason"] and row["role"] == "test"
    )
    source_pct = _percentage(source_files, source_files + skipped_source)
    test_pct = _percentage(test_files, test_files + skipped_test)
    return {
        "source_pct": source_pct,
        "test_pct": test_pct,
        "source": f"{source_pct * 100:.0f}%",
        "tests": f"{test_pct * 100:.0f}%",
        "docs": "100%" if include_docs else "metadata",
        "skipped_source": skipped_source,
        "skipped_test": skipped_test,
    }


def _doc_mode_changed(previous_row: Any, candidate: Candidate, include_docs: bool) -> bool:
    if previous_row is None or not candidate.classification.is_doc:
        return False
    name = candidate.path.rsplit("/", 1)[-1].lower()
    if name.startswith("readme") or name in {
        "contributing.md",
        "architecture.md",
        "security.md",
        "changelog.md",
    }:
        return False
    try:
        meta = json.loads(previous_row["meta_json"] or "{}")
    except (TypeError, ValueError):
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    was_metadata_only = bool(meta.get("metadata_only"))
    return was_metadata_only != (not include_docs)


def _previous_report(previous: Any) -> dict[str, Any] | None:
    if previous is None:
        return None
    try:
        payload = json.loads(previous["report_json"])
        return payload if isinstance(payload, dict) else None
    except (TypeError, ValueError):
        return None


def scan_project(
    root: Path,
    *,
    rules: Mapping[str, Any] | None = None,
    ignores: Sequence[str] = (),
    full: bool = False,
    include_docs: bool = False,
    quiet: bool = False,
) -> dict[str, Any]:
    """Run one complete, incremental scan and return the report object."""

    _ = quiet  # progress output is intentionally minimal; reserved for future use
    root = Path(root).expanduser().resolve()
    if not root.exists():
        raise ScanError(f"path does not exist: {root}")
    if not root.is_dir():
        raise ScanError(f"path is not a directory: {root}")

    rules = prepare_rules(dict(rules) if rules is not None else load_rules(root))
    started_at = now_iso()
    started_perf = time.perf_counter()
    analysis_version = int(rules.get("analysis_version", 1))
    max_workers = max(1, min(int(rules.get("max_workers", 32)), (os.cpu_count() or 4) * 4))

    conn = index.connect(index.db_path(root))
    scan_id: int | None = None
    completed = False
    try:
        previous = index.latest_scan(conn)
        mode = "cold" if previous is None else "incremental"
        scan_id = index.start_scan(conn, started_at, mode, str(rules.get("rules_version", "quality-rules-1")))
        conn.commit()

        discovery = discover(
            root,
            rules,
            ignores,
            quick=previous is not None,
            probe_binary=False,
        )
        existing = index.load_existing(conn)
        current_paths = {candidate.path for candidate in discovery.candidates}
        removed = sorted(set(existing) - current_paths)
        statuses: list[tuple[str, str]] = []
        added = modified = unchanged = analyzed = cached = 0
        skipped_for_analysis: list[dict[str, Any]] = list(discovery.skipped)
        skipped_count = len(skipped_for_analysis)
        total_files = len(discovery.candidates) + len(discovery.skipped)
        pending: list[tuple[Candidate, Any, bool]] = []

        for candidate in discovery.candidates:
            previous_row = existing.get(candidate.path)
            force = bool(full and (candidate.classification.is_source or candidate.classification.is_test))
            force = force or _doc_mode_changed(previous_row, candidate, include_docs)
            if (
                not force
                and previous_row is not None
                and int(previous_row["size"]) == candidate.size
                and int(previous_row["mtime_ns"]) == candidate.mtime_ns
                and int(previous_row["analysis_version"] or 0) == analysis_version
            ):
                if previous_row["skipped_reason"]:
                    skipped_count += 1
                    statuses.append((candidate.path, "skipped"))
                else:
                    cached += 1
                    unchanged += 1
                    statuses.append((candidate.path, "unchanged"))
            else:
                pending.append((candidate, previous_row, force))

        def process_result(
            candidate: Candidate,
            previous_row: Any,
            analysis: Any,
            cache_hit: bool,
            force: bool,
            writes: list[Any],
            metadata_updates: list[tuple[str, int, int, str | None]],
        ) -> None:
            nonlocal added, modified, unchanged, analyzed, cached, skipped_count
            if cache_hit:
                if previous_row is not None and previous_row["skipped_reason"]:
                    skipped_count += 1
                    statuses.append((candidate.path, "skipped"))
                else:
                    cached += 1
                    unchanged += 1
                    statuses.append((candidate.path, "unchanged"))
                return
            if analysis is None:
                statuses.append((candidate.path, "skipped"))
                return
            previous_hash = previous_row["content_hash"] if previous_row is not None else None
            previous_version = int(previous_row["analysis_version"] or 0) if previous_row is not None else 0
            if (
                previous_row is not None
                and analysis.content_hash == previous_hash
                and previous_version == analysis_version
                and not force
            ):
                metadata_updates.append((candidate.path, candidate.size, candidate.mtime_ns, analysis.content_hash))
                cached += 1
                unchanged += 1
                statuses.append((candidate.path, "unchanged"))
                return
            writes.append(analysis)
            analyzed += 1
            if previous_row is None:
                added += 1
                statuses.append((candidate.path, "added"))
            elif analysis.content_hash != previous_hash:
                modified += 1
                statuses.append((candidate.path, "modified"))
            else:
                unchanged += 1
                statuses.append((candidate.path, "unchanged"))

        batch_size = 200
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for start in range(0, len(pending), batch_size):
                batch = pending[start : start + batch_size]
                writes: list[Any] = []
                metadata_updates: list[tuple[str, int, int, str | None]] = []
                futures = [
                    (
                        candidate,
                        previous_row,
                        force,
                        executor.submit(
                            inspect_candidate,
                            root,
                            candidate,
                            rules,
                            previous_row,
                            include_docs,
                            force,
                        ),
                    )
                    for candidate, previous_row, force in batch
                ]
                for candidate, previous_row, force, future in futures:
                    try:
                        analysis, cache_hit = future.result()
                    except AnalysisError as error:
                        index.upsert_skipped(
                            conn,
                            path=candidate.path,
                            language=candidate.classification.language,
                            role=candidate.classification.role,
                            size=candidate.size,
                            mtime_ns=candidate.mtime_ns,
                            reason=str(error),
                            analysis_version=analysis_version,
                        )
                        skipped_for_analysis.append(
                            {
                                "path": candidate.path,
                                "reason": str(error),
                                "role": candidate.classification.role,
                                "size": candidate.size,
                            }
                        )
                        skipped_count += 1
                        statuses.append((candidate.path, "skipped"))
                        continue
                    process_result(candidate, previous_row, analysis, cache_hit, force, writes, metadata_updates)
                if writes:
                    index.upsert_analyses(conn, writes, analysis_version)
                if metadata_updates:
                    index.update_metadata_batch(conn, metadata_updates)
                conn.commit()

        if removed:
            index.delete_files(conn, removed)
            for path in removed:
                statuses.append((path, "deleted"))
        conn.commit()

        churn = None
        if discovery.git_used:
            churn = git_churn(
                root,
                days=int(rules.get("thresholds", {}).get("churn_days", 90)),
                max_commits=int(rules.get("thresholds", {}).get("churn_commits", 500)),
            )
        churn_available = churn is not None
        if churn is not None:
            index.update_churn(conn, churn)
            conn.commit()

        index.record_scan_files(conn, scan_id, statuses)
        files_rows = index.load_files(conn)
        manifest_rows = index.load_manifest_rows(conn)
        manifest_meta = merge_manifest_meta(manifest_rows)
        coverage = _coverage(discovery, files_rows, include_docs)
        findings = detect_findings(
            conn,
            root,
            rules,
            files_rows,
            manifest_meta,
            discovery.excluded_counts,
            churn_available,
            include_docs,
        )
        archetype = detect_archetype(root, files_rows, manifest_meta)
        quality = compute_quality(
            files_rows,
            findings,
            rules,
            manifest_meta,
            coverage,
            archetype,
            entry_points(root, [str(row["path"]) for row in files_rows], manifest_meta),
        )

        previous_report = _previous_report(previous)
        previous_score = None
        if previous_report:
            previous_score = previous_report.get("summary", {}).get("overall_score")
        current_score = quality["summary"].get("overall_score")
        quality_delta = 0
        if isinstance(previous_score, (int, float)) and isinstance(current_score, (int, float)):
            quality_delta = int(current_score) - int(previous_score)
        changes = {
            "since_scan_id": int(previous["id"]) if previous is not None else None,
            "added": added,
            "modified": modified,
            "deleted": len(removed),
            "quality_delta": quality_delta,
        }

        index.upsert_findings(conn, scan_id, findings)
        conn.commit()
        index_size = index.index_size_bytes(root)

        duration_ms = int((time.perf_counter() - started_perf) * 1000)
        finished_at = now_iso()
        scan_meta = {
            "mode": mode,
            "started_at": started_at,
            "finished_at": finished_at,
            "duration_ms": duration_ms,
            "files_total": total_files,
            "files_analyzed": analyzed,
            "files_cached": cached,
            "files_skipped": skipped_count,
            "index_size_bytes": index_size,
            "coverage": coverage,
        }
        report = build_report(
            root=root,
            rules=rules,
            files_rows=files_rows,
            findings=findings,
            quality=quality,
            manifest_meta=manifest_meta,
            excluded_counts=discovery.excluded_counts,
            scan_meta=scan_meta,
            changes=changes,
            git_info=discovery.git_info,
            skipped=skipped_for_analysis,
        )
        index.finish_scan(
            conn,
            scan_id,
            finished_at=finished_at,
            duration_ms=duration_ms,
            commit_hash=report["project"]["git"].get("commit"),
            branch=report["project"]["git"].get("branch"),
            dirty=bool(report["project"]["git"].get("dirty")),
            files_total=total_files,
            files_analyzed=analyzed,
            files_cached=cached,
            files_skipped=skipped_count,
            report_json=json.dumps(report, ensure_ascii=False, sort_keys=True),
        )
        index.enforce_retention(conn, int(rules.get("retention_scans", 20)))
        conn.commit()
        index.checkpoint(conn)
        completed = True
        return report
    finally:
        if not completed and scan_id is not None:
            try:
                conn.execute("DELETE FROM scans WHERE id = ? AND report_json = '{}'", (scan_id,))
                conn.commit()
            except Exception:
                pass
        conn.close()
