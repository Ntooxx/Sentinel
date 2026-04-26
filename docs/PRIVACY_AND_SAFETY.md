# Privacy And Safety

Sentinel is local-first. Repository scans, summaries, cache files, reports, and dashboard actions run on the local machine.

## What Sentinel Writes

- `.sentinel/knowledge_base.json`
- `.sentinel/checkpoints.json`
- `.sentinel/scan_cache.json`
- `.sentinel/reports/`
- `.sentinel/kilo/`
- `.sentinel/adapters/`
- `SENTINEL_REPORT.md`
- `SENTINEL_REPORT.html`
- `CONTEXT.md` when Kilo bridge commands are used
- URL scan bundles under `sentinel-url-reports/`
- URL clone cache under `sentinel-url-reports/.url-cache/`

Use `project-sentinel scan . --no-write` for a scan that avoids updating knowledge, checkpoints, cache, or scan history.

## Secret Handling

Sentinel detects likely secret-bearing files and redacts their content summaries. It flags:

- `.env` style files
- filenames and paths containing `secret`, `credentials`, or private key terms
- private key blocks
- common API key, token, password, and AWS key patterns

Secret detection is a guardrail, not a formal secret scanner. Keep sensitive paths excluded with `--ignore-path` when needed.

## Network Behavior

Normal scans do not require network access. `analyze-url` uses Git to clone or fetch the requested repository source.
