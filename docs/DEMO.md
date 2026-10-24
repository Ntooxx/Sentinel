# Run the Sentinel demo

Analyze any repository from a web page: paste a URL, get a real quality report.
No account, no API key, no upload, no telemetry, and no per-report cost.

This guide covers running it locally and deploying it as a web service.

## What it is

A single-file, standard-library-only HTTP server (`demo/server.py`). It is a thin
harness: it clones the repository (or uses a local folder), calls the same
`scan_project` the CLI calls, and serves the same self-contained HTML report the
`--format html` output produces.

* **Real, not mocked.** Every number in the report comes from an actual scan of
  the repository you pasted.
* **Zero dependencies.** Python standard library only, like the product.
* **It never executes the code it clones.** Scanning reads and parses files.
* **Local by default.** The last line of the report is literally true: no
  network, no telemetry, no cloud.

## Quick start

```bash
python demo/server.py --open        # http://127.0.0.1:8765
```

That is the whole setup. There is nothing to install: the server bootstraps its
own import path.

## Or run it with no install at all

Two ways to get a report without installing anything:

**In your browser** - <https://ntooxx.github.io/Sentinel/>. Drop a folder or a
`.zip` on the page. Python is downloaded once as WebAssembly (Pyodide, ~12 MB) and
the real `scan_project` runs inside the tab. Nothing is uploaded; the files never
leave the page.

**As a single local file** - the same page exists as a self-contained
`index.html` with the package inlined, so it works from `file://` with no server.
Build it with:

```bash
python tools/build_web_page.py --source <your landing page> --out web/index.html
python tools/build_web_bundle.py --out pkg/bundle.json \
    --inline-html web/index.html --inline-out index.html
```

### What the in-browser runner cannot do

| Limitation | Why | Consequence |
|---|---|---|
| No `git` | the WebAssembly sandbox has no processes | commit metadata, churn and the churn-based rules are skipped; the report's coverage section shows churn as unassessed |
| No URL cloning | cloning needs `git` and network egress | to scan a URL, use `demo/server.py` or the CLI |
| No real threads | `RuntimeError: can't start new thread` | the scanner's work is run sequentially through a shim; results are identical |
| Size caps | browser memory | 30 MB total, 8000 files, 4 MB per file |
| Cold start | downloading the runtime | the first scan takes a few seconds; later scans in the same tab are fast |

Everything else - dimensions, findings, evidence, recommendations, the quality
band - is the real analysis, produced by the same code the CLI runs.

## How to use it

1. Type or paste a repository into the box.

   | Input | What happens |
   |---|---|
   | `https://github.com/psf/requests` | shallow clone, then scan |
   | `psf/requests` | shorthand for a GitHub URL |
   | `C:\path\to\your\repo` | scanned in place, no clone, no network |

2. Press **Analyze**. The page streams progress: `cloning → scanning → rendering`.

3. The report opens. It answers six questions:

   * what the repository is (archetype, components, entry points);
   * how healthy each of the six dimensions is (Architecture, Maintainability,
     Tests, Documentation, Dependencies, Security);
   * the top source risks, each with `file:line` evidence and a recommendation;
   * what changed since the previous scan;
   * what to do next, ordered by impact and effort;
   * what was read, sampled, skipped, and excluded, and what the confidence
     level means.

4. Tick **Show all findings** before analyzing to render every finding instead of
   the top five.

5. **Download** saves the report as HTML, JSON, or Markdown. The JSON is the
   stable contract; the HTML is a rendering of the same object.

## How fast it is

Measured on Windows 11, Python 3.11, warm OS cache, this machine:

| What | Time |
|---|---:|
| Paste a URL → report, first time (`psf/requests`, 128 files) | ~3.4 s (clone ~2.9 s) |
| Same repository, cached refresh | ~1.8 s |
| A local folder, no clone | ~0.26 s |
| FastAPI cold scan (1,228 files, 26k source lines) | 3.0 s |
| FastAPI warm scan, nothing changed | 0.34 s |
| Report rendered from the existing index | 0.11 s |
| Index size for that 1,228-file tree | 4.7 MB |

