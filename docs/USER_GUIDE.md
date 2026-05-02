# User Guide

<p align="center">
  <img src="../logos/logo2.png" alt="Sentinel" width="120">
</p>

## What Sentinel Does

Sentinel watches a project the way a focused engineering lead would:

- It inventories the important files.
- It checks for common project quality signals.
- It remembers what it saw last time.
- It recommends the next useful action.

## Common Commands

Single scan of the current directory:

```bash
python sentinel.py scan .
```

Single scan of another directory:

```bash
python sentinel.py scan /path/to/project
```

Continuous monitoring:

```bash
python sentinel.py watch /path/to/project --interval 30
```

Full markdown report:

```bash
python sentinel.py report /path/to/project
```

Custom config file:

```bash
python sentinel.py scan /path/to/project --config custom-config.json
```

Fast compact scan:

```bash
python sentinel.py scan /path/to/project --fast --compact
```

Tiny brief scan:

```bash
python sentinel.py brief /path/to/project --fast --quiet
```

Saved status without rescanning:

```bash
python sentinel.py status /path/to/project
```

Set up Kilo with Sentinel's no-MCP file bridge:

```bash
python sentinel.py kilo-bridge /path/to/workspace --scan-root app --budget small --fast --force
```

Refresh the Kilo bridge files before asking Kilo to work:

```bash
python sentinel.py kilo-refresh /path/to/workspace --scan-root app --budget small --goal next --fast
```

Keep Kilo bridge files fresh while working:

```bash
python sentinel.py kilo-watch /path/to/workspace --scan-root app --budget small --fast --interval 30
```

Ignore a vendored Sentinel folder inside the same project:

```bash
python sentinel.py brief . --fast --quiet --ignore-path tools/sentinel
```

## Understanding the Output

### Health Score

The health score starts at 100 and drops based on detected issues, but issue type matters.
Maintainability-only signals, such as oversized files, should not collapse a project to 0%.
Scores near 0 are reserved for serious breakage such as missing tests, missing runtime entry points, critical security findings, or unreadable project structure.

Sentinel also reports risk by area:

- Structural risk: oversized files, many imports, complex boundaries
- Runtime risk: entry points, executable surfaces, provider/API paths
- Test risk: missing tests, weak or unclear paired coverage around risky files
- Security risk: secret-handling and unsafe-code signals when assessed

### Issues

Sentinel currently flags:

- Missing tests
- Missing README
- Oversized files by line count
- Oversized files by byte size
- TODO and FIXME markers
- Missing entry points in larger projects

### Suggestions

Suggestions are prioritized by severity and intended to be directly actionable. The top suggestion is the recommended next move.

## Speed and Small Output

If you want lower-latency scans or smaller output to share with an AI assistant, use:

- `--fast` for shallower file analysis
- `--compact` for a tiny text summary
- `brief` for the smallest human-readable summary
- `--top 1` to keep only the highest-priority suggestion
- `--quiet` to suppress informational log lines
- `--format json` for structured, low-noise output
- `status` when you only need the last saved summary

## Kilo File Bridge

The Kilo file bridge is the safest integration when MCP tool calls are unreliable. Sentinel writes compact context into normal project files:

- `CONTEXT.md`: root context Kilo can read before broad exploration.
- `.sentinel/kilo/prompt.md`: focused task prompt with next step and constraints.
- `.sentinel/kilo/focus-files.txt`: the first files Kilo should inspect.
- `.sentinel/kilo/status.json`: freshness, token estimates, and health score.

Run this once to create the Kilo files and bridge:

```bash
project-sentinel kilo-bridge . --scan-root axiom --budget small --fast --force
```

Run this before a new Kilo task:

```bash
project-sentinel kilo-refresh . --scan-root axiom --budget small --goal next --fast
```

Then ask Kilo to read `CONTEXT.md`, follow `.sentinel/kilo/prompt.md`, and only open files listed in `.sentinel/kilo/focus-files.txt` unless more context is truly needed.

## Configuration Tips

Edit `config/config.json` to tune:

- Scan interval
- File extensions to monitor
- Ignored directories
- Maximum scanned file size
- Knowledge and checkpoint storage locations
- Audit rule and pattern file locations

## Environment Variables

- `SENTINEL_CONFIG_PATH`
- `SENTINEL_LOG_LEVEL`
- `SENTINEL_DEBUG_MODE`
- `SENTINEL_HOME`

## Testing the Tool

Run:

```bash
python -m unittest discover -s tests -v
```

This exercises the knowledge base, auditor, and the full Sentinel agent workflow.
