"""Sentinel web demo - paste a repository, get a live quality report.

A single-file, standard-library-only HTTP server. It is a demo harness around
the Sentinel 2.0 package: it clones (or accepts a local path to) a repository,
runs the same scan the CLI runs, and serves the same self-contained HTML
report.

Design notes
------------
* Zero third-party dependencies. Standard library only, like the product.
* The demo never reimplements analysis. It imports ``sentinel2`` and calls
  ``scan_project`` - the same entry point the CLI uses.
* Repositories are cloned with the system ``git`` executable (no shell), into
  ``demo/.cache/``. Clones are shallow (``--depth 1``) and time-limited.
* The server does not execute any cloned code. Scanning reads and parses files.
* The report is rendered by ``sentinel2.renderers.render_html``; the demo adds
  one small floating "analyze another" bar to its own copy so you can get back
  to the form. The artifact itself is unchanged.

Run
---
    python demo/server.py                 # http://127.0.0.1:8765
    python demo/server.py --port 9000 --open
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
CACHE_DIR = REPO_ROOT / "demo" / ".cache"
CLONE_TIMEOUT_SECONDS = 300

# Public-mode limits. These exist because a hosted instance lets strangers ask
# the server to download code. They apply only when --public is passed.
PUBLIC_CLONE_TIMEOUT_SECONDS = 120
PUBLIC_MAX_CONCURRENT_JOBS = 2
PUBLIC_RATE_LIMIT_JOBS = 6
PUBLIC_RATE_LIMIT_WINDOW_SECONDS = 600
PUBLIC_CACHE_KEEP_DIRS = 8

# Strict CSP for report responses: the report is self-contained (inline CSS and
# inline SVG only), so nothing else needs to load. The landing page needs its
# inline script and gets its own, looser policy.
REPORT_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'"

# Set by main(); read by the request handlers.
PUBLIC_MODE = False

_JOB_SEMAPHORE = threading.Semaphore(PUBLIC_MAX_CONCURRENT_JOBS)
_RATE_STATE: dict[str, list[float]] = {}
_RATE_LOCK = threading.Lock()

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


def clone_timeout() -> int:
    return PUBLIC_CLONE_TIMEOUT_SECONDS if PUBLIC_MODE else CLONE_TIMEOUT_SECONDS


def rate_limit_ok(client_ip: str) -> tuple[bool, int]:
    """Allow at most ``PUBLIC_RATE_LIMIT_JOBS`` per window per client."""
    now = time.time()
    window = PUBLIC_RATE_LIMIT_WINDOW_SECONDS
    with _RATE_LOCK:
        hits = [stamp for stamp in _RATE_STATE.get(client_ip, []) if now - stamp < window]
        if len(hits) >= PUBLIC_RATE_LIMIT_JOBS:
            retry = int(window - (now - hits[0])) + 1
            return False, retry
        hits.append(now)
        _RATE_STATE[client_ip] = hits
    return True, 0


# --------------------------------------------------------------------------
# Input handling
# --------------------------------------------------------------------------

_SLUG_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
_SHORTHAND = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class InputError(ValueError):
    """The pasted value is not something the demo can analyze."""


def normalize_source(raw: str, *, allow_local: bool = True) -> tuple[str, str, str]:
    """Return ``(kind, value, label)`` for a pasted repo URL or local path.

    ``kind`` is ``"url"`` or ``"path"``. Shorthand ``owner/repo`` becomes a
    GitHub URL. When ``allow_local`` is false (public deployments), local
    filesystem paths are refused.
    """
    text = (raw or "").strip().strip('"').strip("'")
    if not text:
        raise InputError("Paste a repository URL or a local folder path.")
    if len(text) > 500:
        raise InputError("That input is too long to be a repository URL or path.")
    if "\n" in text or "\r" in text:
        raise InputError("Paste a single repository URL or path, not multiple lines.")

    if text.startswith(("http://", "https://", "git://", "ssh://", "git@")):
        label = _repo_label(text)
        return "url", text, label
    if _SHORTHAND.match(text):
        url = f"https://github.com/{text}"
        return "url", url, text
    if not allow_local:
        raise InputError(
            "This deployment only accepts public repository URLs or owner/repo shorthand."
        )
    if text.startswith("file://"):
        parsed = urllib.parse.urlparse(text)
        path = urllib.parse.unquote(parsed.path)
        if os.name == "nt" and re.match(r"^/[A-Za-z]:", path):
            path = path[1:]
        text = path or text
    candidate = Path(text).expanduser()
    if candidate.exists():
        if not candidate.is_dir():
            raise InputError(f"That is a file, not a repository folder: {candidate}")
        return "path", str(candidate.resolve()), candidate.resolve().name
    raise InputError(
        "That is neither an http(s) repository URL, an owner/repo shorthand, "
        "nor an existing local folder."
    )


def _repo_label(url: str) -> str:
    tail = url.rstrip("/")
    if tail.endswith(".git"):
        tail = tail[:-4]
    tail = tail.split("/")[-1] or "repository"
    return tail


def _slug(text: str) -> str:
    cleaned = _SLUG_SAFE.sub("_", text).strip("_")
    return (cleaned or "repo")[:60]


# --------------------------------------------------------------------------
# Git + scan worker
# --------------------------------------------------------------------------


class Job:
    def __init__(self, source: str, label: str, kind: str, value: str, all_findings: bool) -> None:
        self.id = f"job{int(time.time() * 1000):x}{id(self) & 0xffff:x}"
        self.source = source
        self.label = label
        self.kind = kind
        self.value = value
        self.all_findings = all_findings
        self.state = "queued"
        self.messages: list[str] = []
        self.error = ""
        self.started_at = time.time()
        self.finished_at: float | None = None
        self.html: str | None = None
        self.json: str | None = None
        self.markdown: str | None = None
        self.summary: dict[str, Any] = {}
        self.lock = threading.Lock()

    def log(self, message: str) -> None:
        with self.lock:
            self.messages.append(message)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "id": self.id,
                "label": self.label,
                "source": self.source,
                "state": self.state,
                "messages": list(self.messages),
                "error": self.error,
                "elapsed_ms": int(((self.finished_at or time.time()) - self.started_at) * 1000),
                "summary": dict(self.summary),
                "has_report": self.html is not None,
            }


_JOBS: dict[str, Job] = {}
_JOBS_LOCK = threading.Lock()
_JOBS_ORDER: list[str] = []


def _register(job: Job) -> None:
    with _JOBS_LOCK:
        _JOBS[job.id] = job
        _JOBS_ORDER.append(job.id)
        while len(_JOBS_ORDER) > 25:
            _JOBS.pop(_JOBS_ORDER.pop(0), None)


def _run_git(args: list[str], *, cwd: Path | None = None, timeout: int = CLONE_TIMEOUT_SECONDS) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def _checkout(job: Job) -> Path:
    if job.kind == "path":
        job.log("Using local folder (no clone).")
        return Path(job.value)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dest = CACHE_DIR / _slug(job.label)
    timeout = clone_timeout()
    if (dest / ".git").exists():
        job.log(f"Cloning into cache: reusing {dest.name}")
        try:
            fetched = _run_git(["fetch", "--depth", "1", "origin", "HEAD"], cwd=dest, timeout=min(timeout, 120))
            if fetched.returncode == 0:
                _run_git(["reset", "--hard", "FETCH_HEAD"], cwd=dest, timeout=60)
                job.log(f"Cloning done: refreshed {dest.name}")
                return dest
        except subprocess.TimeoutExpired:
            pass
        job.log("Refreshing the cache failed; cloning fresh instead.")
        _rmtree(dest)
    job.log(f"Cloning: git clone --depth 1 {job.value}")
    try:
        result = _run_git(["clone", "--depth", "1", "--single-branch", "--", job.value, str(dest)], timeout=timeout)
    except FileNotFoundError as error:
        raise InputError("The `git` executable was not found on PATH; install git or use a local folder path.") from error
    except subprocess.TimeoutExpired as error:
        _rmtree(dest)
        raise InputError(f"Cloning timed out after {timeout}s. Try a smaller repository.") from error
    if result.returncode != 0:
        _rmtree(dest)
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        tail = detail[-1] if detail else "unknown git error"
        raise InputError(f"git clone failed: {tail}")
    _prune_cache()
    job.log(f"Cloning done: {dest.name}")
    return dest


def _prune_cache(keep: int = PUBLIC_CACHE_KEEP_DIRS) -> None:
    """Keep the cache bounded on a long-lived (hosted) instance."""
    if not CACHE_DIR.exists():
        return
    try:
        entries = [item for item in CACHE_DIR.iterdir() if item.is_dir()]
    except OSError:
        return
    entries.sort(key=lambda item: item.stat().st_mtime if item.exists() else 0, reverse=True)
    for stale in entries[keep:]:
        _rmtree(stale)


def _rmtree(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)


def analyze(job: Job) -> None:
    """Run one job, respecting the concurrency cap."""
    if not _JOB_SEMAPHORE.acquire(blocking=False):
        job.log("Queued: waiting for a free worker slot")
        _JOB_SEMAPHORE.acquire()
    try:
        _analyze(job)
    finally:
        _JOB_SEMAPHORE.release()


def _analyze(job: Job) -> None:
    try:
        with job.lock:
            job.state = "cloning"
        root = _checkout(job)

        from sentinel2.config import load_rules
        from sentinel2.renderers import render
        from sentinel2.scanner import scan_project

        with job.lock:
            job.state = "scanning"
        job.log(f"Scanning: {root.name} (in-process, no subprocess)")
        report = scan_project(root, rules=load_rules(root), quiet=True)

        with job.lock:
            job.state = "rendering"
        job.log("Rendering: self-contained HTML report")
        html = render(report, "html", all_findings=job.all_findings)
        html = _with_demo_chrome(html, job)

        summary = report.get("summary", {}) or {}
        with job.lock:
            job.html = html
            job.json = render(report, "json")
            job.markdown = render(report, "md")
            job.summary = {
                "band": summary.get("band"),
                "overall_score": summary.get("overall_score"),
                "confidence": summary.get("confidence"),
                "findings": len(report.get("findings", []) or []),
                "files": (report.get("scan", {}) or {}).get("files_total"),
                "commit": ((report.get("project", {}) or {}).get("git", {}) or {}).get("commit"),
                "scan_mode": (report.get("scan", {}) or {}).get("mode"),
                "duration_ms": (report.get("scan", {}) or {}).get("duration_ms"),
            }
            job.state = "done"
            job.finished_at = time.time()
        job.log("Ready: report generated")
    except InputError as error:
        with job.lock:
            job.state = "error"
            job.error = str(error)
            job.finished_at = time.time()
    except subprocess.TimeoutExpired:
        with job.lock:
            job.state = "error"
            job.error = "The operation timed out."
            job.finished_at = time.time()
    except Exception as error:  # surface a message, never crash the server
        with job.lock:
            job.state = "error"
            job.error = f"{type(error).__name__}: {error}"
            job.finished_at = time.time()


def _with_demo_chrome(html: str, job: Job) -> str:
    """Add a small floating bar to the served copy so users can run again.

    The report artifact written to disk by the CLI is never modified.
    """
    bar = (
        '<div style="position:fixed;left:50%;bottom:18px;transform:translateX(-50%);z-index:50;'
        "display:flex;gap:8px;align-items:center;padding:8px 14px;border-radius:999px;"
        "background:rgba(9,14,26,.92);border:1px solid #22304f;backdrop-filter:blur(10px);"
        'font:13px/1.4 ui-sans-serif,system-ui,sans-serif;color:#93a3c4;box-shadow:0 12px 30px rgba(0,0,0,.5)">'
        '<span style="color:#e8eefb;font-weight:600">Sentinel web demo</span>'
        f'<span style="opacity:.8">{_escape_html(job.label)}</span>'
        '<a href="/" style="color:#38bdf8;text-decoration:none;font-weight:600">Analyze another</a>'
        f'<a href="/api/download?id={job.id}&amp;kind=html" style="color:#93a3c4;text-decoration:none">Download</a>'
        "</div>"
    )
    return html.replace("</body>", bar + "\n</body>")


def _escape_html(text: str) -> str:
    from html import escape

    return escape(str(text))


# --------------------------------------------------------------------------
# Landing page
# --------------------------------------------------------------------------

INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sentinel - analyze any repository</title>
<style>
:root { color-scheme: dark; }
* { box-sizing: border-box; }
body {
  margin: 0; min-height: 100vh; color: #e8eefb;
  font: 15px/1.6 ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  background:
    radial-gradient(900px 520px at 12% -8%, rgba(56,189,248,.18), transparent 60%),
    radial-gradient(760px 480px at 92% 4%, rgba(167,139,250,.15), transparent 62%),
    #070b16;
  -webkit-font-smoothing: antialiased;
}
.wrap { max-width: 860px; margin: 0 auto; padding: 64px 20px 90px; }
.brand { display: flex; align-items: center; gap: 10px; font-weight: 800; letter-spacing: .16em; font-size: 13px; color: #93a3c4; }
.brand .dot { width: 11px; height: 11px; border-radius: 50%; background: radial-gradient(circle at 30% 30%, #38bdf8, #818cf8); box-shadow: 0 0 14px rgba(56,189,248,.85); }
h1 { font-size: 40px; line-height: 1.12; letter-spacing: -.02em; margin: 26px 0 12px; }
h1 span { background: linear-gradient(100deg, #38bdf8, #a78bfa); -webkit-background-clip: text; background-clip: text; color: transparent; }
p.lead { color: #93a3c4; font-size: 16px; max-width: 62ch; margin: 0 0 30px; }
.card {
  border: 1px solid #22304f; border-radius: 18px; padding: 20px;
  background: linear-gradient(180deg, rgba(15,22,38,.95), rgba(20,29,51,.95));
  box-shadow: 0 24px 60px rgba(0,0,0,.42);
}
form { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
input[type=text] {
  flex: 1; min-width: 260px; padding: 14px 16px; border-radius: 12px;
  border: 1px solid #2f3e64; background: #070b16; color: #e8eefb; font-size: 15px;
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
}
input[type=text]:focus { outline: none; border-color: #38bdf8; box-shadow: 0 0 0 3px rgba(56,189,248,.18); }
button {
  padding: 14px 22px; border-radius: 12px; border: 0; cursor: pointer;
  font-size: 15px; font-weight: 700; color: #04121f;
  background: linear-gradient(120deg, #38bdf8, #818cf8);
}
button:disabled { opacity: .55; cursor: progress; }
.row { flex: 1 0 100%; display: flex; align-items: center; gap: 10px; margin-top: 14px; color: #93a3c4; font-size: 13px; flex-wrap: wrap; }
label.check { display: flex; align-items: center; gap: 7px; cursor: pointer; }
.examples { margin-top: 16px; display: flex; gap: 8px; flex-wrap: wrap; }
.examples button {
  background: rgba(56,189,248,.08); color: #93a3c4; border: 1px solid #22304f;
  font-weight: 600; font-size: 12.5px; padding: 7px 12px; border-radius: 999px;
}
.examples button:hover { color: #e8eefb; border-color: #38bdf8; }
#progress { margin-top: 22px; display: none; }
#progress.show { display: block; }
.log {
  border: 1px solid #22304f; border-radius: 14px; background: #070b16; padding: 14px 16px;
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 13px; color: #93a3c4;
  min-height: 60px;
}
.log .line { display: flex; gap: 10px; }
.log .line.ok { color: #34d399; }
.log .line.err { color: #f87171; }
.bar { height: 4px; border-radius: 999px; background: #141d33; overflow: hidden; margin-bottom: 12px; }
.bar > i { display: block; height: 100%; width: 30%; background: linear-gradient(90deg, #38bdf8, #a78bfa); animation: slide 1.2s ease-in-out infinite; }
@keyframes slide { 0% { margin-left: -30%; } 100% { margin-left: 100%; } }
.errbox { border: 1px solid rgba(248,113,113,.45); background: rgba(248,113,113,.08); color: #fca5a5; border-radius: 12px; padding: 14px 16px; margin-top: 16px; display: none; }
.errbox.show { display: block; }
.note { margin-top: 30px; color: #5f6e8e; font-size: 12.5px; }
.note code { color: #93a3c4; }
.hint { color: #5f6e8e; font-size: 12.5px; margin-top: 10px; }
</style>
</head>
<body>
<div class="wrap">
  <div class="brand"><span class="dot"></span>SENTINEL</div>
  <h1>Paste a repository.<br><span>Get the quality report.</span></h1>
  <p class="lead">
    Sentinel clones the repository (shallow), runs a real scan, and returns a one-page
    report: a quality band, six scored dimensions, and the top source risks with evidence.
    No mock data, no pre-baked results.
  </p>

  <div class="card">
    <form id="form" autocomplete="off">
      <input id="repo" type="text" name="repo" placeholder="https://github.com/psf/requests  or  owner/repo  or  C:\\path\\to\\repo" spellcheck="false">
      <button id="go" type="submit">Analyze</button>
      <div class="row">
        <label class="check"><input id="all" type="checkbox"> Show all findings (not only the top 5)</label>
      </div>
      <div class="examples">
        <button type="button" data-repo="psf/requests">psf/requests</button>
        <button type="button" data-repo="tiangolo/fastapi">tiangolo/fastapi</button>
        <button type="button" data-repo="pallets/flask">pallets/flask</button>
        <button type="button" data-repo="https://github.com/Ntooxx/Sentinel">Ntooxx/Sentinel</button>
      </div>
    </form>

    <div id="progress">
      <div class="bar"><i></i></div>
      <div class="log" id="log"></div>
    </div>
    <div class="errbox" id="errbox"></div>
  </div>

  <div class="hint">
    Clones land in <code>demo/.cache/</code> and are reused. Local folder paths are analyzed in place.
  </div>
  <p class="note">
    Local-only demo server. It never executes the cloned code: scanning reads and parses files.
    The report is Sentinel's own self-contained HTML output, served as-is.
  </p>
</div>

<script>
const form = document.getElementById('form');
const input = document.getElementById('repo');
const go = document.getElementById('go');
const allBox = document.getElementById('all');
const progress = document.getElementById('progress');
const log = document.getElementById('log');
const errbox = document.getElementById('errbox');
let timer = null;

document.querySelectorAll('.examples button').forEach(function (b) {
  b.addEventListener('click', function () { input.value = b.dataset.repo; input.focus(); });
});

function renderLines(messages) {
  log.innerHTML = messages.map(function (m) {
    const ok = m.startsWith('Ready');
    const err = m.startsWith('Error');
    const cls = ok ? 'ok' : err ? 'err' : '';
    const mark = ok ? '✓' : err ? '✕' : '»';
    return '<div class="line ' + cls + '"><span>' + mark + '</span><span>' + escapeHtml(m) + '</span></div>';
  }).join('');
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
  });
}

form.addEventListener('submit', async function (event) {
  event.preventDefault();
  if (timer) { clearInterval(timer); timer = null; }
  errbox.className = 'errbox';
  errbox.textContent = '';
  progress.className = 'progress show';
  progress.classList.add('show');
  log.innerHTML = '<div class="line">» Queued…</div>';
  go.disabled = true;

  let jobId;
  try {
    const res = await fetch('/api/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ repo: input.value, all_findings: allBox.checked })
    });
    const data = await res.json();
    if (!res.ok) { throw new Error(data.error || 'Request failed'); }
    jobId = data.id;
  } catch (error) {
    go.disabled = false;
    progress.classList.remove('show');
    errbox.textContent = String(error.message || error);
    errbox.className = 'errbox show';
    return;
  }

  timer = setInterval(async function () {
    let status;
    try {
      const res = await fetch('/api/status?id=' + encodeURIComponent(jobId));
      status = await res.json();
    } catch (error) {
      return;
    }
    renderLines(status.messages || []);
    if (status.state === 'done') {
      clearInterval(timer); timer = null;
      log.innerHTML += '<div class="line ok"><span>✓</span><span>Opening report…</span></div>';
      window.location.href = '/api/report?id=' + encodeURIComponent(jobId);
    } else if (status.state === 'error') {
      clearInterval(timer); timer = null;
      go.disabled = false;
      const line = document.createElement('div');
      line.className = 'line err';
      line.innerHTML = '<span>✕</span><span>' + escapeHtml(status.error || 'Failed') + '</span>';
      log.appendChild(line);
      errbox.textContent = status.error || 'Analysis failed.';
      errbox.className = 'errbox show';
    }
  }, 600);
});
</script>
</body>
</html>
"""


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------


class DemoServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request: Any, client_address: Any) -> None:
        # Browsers close idle keep-alive sockets; that is not a server error.
        error = sys.exc_info()[1]
        if isinstance(error, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


PUBLIC_INDEX_HTML = (
    INDEX_HTML.replace('  or  C:\\path\\to\\repo"', '"')
    .replace(
        "Clones land in <code>demo/.cache/</code> and are reused. Local folder paths are analyzed in place.",
        "Public instance: repository URLs only. Clones are cached briefly and reused.",
    )
)


class Handler(BaseHTTPRequestHandler):
    server_version = "SentinelDemo/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:  # quieter console
        sys.stderr.write("  http %s\n" % (fmt % args))

    def _send(self, status: int, body: bytes, content_type: str, extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_text(self, status: int, text: str, content_type: str = "text/plain; charset=utf-8") -> None:
        self._send(status, text.encode("utf-8"), content_type)

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = urllib.parse.parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            page = PUBLIC_INDEX_HTML if PUBLIC_MODE else INDEX_HTML
            self._send_text(200, page, "text/html; charset=utf-8")
            return
        if path == "/healthz":
            self._send_json(200, {"ok": True})
            return
        if path == "/api/status":
            job = _JOBS.get((query.get("id") or [""])[0])
            if not job:
                self._send_json(404, {"error": "unknown job"})
                return
            self._send_json(200, job.snapshot())
            return
        if path == "/api/report":
            job = _JOBS.get((query.get("id") or [""])[0])
            if not job or job.html is None:
                self._send_text(404, "report not ready", "text/plain; charset=utf-8")
                return
            self._send(
                200,
                job.html.encode("utf-8"),
                "text/html; charset=utf-8",
                {"Content-Security-Policy": REPORT_CSP, "X-Content-Type-Options": "nosniff"},
            )
            return
        if path == "/api/download":
            job = _JOBS.get((query.get("id") or [""])[0])
            kind = (query.get("kind") or ["html"])[0]
            if not job:
                self._send_text(404, "unknown job")
                return
            body = {"html": job.html, "json": job.json, "md": job.markdown}.get(kind)
            if body is None:
                self._send_text(404, "not available", "text/plain; charset=utf-8")
                return
            ext = {"html": ("html", "text/html"), "json": ("json", "application/json"), "md": ("md", "text/markdown")}[kind]
            name = f"sentinel-{_slug(job.label)}.{ext[0]}"
            self._send(
                200,
                body.encode("utf-8"),
                f"{ext[1]}; charset=utf-8",
                {"Content-Disposition": f'attachment; filename="{name}"'},
            )
            return
        self._send_text(404, "not found")

    def do_POST(self) -> None:
        path = urllib.parse.urlparse(self.path).path.rstrip("/") or "/"
        if path != "/api/analyze":
            self._send_text(404, "not found")
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > 64 * 1024:
            self._send_json(413, {"error": "request too large"})
            return
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
            if not isinstance(payload, dict):
                raise ValueError
        except (ValueError, UnicodeDecodeError):
            self._send_json(400, {"error": "invalid JSON body"})
            return

        client_ip = self.client_address[0] if self.client_address else "unknown"
        if PUBLIC_MODE:
            allowed, retry_after = rate_limit_ok(client_ip)
            if not allowed:
                self._send(
                    429,
                    json.dumps({"error": f"Rate limit reached. Try again in {retry_after}s."}).encode("utf-8"),
                    "application/json; charset=utf-8",
                    {"Retry-After": str(retry_after)},
                )
                return

        try:
            kind, value, label = normalize_source(
                str(payload.get("repo") or ""), allow_local=not PUBLIC_MODE
            )
        except InputError as error:
            self._send_json(400, {"error": str(error)})
            return

        job = Job(str(payload.get("repo") or ""), label, kind, value, bool(payload.get("all_findings")))
        _register(job)
        threading.Thread(target=analyze, args=(job,), daemon=True).start()
        self._send_json(202, {"id": job.id, "label": label})


def main(argv: list[str] | None = None) -> int:
    global PUBLIC_MODE

    parser = argparse.ArgumentParser(description="Sentinel web demo - analyze a pasted repository.")
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8765")))
    parser.add_argument("--open", action="store_true", help="Open the landing page in a browser.")
    parser.add_argument(
        "--public",
        action="store_true",
        help=(
            "Harden for a hosted deployment: URL-only input, concurrency cap, "
            "per-client rate limit, shorter clone timeout, bounded cache."
        ),
    )
    args = parser.parse_args(argv)

    PUBLIC_MODE = bool(args.public) or os.environ.get("SENTINEL_DEMO_MODE", "").strip().lower() == "public"

    server = DemoServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}/"
    print(f"Sentinel web demo listening on {url}")
    if PUBLIC_MODE:
        print(
            "Public mode: URL input only, "
            f"{PUBLIC_MAX_CONCURRENT_JOBS} concurrent jobs, "
            f"{PUBLIC_RATE_LIMIT_JOBS} jobs per {PUBLIC_RATE_LIMIT_WINDOW_SECONDS}s per client, "
            f"{PUBLIC_CLONE_TIMEOUT_SECONDS}s clone timeout, cache capped at {PUBLIC_CACHE_KEEP_DIRS} repos."
        )
        print("There is no authentication or quota by identity; put a proxy/limiter in front for real traffic.")
    else:
        print("Paste a repository URL (or a local folder path). Ctrl+C to stop.")
    if args.open:
        threading.Thread(target=lambda: (time.sleep(0.6), webbrowser.open(url)), daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
