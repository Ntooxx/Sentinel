"""SQLite index: schema, migrations, facts and scan history."""

from __future__ import annotations

import contextlib
import json
import sqlite3
import time
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .analyze import FileAnalysis

from .utils import now_iso

DB_RELATIVE = Path(".sentinel") / "quality.db"
SCHEMA_VERSION = 1

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;
PRAGMA temp_store = MEMORY;

CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS files (
  path TEXT PRIMARY KEY,
  language TEXT,
  role TEXT,
  size INTEGER NOT NULL,
  lines INTEGER NOT NULL,
  mtime_ns INTEGER NOT NULL,
  content_hash TEXT,
  is_source INTEGER NOT NULL DEFAULT 0,
  is_test INTEGER NOT NULL DEFAULT 0,
  is_doc INTEGER NOT NULL DEFAULT 0,
  is_vendor INTEGER NOT NULL DEFAULT 0,
  is_generated INTEGER NOT NULL DEFAULT 0,
  is_config INTEGER NOT NULL DEFAULT 0,
  is_translation INTEGER NOT NULL DEFAULT 0,
  is_example INTEGER NOT NULL DEFAULT 0,
  analysis_version INTEGER NOT NULL DEFAULT 0,
  parse_error TEXT,
  encoding_replaced INTEGER NOT NULL DEFAULT 0,
  todos INTEGER NOT NULL DEFAULT 0,
  max_complexity INTEGER NOT NULL DEFAULT 0,
  max_function_length INTEGER NOT NULL DEFAULT 0,
  function_count INTEGER NOT NULL DEFAULT 0,
  class_count INTEGER NOT NULL DEFAULT 0,
  sampled INTEGER NOT NULL DEFAULT 0,
  churn INTEGER NOT NULL DEFAULT 0,
  meta_json TEXT,
  last_analyzed_at TEXT,
  skipped_reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_files_source ON files(is_source);
CREATE INDEX IF NOT EXISTS idx_files_language ON files(language);
CREATE INDEX IF NOT EXISTS idx_files_role ON files(role);
CREATE INDEX IF NOT EXISTS idx_files_test ON files(is_test);
CREATE INDEX IF NOT EXISTS idx_files_doc ON files(is_doc);

CREATE TABLE IF NOT EXISTS symbols (
  file TEXT NOT NULL,
  name TEXT NOT NULL,
  qualname TEXT NOT NULL,
  kind TEXT NOT NULL,
  line INTEGER NOT NULL,
  end_line INTEGER NOT NULL,
  complexity INTEGER NOT NULL DEFAULT 0,
  length INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (file, qualname, line),
  FOREIGN KEY (file) REFERENCES files(path) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(file);
CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
CREATE INDEX IF NOT EXISTS idx_symbols_complexity ON symbols(complexity);

CREATE TABLE IF NOT EXISTS imports (
  file TEXT NOT NULL,
  imported TEXT NOT NULL,
  kind TEXT NOT NULL,
  PRIMARY KEY (file, imported, kind),
  FOREIGN KEY (file) REFERENCES files(path) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_imports_file ON imports(file);
CREATE INDEX IF NOT EXISTS idx_imports_imported ON imports(imported);

CREATE TABLE IF NOT EXISTS findings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  rule_id TEXT NOT NULL,
  evidence_key TEXT NOT NULL,
  file TEXT,
  dimension TEXT NOT NULL,
  severity TEXT NOT NULL,
  confidence TEXT NOT NULL,
  title TEXT NOT NULL,
  evidence_json TEXT NOT NULL,
  why TEXT NOT NULL,
  recommendation TEXT NOT NULL,
  first_seen_scan INTEGER,
  last_seen_scan INTEGER,
  UNIQUE (rule_id, evidence_key)
);

CREATE INDEX IF NOT EXISTS idx_findings_scan ON findings(last_seen_scan);
CREATE INDEX IF NOT EXISTS idx_findings_file ON findings(file);
CREATE INDEX IF NOT EXISTS idx_findings_rule ON findings(rule_id);

CREATE TABLE IF NOT EXISTS scans (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT NOT NULL,
  finished_at TEXT NOT NULL,
  mode TEXT NOT NULL,
  commit_hash TEXT,
  branch TEXT,
  dirty INTEGER NOT NULL DEFAULT 0,
  duration_ms INTEGER NOT NULL,
  files_total INTEGER NOT NULL,
  files_analyzed INTEGER NOT NULL,
  files_cached INTEGER NOT NULL,
  files_skipped INTEGER NOT NULL,
  rules_version TEXT NOT NULL,
  report_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scan_files (
  scan_id INTEGER NOT NULL,
  path TEXT NOT NULL,
  status TEXT NOT NULL,
  PRIMARY KEY (scan_id, path),
  FOREIGN KEY (scan_id) REFERENCES scans(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_scan_files_path ON scan_files(path);
"""

FILE_COLUMNS = (
    "path",
    "language",
    "role",
    "size",
    "lines",
    "mtime_ns",
    "content_hash",
    "is_source",
    "is_test",
    "is_doc",
    "is_vendor",
    "is_generated",
    "is_config",
    "is_translation",
    "is_example",
    "analysis_version",
    "parse_error",
    "encoding_replaced",
    "todos",
    "max_complexity",
    "max_function_length",
    "function_count",
    "class_count",
    "sampled",
    "churn",
    "meta_json",
    "last_analyzed_at",
    "skipped_reason",
)


def db_path(root: Path) -> Path:
    return root / DB_RELATIVE


def connect(path: Path, readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        uri = path.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if version > SCHEMA_VERSION:
        raise sqlite3.DatabaseError(f"index schema {version} is newer than supported {SCHEMA_VERSION}")
    if version < SCHEMA_VERSION:
        _ensure_file_columns(conn)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()


def _ensure_file_columns(conn: sqlite3.Connection) -> None:
    """Additive migrations for indexes created by an earlier 2.x build."""

    existing = {row[1] for row in conn.execute("PRAGMA table_info(files)")}
    additions = {
        "is_translation": "INTEGER NOT NULL DEFAULT 0",
        "is_example": "INTEGER NOT NULL DEFAULT 0",
        "todos": "INTEGER NOT NULL DEFAULT 0",
        "max_complexity": "INTEGER NOT NULL DEFAULT 0",
        "max_function_length": "INTEGER NOT NULL DEFAULT 0",
        "function_count": "INTEGER NOT NULL DEFAULT 0",
        "class_count": "INTEGER NOT NULL DEFAULT 0",
        "sampled": "INTEGER NOT NULL DEFAULT 0",
        "churn": "INTEGER NOT NULL DEFAULT 0",
        "meta_json": "TEXT",
    }
    for column, definition in additions.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE files ADD COLUMN {column} {definition}")


def _row_to_payload(analysis: FileAnalysis) -> dict[str, Any]:
    classification = analysis.classification
    return {
        "path": analysis.path,
        "language": analysis.language,
        "role": analysis.role,
        "size": int(analysis.size),
        "lines": int(analysis.lines),
        "mtime_ns": int(analysis.mtime_ns),
        "content_hash": analysis.content_hash,
        "is_source": int(classification.is_source),
        "is_test": int(classification.is_test),
        "is_doc": int(classification.is_doc),
        "is_vendor": int(classification.is_vendor),
        "is_generated": int(classification.is_generated),
        "is_config": int(classification.is_config),
        "is_translation": int(classification.is_translation),
        "is_example": int(classification.is_example),
        "analysis_version": 0,
        "parse_error": analysis.parse_error,
        "encoding_replaced": int(analysis.encoding_replaced),
        "todos": int(analysis.todos),
        "max_complexity": int(analysis.max_complexity),
        "max_function_length": int(analysis.max_function_length),
        "function_count": int(analysis.function_count),
        "class_count": int(analysis.class_count),
        "sampled": int(analysis.sampled),
        "churn": 0,
        "meta_json": json.dumps(analysis.meta, ensure_ascii=False, sort_keys=True) if analysis.meta else None,
        "last_analyzed_at": now_iso(),
        "skipped_reason": None,
    }


def upsert_analyses(conn: sqlite3.Connection, analyses: Sequence[FileAnalysis], rules_version: int) -> None:
    """Upsert a batch of file facts with executemany to keep cold scans fast."""

    if not analyses:
        return
    payloads: list[list[Any]] = []
    for analysis in analyses:
        payload = _row_to_payload(analysis)
        payload["analysis_version"] = int(rules_version)
        payloads.append([payload[column] for column in FILE_COLUMNS])

    placeholders = ", ".join("?" for _ in FILE_COLUMNS)
    updates = ", ".join(f"{column}=excluded.{column}" for column in FILE_COLUMNS if column != "path")
    conn.executemany(
        f"INSERT INTO files ({', '.join(FILE_COLUMNS)}) VALUES ({placeholders}) "
        f"ON CONFLICT(path) DO UPDATE SET {updates}",
        payloads,
    )

    paths = [analysis.path for analysis in analyses]
    path_placeholders = ", ".join("?" for _ in paths)
    conn.execute(f"DELETE FROM symbols WHERE file IN ({path_placeholders})", paths)
    conn.execute(f"DELETE FROM imports WHERE file IN ({path_placeholders})", paths)

    symbol_rows: list[tuple[Any, ...]] = []
    import_rows: list[tuple[Any, ...]] = []
    for analysis in analyses:
        seen_symbols: set[tuple[str, int]] = set()
        for symbol in analysis.symbols:
            key = (symbol.qualname, symbol.line)
            if key in seen_symbols:
                continue
            seen_symbols.add(key)
            symbol_rows.append(
                (
                    analysis.path,
                    symbol.name,
                    symbol.qualname,
                    symbol.kind,
                    int(symbol.line),
                    int(symbol.end_line),
                    int(symbol.complexity),
                    int(symbol.length),
                )
            )
        for imported, kind in sorted(set(analysis.imports)):
            import_rows.append((analysis.path, imported, kind))
    if symbol_rows:
        conn.executemany(
            "INSERT INTO symbols (file, name, qualname, kind, line, end_line, complexity, length) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            symbol_rows,
        )
    if import_rows:
        conn.executemany(
            "INSERT OR IGNORE INTO imports (file, imported, kind) VALUES (?, ?, ?)",
            import_rows,
        )


def upsert_analysis(conn: sqlite3.Connection, analysis: FileAnalysis, rules_version: int) -> None:
    upsert_analyses(conn, [analysis], rules_version)


def update_metadata_batch(
    conn: sqlite3.Connection,
    rows: Sequence[tuple[str, int, int, str | None]],
) -> None:
    if rows:
        conn.executemany(
            "UPDATE files SET size = ?, mtime_ns = ?, content_hash = COALESCE(?, content_hash) WHERE path = ?",
            ((int(size), int(mtime), content_hash, path) for path, size, mtime, content_hash in rows),
        )


def upsert_skipped(
    conn: sqlite3.Connection,
    *,
    path: str,
    language: str,
    role: str,
    size: int,
    mtime_ns: int,
    reason: str,
    analysis_version: int,
) -> None:
    """Persist a lightweight skipped-file row so warm scans do not re-read it."""

    conn.execute(
        "INSERT INTO files (path, language, role, size, lines, mtime_ns, content_hash, "
        "analysis_version, skipped_reason, last_analyzed_at) VALUES (?, ?, ?, ?, 0, ?, NULL, ?, ?, ?) "
        "ON CONFLICT(path) DO UPDATE SET language=excluded.language, role=excluded.role, size=excluded.size, "
        "lines=0, mtime_ns=excluded.mtime_ns, content_hash=NULL, is_source=0, is_test=0, is_doc=0, "
        "is_vendor=0, is_generated=0, is_config=0, is_translation=0, is_example=0, "
        "analysis_version=excluded.analysis_version, parse_error=NULL, encoding_replaced=0, todos=0, "
        "max_complexity=0, max_function_length=0, function_count=0, class_count=0, sampled=0, churn=0, "
        "meta_json=NULL, skipped_reason=excluded.skipped_reason, last_analyzed_at=excluded.last_analyzed_at",
        (path, language, role, int(size), int(mtime_ns), int(analysis_version), reason, now_iso()),
    )
    conn.execute("DELETE FROM symbols WHERE file = ?", (path,))
    conn.execute("DELETE FROM imports WHERE file = ?", (path,))


def update_metadata(
    conn: sqlite3.Connection,
    path: str,
    size: int,
    mtime_ns: int,
    content_hash: str | None,
) -> None:
    conn.execute(
        "UPDATE files SET size = ?, mtime_ns = ?, content_hash = COALESCE(?, content_hash) WHERE path = ?",
        (int(size), int(mtime_ns), content_hash, path),
    )


def delete_files(conn: sqlite3.Connection, paths: Iterable[str]) -> int:
    path_list = list(paths)
    deleted = 0
    for start in range(0, len(path_list), 500):
        chunk = path_list[start : start + 500]
        placeholders = ", ".join("?" for _ in chunk)
        cursor = conn.execute(f"DELETE FROM files WHERE path IN ({placeholders})", chunk)
        deleted += cursor.rowcount
    return deleted


def load_existing(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    return {row["path"]: row for row in conn.execute("SELECT * FROM files")}


def load_files(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM files ORDER BY path"))


def load_manifest_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT * FROM files WHERE meta_json IS NOT NULL AND "
            "(role = 'config' OR is_source = 1) ORDER BY path"
        )
    )


def load_imports(conn: sqlite3.Connection, source_only: bool = True) -> list[sqlite3.Row]:
    if source_only:
        return list(
            conn.execute(
                "SELECT imports.file, imports.imported, imports.kind FROM imports "
                "JOIN files ON files.path = imports.file WHERE files.is_source = 1 ORDER BY imports.file, imports.imported"
            )
        )
    return list(conn.execute("SELECT file, imported, kind FROM imports ORDER BY file, imported"))


def update_churn(conn: sqlite3.Connection, churn: Mapping[str, int]) -> None:
    conn.execute("UPDATE files SET churn = 0")
    if churn:
        conn.executemany(
            "UPDATE files SET churn = ? WHERE path = ?",
            ((int(count), path) for path, count in churn.items()),
        )


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def get_meta(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def start_scan(conn: sqlite3.Connection, started_at: str, mode: str, rules_version: str) -> int:
    cursor = conn.execute(
        "INSERT INTO scans (started_at, finished_at, mode, duration_ms, files_total, files_analyzed, "
        "files_cached, files_skipped, rules_version, report_json) VALUES (?, ?, ?, 0, 0, 0, 0, 0, ?, '{}')",
        (started_at, started_at, mode, rules_version),
    )
    return int(cursor.lastrowid)


def finish_scan(
    conn: sqlite3.Connection,
    scan_id: int,
    *,
    finished_at: str,
    duration_ms: int,
    commit_hash: str | None,
    branch: str | None,
    dirty: bool,
    files_total: int,
    files_analyzed: int,
    files_cached: int,
    files_skipped: int,
    report_json: str,
) -> None:
    conn.execute(
        "UPDATE scans SET finished_at = ?, commit_hash = ?, branch = ?, dirty = ?, duration_ms = ?, "
        "files_total = ?, files_analyzed = ?, files_cached = ?, files_skipped = ?, report_json = ? WHERE id = ?",
        (
            finished_at,
            commit_hash,
            branch,
            int(dirty),
            int(duration_ms),
            int(files_total),
            int(files_analyzed),
            int(files_cached),
            int(files_skipped),
            report_json,
            int(scan_id),
        ),
    )


def record_scan_files(conn: sqlite3.Connection, scan_id: int, statuses: Iterable[tuple[str, str]]) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO scan_files (scan_id, path, status) VALUES (?, ?, ?)",
        ((int(scan_id), path, status) for path, status in statuses),
    )


def latest_scan(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM scans WHERE report_json != '{}' ORDER BY id DESC LIMIT 1"
    ).fetchone()


def scan_by_id(conn: sqlite3.Connection, scan_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM scans WHERE id = ?", (scan_id,)).fetchone()


def previous_scan(conn: sqlite3.Connection, scan_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM scans WHERE id < ? AND report_json != '{}' ORDER BY id DESC LIMIT 1",
        (scan_id,),
    ).fetchone()


def scan_count(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM scans").fetchone()[0])


def scan_file_statuses(conn: sqlite3.Connection, scan_id: int) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT path, status FROM scan_files WHERE scan_id = ? ORDER BY path", (scan_id,)))


def upsert_findings(conn: sqlite3.Connection, scan_id: int, findings: Sequence[Any]) -> None:
    existing = {
        (row["rule_id"], row["evidence_key"]): row
        for row in conn.execute("SELECT rule_id, evidence_key, first_seen_scan FROM findings")
    }
    for finding in findings:
        key = (finding.rule_id, finding.evidence_key)
        first_seen = existing[key]["first_seen_scan"] if key in existing else scan_id
        conn.execute(
            "INSERT INTO findings (rule_id, evidence_key, file, dimension, severity, confidence, title, "
            "evidence_json, why, recommendation, first_seen_scan, last_seen_scan) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(rule_id, evidence_key) DO UPDATE SET file=excluded.file, dimension=excluded.dimension, "
            "severity=excluded.severity, confidence=excluded.confidence, title=excluded.title, "
            "evidence_json=excluded.evidence_json, why=excluded.why, recommendation=excluded.recommendation, "
            "last_seen_scan=excluded.last_seen_scan",
            (
                finding.rule_id,
                finding.evidence_key,
                finding.file,
                finding.dimension,
                finding.severity,
                finding.confidence,
                finding.title,
                json.dumps(finding.evidence, ensure_ascii=False),
                finding.why,
                finding.recommendation,
                first_seen,
                int(scan_id),
            ),
        )
    conn.execute("DELETE FROM findings WHERE last_seen_scan IS NULL OR last_seen_scan != ?", (int(scan_id),))


def load_findings(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM findings ORDER BY rule_id, evidence_key"))


def enforce_retention(conn: sqlite3.Connection, keep: int = 20) -> int:
    rows = list(conn.execute("SELECT id FROM scans ORDER BY id DESC").fetchall())
    remove = [int(row["id"]) for row in rows[keep:]]
    for scan_id in remove:
        conn.execute("DELETE FROM scans WHERE id = ?", (scan_id,))
    return len(remove)


def checkpoint(conn: sqlite3.Connection) -> None:
    with contextlib.suppress(sqlite3.DatabaseError):
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def integrity_check(conn: sqlite3.Connection) -> str:
    return str(conn.execute("PRAGMA integrity_check").fetchone()[0])


def index_size_bytes(root: Path) -> int:
    base = db_path(root)
    total = 0
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(base) + suffix)
        try:
            if candidate.exists():
                total += candidate.stat().st_size
        except OSError:
            continue
    return total


def wait_for_lock(conn: sqlite3.Connection, attempts: int = 3) -> None:
    for attempt in range(attempts):
        try:
            conn.execute("SELECT 1").fetchone()
            return
        except sqlite3.OperationalError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.1)
