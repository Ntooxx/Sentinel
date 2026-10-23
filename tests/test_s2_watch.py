from __future__ import annotations

import threading
import time

from s2_helpers import make_clean

from sentinel2 import index
from sentinel2.config import load_rules
from sentinel2.watch import watch_project


def test_watch_updates_report_and_ignores_its_own_output(tmp_path):
    root = make_clean(tmp_path / "clean")
    out = root / "live.html"
    rules = load_rules()
    initial = threading.Event()
    updated = threading.Event()
    updates: list[dict] = []
    result: dict[str, int] = {}

    def on_update(report):
        updates.append(report)
        if len(updates) == 1:
            initial.set()
        if len(updates) >= 2:
            updated.set()

    def run():
        result["code"] = watch_project(
            root,
            rules=rules,
            fmt="html",
            out=out,
            interval=0.05,
            debounce_ms=20,
            quiet=True,
            max_seconds=6,
            on_update=on_update,
        )

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert initial.wait(5), "watch did not write the initial report"
    time.sleep(0.2)
    (root / "src" / "clean_app" / "core.py").write_text(
        "def add(left: int, right: int) -> int:\n"
        "    if left < 0:\n"
        "        return right\n"
        "    if right < 0:\n"
        "        return left\n"
        "    return left + right\n",
        encoding="utf-8",
    )
    assert updated.wait(6), "watch did not refresh after a source edit"
    thread.join(timeout=8)
    assert not thread.is_alive()
    assert result.get("code") == 0
    assert out.is_file()
    assert "SENTINEL QUALITY REPORT" in out.read_text(encoding="utf-8")
    assert not list(root.glob("*.tmp"))
    assert not list(root.glob(".live.html.*.tmp"))

    conn = index.connect(index.db_path(root), readonly=True)
    try:
        paths = {row["path"] for row in conn.execute("SELECT path FROM files")}
    finally:
        conn.close()
    assert "live.html" not in paths


def test_watch_detects_deleted_files(tmp_path):
    root = make_clean(tmp_path / "clean")
    rules = load_rules()
    initial = threading.Event()
    updated = threading.Event()
    updates: list[dict] = []

    def on_update(report):
        updates.append(report)
        if len(updates) == 1:
            initial.set()
        elif len(updates) >= 2:
            updated.set()

    thread = threading.Thread(
        target=lambda: watch_project(
            root,
            rules=rules,
            fmt="json",
            out=root / "live.json",
            interval=0.05,
            debounce_ms=20,
            quiet=True,
            max_seconds=6,
            on_update=on_update,
        ),
        daemon=True,
    )
    thread.start()
    assert initial.wait(5)
    time.sleep(0.2)
    (root / "src" / "clean_app" / "core.py").unlink()
    assert updated.wait(6)
    thread.join(timeout=8)
    assert not thread.is_alive()
    assert any(report["changes"]["deleted"] >= 1 for report in updates[1:])
