# Sentinel web demo

Paste a repository URL, get a real Sentinel quality report. No mock data and no
pre-computed results: the demo clones the repository, runs the same scan the CLI
runs, and serves the same self-contained HTML report.

```bash
python demo/server.py --open        # http://127.0.0.1:8765
```

Then paste `psf/requests`, `https://github.com/psf/requests`, or a local folder
path. Nothing to install; the server bootstraps its own import path.

**Full guide: [`docs/DEMO.md`](../docs/DEMO.md)** - how to use it, how fast it is,
why local-and-free matters, how to deploy it as a web service, public-mode
limits, and troubleshooting.

## Files

| Path | Purpose |
|---|---|
| [`server.py`](server.py) | the whole server: zero dependencies, standard library only |
| [`Dockerfile`](Dockerfile) | container image for hosting |
| [`../render.yaml`](../render.yaml) | Render blueprint (one-click deploy from this repo) |
| [`../fly.toml`](../fly.toml) | Fly.io config |
| [`../Procfile`](../Procfile) | Railway / Heroku-style process definition |
| [`../tests/test_demo_server.py`](../tests/test_demo_server.py) | tests for input handling and public-mode limits |

## What it is, and what it is not

* It is a **thin harness**. All analysis comes from `sentinel2.scanner.scan_project`
  and all rendering from `sentinel2.renderers.render_html`. The demo adds nothing
  to the quality model.
* It is **zero-dependency** and **local by default** (binds to `127.0.0.1`).
* The demo **never executes the cloned code**. Scanning reads and parses files;
  it never imports, runs, or installs anything from the repository.
* The only change to the served report is a small floating "Analyze another"
  bar appended to the demo's copy. The artifact the CLI writes is untouched.

On a public host, always run with `--public` (or `SENTINEL_DEMO_MODE=public`),
which restricts input to repository URLs, caps concurrency, rate-limits per
client, shortens the clone timeout, and bounds the clone cache. It is still not
a hardened multi-tenant service; see [`docs/DEMO.md`](../docs/DEMO.md).

## Cache

Clones live in `demo/.cache/<label>/` and are reused (shallow `git fetch` +
`git reset --hard`, then a fresh clone if that fails). The directory is
git-ignored; delete it any time to start clean.