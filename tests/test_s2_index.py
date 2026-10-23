from __future__ import annotations

import sqlite3

from sentinel2 import index


def test_schema_integrity_foreign_keys_and_wal(tmp_path):
    db = tmp_path / ".sentinel" / "quality.db"
    conn = index.connect(db)
    try:
        assert index.integrity_check(conn) == "ok"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA user_version").fetchone()[0] == index.SCHEMA_VERSION
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"meta", "files", "symbols", "imports", "findings", "scans", "scan_files"} <= tables
    finally:
        conn.close()


def test_scan_retention_and_cascade(tmp_path):
    conn = index.connect(tmp_path / "quality.db")
    try:
        ids = []
        for _ in range(5):
            ids.append(index.start_scan(conn, "2026-01-01T00:00:00+00:00", "cold", "test-rules"))
        index.record_scan_files(conn, ids[0], [("a.py", "added")])
        conn.commit()
        assert index.enforce_retention(conn, keep=2) == 3
        remaining = [row["id"] for row in conn.execute("SELECT id FROM scans ORDER BY id")]
        assert remaining == ids[-2:]
        assert conn.execute("SELECT COUNT(*) FROM scan_files").fetchone()[0] == 0
        assert index.integrity_check(conn) == "ok"
    finally:
        conn.close()


def test_upsert_findings_preserves_first_seen(tmp_path):
    class FakeFinding:
        rule_id = "MNT001"
        evidence_key = "a.py"
        file = "a.py"
        dimension = "maintainability"
        severity = "medium"
        confidence = "high"
        title = "Large source file"
        evidence = ["600 lines"]
        why = "why"
        recommendation = "next"

    conn = index.connect(tmp_path / "quality.db")
    try:
        first = index.start_scan(conn, "2026-01-01T00:00:00+00:00", "cold", "test")
        index.upsert_findings(conn, first, [FakeFinding()])
        second = index.start_scan(conn, "2026-01-01T00:00:01+00:00", "incremental", "test")
        index.upsert_findings(conn, second, [FakeFinding()])
        rows = index.load_findings(conn)
        assert len(rows) == 1
        assert rows[0]["first_seen_scan"] == first
        assert rows[0]["last_seen_scan"] == second
    finally:
        conn.close()


def test_readonly_connection_does_not_create_schema(tmp_path):
    db = tmp_path / "missing.db"
    try:
        index.connect(db, readonly=True)
    except sqlite3.OperationalError:
        pass
    else:
        raise AssertionError("read-only open should fail for a missing database")
    assert not db.exists()