The clone is usually the slow part, not the analysis. A warm scan only
re-analyzes files whose contents changed, which is why it drops to a fraction of
a second.

These are measurements, not promises. Re-run them yourself with
[`tools/benchmark.py`](../tools/benchmark.py).

## Why local and free matters

Paste-a-repo services generally upload your code, meter you, or queue you. This
does not:

* your code never leaves the machine running the server;
* there are no tokens, keys, quotas, or billing;
* the software is Apache-2.0, so hosting it yourself costs only the host;
* a local folder path needs no network at all - it works offline.

That is the trade in one line: **you trade a hosted convenience for a scan that
is instant, private, and unmetered.**

## Deploy it as a web service

The image is ~60 MB and needs `git` at runtime. Any container host works.

### Docker (anywhere)

```bash
docker build -f demo/Dockerfile -t sentinel-demo .
docker run -p 8080:8080 sentinel-demo          # http://localhost:8080
```

### Render (free tier, one click from the repo)

[`render.yaml`](../render.yaml) is a blueprint for this repository. In Render:
**New → Blueprint → pick this repo**. It builds `demo/Dockerfile`, health-checks
`/healthz`, and deploys. The free instance sleeps after inactivity and wakes on
the next request.

### Fly.io

```bash
fly launch --copy-config --dockerfile demo/Dockerfile   # uses fly.toml
fly deploy
```

[`fly.toml`](../fly.toml) scales to zero machines and stops them when idle.

### Railway / Heroku-style

A [`Procfile`](../Procfile) is provided:

```text
web: python demo/server.py --public
```

The server reads `PORT` from the environment, so these platforms work without
extra configuration.

### Always pass `--public` on a host

`--public` turns on the limits a shared instance needs:

| Limit | Value |
|---|---|
| Accepted input | public repository URLs and `owner/repo` only (no local paths) |
| Concurrent jobs | 2 |
| Rate limit | 6 jobs per 600 s per client |
| Clone timeout | 120 s |
| Cache | bounded to the 8 most recent repositories |

Report responses also carry a strict `Content-Security-Policy`
(`default-src 'none'; style-src 'unsafe-inline'; img-src data:`): the report is
self-contained, so nothing else is allowed to load.

#### What `--public` does **not** solve

This instance has no authentication and no per-identity quota. Anyone who can
reach it can make the server clone and parse public repositories. Before putting
it on the open internet for real traffic, put a reverse proxy or platform
limiter in front of it, and cap the container's CPU, memory, and disk. Treat it
as a demo, not a hardened multi-tenant service.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `git clone failed: ...` | the repository is private, misspelled, or unreachable. Pass a full URL. |
| `The 'git' executable was not found` | install git, or use a local folder path (no git needed). |
| Cloning times out | the repository is very large; the demo clones shallow but not partial. |
| The report says a dimension is `n/a` | that dimension was not assessed for this repository. It is excluded from the weighted score. |
| The scan finds almost nothing | your paste resolved to a directory with no source. Check the coverage section of the report. |

## Files

| Path | Purpose |
|---|---|
| [`demo/server.py`](../demo/server.py) | the whole server |
| [`demo/Dockerfile`](../demo/Dockerfile) | container image for hosting |
| [`render.yaml`](../render.yaml), [`fly.toml`](../fly.toml), [`Procfile`](../Procfile) | deploy configs |
| [`web/index.html`](../web/index.html) | the landing page with the in-browser runner |
| [`web/_runner.block.html`](../web/_runner.block.html) | the runner block, injected into the landing page |
| [`tools/build_web_page.py`](../tools/build_web_page.py) | injects the runner block into a landing page |
| [`tools/build_web_bundle.py`](../tools/build_web_bundle.py) | bundles the package for the browser, optionally inlined |
| [`.github/workflows/pages.yml`](../.github/workflows/pages.yml) | builds and deploys the static site |
| [`tests/test_demo_server.py`](../tests/test_demo_server.py), [`tests/test_web_build.py`](../tests/test_web_build.py) | tests for input handling, public-mode limits and the web build |