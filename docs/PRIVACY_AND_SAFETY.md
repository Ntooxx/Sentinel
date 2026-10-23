# Privacy and Safety

Sentinel 2.0 is local-only by design.

## Network

- No network calls during `scan`, `report`, or `watch`.
- No telemetry, analytics, remote logging, or crash uploads.
- No LLM calls, embeddings, or cloud APIs.
- No CDN assets in HTML; the report is a single self-contained file.

The privacy test monkeypatches `socket.socket` and `urllib.request.urlopen` to fail and then runs scan and every renderer.

## What is stored

Runtime state is stored only under `.sentinel/` in the scanned root:

- `.sentinel/quality.db` plus SQLite `-wal`/`-shm`;
- optional live report output, default `.sentinel/quality.html`.

The database contains file metadata, symbols, imports, findings, and the latest 20 report snapshots. It does not store full source files. Findings may include short evidence strings and redacted secret matches.

No JSON checkpoint or knowledge-base file is used. Old 1.x `.sentinel` state is not migrated and is ignored by discovery.

## What is sent anywhere

Nothing. Sentinel has no remote endpoint and no opt-in upload path.

## Secret handling

The security dimension performs basic pattern checks for:

- provider-shaped tokens (AWS, GitHub, Slack, Google, OpenAI-style, Stripe);
- assigned credentials such as `api_key = "..."`;
- dangerous dynamic execution (`eval`, `exec`, `os.system`, shell-enabled subprocess, unsafe `pickle`/YAML loading);
- hardcoded debug mode.

Matches are redacted before they enter findings, then re-redacted at render time. A redacted value is never included in text, Markdown, HTML, or JSON.

This is **not a security audit**. It is a small set of pattern checks intended to catch obvious mistakes. It can miss real vulnerabilities and can flag test fixtures; test files only receive provider-shaped token checks to reduce fake-credential noise.

## Paths and report safety

- Report paths are project-relative by default.
- HTML escapes project names, file paths, titles, evidence, actions, and limitations.
- Discovery does not follow symlinks.
- Binary files and oversized files are skipped with a reason.
- `.sentinel/` and the watch output file are ignored.
- The report never embeds source files; only short evidence snippets and redacted values.

## Limits

- Sentinel does not execute tests or ingest coverage. "Test mapping" is static import/name evidence.
- Non-Python analysis is conservative heuristics, not deep semantic analysis.
- Security checks are basic and explicitly not an audit.
- Churn requires git history; without git the hotspot rules are skipped and the report says so.
- A cold scan of a very large repository is still I/O-bound; budgets are documented in `sentinel_2.md`.
