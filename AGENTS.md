# Project Agent Instructions

Use Sentinel before broad project exploration.

Primary path, no MCP required:
- Read `CONTEXT.md`, which Sentinel generates for `.` with budget `small`.
- Use `.sentinel/kilo/prompt.md` as the task brief.
- Start with `.sentinel/kilo/focus-files.txt` and only read more files when needed.
- Use `project-sentinel retrieve . --query "..."` for task-specific context.
- Use `project-sentinel verify .` after edits to run narrow checks.
- If context is stale or missing, run `project-sentinel kilo-refresh . --scan-root . --budget small --fast`.
- After meaningful edits, refresh Sentinel before continuing broad analysis.

Optional MCP path, if MCP is healthy:
- Use Kilo's MCP dispatcher with `server_name: sentinel` and `tool_name: sentinel_prompt`.
- Do not write a fake `<function=tool>` block in chat.
- Do not pass `sentinel_sentinel_prompt` as the MCP `tool_name`; that is only Kilo's permission key.

Sentinel is configured to scan `.` by default in this workspace.
