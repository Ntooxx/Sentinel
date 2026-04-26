# Sentinel file bridge

Sentinel writes compact project context into normal workspace files so Kilo can use it without MCP.

## Required flow
- Read `CONTEXT.md` before broad exploration.
- If `CONTEXT.md` is missing or stale, run `project-sentinel kilo-refresh . --scan-root . --budget small --fast`.
- Use `.sentinel/kilo/prompt.md` as the task brief when implementation, debugging, planning, or review starts.
- Use `.sentinel/kilo/focus-files.txt` as the first file list to inspect.
- Only search or open extra files after the focus files are insufficient.
- Refresh with `project-sentinel kilo-refresh . --scan-root . --budget small --fast` after meaningful edits.
